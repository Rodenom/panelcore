"""SQLite layer for PanelCore. Stdlib only — no install step on the client's machine."""
import sqlite3, os, threading, json, hashlib, secrets
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'panel.db')
# On the public stand every visitor gets their own file under demos/, so one
# person switching business or wiping the board is invisible to everyone else.
DEMO_DIR = os.path.join(HERE, 'demos')
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',   -- admin | operator | viewer
    pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Anything with a capacity: an account, a driver, a machine, a phone number.
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'account',
    status TEXT NOT NULL DEFAULT 'active',   -- active | paused | blocked
    daily_limit INTEGER NOT NULL DEFAULT 0,  -- 0 = unlimited
    used_today INTEGER NOT NULL DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- A unit of work moving across the board.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    customer TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',      -- new | queued | running | done | failed
    priority INTEGER NOT NULL DEFAULT 2,     -- 1 high, 2 normal, 3 low
    assignee_id INTEGER,
    resource_id INTEGER,
    payload TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL DEFAULT 'info',      -- info | warn | error
    actor TEXT DEFAULT 'system',
    message TEXT NOT NULL,
    job_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at DESC);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def use(path):
    """Point this thread at one visitor's database for the rest of the request."""
    _local.path = path


def release():
    """Close the request's connection. Without this a busy stand would hold
    one open file handle per visitor per thread until the process dies."""
    c = getattr(_local, 'c', None)
    if c is not None:
        c.close()
    _local.c = None
    _local.opened = None
    _local.path = None


def conn():
    path = getattr(_local, 'path', None) or DB_PATH
    if getattr(_local, 'c', None) is None or getattr(_local, 'opened', None) != path:
        old = getattr(_local, 'c', None)
        if old is not None:
            old.close()
        c = sqlite3.connect(path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        _local.c, _local.opened = c, path
    return _local.c


def init():
    conn().executescript(SCHEMA)
    conn().commit()


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid


# --- settings ---------------------------------------------------------

def get_setting(key, default=None):
    row = one('SELECT value FROM settings WHERE key = ?', (key,))
    return row['value'] if row else default


def set_setting(key, value):
    run('INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))


# --- auth -------------------------------------------------------------

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + pw).encode()).hexdigest()
    return f'{salt}${h}'


def check_pw(pw, stored):
    try:
        salt, _ = stored.split('$', 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_pw(pw, salt), stored)


def new_session(user_id):
    token = secrets.token_urlsafe(24)
    run('INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)',
        (token, user_id, now()))
    return token


def login(login_name, pw):
    u = one('SELECT * FROM users WHERE login = ?', (login_name,))
    if not u or not check_pw(pw, u['pw_hash']):
        return None
    return new_session(u['id'])


def user_by_token(token):
    if not token:
        return None
    return one('SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id '
               'WHERE s.token = ?', (token,))


def log(message, level='info', actor='system', job_id=None):
    run('INSERT INTO activity (level, actor, message, job_id, created_at) '
        'VALUES (?,?,?,?,?)', (level, actor, message, job_id, now()))
