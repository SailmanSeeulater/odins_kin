"""Focus tracking: which window is in front, and a recording session built from that stream."""

import json
from datetime import datetime, timezone
from pathlib import Path

from privacy import redact_title

UNKNOWN = "Unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_active_window():
    """Returns (window_title, process_name, exe_path) of the foreground window."""
    import psutil
    import win32gui
    import win32process

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "", UNKNOWN, ""
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return "", UNKNOWN, ""

    # name() usually works even for elevated processes whose exe() is denied
    name, exe = UNKNOWN, ""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        exe = proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return title, name, exe


class Session:
    """One recording: a list of focus events, each closed when focus moves elsewhere."""

    def __init__(self, now: datetime | None = None):
        self.started_at = now or utc_now()
        self.ended_at = None
        self.events = []

    @property
    def current(self):
        if self.events and self.events[-1]["end_time"] is None:
            return self.events[-1]
        return None

    def observe(self, title: str, process: str, exe: str, now: datetime | None = None) -> bool:
        """Record what has focus right now. Returns True when a new event opened.

        The raw title is redacted here, before it is stored anywhere, so switching
        files in an editor doesn't split events or leave a trace; a new YouTube
        video does."""
        now = now or utc_now()
        title = redact_title(process, title)
        current = self.current
        if current and (current["window_title"], current["process_name"]) == (title, process):
            return False
        self._close_current(now)
        self.events.append(
            {
                "start_time": now.isoformat(),
                "end_time": None,
                "duration_seconds": None,
                "window_title": title,
                "process_name": process,
                "exe_path": exe,
                "url": None,
            }
        )
        return True

    def stop(self, now: datetime | None = None):
        now = now or utc_now()
        self._close_current(now)
        self.ended_at = now

    def _close_current(self, now: datetime):
        current = self.current
        if current:
            current["end_time"] = now.isoformat()
            start = datetime.fromisoformat(current["start_time"])
            current["duration_seconds"] = round(max(0.0, (now - start).total_seconds()), 2)

    def app_totals(self, now: datetime | None = None) -> list:
        """[(process_name, seconds)] biggest first, counting the open event up to now."""
        now = now or utc_now()
        totals = {}
        for ev in self.events:
            if ev["duration_seconds"] is not None:
                secs = ev["duration_seconds"]
            else:
                secs = (now - datetime.fromisoformat(ev["start_time"])).total_seconds()
            totals[ev["process_name"]] = totals.get(ev["process_name"], 0.0) + max(0.0, secs)
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)

    def switch_count(self) -> int:
        return max(0, len(self.events) - 1)

    def exe_for(self, process: str) -> str:
        for ev in reversed(self.events):
            if ev["process_name"] == process and ev["exe_path"]:
                return ev["exe_path"]
        return ""

    def to_payload(self) -> dict:
        return {
            "session": {
                "start": self.started_at.isoformat(),
                "end": (self.ended_at or utc_now()).isoformat(),
                "total_events": len(self.events),
            },
            "time_per_app_seconds": {
                name: round(secs, 2) for name, secs in self.app_totals(self.ended_at)
            },
            "events": self.events,
        }


def export_json(payload: dict, folder: Path) -> Path:
    """Write the session as a human-readable JSON file and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(payload["session"]["start"]).astimezone()
    out = folder / f"activity_session_{start:%Y%m%d_%H%M%S}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out
