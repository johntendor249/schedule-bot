import aiosqlite

from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT NOT NULL DEFAULT '',
                notify INTEGER NOT NULL DEFAULT 0,
                username TEXT,
                first_name TEXT,
                last_seen TEXT,
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
        await _ensure_columns(conn)
        await conn.commit()


async def _ensure_columns(conn):
    async with conn.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for name, decl in (
        ("notify", "INTEGER NOT NULL DEFAULT 0"),
        ("username", "TEXT"),
        ("first_name", "TEXT"),
        ("last_seen", "TEXT"),
    ):
        if name not in cols:
            await conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")


async def touch_user(user_id, username, first_name):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO users (user_id, group_name, username, first_name, last_seen) "
            "VALUES (?, '', ?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username = excluded.username, first_name = excluded.first_name, "
            "last_seen = datetime('now')",
            (user_id, username, first_name),
        )
        await conn.commit()


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


async def stats():
    async with aiosqlite.connect(DB_PATH) as conn:
        async def count(where=""):
            q = "SELECT COUNT(*) FROM users" + (f" WHERE {where}" if where else "")
            async with conn.execute(q) as cur:
                return (await cur.fetchone())[0]

        total = await count()
        with_group = await count("group_name <> ''")
        subs = await count("notify = 1")
        active = await count("last_seen >= datetime('now', '-7 days')")
        async with conn.execute(
            "SELECT group_name, COUNT(*) c FROM users WHERE group_name <> '' "
            "GROUP BY group_name ORDER BY c DESC LIMIT 10"
        ) as cur:
            groups = await cur.fetchall()
    return {
        "total": total,
        "with_group": with_group,
        "subs": subs,
        "active": active,
        "groups": groups,
    }


async def recent_users(limit=10):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, username, first_name, group_name, last_seen "
            "FROM users ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()
