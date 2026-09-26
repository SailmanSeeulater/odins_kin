"""Odin's Kin web dashboard: a local Flask app over the same database as the tracker."""

import hmac
import os
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import appinfo
from database import (
    get_all_sessions,
    get_events_between,
    get_known_apps,
    get_session_events,
    get_youtube_between,
    init_db,
    parse_iso,
)
from privacy import canonical_youtube_url, redact_title, video_name

APP_ID = "odins-kin"
HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("ODINSKIN_PORT", 5000))

app = Flask(__name__)
init_db()


# Auth helper for a future write API. Fails closed: with no key configured, nothing passes.
API_KEY = os.environ.get("ODINSKIN_API_KEY", "")


def require_api_key():
    key = request.headers.get("X-API-Key", "")
    if not API_KEY or not hmac.compare_digest(key, API_KEY):
        abort(401, description="Invalid or missing API key")


# Frontend
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/icons/<path:filename>")
def icon(filename):
    return send_from_directory(appinfo.icons_dir(), filename, max_age=86400)


# Read API
@app.route("/api/health")
def api_health():
    return jsonify({"app": APP_ID})


@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_all_sessions())


def public_event(ev: dict) -> dict:
    """What the page may see of an event. Titles go through the redactor again, which also
    covers rows recorded before redaction existed; executable paths never leave the server."""
    title = redact_title(ev["process_name"], ev.get("window_title") or "")
    return {
        "id": ev["id"],
        "start_time": ev["start_time"],
        "end_time": ev["end_time"],
        "duration_seconds": ev["duration_seconds"],
        "process_name": ev["process_name"],
        "window_title": title,
        "url": canonical_youtube_url(ev.get("url") or "") if title else "",
    }


def requested_range(default_days: int = 14):
    now = datetime.now(timezone.utc)
    try:
        start = parse_iso(request.args["from"]) if "from" in request.args else now - timedelta(days=default_days)
        end = parse_iso(request.args["to"]) if "to" in request.args else now
    except ValueError:
        abort(400, description="from/to must be ISO 8601 timestamps")
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


@app.route("/api/sessions/<int:session_id>/events")
def api_session_events(session_id):
    return jsonify([public_event(ev) for ev in get_session_events(session_id)])


@app.route("/api/events")
def api_events():
    """Events overlapping ?from=&to= (ISO 8601), without titles, for usage charts."""
    return jsonify(get_events_between(*requested_range()))


@app.route("/api/youtube")
def api_youtube():
    """YouTube videos watched in ?from=&to=, one entry per video, most recent first."""
    videos = {}
    for ev in get_youtube_between(*requested_range()):
        title = redact_title(ev["process_name"], ev["window_title"] or "")
        if not title:
            continue
        url = canonical_youtube_url(ev.get("url") or "")
        key = url or f"title:{title.casefold()}"
        video = videos.setdefault(key, {
            "title": video_name(title),
            "music": title.endswith("YouTube Music"),
            "url": url,
            "seconds": 0.0,
            "first_watched": ev["start_time"],
            "last_watched": ev["end_time"],
            "session_id": ev["session_id"],
        })
        video["seconds"] += ev["duration_seconds"] or 0
        if ev["end_time"] > video["last_watched"]:
            video["last_watched"] = ev["end_time"]
            video["session_id"] = ev["session_id"]
    return jsonify(sorted(videos.values(), key=lambda v: v["last_watched"], reverse=True))


@app.route("/api/apps")
def api_apps():
    """Display name and icon URL for every process in the database."""
    apps = {}
    for row in get_known_apps():
        entry = appinfo.resolve(row["process_name"], row["exe_path"] or "")
        apps[row["process_name"]] = {
            "name": entry["name"],
            "icon": f"/icons/{entry['icon']}" if entry.get("icon") else None,
        }
    return jsonify(apps)


# Embedded use: the desktop app serves the dashboard from a background thread
_server = None
_server_lock = threading.Lock()


def serve_in_background(port: int = DEFAULT_PORT) -> str:
    """Start (once) a local server thread and return its URL. Tries a few ports."""
    global _server
    import logging

    from werkzeug.serving import make_server

    # A windowed .exe has no console; keep request logging out of it
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    with _server_lock:
        if _server is None:
            last_error = None
            for candidate in range(port, port + 10):
                try:
                    _server = make_server(HOST, candidate, app, threaded=True)
                    break
                except OSError as exc:
                    last_error = exc
            if _server is None:
                raise last_error
            threading.Thread(target=_server.serve_forever, daemon=True).start()
        return f"http://{HOST}:{_server.server_port}/"


if __name__ == "__main__":
    app.run(host=HOST, port=DEFAULT_PORT, debug=os.environ.get("ODINSKIN_DEBUG") == "1")
