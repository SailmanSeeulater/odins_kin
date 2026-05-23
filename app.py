import json
import tkinter as tk
import winreg
from datetime import datetime, timezone
from pathlib import Path

import psutil
import win32gui
import win32process

from database import init_db, save_session

init_db()  # Creating the database duh

BG = "#0A0A0F"
SURFACE = "#12121A"
BORDER = "#1E1E2E"
TEXT = "#E8E8F0"
ACCENT = "#00FF99"
MUTED = "#5A5A7A"
RED = "#FF4466"

elapsed = 0
running = False
tick_job = None

events = []
last_window = None

root = tk.Tk()
root.title("Activity Tracker")
root.geometry("480x520")
root.resizable(False, False)
root.configure(bg=BG)

# Header
tk.Label(
    root, text="ACTIVITY TRACKER", bg=BG, fg=ACCENT, font=("Courier New", 20, "bold")
).pack(anchor="w", padx=28, pady=(28, 4))

tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=28, pady=12)

# Timer display
timer_frame = tk.Frame(
    root, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1
)
timer_frame.pack(fill="x", padx=28, pady=(0, 16))

inner = tk.Frame(timer_frame, bg=SURFACE)
inner.pack(padx=20, pady=16)

tk.Label(
    inner, text="ELAPSED", bg=SURFACE, fg=MUTED, font=("Courier New", 9, "bold")
).pack()

timer_label = tk.Label(
    inner, text="00:00:00", bg=SURFACE, fg=TEXT, font=("Courier New", 40, "bold")
)

timer_label.pack()

# Live feed
live_frame = tk.Frame(
    root, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1
)
live_frame.pack(fill="x", padx=28, pady=(0, 16))

lf_inner = tk.Frame(live_frame, bg=SURFACE)
lf_inner.pack(fill="x", padx=20, pady=12)

tk.Label(
    lf_inner,
    text="ACTIVE WINDOW",
    bg=SURFACE,
    fg=MUTED,
    font=("Courier New", 9, "bold"),
).pack(anchor="w")

live_var = tk.StringVar(value="—")
tk.Label(
    lf_inner,
    textvariable=live_var,
    bg=SURFACE,
    fg=ACCENT,
    font=("Courier New", 10),
    wraplength=400,
    justify="left",
).pack(anchor="w")

# Event counter
count_label = tk.Label(
    root, text="0 focus events recorded", bg=BG, fg=MUTED, font=("Courier New", 9)
)
count_label.pack(anchor="w", padx=28)

# Path label
path_label = tk.Label(
    root, text="", bg=BG, fg=MUTED, font=("Courier New", 8), wraplength=420
)
path_label.pack(anchor="w", padx=28, pady=(4, 0))

# Status dot + text
status_row = tk.Frame(inner, bg=SURFACE)
status_row.pack(pady=(4, 0))
dot = tk.Label(status_row, text="●", bg=SURFACE, fg=MUTED, font=("Courier New", 10))
dot.pack(side="left")
status_label = tk.Label(
    status_row, text="  IDLE", bg=SURFACE, fg=MUTED, font=("Courier New", 10, "bold")
)
status_label.pack(side="left")


