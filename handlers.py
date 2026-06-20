import logging
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import config
import db
import schedule

router = Router()

BTN_NOW = "Сейчас"
BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"
BTN_WEEK = "Неделя"
BTN_UPCOMING = "Ближайшие даты"
BTN_GROUP = "Сменить группу"
BTN_NOTIFY = "Уведомления"
BTN_TEACHER = "Преподаватель"
BTN_ROOM = "Кабинет"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

HELP_TEXT = (
    "Показываю расписание ТСПК.\n\n"
    "Сейчас — текущая и следующая пара.\n"
    "Сегодня / Завтра / Неделя / Ближайшие даты — расписание твоей группы.\n"
    "Дату можно прислать текстом: 05.06 или 05.06.2026.\n"
    "Преподаватель — расписание по фамилии.\n"
    "Кабинет — что проходит в кабинете.\n\n"
    "/group — сменить группу\n"
    "/groups — список всех групп\n"
    "/look ГРУППА [дата] — расписание другой группы без сохранения\n"
    "/teacher — поиск по преподавателю\n"
    "/room — поиск по кабинету\n"
    "/subscribe — уведомления об изменениях\n"
    "/unsubscribe — выключить уведомления"
)

teacher_lookup = {}
room_lookup = {}


class Form(StatesGroup):
    waiting_group = State()
    waiting_teacher = State()
    waiting_room = State()


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NOW)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_UPCOMING)],
            [KeyboardButton(text=BTN_TEACHER), KeyboardButton(text=BTN_ROOM)],
            [KeyboardButton(text=BTN_GROUP), KeyboardButton(text=BTN_NOTIFY)],
        ],
        resize_keyboard=True,
    )


def date_label(d):
    return f"{d.strftime('%d.%m')} {WEEKDAYS[d.weekday()]}"


