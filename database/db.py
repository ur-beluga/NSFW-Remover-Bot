import sqlite3
import time
from config import DB_PATH, DEFAULT_THRESHOLD

_CREATE_GROUP_SETTINGS = f"""
CREATE TABLE IF NOT EXISTS group_settings (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    threshold REAL DEFAULT {DEFAULT_THRESHOLD},
    scan_photos INTEGER DEFAULT 1,
    scan_videos INTEGER DEFAULT 1,
    scan_gifs INTEGER DEFAULT 1,
    scan_stickers INTEGER DEFAULT 1,
    action TEXT DEFAULT 'delete',
    filter_swimwear INTEGER DEFAULT 1
);
"""

_CREATE_MEDIA_CACHE = """
CREATE TABLE IF NOT EXISTS media_cache (
    file_unique_id TEXT PRIMARY KEY,
    score REAL NOT NULL,
    swimwear_score REAL,
    kind TEXT,
    checked_at REAL
);
"""

_CREATE_STATS = """
CREATE TABLE IF NOT EXISTS stats (
    chat_id INTEGER PRIMARY KEY,
    scanned INTEGER DEFAULT 0,
    flagged INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0
);
"""

_CREATE_STICKER_BLACKLIST = """
CREATE TABLE IF NOT EXISTS sticker_blacklist (
    chat_id INTEGER,
    set_name TEXT,
    PRIMARY KEY (chat_id, set_name)
);
"""


def _connect():
    # timeout: how long to wait for a lock before giving up, instead of
    # immediately raising "database is locked" (which is what was happening
    # once multiple worker threads started hitting the DB at the same time).
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL mode allows one writer + multiple readers concurrently, which is a
    # much better fit for a multi-threaded bot than SQLite's default journal
    # mode (which locks the whole file during writes).
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _ensure_column(conn, table: str, column: str, coldef: str):
    """Add a column to an existing table if it isn't already there - lets us
    evolve the schema over time without wiping out an existing bot's data."""
    existing = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def init_db():
    conn = _connect()
    conn.execute(_CREATE_GROUP_SETTINGS)
    conn.execute(_CREATE_MEDIA_CACHE)
    conn.execute(_CREATE_STATS)
    conn.execute(_CREATE_STICKER_BLACKLIST)

    # Migrations for bots upgraded from an earlier version of this schema.
    # NULL (not 0) is used as the default here on purpose - it lets us tell
    # the difference between "we checked and found no swimwear content" (0.0)
    # and "this entry predates swimwear detection entirely and was never
    # actually checked for it" (NULL). Treating old entries as 0 would have
    # silently marked previously-flagged content as safe.
    _ensure_column(conn, "group_settings", "filter_swimwear", "INTEGER DEFAULT 1")
    _ensure_column(conn, "media_cache", "swimwear_score", "REAL")
    # Note: older installs may still have unused "bypass_admins" and
    # "strikes_before_punish" columns left over from a previous version -
    # they're harmless leftovers, just no longer read or written by the code.

    conn.commit()
    conn.close()


# ---------- group settings ----------

def get_settings(chat_id: int) -> dict:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()

    if row is None:
        conn.execute("INSERT INTO group_settings (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()

    conn.close()
    return dict(row)


def update_setting(chat_id: int, field: str, value):
    allowed_fields = {
        "enabled", "threshold", "scan_photos", "scan_videos",
        "scan_gifs", "scan_stickers", "action", "filter_swimwear",
    }
    if field not in allowed_fields:
        raise ValueError(f"Invalid field: {field}")

    conn = _connect()
    conn.execute("INSERT OR IGNORE INTO group_settings (chat_id) VALUES (?)", (chat_id,))
    conn.execute(f"UPDATE group_settings SET {field} = ? WHERE chat_id = ?", (value, chat_id))
    conn.commit()
    conn.close()


# ---------- media cache (avoids re-checking the same sticker/gif twice) ----------

def get_cached_score(file_unique_id: str):
    """
    Returns a dict {"severity": float, "swimwear": float} for this exact file,
    or None if never checked before (or checked before swimwear detection was
    added, in which case we deliberately treat it as never-checked so it gets
    a fresh, complete check rather than silently trusting a stale value).
    """
    if not file_unique_id:
        return None
    conn = _connect()
    row = conn.execute(
        "SELECT score, swimwear_score FROM media_cache WHERE file_unique_id = ?",
        (file_unique_id,),
    ).fetchone()
    conn.close()
    if row is None or row["swimwear_score"] is None:
        return None
    return {"severity": row["score"], "swimwear": row["swimwear_score"]}


def set_cached_score(file_unique_id: str, severity: float, swimwear: float, kind: str):
    if not file_unique_id:
        return
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO media_cache (file_unique_id, score, swimwear_score, kind, checked_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_unique_id, severity, swimwear, kind, time.time()),
    )
    conn.commit()
    conn.close()


# ---------- stats ----------

def bump_stats(chat_id: int, scanned=0, flagged=0, cache_hits=0):
    conn = _connect()
    conn.execute("INSERT OR IGNORE INTO stats (chat_id) VALUES (?)", (chat_id,))
    conn.execute(
        "UPDATE stats SET scanned = scanned + ?, flagged = flagged + ?, "
        "cache_hits = cache_hits + ? WHERE chat_id = ?",
        (scanned, flagged, cache_hits, chat_id),
    )
    conn.commit()
    conn.close()


def get_stats(chat_id: int) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM stats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    if row is None:
        return {"chat_id": chat_id, "scanned": 0, "flagged": 0, "cache_hits": 0}
    return dict(row)


# ---------- sticker pack blacklist ----------

def add_blacklisted_pack(chat_id: int, set_name: str):
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO sticker_blacklist (chat_id, set_name) VALUES (?, ?)",
        (chat_id, set_name.lower()),
    )
    conn.commit()
    conn.close()


def remove_blacklisted_pack(chat_id: int, set_name: str):
    conn = _connect()
    conn.execute(
        "DELETE FROM sticker_blacklist WHERE chat_id = ? AND set_name = ?",
        (chat_id, set_name.lower()),
    )
    conn.commit()
    conn.close()


def list_blacklisted_packs(chat_id: int) -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT set_name FROM sticker_blacklist WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return [r["set_name"] for r in rows]


def is_pack_blacklisted(chat_id: int, set_name: str) -> bool:
    if not set_name:
        return False
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM sticker_blacklist WHERE chat_id = ? AND set_name = ?",
        (chat_id, set_name.lower()),
    ).fetchone()
    conn.close()
    return row is not None
