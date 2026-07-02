import asyncio
import json
import logging
from datetime import timedelta

import config
import db
import schedule


async def _lessons_for_date(d, groups):
    sheet_id = await schedule.get_sheet_id(d)
    rows = await schedule.fetch_csv(sheet_id) if sheet_id else None
    result = {}
    for group in groups:
        if rows is None or not schedule.has_schedule(rows):
            result[group] = None
        else:
            result[group] = schedule.extract_for_group(rows, group)
    return result


async def watch_cycle(bot):
    groups = await db.subscribed_groups()
    if not groups:
        return
    start = config.today()
    days = [start + timedelta(days=i) for i in range(config.WATCH_DAYS + 1)]
    for d in days:
        try:
            lessons_by_group = await _lessons_for_date(d, groups)
        except Exception:
            logging.exception("fetch failed for %s", d)
            continue
        for group, lessons in lessons_by_group.items():
            sig = schedule.signature(lessons)
            old_sig, old_json = await db.get_cache(group, d.isoformat())
            dumped = json.dumps(lessons, ensure_ascii=False) if lessons else None
            await db.set_cache(group, d.isoformat(), sig, dumped)
            if old_sig is not None and old_sig != sig:
                old_lessons = json.loads(old_json) if old_json else None
                await _notify(bot, group, d, old_lessons, lessons)


async def _notify(bot, group, d, old_lessons, lessons):
    head = f"Расписание на {d.strftime('%d.%m.%Y')}, группа {group}"
    if not lessons:
        text = head + " — пары убрали"
    else:
        changes = schedule.diff_lessons(old_lessons, lessons)
        summary = "\n".join(changes) if changes else "обновилось"
        text = (
            head + " изменилось:\n" + summary + "\n\n"
            + schedule.format_schedule(d, group, lessons)
        )
    for user_id in await db.users_for_group(group):
        try:
            await bot.send_message(user_id, text)
        except Exception:
            logging.exception("send to %s failed", user_id)
        await asyncio.sleep(0.05)


async def run_watcher(bot):
    while True:
        try:
            await watch_cycle(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("watch cycle failed")
        await asyncio.sleep(config.CHECK_INTERVAL)


async def send_digest(bot, kind, d):
    users = await db.digest_users()
    if not users:
        return
    cache = {}
    label = "Расписание на сегодня" if kind == "morning" else "Расписание на завтра"
    for user_id, group in users:
        if group not in cache:
            try:
                cache[group] = await schedule.get_schedule(d, group)
            except Exception:
                logging.exception("digest fetch failed for %s", group)
                cache[group] = ("error", None)
        status, lessons = cache[group]
        if status != "ok":
            continue
        text = label + "\n\n" + schedule.format_schedule(d, group, lessons)
        try:
            await bot.send_message(user_id, text)
        except Exception:
            logging.exception("digest send to %s failed", user_id)
        await asyncio.sleep(0.05)


async def digest_tick(bot):
    now = config.now()
    today = now.date()
    cur = now.hour * 60 + now.minute
    plan = (
        ("morning", config.DIGEST_MORNING, 0),
        ("evening", config.DIGEST_EVENING, 1),
    )
    for kind, (h, m), offset in plan:
        target = h * 60 + m
        if not (target <= cur < target + config.DIGEST_WINDOW):
            continue
        if await db.get_digest_sent(kind) == today.isoformat():
            continue
        await send_digest(bot, kind, today + timedelta(days=offset))
        await db.set_digest_sent(kind, today.isoformat())


async def run_digest(bot):
    while True:
        try:
            await digest_tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("digest tick failed")
        await asyncio.sleep(config.DIGEST_TICK)


async def remind_tick(bot):
    users = await db.remind_users()
    if not users:
        return
    groups = {}
    for user_id, group in users:
        groups.setdefault(group, []).append(user_id)
    now = config.now()
    today = now.date()
    cur = now.hour * 60 + now.minute
    for group, uids in groups.items():
        try:
            status, lessons = await schedule.get_schedule(today, group)
        except Exception:
            logging.exception("remind fetch failed for %s", group)
            continue
        if status != "ok" or not lessons:
            continue
        for num, tm, cell in lessons:
            start, _ = schedule.parse_times(tm)
            if start is None:
                continue
            target = start - config.REMIND_LEAD
            if not (target <= cur < target + config.REMIND_WINDOW):
                continue
            pair = num or tm
            if await db.was_reminded(group, today.isoformat(), pair):
                continue
            await db.mark_reminded(group, today.isoformat(), pair)
            head = f"Через {start - cur} мин {num} пара" if num else f"Через {start - cur} мин пара"
            text = f"{head} ({schedule.fmt_hm(start)}): {schedule.oneline_cell(cell)}"
            for user_id in uids:
                try:
                    await bot.send_message(user_id, text)
                except Exception:
                    logging.exception("remind send to %s failed", user_id)
                await asyncio.sleep(0.05)


async def run_reminder(bot):
    while True:
        try:
            await remind_tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("remind tick failed")
        await asyncio.sleep(config.REMIND_TICK)