def parse_user_date(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m"):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if fmt == "%d.%m":
            year = (
                config.ACADEMIC_YEAR_START.year
                if d.month >= 9
                else config.ACADEMIC_YEAR_END.year
            )
            d = d.replace(year=year)
        return d
    return None


async def send_long(message: Message, text):
    limit = 4000
    buf = ""
    for block in text.split("\n\n"):
        if buf and len(buf) + len(block) + 2 > limit:
            await message.answer(buf)
            buf = ""
        buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        await message.answer(buf)


async def source_footer(d):
    try:
        url = await schedule.sheet_url(d)
        updated = await schedule.page_updated()
    except Exception:
        return ""
    lines = []
    if updated:
        lines.append(f"Обновлено на сайте: {updated}")
    if url:
        lines.append(f"Оригинал: {url}")
    return "\n\n" + "\n".join(lines) if lines else ""


async def send_schedule(message: Message, group, d):
    try:
        status, payload = await schedule.get_schedule(d, group)
    except Exception:
        logging.exception("get_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        await message.answer(schedule.format_schedule(d, group, payload) + await source_footer(d))
    elif status == "no_date":
        await message.answer(f"На {d.strftime('%d.%m.%Y')} расписания нет")
    elif status == "no_group":
        groups = ", ".join(payload)
        await message.answer(
            f"Группа {group} на эту дату не найдена.\nДоступные группы:\n{groups}"
        )
    else:
        await message.answer("Не получилось прочитать таблицу с расписанием, попробуй позже")


async def require_group(message: Message):
    group = await db.get_group(message.from_user.id)
    if not group:
        await message.answer("Сначала укажи группу: /group")
    return group


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    group = await db.get_group(message.from_user.id)
    if group:
        await message.answer(f"Твоя группа: {group}", reply_markup=main_kb())
    else:
        await state.set_state(Form.waiting_group)
        await message.answer("Привет. Напиши свою группу, например ИСиП-21")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(Command("group"))
@router.message(F.text == BTN_GROUP)
async def ask_group(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_group)
    await message.answer("Напиши группу, например ИСиП-21")


@router.message(Form.waiting_group)
async def save_group(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Напиши группу текстом, например ИСиП-21")
        return
    group = message.text.strip()
    found = True
    try:
        canonical = await schedule.canonical_group(group, config.today())
        if canonical:
            group = canonical
        else:
            found = False
    except Exception:
        logging.exception("canonical_group failed")
    await db.set_group(message.from_user.id, group)
    await state.clear()
    if found:
        await message.answer(f"Группа сохранена: {group}", reply_markup=main_kb())
    else:
        await message.answer(
            f"Сохранил {group}, но такой группы в расписании нет. Проверь написание, если что - /group",
            reply_markup=main_kb(),
        )


async def enable_notify(message: Message, group):
    try:
        exists = await schedule.canonical_group(group, config.today()) is not None
    except Exception:
        logging.exception("canonical_group failed")
        exists = True
    if not exists:
        await message.answer(
            f"Не могу подписать: группы {group} нет в расписании. Смени группу через /group"
        )
        return
    await db.set_notify(message.from_user.id, True)
    await message.answer(f"Подписка включена. Пришлю, если расписание {group} изменится")


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    group = await require_group(message)
    if group:
        await enable_notify(message, group)


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    await db.set_notify(message.from_user.id, False)
    await message.answer("Уведомления выключены")


@router.message(Command("groups"))
async def cmd_groups(message: Message):
    try:
        groups = await schedule.all_known_groups(config.today())
    except Exception:
        logging.exception("all_known_groups failed")
        groups = []
    if not groups:
        await message.answer("Не удалось получить список групп, попробуй позже")
        return
    await send_long(message, "Доступные группы:\n" + ", ".join(groups))


@router.message(Command("look"))
async def cmd_look(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /look ГРУППА [дата]\nНапример: /look ИСиП-23 25.06")
        return
    group = parts[1]
    if len(parts) > 2:
        d = parse_user_date(parts[2])
        if d is None:
            await message.answer("Не понял дату. Пример: /look ИСиП-23 25.06")
            return
    else:
        d = config.today()
    await send_schedule(message, group, d)


@router.message(F.text == BTN_NOTIFY)
async def btn_notify(message: Message):
    group = await require_group(message)
    if not group:
        return
    if await db.get_notify(message.from_user.id):
        await db.set_notify(message.from_user.id, False)
        await message.answer("Уведомления выключены")
    else:
        await enable_notify(message, group)


@router.message(F.text == BTN_TODAY)
async def btn_today(message: Message):
    group = await require_group(message)
    if group:
        await send_schedule(message, group, config.today())


@router.message(F.text == BTN_TOMORROW)
async def btn_tomorrow(message: Message):
    group = await require_group(message)
    if group:
        await send_schedule(message, group, config.today() + timedelta(days=1))


@router.message(Command("now"))
@router.message(F.text == BTN_NOW)
async def btn_now(message: Message):
    group = await require_group(message)
    if not group:
        return
    d = config.today()
    try:
        status, payload = await schedule.get_schedule(d, group)
    except Exception:
        logging.exception("get_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        n = config.now()
        await message.answer(
            schedule.format_now_next(d, group, payload, n.hour * 60 + n.minute)
        )
    elif status == "no_date":
        await message.answer("На сегодня расписания нет")
    elif status == "no_group":
        await message.answer(f"Группы {group} нет в расписании на сегодня. Смени через /group")
    else:
        await message.answer("Не получилось прочитать таблицу с расписанием, попробуй позже")


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def btn_week(message: Message):
    group = await require_group(message)
    if not group:
        return
    dates = await schedule.upcoming_dates(config.today(), limit=6)
    if not dates:
        await message.answer("Ближайших дней с расписанием нет")
        return
    chunks = []
    for d in dates:
        try:
            status, payload = await schedule.get_schedule(d, group)
        except Exception:
            logging.exception("get_schedule failed")
            continue
        if status == "ok":
            chunks.append(schedule.format_schedule(d, group, payload))
    if not chunks:
        await message.answer(f"Расписание для группы {group} не найдено")
        return
    await send_long(message, "\n\n———\n\n".join(chunks) + await source_footer(dates[0]))


@router.message(F.text == BTN_UPCOMING)
async def btn_upcoming(message: Message):
    dates = await schedule.upcoming_dates(config.today())
    if not dates:
        await message.answer("Ближайших дат с расписанием нет")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=date_label(d), callback_data=f"date:{d.isoformat()}")]
            for d in dates
        ]
    )
    await message.answer("Выбери дату:", reply_markup=kb)


@router.callback_query(F.data.startswith("date:"))
async def pick_date(callback: CallbackQuery):
    group = await db.get_group(callback.from_user.id)
    if not group:
        await callback.message.answer("Сначала укажи группу: /group")
        await callback.answer()
        return
    d = date.fromisoformat(callback.data.split(":", 1)[1])
    await send_schedule(callback.message, group, d)
    await callback.answer()


async def send_teacher(message: Message, teacher, d):
    try:
        status, payload = await schedule.get_teacher_schedule(d, teacher)
    except Exception:
        logging.exception("get_teacher_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        text = schedule.format_teacher_schedule(
            d, teacher, payload["lessons"], payload["windows"]
        )
        await message.answer(text + await source_footer(d))
    elif status == "no_date":
        await message.answer(f"На {d.strftime('%d.%m.%Y')} расписания нет")
    elif status == "not_found":
        await message.answer(
            f"На {d.strftime('%d.%m.%Y')} пар у преподавателя {teacher} не найдено. "
            "Проверь фамилию или выбери другую дату."
        )
    else:
        await message.answer("Не получилось прочитать таблицу с расписанием, попробуй позже")


async def send_teacher_dates(message: Message):
    dates = await schedule.upcoming_dates(config.today())
    if not dates:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=date_label(d), callback_data=f"tdate:{d.isoformat()}")]
            for d in dates
        ]
    )
    await message.answer("Другая дата:", reply_markup=kb)


