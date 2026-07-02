import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or "0")

PAGE_URL = "https://tspk.org/studentam/novoe-raspisanie-demo.html"

ACADEMIC_YEAR_START = date(2025, 9, 1)
ACADEMIC_YEAR_END = date(2026, 6, 30)

PAGE_CACHE_TTL = 600
DB_PATH = "bot.db"

TZ = ZoneInfo("Europe/Samara")
CHECK_INTERVAL = 900
WATCH_DAYS = 2

DIGEST_MORNING = (7, 30)
DIGEST_EVENING = (20, 0)
DIGEST_WINDOW = 60
DIGEST_TICK = 60

REMIND_LEAD = 15
REMIND_WINDOW = 10
REMIND_TICK = 60


def today():
    return datetime.now(TZ).date()


def now():
    return datetime.now(TZ)
