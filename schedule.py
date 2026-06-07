import asyncio
import csv
import hashlib
import io
import re
import time
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from config import (
    PAGE_URL,
    ACADEMIC_YEAR_START,
    ACADEMIC_YEAR_END,
    PAGE_CACHE_TTL,
)

MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

_page_cache = {"dates": None, "ts": 0.0}


async def _fetch(url):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


def _inline(s):
    return " ".join(s.split())


def _norm_group(s):
    return re.sub(r"[^0-9a-zа-яё]", "", _inline(s).casefold())


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


async def get_dates():
    now = time.time()
    if _page_cache["dates"] is None or now - _page_cache["ts"] > PAGE_CACHE_TTL:
        html = await _fetch(PAGE_URL)
        _page_cache["dates"] = parse_dates(html)
        _page_cache["ts"] = now
    return _page_cache["dates"]


async def get_sheet_id(d):
    return (await get_dates()).get(d)


async def upcoming_dates(today, limit=10):
    dates = await get_dates()
    return sorted(d for d in dates if d >= today)[:limit]


async def fetch_csv(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    text = await _fetch(url)
    return list(csv.reader(io.StringIO(text)))


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