@router.message(Command("teacher"))
@router.message(F.text == BTN_TEACHER)
async def ask_teacher(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_teacher)
    await message.answer("Напиши фамилию преподавателя, например Соколова")


@router.message(Form.waiting_teacher)
async def search_teacher(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Напиши фамилию преподавателя текстом, например Соколова")
        return
    teacher = message.text.strip()
    teacher_lookup[message.from_user.id] = teacher
    await state.clear()
    await send_teacher(message, teacher, config.today())
    await send_teacher_dates(message)


@router.callback_query(F.data.startswith("tdate:"))
async def pick_teacher_date(callback: CallbackQuery):
    teacher = teacher_lookup.get(callback.from_user.id)
    if not teacher:
        await callback.message.answer("Повтори поиск: /teacher и фамилию")
        await callback.answer()
        return
    d = date.fromisoformat(callback.data.split(":", 1)[1])
    await send_teacher(callback.message, teacher, d)
    await callback.answer()


async def send_room(message: Message, room, d):
    try:
        status, payload = await schedule.get_room_schedule(d, room)
    except Exception:
        logging.exception("get_room_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        await message.answer(schedule.format_room_schedule(d, room, payload) + await source_footer(d))
    elif status == "no_date":
        await message.answer(f"На {d.strftime('%d.%m.%Y')} расписания нет")
    elif status == "not_found":
        await message.answer(
            f"На {d.strftime('%d.%m.%Y')} занятий в кабинете {room} не найдено. "
            "Проверь номер или выбери другую дату."
        )
    else:
        await message.answer("Не получилось прочитать таблицу с расписанием, попробуй позже")


async def send_room_dates(message: Message):
    dates = await schedule.upcoming_dates(config.today())
    if not dates:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=date_label(d), callback_data=f"rdate:{d.isoformat()}")]
            for d in dates
        ]
    )
    await message.answer("Другая дата:", reply_markup=kb)


@router.message(Command("room"))
@router.message(F.text == BTN_ROOM)
async def ask_room(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_room)
    await message.answer("Напиши номер кабинета, например 305")


@router.message(Form.waiting_room)
async def search_room(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Напиши номер кабинета, например 305")
        return
    room = message.text.strip()
    room_lookup[message.from_user.id] = room
    await state.clear()
    await send_room(message, room, config.today())
    await send_room_dates(message)


@router.callback_query(F.data.startswith("rdate:"))
async def pick_room_date(callback: CallbackQuery):
    room = room_lookup.get(callback.from_user.id)
    if not room:
        await callback.message.answer("Повтори поиск: /room и номер кабинета")
        await callback.answer()
        return
    d = date.fromisoformat(callback.data.split(":", 1)[1])
    await send_room(callback.message, room, d)
    await callback.answer()


@router.message(F.text)
async def free_text(message: Message):
    d = parse_user_date(message.text)
    if d is None:
        await message.answer("Не понял. Список команд: /help")
        return
    group = await require_group(message)
    if group:
        await send_schedule(message, group, d)
