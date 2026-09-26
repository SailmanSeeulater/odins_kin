"""What Odin's Kin is allowed to remember about a window.

Odin's Kin only ever reads a window's title bar, never what's inside the window.
Titles still leak, though: file and project names in editors, paths and commands
in terminals, email subjects, chat names, document names. So titles are dropped
by default, before they reach memory, the database, a JSON export or the dashboard.

The one exception the owner asked for is YouTube. In a browser window whose title
says it's a YouTube video, the video title is kept (never from InPrivate/Incognito
windows). After a session, each video's link is looked up in that browser's own
history: a temporary copy of the history file is read for YouTube watch URLs visited
during the session, and the copy is deleted straight away.
"""

import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
ROAMING = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")

# Browser process -> folders holding its history (Chromium profiles or Firefox profiles)
CHROMIUM = {
    "chrome.exe": [LOCAL / "Google" / "Chrome" / "User Data"],
    "msedge.exe": [LOCAL / "Microsoft" / "Edge" / "User Data"],
    "brave.exe": [LOCAL / "BraveSoftware" / "Brave-Browser" / "User Data"],
    "vivaldi.exe": [LOCAL / "Vivaldi" / "User Data"],
    "opera.exe": [ROAMING / "Opera Software" / "Opera Stable"],
}
FIREFOX = {"firefox.exe": [ROAMING / "Mozilla" / "Firefox" / "Profiles"]}
BROWSERS = set(CHROMIUM) | set(FIREFOX)

PRIVATE = re.compile(r"InPrivate|Incognito|Private Browsing", re.I)
# "(3) Video - YouTube - Google Chrome", "Video - YouTube and 2 more pages - Personal - Microsoft Edge",
# "Video - YouTube — Mozilla Firefox", "Song - YouTube Music - Google Chrome"
YOUTUBE_TITLE = re.compile(
    r"^(?:\(\d+\)\s+)?(?P<video>.+?) - (?P<site>YouTube(?: Music)?)"
    r"(?: and \d+ more pages?)?(?:\s[-—]\s.+)?$"
)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
VIDEO_ID = re.compile(r"^[\w-]{6,20}$")


def redact_title(process: str, title: str) -> str:
    """The only window text worth keeping: 'Video - YouTube' from a normal browser window.
    Everything else becomes ''. Idempotent, so it can be re-applied to stored titles."""
    if not title or (process or "").lower() not in BROWSERS or PRIVATE.search(title):
        return ""
    match = YOUTUBE_TITLE.match(title.strip())
    if not match:
        return ""
    video = match["video"].strip()
    if not video or video.casefold() in ("youtube", "youtube music"):
        return ""
    return f"{video} - {match['site']}"


def video_name(recorded_title: str) -> str:
    """'Video - YouTube' -> 'Video'."""
    return re.sub(r"\s+-\s+YouTube(?: Music)?$", "", recorded_title or "").strip()


def canonical_youtube_url(url: str) -> str:
    """A clean watch/shorts link without tracking parameters, or '' if it isn't a YouTube video."""
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in YOUTUBE_HOSTS:
        return ""
    video_id = ""
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0]
    elif parts.path == "/watch":
        video_id = (parse_qs(parts.query).get("v") or [""])[0]
    elif parts.path.startswith("/shorts/"):
        short = parts.path.split("/")[2] if len(parts.path.split("/")) > 2 else ""
        return f"https://www.youtube.com/shorts/{short}" if VIDEO_ID.match(short) else ""
    if not VIDEO_ID.match(video_id):
        return ""
    base = "https://music.youtube.com" if host == "music.youtube.com" else "https://www.youtube.com"
    return f"{base}/watch?v={video_id}"


def _normalize(title: str) -> str:
    title = re.sub(r"^\(\d+\)\s+", "", title or "")
    return " ".join(video_name(title).split()).casefold()


# Browser history
_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_URL_FILTER = "(u LIKE '%youtube.com/watch%' OR u LIKE '%youtube.com/shorts/%' OR u LIKE '%youtu.be/%')"


def _read_copy(files, query, params):
    """Copy a (possibly locked) SQLite file and its WAL to a private temp dir, query, delete."""
    with tempfile.TemporaryDirectory(prefix="odinskin-") as tmp:
        main = Path(tmp) / files[0].name
        try:
            for f in files:
                if f.exists():
                    shutil.copyfile(f, Path(tmp) / f.name)
        except OSError:
            return []
        conn = sqlite3.connect(f"file:{main.as_posix()}?mode=ro", uri=True)
        try:
            return conn.execute(query, params).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()


def _chromium_visits(root: Path, since: datetime, until: datetime):
    to_webkit = lambda dt: int((dt - _WEBKIT_EPOCH).total_seconds() * 1_000_000)  # noqa: E731
    histories = [root / "History"] + sorted(root.glob("*/History"))
    query = (
        "SELECT v.visit_time, u.title, u.url FROM visits v "
        "JOIN urls u ON u.id = v.url "
        f"WHERE v.visit_time BETWEEN ? AND ? AND {_URL_FILTER.replace('u LIKE', 'u.url LIKE')}"
    )
    for history in histories:
        if history.is_file():
            for stamp, title, url in _read_copy([history], query, (to_webkit(since), to_webkit(until))):
                yield _WEBKIT_EPOCH + timedelta(microseconds=stamp), title or "", url


def _firefox_visits(root: Path, since: datetime, until: datetime):
    to_unix = lambda dt: int(dt.timestamp() * 1_000_000)  # noqa: E731
    query = (
        "SELECT v.visit_date, p.title, p.url FROM moz_historyvisits v "
        "JOIN moz_places p ON p.id = v.place_id "
        f"WHERE v.visit_date BETWEEN ? AND ? AND {_URL_FILTER.replace('u LIKE', 'p.url LIKE')}"
    )
    for places in sorted(root.glob("*/places.sqlite")):
        files = [places, places.with_name("places.sqlite-wal")]
        for stamp, title, url in _read_copy(files, query, (to_unix(since), to_unix(until))):
            yield datetime.fromtimestamp(stamp / 1_000_000, tz=timezone.utc), title or "", url


def youtube_visits(process: str, since: datetime, until: datetime) -> list:
    """[(visit_time, page_title, canonical_url)] from one browser's history, YouTube videos only."""
    process = process.lower()
    found = []
    for root in CHROMIUM.get(process, []):
        found += list(_chromium_visits(root, since, until))
    for root in FIREFOX.get(process, []):
        found += list(_firefox_visits(root, since, until))
    return [(t, title, url) for t, title, raw in found if (url := canonical_youtube_url(raw))]


def attach_youtube_links(events: list, started: datetime, ended: datetime, visits_for=youtube_visits) -> int:
    """Fill ev['url'] for YouTube events by matching their titles against browser history.
    Only browsers that showed a YouTube video this session are read. Returns links found."""
    targets = [ev for ev in events if ev.get("window_title") and not ev.get("url")]
    if not targets:
        return 0
    since, until = started - timedelta(minutes=15), ended + timedelta(minutes=1)
    by_title = {}
    for process in {ev["process_name"].lower() for ev in targets}:
        for when, title, url in visits_for(process, since, until):
            by_title.setdefault(_normalize(title), []).append((when, url))

    found = 0
    for ev in targets:
        candidates = by_title.get(_normalize(ev["window_title"]))
        if not candidates:
            continue
        began = datetime.fromisoformat(ev["start_time"])
        earlier = [c for c in candidates if c[0] <= began + timedelta(minutes=2)]
        ev["url"] = (max(earlier) if earlier else min(candidates))[1]
        found += 1
    return found
