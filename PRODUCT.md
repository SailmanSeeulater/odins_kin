# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the owner of the machine. They start a session when they sit down to work, switch between apps as usual, and stop the session when they're done. Later they open the local dashboard to see where the time actually went. It is a personal mirror, not a surveillance or team tool.

## Product Purpose

Odin's Kin records which Windows application has focus, and for how long, during a session the user starts and stops explicitly. It turns that raw focus stream into an honest picture of a work block: total time, how often focus switched, and which apps took the time. Success means the user trusts the numbers and wants to look at them.

## Positioning

The capture is manual and session-scoped: nothing records unless the user presses start, and every session ends with a local, human-readable export. The data never leaves the machine unless the user points it at their own Postgres.

## Operating Context

- **Desktop tracker** (`app.py`): a small, always-available Windows window built with Tkinter plus Pillow-rendered graphics. It polls the foreground window with pywin32 and psutil. Start/stop is the whole ritual. It must never lose a session, including when the window is closed mid-recording.
- **Web dashboard** (`server.py` + `templates/index.html`): a Flask app on localhost, opened in a browser after or between sessions. It reads the same database and exists for reflection: totals, top apps, a per-session timeline, and drill-down into focus events.
- **Packaging**: the desktop app ships as a PyInstaller one-file `.exe` (`Odins_Kin.spec`), so all writable data must live under `%LOCALAPPDATA%`, never next to the executable.

## Capabilities and Constraints

- Storage: SQLite in `%LOCALAPPDATA%\Odins_Kin\sessions.db` by default (the folder earlier builds already used). Setting `ODINSKIN_DATABASE_URL` to a Postgres URL switches both apps to Postgres.
- Each session is also exported as JSON under `%LOCALAPPDATA%\Odins_Kin\sessions\`.
- Windows only (pywin32).
- Privacy (owner requirement, 2026-09-26): no sensitive window text is recorded or sent to the dashboard, including terminal contents and paths (PowerShell etc.) and editor file names (VS Code etc.). Only the title bar is ever read, never window contents, and titles are dropped by default (`privacy.py`), before storage and again on API output. The only exception is YouTube.
- YouTube history (owner requirement): video titles from normal (not InPrivate/Incognito) browser windows are kept. After each session, their links are matched from that browser's own history file (read from a temporary copy, YouTube watch URLs only, copy deleted). The dashboard lists videos with watch time and links. Recorded titles are still untrusted text: always escaped, never rendered as HTML.
- Executable paths stay on the machine: they're used for icons and names and never reach the page.
- Undecided: idle detection, excluding the tracker's own window, and a remote ingest API. The API-key helper in `server.py` hints at an ingest API but no endpoint exists.

## Brand Commitments

- Name: **Odin's Kin**.
- Owner-pinned aesthetic: "Apple and Instagram had a baby." That means Apple-grade restraint, type, and material quality, with Instagram's warm gradient energy and social-feed intimacy. This covers both the desktop app and the web dashboard, as one world.

## Evidence on Hand

- Real sample data: `sessions.db` (4 sessions) and `sessions/activity_session_20260523_013051.json`.
- Mark: a story ring around the Ansuz rune (Odin's rune), drawn in code (`graphics.app_mark`, `static/mark.svg`, `assets/odins_kin.ico`) during the 2026-09 redesign because the app needed a window icon. It is provisional and the owner may replace it.
- No testimonials or metrics exist. Do not invent them.

## Product Principles

1. Never lose a session. A crash, close, or database failure must still leave the user's data somewhere they can find it.
2. Numbers first, honestly. Durations add up, rounding is consistent, and nothing is inflated.
3. Local and private by default.
4. The ritual is one button. Starting and stopping should feel effortless and satisfying.

## Accessibility & Inclusion

No product-specific requirement was established. The general floor applies: readable contrast, keyboard operability on the web dashboard, and respect for reduced-motion preferences.
