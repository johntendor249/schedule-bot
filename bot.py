import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher

import db
import notifier
from config import BOT_TOKEN
from handlers import router


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Прокинь переменную окружения BOT_TOKEN.")

    await db.init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    watcher = asyncio.create_task(notifier.run_watcher(bot))
    try:
        await dp.start_polling(bot)
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
