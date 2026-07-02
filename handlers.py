import logging
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
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


def _feature_of(event):
    text = getattr(event, "text", None)
    if text:
        text = text.strip()
        if text in MENU_BUTTONS:
            return text
        if text.startswith("/"):
            return text.split()[0].lower()
        return "ввод текста"
    if getattr(event, "data", None):
        return "выбор даты"
    return None


async def track_user(handler, event, data):
    user = getattr(event, "from_user", None)
    if user:
        try:
            await db.touch_user(user.id, user.username, user.first_name)
            feature = _feature_of(event)
            if feature:
                await db.bump_feature(feature)
        except Exception:
            logging.exception("track_user failed")
    return await handler(event, data)


router.message.middleware(track_user)
router.callback_query.middleware(track_user)

BTN_NOW = "Сейчас"
BTN_BELLS = "Звонки"
BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"
BTN_WEEK = "Неделя"
BTN_UPCOMING = "Ближайшие даты"
BTN_GROUP = "Сменить группу"
BTN_NOTIFY = "Уведомления"
BTN_TEACHER = "Преподаватель"
BTN_ROOM = "Кабинет"

MENU_BUTTONS = {
    BTN_NOW, BTN_BELLS, BTN_TODAY, BTN_TOMORROW, BTN_WEEK, BTN_UPCOMING,
    BTN_GROUP, BTN_NOTIFY, BTN_TEACHER, BTN_ROOM,
}

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

HELP_TEXT = (
    "Показываю расписание ТСПК.\n\n"
    "Сейчас — текущая и следующая пара.\n"
    "Звонки — время начала и конца пар.\n"
    "Сегодня / Завтра / Неделя / Ближайшие даты — расписание твоей группы.\n"
    "Дату можно прислать текстом: 05.06 или 05.06.2026.\n"
    "Преподаватель — расписание по фамилии, можно запомнить свою.\n"
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
            [KeyboardButton(text=BTN_NOW), KeyboardButton(text=BTN_BELLS)],
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


def ics_day_kb(d):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="В календарь", callback_data=f"ics:{d.isoformat()}")]
        ]
    )


async def send_schedule(message: Message, group, d, offer_ics=False):
    try:
        status, payload = await schedule.get_schedule(d, group)
    except Exception:
        logging.exception("get_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        kb = ics_day_kb(d) if offer_ics and payload else None
        await message.answer(
            schedule.format_schedule(d, group, payload) + await source_footer(d),
            reply_markup=kb,
        )
    elif status == "no_date":
        await message.answer(f"На {d.strftime('%d.%m.%Y')} расписания нет")
    elif status == "no_group":
        groups = ", ".join(payload)
        await message.answer(
            f"Группа {group} на эту дату не найдена.\nДоступные группы:\n{groups}"
        )
    else:
        await message.answer("Не получилось прочитать таблицу с расписанием, попробуй позже")


async def require_group(message: Message, state: FSMContext):
    group = await db.get_group(message.from_user.id)
    if not group:
        await state.set_state(Form.waiting_group)
        await message.answer("Сначала напиши свою группу, например ИСиП-21")
    return group


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    group = await db.get_group(message.from_user.id)
    hint = (
        "Я бот с расписанием ТСПК.\n\n"
        "Студентам: нажми Сегодня, Завтра или Неделя — один раз спрошу группу.\n"
        "Преподавателям: нажми Преподаватель и введи фамилию.\n\n"
        "Все кнопки на клавиатуре снизу. Команды — /help"
    )
    head = f"Твоя группа: {group}" if group else "Привет!"
    await message.answer(f"{head}\n\n{hint}", reply_markup=main_kb())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Твой id: {message.from_user.id}")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        return
    s = await db.stats()
    lines = [
        "Статистика бота",
        "",
        f"Всего: {s['total']} (с группой {s['with_group']}, без группы {s['no_group']})",
        f"Подписаны: {s['subs']}",
        f"Активны: сегодня {s['dau']}, 7 дней {s['wau']}, 30 дней {s['mau']}",
        f"Новые: сегодня {s['new_today']}, за неделю {s['new_week']}",
        f"Всего действий: {s['total_hits']}",
    ]
    if s["specialties"]:
        lines += ["", "По специальностям:"] + [f"{k} — {c}" for k, c in s["specialties"]]
    if s["groups"]:
        lines += ["", "Топ групп:"] + [f"{g} — {c}" for g, c in s["groups"]]
    feats = await db.feature_stats()
    if feats:
        lines += ["", "Использование:"] + [f"{f} — {c}" for f, c in feats]
    recent = await db.recent_users(10)
    if recent:
        lines += ["", "Последние:"]
        for uid, username, first, group, seen in recent:
            who = f"@{username}" if username else (first or f"id{uid}")
            lines.append(f"{who} — {group or '—'} — {seen or '?'}")
    await send_long(message, "\n".join(lines))


@router.message(Command("group"))
@router.message(F.text == BTN_GROUP)
async def ask_group(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_group)
    await message.answer("Напиши группу, например ИСиП-21")


@router.message(Form.waiting_group, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))
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
async def cmd_subscribe(message: Message, state: FSMContext):
    group = await require_group(message, state)
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
async def btn_notify(message: Message, state: FSMContext):
    group = await require_group(message, state)
    if not group:
        return
    if await db.get_notify(message.from_user.id):
        await db.set_notify(message.from_user.id, False)
        await message.answer("Уведомления выключены")
    else:
        await enable_notify(message, group)


@router.message(F.text == BTN_TODAY)
async def btn_today(message: Message, state: FSMContext):
    group = await require_group(message, state)
    if group:
        await send_schedule(message, group, config.today(), offer_ics=True)


