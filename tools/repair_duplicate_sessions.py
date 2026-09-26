"""Undo the double counting left by the old tracker.

Before the rewrite, app.py kept one global event list, so every session after the
first in a single launch re-saved the previous session's events in front of its
own. This finds sessions whose events begin with an earlier session's exact event
list, removes that copied prefix, and recomputes start time, event count and
per-app summary.

    python tools/repair_duplicate_sessions.py            # dry run: report only
    python tools/repair_duplicate_sessions.py --apply    # backs up the DB first
"""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402

FIELDS = "start_time, end_time, duration_seconds, window_title, process_name, exe_path"


def events_of(conn, session_id):
    return conn.execute(
        f"SELECT id, {FIELDS} FROM events WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()


def find_repairs(conn):
    sessions = conn.execute("SELECT id FROM sessions ORDER BY id").fetchall()
    ids = [row[0] for row in sessions]
    repairs = []
    for i, later in enumerate(ids):
        later_events = events_of(conn, later)
        for earlier in reversed(ids[:i]):
            prefix = events_of(conn, earlier)
            n = len(prefix)
            if n and len(later_events) > n and [e[1:] for e in later_events[:n]] == [e[1:] for e in prefix]:
                repairs.append((earlier, later, [e[0] for e in later_events[:n]], later_events[n:]))
                break
    return repairs


def main():
    if database.IS_POSTGRES:
        sys.exit("This repair only handles the local SQLite database.")
    apply = "--apply" in sys.argv
    path = database.DB_PATH
    if not path.exists():
        sys.exit(f"No database at {path}")

    conn = sqlite3.connect(path)
    repairs = find_repairs(conn)
    if not repairs:
        print("No duplicated sessions found.")
        return

    for earlier, later, copied, kept in repairs:
        seconds = sum(e[3] or 0 for e in kept)
        print(f"Session {later} repeats all {len(copied)} events of session {earlier}; "
              f"keeping its own {len(kept)} events ({seconds / 60:.0f} min).")

    if not apply:
        print("\nDry run. Re-run with --apply to fix (a backup is made first).")
        return

    backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

    with conn:
        for _, later, copied, kept in repairs:
            conn.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in copied])
            summary = {}
            for e in kept:
                summary[e[5]] = round(summary.get(e[5], 0) + (e[3] or 0), 2)
            conn.execute(
                "UPDATE sessions SET start_time = ?, total_events = ?, summary_json = ? WHERE id = ?",
                (kept[0][1], len(kept), json.dumps(summary), later),
            )
    print(f"Repaired {len(repairs)} session(s).")


if __name__ == "__main__":
    main()
