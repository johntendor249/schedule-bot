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
                joined_at TEXT,
                hits INTEGER NOT NULL DEFAULT 0,
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
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_counts (
                feature TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, value)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_log (
                kind TEXT PRIMARY KEY,
                day TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_log (
                group_name TEXT NOT NULL,
                day TEXT NOT NULL,
                pair TEXT NOT NULL,
                PRIMARY KEY (group_name, day, pair)
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
        ("joined_at", "TEXT"),
        ("hits", "INTEGER NOT NULL DEFAULT 0"),
        ("teacher_name", "TEXT"),
        ("digest", "INTEGER NOT NULL DEFAULT 0"),
        ("remind", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in cols:
            await conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
    async with conn.execute("PRAGMA table_info(schedule_cache)") as cur:
        cache_cols = {row[1] for row in await cur.fetchall()}
    if "lessons" not in cache_cols:
        await conn.execute("ALTER TABLE schedule_cache ADD COLUMN lessons TEXT")


async def touch_user(user_id, username, first_name):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO users "
            "(user_id, group_name, username, first_name, last_seen, joined_at, hits) "
            "VALUES (?, '', ?, ?, datetime('now'), datetime('now'), 1) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username = excluded.username, first_name = excluded.first_name, "
            "last_seen = datetime('now'), hits = hits + 1",
            (user_id, username, first_name),
        )
        await conn.commit()


async def bump_feature(feature):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO feature_counts (feature, count) VALUES (?, 1) "
            "ON CONFLICT(feature) DO UPDATE SET count = count + 1",
            (feature,),
        )
        await conn.commit()


async def feature_stats(limit=20):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT feature, count FROM feature_counts ORDER BY count DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


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


async def set_teacher(user_id, name):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO users (user_id, teacher_name) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "teacher_name = excluded.teacher_name, updated_at = datetime('now')",
            (user_id, name),
        )
        await conn.commit()


async def get_teacher(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT teacher_name FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def add_favorite(user_id, kind, value):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, kind, value) VALUES (?, ?, ?)",
            (user_id, kind, value),
        )
        await conn.commit()


async def remove_favorite(user_id, kind, value):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND kind = ? AND value = ?",
            (user_id, kind, value),
        )
        await conn.commit()


async def list_favorites(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT kind, value FROM favorites WHERE user_id = ? ORDER BY kind, value",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


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


async def set_digest(user_id, on):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET digest = ? WHERE user_id = ?",
            (1 if on else 0, user_id),
        )
        await conn.commit()


async def get_digest(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT digest FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def digest_users():
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, group_name FROM users WHERE digest = 1 AND group_name <> ''"
        ) as cur:
            return await cur.fetchall()


async def get_digest_sent(kind):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT day FROM digest_log WHERE kind = ?", (kind,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_digest_sent(kind, day):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO digest_log (kind, day) VALUES (?, ?) "
            "ON CONFLICT(kind) DO UPDATE SET day = excluded.day",
            (kind, day),
        )
        await conn.commit()


async def set_remind(user_id, on):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET remind = ? WHERE user_id = ?",
            (1 if on else 0, user_id),
        )
        await conn.commit()


async def get_remind(user_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT remind FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def remind_users():
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, group_name FROM users WHERE remind = 1 AND group_name <> ''"
        ) as cur:
            return await cur.fetchall()


async def was_reminded(group, day, pair):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM reminder_log WHERE group_name = ? AND day = ? AND pair = ?",
            (group, day, pair),
        ) as cur:
            return await cur.fetchone() is not None


async def mark_reminded(group, day, pair):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO reminder_log (group_name, day, pair) VALUES (?, ?, ?)",
            (group, day, pair),
        )
        await conn.commit()


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


async def get_cache(group, day):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT signature, lessons FROM schedule_cache WHERE group_name = ? AND day = ?",
            (group, day),
        ) as cur:
            row = await cur.fetchone()
            return (row[0], row[1]) if row else (None, None)


async def set_cache(group, day, signature, lessons):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO schedule_cache (group_name, day, signature, lessons) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_name, day) DO UPDATE SET "
            "signature = excluded.signature, lessons = excluded.lessons",
            (group, day, signature, lessons),
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
        dau = await count("last_seen >= date('now')")
        wau = await count("last_seen >= datetime('now', '-7 days')")
        mau = await count("last_seen >= datetime('now', '-30 days')")
        new_today = await count("joined_at >= date('now')")
        new_week = await count("joined_at >= datetime('now', '-7 days')")
        async with conn.execute("SELECT COALESCE(SUM(hits), 0) FROM users") as cur:
            total_hits = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT group_name, COUNT(*) c FROM users WHERE group_name <> '' "
            "GROUP BY group_name ORDER BY c DESC LIMIT 10"
        ) as cur:
            groups = await cur.fetchall()
        async with conn.execute(
            "SELECT group_name FROM users WHERE group_name <> ''"
        ) as cur:
            gnames = [r[0] for r in await cur.fetchall()]
    spec = {}
    for g in gnames:
        key = g.split("-")[0] if "-" in g else g
        spec[key] = spec.get(key, 0) + 1
    specialties = sorted(spec.items(), key=lambda kv: -kv[1])
    return {
        "total": total,
        "with_group": with_group,
        "no_group": total - with_group,
        "subs": subs,
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "new_today": new_today,
        "new_week": new_week,
        "total_hits": total_hits,
        "groups": groups,
        "specialties": specialties,
    }


async def recent_users(limit=10):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, username, first_name, group_name, last_seen "
            "FROM users ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()
