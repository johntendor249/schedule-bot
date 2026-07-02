import asyncio
import csv
import hashlib
import io
import re
import time
from datetime import date, datetime, timezone

import aiohttp
from bs4 import BeautifulSoup

from config import (
    PAGE_URL,
    ACADEMIC_YEAR_START,
    ACADEMIC_YEAR_END,
    PAGE_CACHE_TTL,
    SHEET_CACHE_TTL,
    TZ,
)

MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

_page_cache = {"dates": None, "ts": 0.0, "updated": None}
_csv_cache = {}


async def _fetch(url, attempts=3):
    timeout = aiohttp.ClientTimeout(total=15)
    last = None
    for i in range(attempts):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.text()
        except Exception as e:
            last = e
            if i < attempts - 1:
                await asyncio.sleep(1 + i)
    raise last


async def check_source():
    try:
        await _fetch(PAGE_URL)
        return True
    except Exception:
        return False


def _inline(s):
    return " ".join(s.split())


def _norm_group(s):
    return re.sub(r"[^0-9a-zа-яё]", "", _inline(s).casefold())


def _norm_text(s):
    return _inline(s).casefold().replace("ё", "е")


def _parse_times(tm):
    toks = re.findall(r"(\d{1,2})[.:](\d{2})", tm or "")
    mins = [int(h) * 60 + int(m) for h, m in toks]
    start = mins[0] if mins else None
    end = mins[1] if len(mins) > 1 else None
    return start, end


def _lines(s):
    return [ln.strip() for ln in s.splitlines() if ln.strip()]


def _extract_sheet_id(href):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", href)
    return m.group(1) if m else None


def _parse_caption(text):
    text = text.lower()
    m = re.search(r"20\d{2}", text)
    year = int(m.group()) if m else None
    month = next((num for name, num in MONTHS.items() if name in text), None)
    if month is None or year is None:
        return None, None
    return month, year


