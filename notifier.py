import asyncio
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
            old = await db.get_signature(group, d.isoformat())
            await db.set_signature(group, d.isoformat(), sig)
            if old is not None and old != sig:
                await _notify(bot, group, d, lessons)


async def _notify(bot, group, d, lessons):
    if lessons:
        text = "Обновилось расписание!\n\n" + schedule.format_schedule(d, group, lessons)
    else:
        text = f"Расписание на {d.strftime('%d.%m.%Y')} для группы {group} убрали"
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