# Window Detection Portion
def get_active_window():
    """Returns (window_title, process_name, exe_path) of the foreground window."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            exe = proc.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = "Unknown"
            exe = ""
        return title, name, exe
    except Exception:
        return "", "Unknown", ""


# Concerning time update
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def update_live():
    global last_window
    if not running:
        return

    title, process, exe = get_active_window()
    key = (title, process)

    # Update the live label
    display = f"{process} - {title[:55]}{'…' if len(title) > 55 else ''}"
    live_var.set(display)

    # Detect a focus change
    if key != last_window:
        timestamp = now_iso()

        # Close the previous event
        if events and events[-1]["end_time"] is None:
            events[-1]["end_time"] = timestamp
            start_dt = datetime.fromisoformat(events[-1]["start_time"])
            end_dt = datetime.fromisoformat(timestamp)
            events[-1]["duration_seconds"] = round(
                (end_dt - start_dt).total_seconds(), 2
            )

        # Open a new event
        events.append(
            {
                "start_time": timestamp,
                "end_time": None,
                "duration_seconds": None,
                "window_title": title,
                "process_name": process,
                "exe_path": exe,
            }
        )

        last_window = key
        count_label.config(text=f"{len(events)} focus events recorded")

    root.after(1500, update_live)


# Buttons
btn_frame = tk.Frame(root, bg=BG)
btn_frame.pack(fill="x", padx=28, pady=20)


def tick():
    global elapsed, tick_job
    elapsed += 1
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60
    timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
    tick_job = root.after(1000, tick)


def start():
    global elapsed, running
    elapsed = 0
    running = True
    timer_label.config(text="00:00:00")
    dot.config(fg=ACCENT)
    status_label.config(text="  RECORDING", fg=ACCENT)
    start_btn.config(state="disabled", bg=BORDER, fg=MUTED)
    stop_btn.config(state="normal", bg=RED, fg="white")
    tick()
    update_live()


# The stop button is basically an export button
def stop():
    global running, tick_job
    running = False
    if tick_job:
        root.after_cancel(tick_job)
        tick_job = None

    # Close the last open event
    if events and events[-1]["end_time"] is None:
        events[-1]["end_time"] = now_iso()
        start_dt = datetime.fromisoformat(events[-1]["start_time"])
        end_dt = datetime.fromisoformat(events[-1]["end_time"])
        events[-1]["duration_seconds"] = round((end_dt - start_dt).total_seconds(), 2)

    # Build time per app
    summary = {}
    for ev in events:
        dur = ev.get("duration_seconds") or 0
        name = ev["process_name"]
        summary[name] = round(summary.get(name, 0) + dur, 2)

    payload = {
        "session": {
            "start": events[0]["start_time"] if events else None,
            "end": events[-1]["end_time"] if events else None,
            "total_events": len(events),
        },
        "time_per_app_seconds": summary,
        "events": events,
    }

    save_session(payload)

    # Export JSON
    out = export_json()
    path_label.config(text=f"✓ Saved → {out}", fg=ACCENT)

    # Reset UI
    live_var.set("—")
    dot.config(fg=MUTED)
    status_label.config(text="  IDLE", fg=MUTED)
    start_btn.config(state="normal", bg=ACCENT, fg=BG)
    stop_btn.config(state="disabled", bg=SURFACE, fg=MUTED)


def export_json():
    summary = {}
    for ev in events:
        dur = ev.get("duration_seconds") or 0
        key = ev["process_name"]
        summary[key] = round(summary.get(key, 0) + dur, 2)

    summary_sorted = dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    payload = {
        "session": {
            "start": events[0]["start_time"] if events else None,
            "end": events[-1]["end_time"] if events else None,
            "total_events": len(events),
        },
        "time_per_app_seconds": summary_sorted,
        "events": events,
    }

    output_dir = Path(__file__).parent / "sessions"
    output_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = output_dir / f"activity_session_{ts}.json"

    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return out


start_btn = tk.Button(
    btn_frame,
    text="▶  START SESSION",
    bg=ACCENT,
    fg=BG,
    font=("Courier New", 11, "bold"),
    relief="flat",
    padx=20,
    pady=12,
    cursor="hand2",
    command=start,
)
start_btn.pack(fill="x", pady=(0, 8))

stop_btn = tk.Button(
    btn_frame,
    text="■  STOP SESSION",
    bg=SURFACE,
    fg=MUTED,
    font=("Courier New", 11, "bold"),
    relief="flat",
    padx=20,
    pady=12,
    cursor="hand2",
    state="disabled",
    highlightbackground=BORDER,
    highlightthickness=1,
    command=stop,
)

stop_btn.pack(fill="x")

root.mainloop()