def parse_dates(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for table in soup.select("table[class*=cal-table]"):
        caption = table.find("caption")
        if not caption:
            continue
        month, year = _parse_caption(caption.get_text())
        if month is None:
            continue
        for a in table.find_all("a", href=True):
            sheet_id = _extract_sheet_id(a["href"])
            day = a.get_text(strip=True)
            if not sheet_id or not day.isdigit():
                continue
            d = date(year, month, int(day))
            if ACADEMIC_YEAR_START <= d <= ACADEMIC_YEAR_END:
                result.setdefault(d, sheet_id)
    return result


def _parse_updated(html):
    m = re.search(
        r"Обновлено\s+(\d{2}\.\d{2}\.\d{4}(?:\s+\d{1,2}[:.]\d{2})?)", html
    )
    return m.group(1) if m else None


async def get_dates():
    now = time.time()
    if _page_cache["dates"] is None or now - _page_cache["ts"] > PAGE_CACHE_TTL:
        html = await _fetch(PAGE_URL)
        _page_cache["dates"] = parse_dates(html)
        _page_cache["updated"] = _parse_updated(html)
        _page_cache["ts"] = now
    return _page_cache["dates"]


async def page_updated():
    await get_dates()
    return _page_cache["updated"]


async def sheet_url(d):
    sid = await get_sheet_id(d)
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None


async def get_sheet_id(d):
    return (await get_dates()).get(d)


async def upcoming_dates(today, limit=10):
    dates = await get_dates()
    return sorted(d for d in dates if d >= today)[:limit]


async def fetch_csv(sheet_id, force=False):
    now = time.time()
    if not force:
        hit = _csv_cache.get(sheet_id)
        if hit and now - hit[1] < SHEET_CACHE_TTL:
            return hit[0]
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    text = await _fetch(url)
    rows = list(csv.reader(io.StringIO(text)))
    _csv_cache[sheet_id] = (rows, now)
    return rows


def _is_header(row):
    return len(row) >= 2 and row[0].strip() == "Пара" and row[1].strip() == "Время"


def has_schedule(rows):
    return any(_is_header(r) for r in rows)


def all_groups(rows):
    groups = []
    for r in rows:
        if _is_header(r):
            groups += [_inline(c) for c in r[2:] if _inline(c)]
    return sorted(set(groups))


def day_pairs(rows):
    seen = {}
    for r in rows:
        if _is_header(r) or not r:
            continue
        num = _inline(r[0]) if len(r) > 0 else ""
        tm = "-".join(_lines(r[1])) if len(r) > 1 else ""
        if num.isdigit() and num not in seen:
            seen[num] = tm
    return [(n, seen[n]) for n in sorted(seen, key=int)]


def current_and_next(lessons, now_min):
    timed = []
    for num, tm, cell in lessons:
        start, end = _parse_times(tm)
        if start is not None:
            timed.append((start, end, num, tm, cell))
    timed.sort()
    current = nxt = None
    for start, end, num, tm, cell in timed:
        if end is not None and start <= now_min <= end:
            current = (num, tm, cell)
        elif start > now_min and nxt is None:
            nxt = (num, tm, cell)
    return current, nxt


def extract_for_room(rows, room):
    digits = re.sub(r"\D", "", room)
    if not digits:
        return []
    pattern = re.compile(r"каб\.?\s*0*" + re.escape(digits) + r"\b")
    result = []
    header = None
    for r in rows:
        if _is_header(r):
            header = r
            continue
        if header is None:
            continue
        num = _inline(r[0]) if len(r) > 0 else ""
        tm = "-".join(_lines(r[1])) if len(r) > 1 else ""
        for col in range(2, len(r)):
            raw = r[col]
            if not raw.strip():
                continue
            if pattern.search(_norm_text(raw)):
                group = _inline(header[col]) if col < len(header) else ""
                result.append((num, tm, group, "\n".join(_lines(raw))))
    return result


def teacher_windows(rows, lessons):
    busy = sorted({int(n) for n, tm, g, c in lessons if n.isdigit()})
    if not busy:
        return []
    lo, hi = busy[0], busy[-1]
    return [
        (n, tm)
        for n, tm in day_pairs(rows)
        if n.isdigit() and lo < int(n) < hi and int(n) not in busy
    ]


def extract_for_group(rows, group):
    target = _norm_group(group)
    for i, r in enumerate(rows):
        if not _is_header(r):
            continue
        cols = [_norm_group(c) for c in r]
        if target not in cols:
            continue
        col = cols.index(target)
        lessons = []
        for r2 in rows[i + 1:]:
            if _is_header(r2):
                break
            num = _inline(r2[0]) if len(r2) > 0 else ""
            tm = "-".join(_lines(r2[1])) if len(r2) > 1 else ""
            cell = "\n".join(_lines(r2[col])) if len(r2) > col else ""
            if cell:
                lessons.append((num, tm, cell))
        return lessons
    return None


def extract_for_teacher(rows, teacher):
    words = _norm_text(teacher).split()
    if not words:
        return []
    pattern = re.compile(r"\b" + re.escape(words[0]) + r"\b")
    result = []
    header = None
    for r in rows:
        if _is_header(r):
            header = r
            continue
        if header is None:
            continue
        num = _inline(r[0]) if len(r) > 0 else ""
        tm = "-".join(_lines(r[1])) if len(r) > 1 else ""
        for col in range(2, len(r)):
            raw = r[col]
            if not raw.strip():
                continue
            if pattern.search(_norm_text(raw)):
                group = _inline(header[col]) if col < len(header) else ""
                result.append((num, tm, group, "\n".join(_lines(raw))))
    return result


def format_teacher_schedule(d, teacher, lessons, windows=None):
    head = f"Расписание преподавателя на {d.strftime('%d.%m.%Y')}\nПреподаватель: {teacher}"
    if not lessons:
        return head + "\n\nПар нет"
    blocks = [head]
    for num, tm, group, cell in lessons:
        title = (f"{num} пара" + (f", {tm}" if tm else "")) if num else tm
        parts = [p for p in (title, f"Группа {group}" if group else "", cell) if p]
        blocks.append("\n".join(parts))
    if windows:
        w = ", ".join(f"{n} пара ({tm})" if tm else f"{n} пара" for n, tm in windows)
        blocks.append("Окна между парами: " + w)
    return "\n\n".join(blocks)


def _fmt_hm(m):
    return f"{m // 60}:{m % 60:02d}"


def format_now_next(d, group, lessons, now_min):
    head = f"Группа {group}, {d.strftime('%d.%m.%Y')}"
    if not lessons:
        return head + "\n\nНа сегодня расписания нет"
    current, nxt = current_and_next(lessons, now_min)
    parts = [head]
    if current:
        num, tm, cell = current
        start, end = _parse_times(tm)
        tail = f" (до {_fmt_hm(end)}, осталось {end - now_min} мин)" if end is not None else ""
        parts.append(f"Сейчас идет {num} пара{tail}:\n{cell}")
    else:
        parts.append("Сейчас пары нет")
    if nxt:
        num, tm, cell = nxt
        start, end = _parse_times(tm)
        tail = f" в {_fmt_hm(start)} (через {start - now_min} мин)" if start is not None else ""
        parts.append(f"Следующая — {num} пара{tail}:\n{cell}")
    else:
        parts.append("Дальше пар на сегодня нет")
    return "\n\n".join(parts)


def format_bells(d, pairs):
    head = f"Расписание звонков на {d.strftime('%d.%m.%Y')}"
    if not pairs:
        return head + "\n\nНа этот день пар нет"
    lines = [head, ""]
    for num, tm in pairs:
        start, end = _parse_times(tm)
        if start is not None and end is not None:
            lines.append(f"{num} пара — {_fmt_hm(start)}-{_fmt_hm(end)}")
        elif start is not None:
            lines.append(f"{num} пара — {_fmt_hm(start)}")
        else:
            lines.append(f"{num} пара — {tm}")
    return "\n".join(lines)


def _dt_utc(d, minutes):
    local = datetime(d.year, d.month, d.day, minutes // 60, minutes % 60, tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def lessons_to_events(d, group, lessons):
    events = []
    for num, tm, cell in lessons:
        start, end = _parse_times(tm)
        if start is None:
            continue
        if end is None:
            end = start + 90
        subject = cell.split("\n")[0] if cell else "Пара"
        summary = f"{num} пара: {subject}" if num else subject
        events.append((_dt_utc(d, start), _dt_utc(d, end), summary, cell))
    return events


def build_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//tspk schedule bot//RU",
        "CALSCALE:GREGORIAN",
    ]
    for i, (start, end, summary, desc) in enumerate(events):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{start}-{i}@tspk-bot",
            f"DTSTAMP:{start}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{_ics_escape(summary)}",
        ]
        if desc:
            lines.append(f"DESCRIPTION:{_ics_escape(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def format_room_schedule(d, room, lessons):
    head = f"Кабинет {room}, {d.strftime('%d.%m.%Y')}"
    if not lessons:
        return head + "\n\nЗанятий нет"
    blocks = [head]
    for num, tm, group, cell in lessons:
        title = (f"{num} пара" + (f", {tm}" if tm else "")) if num else tm
        parts = [p for p in (title, f"Группа {group}" if group else "", cell) if p]
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def format_schedule(d, group, lessons):
    head = f"Расписание на {d.strftime('%d.%m.%Y')}\nГруппа: {group}"
    if not lessons:
        return head + "\n\nПар нет"
    blocks = [head]
    for num, tm, cell in lessons:
        if num:
            title = f"{num} пара" + (f", {tm}" if tm else "")
            blocks.append(f"{title}\n{cell}")
        else:
            blocks.append(cell)
    return "\n\n".join(blocks)


def signature(lessons):
    if not lessons:
        return "none"
    text = "\n".join(f"{num}|{tm}|{cell}" for num, tm, cell in lessons)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _oneline_cell(cell):
    return ", ".join(_lines(cell)) if cell else ""


def diff_lessons(old, new):
    def by_num(ls):
        numbered = {}
        extras = []
        for num, tm, cell in ls or []:
            if num:
                numbered[num] = (tm, cell)
            else:
                extras.append(cell)
        return numbered, extras

    old_num, old_extra = by_num(old)
    new_num, new_extra = by_num(new)
    lines = []
    nums = sorted(
        set(old_num) | set(new_num),
        key=lambda n: int(n) if n.isdigit() else 999,
    )
    for num in nums:
        if num in old_num and num not in new_num:
            lines.append(f"{num} пара — убрали")
        elif num not in old_num and num in new_num:
            lines.append(f"{num} пара — поставили: {_oneline_cell(new_num[num][1])}")
        elif old_num[num] != new_num[num]:
            lines.append(f"{num} пара — теперь: {_oneline_cell(new_num[num][1])}")
    for cell in new_extra:
        if cell not in old_extra:
            lines.append(f"добавили: {_oneline_cell(cell)}")
    for cell in old_extra:
        if cell not in new_extra:
            lines.append(f"убрали: {_oneline_cell(cell)}")
    return lines


async def canonical_group(group, around):
    target = _norm_group(group)
    dates = await upcoming_dates(around, limit=3)
    if not dates:
        known = sorted(await get_dates())
        dates = known[-3:]
    for d in dates:
        sheet_id = await get_sheet_id(d)
        if not sheet_id:
            continue
        rows = await fetch_csv(sheet_id)
        if not has_schedule(rows):
            continue
        for g in all_groups(rows):
            if _norm_group(g) == target:
                return g
    return None


async def get_schedule(d, group):
    sheet_id = await get_sheet_id(d)
    if not sheet_id:
        return "no_date", None
    rows = await fetch_csv(sheet_id)
    if not has_schedule(rows):
        return "bad_sheet", None
    lessons = extract_for_group(rows, group)
    if lessons is None:
        return "no_group", all_groups(rows)
    return "ok", lessons


async def get_teacher_schedule(d, teacher):
    sheet_id = await get_sheet_id(d)
    if not sheet_id:
        return "no_date", None
    rows = await fetch_csv(sheet_id)
    if not has_schedule(rows):
        return "bad_sheet", None
    lessons = extract_for_teacher(rows, teacher)
    if not lessons:
        return "not_found", None
    return "ok", {"lessons": lessons, "windows": teacher_windows(rows, lessons)}


async def get_room_schedule(d, room):
    sheet_id = await get_sheet_id(d)
    if not sheet_id:
        return "no_date", None
    rows = await fetch_csv(sheet_id)
    if not has_schedule(rows):
        return "bad_sheet", None
    lessons = extract_for_room(rows, room)
    if not lessons:
        return "not_found", None
    return "ok", lessons


async def get_bells(d):
    sheet_id = await get_sheet_id(d)
    if not sheet_id:
        return "no_date", None
    rows = await fetch_csv(sheet_id)
    if not has_schedule(rows):
        return "bad_sheet", None
    return "ok", day_pairs(rows)


async def all_known_groups(around):
    dates = await upcoming_dates(around, limit=5)
    if not dates:
        known = sorted(await get_dates())
        dates = known[-5:]
    for d in dates:
        sid = await get_sheet_id(d)
        if not sid:
            continue
        rows = await fetch_csv(sid)
        if has_schedule(rows):
            return all_groups(rows)
    return []


parse_times = _parse_times
fmt_hm = _fmt_hm
oneline_cell = _oneline_cell


if __name__ == "__main__":
    import sys

    async def _main():
        args = sys.argv[1:]
        d = date.fromisoformat(args[0]) if args else date(2025, 9, 1)
        group = args[1] if len(args) > 1 else "ИСиП-21"
        status, payload = await get_schedule(d, group)
        if status == "ok":
            print(format_schedule(d, group, payload))
        elif status == "no_group":
            print("Группа не найдена. Доступные:")
            print(", ".join(payload))
        else:
            print("Статус:", status)

    asyncio.run(_main())
