"""Apply today's privacy rules to sessions recorded before them.

Older versions stored every window title verbatim: terminal paths and commands,
editor file names, email subjects and so on. This rewrites stored titles through
privacy.redact_title, so only YouTube video titles remain, in:

  - the SQLite database (then VACUUMs it so the old text doesn't linger in free pages)
  - any sessions.backup-*.db left by repair_duplicate_sessions.py
  - JSON exports in %LOCALAPPDATA%\\Odins_Kin\\sessions and the old ...\\ActivityTracker\\sessions

No backup is made on purpose: a backup would keep exactly the text being removed.

    python tools/scrub_titles.py            # dry run: counts only, prints no titles
    python tools/scrub_titles.py --apply
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from privacy import redact_title  # noqa: E402


def scrub_db(path: Path, apply: bool) -> int:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT id, process_name, window_title FROM events").fetchall()
        changes = [
            (redact_title(proc or "", title or ""), row_id)
            for row_id, proc, title in rows
            if (title or "") != redact_title(proc or "", title or "")
        ]
        if apply and changes:
            with conn:
                conn.executemany("UPDATE events SET window_title = ? WHERE id = ?", changes)
            conn.execute("VACUUM")
        return len(changes)
    finally:
        conn.close()


def scrub_json(path: Path, apply: bool) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    changed = 0
    for ev in payload.get("events", []):
        clean = redact_title(ev.get("process_name", ""), ev.get("window_title") or "")
        if (ev.get("window_title") or "") != clean:
            ev["window_title"] = clean
            changed += 1
    if apply and changed:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed


def main():
    apply = "--apply" in sys.argv
    if database.IS_POSTGRES:
        sys.exit("This scrub only handles the local SQLite database and JSON exports.")

    data = database.data_dir()
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    total = 0

    for db in [database.DB_PATH, *sorted(data.glob("sessions.backup-*.db"))]:
        if db.exists():
            n = scrub_db(db, apply)
            total += n
            print(f"{db}: {n} titles to remove" if not apply else f"{db}: removed {n} titles")

    for folder in (data / "sessions", local / "ActivityTracker" / "sessions"):
        files = sorted(folder.glob("*.json")) if folder.exists() else []
        n = sum(scrub_json(f, apply) for f in files)
        total += n
        if files:
            verb = "removed" if apply else "to remove"
            print(f"{folder}: {n} titles {verb} across {len(files)} exports")

    if not apply:
        print(f"\nDry run: {total} titles would be removed. Re-run with --apply.")


if __name__ == "__main__":
    main()
