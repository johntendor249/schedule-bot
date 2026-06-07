import aiosqlite

from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT NOT NULL,
                notify INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_cache (
                group_name TEXT NOT NULL,
                day TEXT NOT NULL,
                signature TEXT NOT NULL,
                PRIMARY KEY (group_name, day)
            )
            """
        )
        await _ensure_notify_column(conn)
        await conn.commit()


async def _ensure_notify_column(conn):
    async with conn.execute("PRAGMA table_info(users)") as cur:
        cols = [row[1] for row in await cur.fetchall()]
    if "notify" not in cols:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN notify INTEGER NOT NULL DEFAULT 0"
        )


async def set_group(user_id, group):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO users (user_id, group_name) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "group_name = excluded.group_name, updated_at = datetime('now')",
            (user_id, group),
        )
        await conn.commit()


async def get_group(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT group_name FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_notify(user_id, on):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET notify = ? WHERE user_id = ?",
            (1 if on else 0, user_id),
        )
        await conn.commit()


async def get_notify(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT notify FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def subscribed_groups():
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT DISTINCT group_name FROM users WHERE notify = 1"
        ) as cur:
            return [row[0] for row in await cur.fetchall()]


async def users_for_group(group):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id FROM users WHERE notify = 1 AND group_name = ?", (group,)
        ) as cur:
            return [row[0] for row in await cur.fetchall()]


async def get_signature(group, day):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT signature FROM schedule_cache WHERE group_name = ? AND day = ?",
            (group, day),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_signature(group, day, signature):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO schedule_cache (group_name, day, signature) VALUES (?, ?, ?) "
            "ON CONFLICT(group_name, day) DO UPDATE SET signature = excluded.signature",
            (group, day, signature),
        )
        await conn.commit()