@router.message(F.text == BTN_TOMORROW)
async def btn_tomorrow(message: Message, state: FSMContext):
    group = await require_group(message, state)
    if group:
        await send_schedule(message, group, config.today() + timedelta(days=1), offer_ics=True)


@router.message(Command("now"))
@router.message(F.text == BTN_NOW)
async def btn_now(message: Message, state: FSMContext):
    group = await require_group(message, state)
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


@router.message(Command("bells"))
@router.message(F.text == BTN_BELLS)
async def btn_bells(message: Message):
    d = config.today()
    try:
        status, pairs = await schedule.get_bells(d)
        if status == "no_date":
            dates = await schedule.upcoming_dates(d, limit=1)
            if dates:
                d = dates[0]
                status, pairs = await schedule.get_bells(d)
    except Exception:
        logging.exception("get_bells failed")
        await message.answer("Не получилось загрузить звонки, попробуй позже")
        return
    if status == "ok":
        await message.answer(schedule.format_bells(d, pairs))
    elif status == "no_date":
        await message.answer("Ближайших дней с расписанием нет")
    else:
        await message.answer("Не получилось прочитать расписание звонков, попробуй позже")


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def btn_week(message: Message, state: FSMContext):
    group = await require_group(message, state)
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
    await message.answer(
        "Добавить эти дни в календарь:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Неделя в календарь", callback_data="icsweek")]
            ]
        ),
    )


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
    await send_schedule(callback.message, group, d, offer_ics=True)
    await callback.answer()


async def send_ics(message: Message, events, filename):
    ics = schedule.build_ics(events)
    await message.answer_document(
        BufferedInputFile(ics.encode("utf-8"), filename=filename)
    )


@router.callback_query(F.data.startswith("ics:"))
async def ics_day(callback: CallbackQuery):
    group = await db.get_group(callback.from_user.id)
    if not group:
        await callback.answer("Сначала укажи группу", show_alert=True)
        return
    d = date.fromisoformat(callback.data.split(":", 1)[1])
    try:
        status, lessons = await schedule.get_schedule(d, group)
    except Exception:
        logging.exception("ics get_schedule failed")
        await callback.answer("Не получилось загрузить расписание", show_alert=True)
        return
    events = schedule.lessons_to_events(d, group, lessons) if status == "ok" else []
    if not events:
        await callback.answer("Нечего добавить в календарь", show_alert=True)
        return
    await send_ics(callback.message, events, f"raspisanie_{d.isoformat()}.ics")
    await callback.answer("Файл готов")


@router.callback_query(F.data == "icsweek")
async def ics_week(callback: CallbackQuery):
    group = await db.get_group(callback.from_user.id)
    if not group:
        await callback.answer("Сначала укажи группу", show_alert=True)
        return
    dates = await schedule.upcoming_dates(config.today(), limit=6)
    events = []
    for d in dates:
        try:
            status, lessons = await schedule.get_schedule(d, group)
        except Exception:
            logging.exception("ics week get_schedule failed")
            continue
        if status == "ok":
            events += schedule.lessons_to_events(d, group, lessons)
    if not events:
        await callback.answer("Нечего добавить в календарь", show_alert=True)
        return
    await send_ics(callback.message, events, "raspisanie_nedelya.ics")
    await callback.answer("Файл готов")


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


async def teacher_reply(message: Message, teacher, d, is_saved):
    await send_teacher(message, teacher, d)
    dates = await schedule.upcoming_dates(config.today())
    rows = [
        [InlineKeyboardButton(text=date_label(x), callback_data=f"tdate:{x.isoformat()}")]
        for x in dates
    ]
    actions = []
    if not is_saved:
        actions.append(InlineKeyboardButton(text="Запомнить как мою", callback_data="tsave"))
    actions.append(InlineKeyboardButton(text="Другой преподаватель", callback_data="tother"))
    rows.append(actions)
    await message.answer(
        "Другая дата, запомнить фамилию или сменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("teacher"))
@router.message(F.text == BTN_TEACHER)
async def ask_teacher(message: Message, state: FSMContext):
    saved = await db.get_teacher(message.from_user.id)
    if saved:
        teacher_lookup[message.from_user.id] = saved
        await teacher_reply(message, saved, config.today(), is_saved=True)
        return
    await state.set_state(Form.waiting_teacher)
    await message.answer("Напиши фамилию преподавателя, например Соколова")


@router.message(Form.waiting_teacher, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))
async def search_teacher(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Напиши фамилию преподавателя текстом, например Соколова")
        return
    teacher = message.text.strip()
    teacher_lookup[message.from_user.id] = teacher
    await state.clear()
    saved = await db.get_teacher(message.from_user.id)
    await teacher_reply(message, teacher, config.today(), is_saved=(saved == teacher))


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


@router.callback_query(F.data == "tsave")
async def save_teacher(callback: CallbackQuery):
    teacher = teacher_lookup.get(callback.from_user.id)
    if not teacher:
        await callback.answer("Сначала найди преподавателя", show_alert=True)
        return
    await db.set_teacher(callback.from_user.id, teacher)
    await callback.message.answer(
        f"Запомнил: {teacher}. Теперь по кнопке Преподаватель сразу твое расписание"
    )
    await callback.answer()


@router.callback_query(F.data == "tother")
async def other_teacher(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_teacher)
    await callback.message.answer("Напиши фамилию преподавателя, например Соколова")
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


@router.message(Form.waiting_room, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))
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
    group = await require_group(message, state)
    if group:
        await send_schedule(message, group, d)
