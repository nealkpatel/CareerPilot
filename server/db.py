"""SQLite storage. One shared connection guarded by a lock (FastAPI threads + engine thread)."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    contact_json TEXT NOT NULL DEFAULT '[]',
    jobs_json TEXT NOT NULL DEFAULT '[]',
    education_json TEXT NOT NULL DEFAULT '[]',
    certifications_json TEXT NOT NULL DEFAULT '[]',
    background TEXT NOT NULL DEFAULT '',
    prompt_id INTEGER,
    template_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    question_text TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_templates (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    html TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    url_key TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    work_arrangement TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    questions TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'saved',
    source TEXT NOT NULL DEFAULT 'manual',
    job_id INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    applied_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_user ON applications(user_id, created_at);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    user_id INTEGER,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'ai',
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    prompt_id INTEGER,
    template_id TEXT NOT NULL DEFAULT '',
    profile_json TEXT NOT NULL DEFAULT '{}',
    resume_json TEXT NOT NULL DEFAULT '',
    cover_letter TEXT NOT NULL DEFAULT '',
    answers_json TEXT NOT NULL DEFAULT '[]',
    raw_response TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS answer_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    questions TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS shares (
    token TEXT PRIMARY KEY,
    generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    token TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    last_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(provider, token)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES job_sources(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    remote INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    imported_at TEXT NOT NULL,
    UNIQUE(provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_at);
CREATE TABLE IF NOT EXISTS job_marks (
    user_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    mark TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS profile_assignments (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, user_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(conn):
    """Roles are master (platform manager), admin (manages own bidders) and bidder."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN parent_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
    conn.execute("UPDATE users SET role = 'bidder' WHERE role NOT IN ('master', 'admin', 'bidder')")
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "logo" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN logo TEXT NOT NULL DEFAULT ''")
    if "url_key" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN url_key TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url_key ON jobs(url_key)")
    for col, ddl in (("apply_url", "TEXT NOT NULL DEFAULT ''"), ("apply_status", "TEXT NOT NULL DEFAULT ''"),
                     ("apply_host", "TEXT NOT NULL DEFAULT ''"), ("apply_reason", "TEXT NOT NULL DEFAULT ''"), ("apply_checked_at", "TEXT")):
        if col not in job_cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_apply_status ON jobs(apply_status)")
    # Auto apply was removed; an existing apply_runs table is left in place, unused.
    # Each user's own DeepSeek runs their requests: remember who asked (an admin can generate for a bidder's application).
    for table in ("generations", "answer_requests"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "engine_user_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN engine_user_id INTEGER")
    prof_cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "answers_json" not in prof_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN answers_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_url_key ON applications(url_key)")
    missing = conn.execute("SELECT id, url FROM jobs WHERE url_key = '' AND url != ''").fetchall()
    if missing:
        from .scraping.job_url import url_key  # normalised link, used to de-duplicate shared jobs
        conn.executemany("UPDATE jobs SET url_key = ? WHERE id = ?", [(url_key(u), i) for i, u in missing])
    if not conn.execute("SELECT 1 FROM users WHERE role = 'master'").fetchone():
        first = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()             or conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if first:
            conn.execute("UPDATE users SET role = 'master', parent_id = NULL WHERE id = ?", (first[0],))


def _row(r):
    return dict(r) if r is not None else None


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    with _lock:
        return _row(connect().execute(sql, params).fetchone())


def execute(sql, params=()):
    with _lock:
        cur = connect().execute(sql, params)
        return cur


def insert(table, data):
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    with _lock:
        cur = connect().execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
        return cur.lastrowid


def update(table, row_id, data, key="id"):
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    with _lock:
        connect().execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", (*data.values(), row_id))


def jload(text, default):
    try:
        value = json.loads(text) if text else default
        return value if value is not None else default
    except Exception:
        return default


def jdump(value):
    return json.dumps(value, ensure_ascii=False)


def get_setting(key, default=""):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, "" if value is None else str(value)),
    )


def get_user_setting(user_id, key, default=""):
    row = query_one("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
    return row["value"] if row and row["value"] is not None else default


def set_user_setting(user_id, key, value):
    execute(
        """INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value""",
        (user_id, key, "" if value is None else str(value)),
    )


def audit(user_id, action, detail=""):
    try:
        insert("audit", {"user_id": user_id, "action": action, "detail": str(detail)[:500], "created_at": now_iso()})
    except Exception:
        pass
