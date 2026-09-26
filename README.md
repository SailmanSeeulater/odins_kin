# Odin's Kin

A Windows desktop app that shows where your screen time goes. Start a session, work as usual, stop it. Odin's Kin records which app had focus and for how long, stores it locally, and shows it in a Screen Time–style dashboard.

## Stack

| Part             | Built with                                             |
| ---------------- | ------------------------------------------------------ |
| Desktop tracker  | Python, Tkinter, Pillow (anti-aliased rings and pills) |
| Window detection | pywin32 + psutil                                       |
| Database         | SQLite by default, PostgreSQL optional                 |
| Web dashboard    | Flask + HTML/CSS/vanilla JS                            |
| Type             | Inter (self-hosted, OFL), in both apps                 |

## Setup

```bash
pip install -r requirements.txt
```

### Run the tracker

```bash
python app.py
```

- **Start session** begins recording. The ring turns while it records, and the apps you use rank themselves underneath with their real icons. A YouTube video shows its title under its app.
- **Stop and save** writes the session to the database and exports a JSON copy. Closing the window mid-session saves it too.
- Enter or Space toggles recording when the window is focused.
- **Dashboard ↗** opens the web dashboard in your browser. The app serves it itself, so there's nothing else to run.

### Run the dashboard on its own

```bash
python server.py
```

Then open <http://127.0.0.1:5000>. Day and Week views work like iOS Screen Time. Click a day's bar to drill into it, or a session to see its timeline and every focus event. The ← and → keys step through days or weeks.

### Run the tests

```bash
python -m unittest discover tests
```

### Build the .exe

```bash
pyinstaller Odins_Kin.spec
```

## Where your data lives

Everything stays under `%LOCALAPPDATA%\Odins_Kin\`:

| Path             | What                                                  |
| ---------------- | ----------------------------------------------------- |
| `sessions.db`    | SQLite database with all sessions and focus events    |
| `sessions\*.json` | One human-readable export per session                |
| `icons\`, `apps.json` | App icons and names read from each executable    |
| `settings.json`  | Window position                                       |

Environment variables:

| Variable                | Effect                                                         |
| ----------------------- | -------------------------------------------------------------- |
| `ODINSKIN_DATABASE_URL` | `postgresql://user:pass@host/db` switches both apps to Postgres (`pip install psycopg2-binary`) |
| `ODINSKIN_DATA_DIR`     | Use a different data folder (handy for testing)                |
| `ODINSKIN_PORT`         | Dashboard port (default 5000)                                  |

## Privacy

Odin's Kin only ever reads a window's **title bar**, never what's inside the window. Titles still leak things like terminal paths and commands, editor file names, and email subjects, so they are **dropped by default** (`privacy.py`). Only the app and its time are recorded.

The one exception is **YouTube**. When a normal (not InPrivate or Incognito) Chrome, Edge, Brave, Vivaldi, Opera or Firefox window shows a YouTube video, the video title is kept. When the session stops, each video's link is matched from that browser's own history file. The app reads a temporary copy, keeps only YouTube watch links from the session's time window, and deletes the copy. The dashboard's **YouTube** section lists what you watched, for how long, with links.

The dashboard server runs every title through the same filter again before sending it, so even sessions recorded by older versions don't expose raw titles. Executable paths never leave the server.

To remove titles that older versions already saved, from the database and the JSON exports:

```bash
python tools/scrub_titles.py          # report counts only
python tools/scrub_titles.py --apply  # rewrite, then VACUUM the database
```

## Fixing sessions recorded by older versions

Before this version, a second session in the same launch re-saved the first session's events. To find and undo that double counting:

```bash
python tools/repair_duplicate_sessions.py          # report only
python tools/repair_duplicate_sessions.py --apply  # backs up the DB, then fixes it
```
