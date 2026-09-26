"""Session storage for Odin's Kin.

SQLite in %LOCALAPPDATA%\\Odins_Kin\\sessions.db by default. Set
ODINSKIN_DATABASE_URL to a postgres:// URL to use PostgreSQL instead.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get("ODINSKIN_DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))


def data_dir() -> Path:
    """Writable per-user folder. Never next to the .exe (PyInstaller unpacks to a temp dir)."""
    override = os.environ.get("ODINSKIN_DATA_DIR")
    if override:
        root = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        root = Path(local) / "Odins_Kin"
    root.mkdir(parents=True, exist_ok=True)
    return root


DB_PATH = data_dir() / "sessions.db"


# Connection
@contextmanager
def _connect():
    if IS_POSTGRES:
        import psycopg2

        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sql(query: str) -> str:
    """Queries are written with ? placeholders; psycopg2 wants %s."""
    return query.replace("?", "%s") if IS_POSTGRES else query


def _rows(cursor) -> list:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Schema
def init_db():
    pk = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with _connect() as conn:
        c = conn.cursor()
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS sessions (
                id            {pk},
                start_time    TEXT,
                end_time      TEXT,
                total_events  INTEGER,
                summary_json  TEXT,
                created_at    TEXT
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS events (
                id                {pk},
                session_id        INTEGER REFERENCES sessions(id),
                start_time        TEXT,
                end_time          TEXT,
                duration_seconds  REAL,
                window_title      TEXT,
                process_name      TEXT,
                exe_path          TEXT,
                url               TEXT
            )
        """)
        # Databases from before the YouTube links feature lack the url column
        if IS_POSTGRES:
            c.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS url TEXT")
        elif "url" not in [row[1] for row in c.execute("PRAGMA table_info(events)")]:
            c.execute("ALTER TABLE events ADD COLUMN url TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_time)")
        c.close()


# Write
def save_session(payload: dict) -> int:
    """Store one finished session and its focus events in a single transaction."""
    session = payload["session"]
    insert_session = _sql("""
        INSERT INTO sessions (start_time, end_time, total_events, summary_json, created_at)
        VALUES (?, ?, ?, ?, ?)
    """)
    values = (
        session["start"],
        session["end"],
        session["total_events"],
        json.dumps(payload["time_per_app_seconds"]),
        utc_now_iso(),
    )

    with _connect() as conn:
        c = conn.cursor()
        if IS_POSTGRES:
            c.execute(insert_session + " RETURNING id", values)
            session_id = c.fetchone()[0]
        else:
            c.execute(insert_session, values)
            session_id = c.lastrowid

        c.executemany(
            _sql("""
                INSERT INTO events (session_id, start_time, end_time, duration_seconds,
                                    window_title, process_name, exe_path, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """),
            [
                (
                    session_id,
                    ev["start_time"],
                    ev["end_time"],
                    ev["duration_seconds"],
                    ev["window_title"],
                    ev["process_name"],
                    ev["exe_path"],
                    ev.get("url"),
                )
                for ev in payload["events"]
            ],
        )
        c.close()
    return session_id


# Read
def get_all_sessions() -> list:
    """All sessions, newest first, each with its per-app summary decoded."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, start_time, end_time, total_events, summary_json, created_at "
            "FROM sessions ORDER BY start_time DESC, id DESC"
        )
        rows = _rows(c)
        c.close()
    for row in rows:
        row["summary"] = json.loads(row.pop("summary_json") or "{}")
    return rows


def get_session_events(session_id: int) -> list:
    with _connect() as conn:
        c = conn.cursor()
        c.execute(
            _sql("SELECT * FROM events WHERE session_id = ? ORDER BY start_time ASC, id ASC"),
            (session_id,),
        )
        rows = _rows(c)
        c.close()
    return rows


def get_events_between(start_iso: str, end_iso: str) -> list:
    """Focus events overlapping [start, end), without window titles (for usage charts)."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute(
            _sql("""
                SELECT session_id, start_time, end_time, duration_seconds, process_name
                FROM events
                WHERE start_time < ? AND end_time > ?
                ORDER BY start_time ASC
            """),
            (end_iso, start_iso),
        )
        rows = _rows(c)
        c.close()
    return rows


def get_youtube_between(start_iso: str, end_iso: str) -> list:
    """Events in [start, end) that might be YouTube videos (titles are re-checked by the caller)."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute(
            _sql("""
                SELECT session_id, start_time, end_time, duration_seconds,
                       window_title, process_name, url
                FROM events
                WHERE start_time < ? AND end_time > ? AND window_title LIKE '%YouTube%'
                ORDER BY start_time ASC
            """),
            (end_iso, start_iso),
        )
        rows = _rows(c)
        c.close()
    return rows


def get_known_apps() -> list:
    """Every process seen so far with one executable path for it (for names and icons)."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT process_name, MAX(exe_path) AS exe_path
            FROM events
            GROUP BY process_name
        """)
        rows = _rows(c)
        c.close()
    return rows


def get_all_time_ranking() -> list:
    """Process names ordered by total tracked time, biggest first."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT process_name, SUM(duration_seconds) AS total
            FROM events
            GROUP BY process_name
            ORDER BY total DESC
        """)
        ranking = [row[0] for row in c.fetchall()]
        c.close()
    return ranking


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def app_totals_between(start: datetime, end: datetime) -> dict:
    """Seconds per process inside [start, end), clipping events that straddle the edges."""
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    totals = {}
    for ev in get_events_between(start.isoformat(), end.isoformat()):
        overlap = (
            min(parse_iso(ev["end_time"]), end) - max(parse_iso(ev["start_time"]), start)
        ).total_seconds()
        if overlap > 0:
            totals[ev["process_name"]] = totals.get(ev["process_name"], 0.0) + overlap
    return totals
