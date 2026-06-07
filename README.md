# Schedule bot

Телеграм-бот с расписанием ТСПК. Берет расписание с сайта колледжа (там на каждый
день своя гугл-таблица), показывает по твоей группе на нужную дату и пишет, если
расписание на ближайшие дни поменялось.

## Что умеет

- расписание на сегодня, завтра и ближайшие даты
- дату можно прислать текстом: `05.06`, `05.06.2026` или `2026-06-05`
- показывает только твою группу, а не всю простыню за день
- подписка: сам проверяет таблицы и пишет, когда расписание на ближайшие дни меняется

## Запуск локально

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

В `.env` положи `BOT_TOKEN` от @BotFather. Файл в `.gitignore`, в репозиторий не попадет.

## Деплой на VPS (systemd)

Бот крутится под отдельным пользователем `schedulebot`, не под root.

```
sudo adduser --system --group --home /opt/schedule-bot schedulebot
sudo git clone <repo> /opt/schedule-bot
sudo chown -R schedulebot:schedulebot /opt/schedule-bot

cd /opt/schedule-bot
sudo -u schedulebot python3 -m venv venv
sudo -u schedulebot venv/bin/pip install -r requirements.txt
sudo -u schedulebot cp .env.example .env

sudo cp deploy/schedule-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now schedule-bot
journalctl -u schedule-bot -f
```

Не забудь вписать `BOT_TOKEN` в `/opt/schedule-bot/.env`. При падении systemd поднимает
бота заново. База `bot.db` и подписки лежат рядом, в рабочем каталоге.

## Настройки

Все в `config.py`:

- `TZ` - часовой пояс для "сегодня/завтра", по умолчанию `Europe/Samara` (Тольятти)
- `CHECK_INTERVAL` - как часто проверять таблицы, в секундах
- `WATCH_DAYS` - на сколько дней вперед следить за изменениями
- `ACADEMIC_YEAR_START` / `ACADEMIC_YEAR_END` - границы учебного года
