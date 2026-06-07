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

BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"
BTN_UPCOMING = "Ближайшие даты"
BTN_GROUP = "Сменить группу"
BTN_NOTIFY = "Уведомления"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

HELP_TEXT = (
    "Показываю расписание ТСПК по твоей группе.\n\n"
    "Кнопки: Сегодня, Завтра, Ближайшие даты.\n"
    "Можно прислать дату текстом: 05.06 или 05.06.2026.\n\n"
    "/group — сменить группу\n"
    "/subscribe — присылать уведомления об изменениях\n"
    "/unsubscribe — выключить уведомления"
)


class Form(StatesGroup):
    waiting_group = State()


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_UPCOMING)],
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


async def send_schedule(message: Message, group, d):
    try:
        status, payload = await schedule.get_schedule(d, group)
    except Exception:
        logging.exception("get_schedule failed")
        await message.answer("Не получилось загрузить расписание, попробуй позже")
        return
    if status == "ok":
        await message.answer(schedule.format_schedule(d, group, payload))
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


@router.message(F.text)
async def free_text(message: Message):
    d = parse_user_date(message.text)
    if d is None:
        await message.answer("Не понял. Список команд: /help")
        return
    group = await require_group(message)
    if group:
        await send_schedule(message, group, d)
