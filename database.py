import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "sessions.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn


def init_db():
    """Create tables if they don't exist yet."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time  TEXT,
            end_time    TEXT,
            total_events INTEGER,
            summary_json TEXT,
            created_at  TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER,
            start_time      TEXT,
            end_time        TEXT,
            duration_seconds REAL,
            window_title    TEXT,
            process_name    TEXT,
            exe_path        TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)

    conn.commit()
    conn.close()


def save_session(payload: dict) -> int:
    """Save a full session payload to the DB. Returns the new session id."""
    conn = get_connection()
    c = conn.cursor()

    session = payload["session"]
    summary = payload["time_per_app_seconds"]

    c.execute(
        """
        INSERT INTO sessions (start_time, end_time, total_events, summary_json, created_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (
            session["start"],
            session["end"],
            session["total_events"],
            json.dumps(summary),
            datetime.utcnow().isoformat(),
        ),
    )

    session_id = c.lastrowid or 0

    for ev in payload["events"]:
        c.execute(
            """
            INSERT INTO events
                (session_id, start_time, end_time, duration_seconds,
                 window_title, process_name, exe_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                ev["start_time"],
                ev["end_time"],
                ev["duration_seconds"],
                ev["window_title"],
                ev["process_name"],
                ev["exe_path"],
            ),
        )

    conn.commit()
    conn.close()
    return session_id


def get_all_sessions() -> list:
    """Return all sessions, newest first."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM sessions ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    for row in rows:
        row["summary"] = json.loads(row["summary_json"])
    conn.close()
    return rows


def get_session_events(session_id: int) -> list:
    """Return all events for a given session."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM events WHERE session_id = ? ORDER BY id ASC", (session_id,)
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
