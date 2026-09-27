"""Odin's Kin desktop tracker.

Start a session, work as usual, stop it. While recording, a gradient ring turns
around the timer and the apps you use rank themselves underneath. On stop the ring
settles into this session's app split, and the session is written to the database
and exported as JSON. Closing the window mid-session saves it too.
"""

import ctypes
import json
import queue
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime

from PIL import Image, ImageChops, ImageTk

import appinfo
import database
import graphics as g
from privacy import attach_youtube_links, video_name
from tracker import Session, export_json, get_active_window

POLL_MS = 1000
RING_STEPS = 32  # frames per revolution
RING_FRAME_MS = 125  # ~4 s per turn at 8 fps; cached, so it costs almost nothing
SWEEP_SECONDS = 0.7  # stop: the gradient gives way to the session's app split
FRAME_SECONDS = 1 / 60
MAX_ROWS = 4

# Logical layout (multiplied by the display scale)
W, H = 380, 704
SIDE = 16
RING_SIZE, RING_WIDTH, RING_CY = 232, 12, 182
GROUP_LABEL_Y, GROUP_Y = 318, 334
ROW_H = 56
BUTTON_Y, BUTTON_H = 606, 52
FOOT_Y = 682

THEMES = {
    "light": {
        "ground": "#F2F2F7", "group": "#FFFFFF", "ink": "#000000", "secondary": "#6C6C70",
        "tertiary": "#AEAEB2", "separator": "#D1D1D6", "track": "#E5E5EA", "tile": "#F2F2F7",
        "other": "#C7C7CC", "stop_bg": "#000000", "stop_fg": "#FFFFFF",
        "idle_bg": "#E5E5EA", "idle_fg": "#8E8E93",
    },
    "dark": {
        "ground": "#000000", "group": "#1C1C1E", "ink": "#FFFFFF", "secondary": "#98989F",
        "tertiary": "#636366", "separator": "#38383A", "track": "#2C2C2E", "tile": "#2C2C2E",
        "other": "#48484A", "stop_bg": "#FFFFFF", "stop_fg": "#000000",
        "idle_bg": "#2C2C2E", "idle_fg": "#8E8E93",
    },
}


# Windows integration
def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def system_theme() -> str:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if light else "dark"
    except Exception:
        return "light"


def animations_enabled() -> bool:
    """Honors Settings > Ease of Access > Show animations in Windows."""
    try:
        enabled = ctypes.c_bool(True)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value)
    except Exception:
        return True


_fine_timer = False


def fine_timer(on: bool):
    """1 ms scheduler ticks for smooth animation; released as soon as it ends."""
    global _fine_timer
    if on == _fine_timer:
        return
    try:
        (ctypes.windll.winmm.timeBeginPeriod if on else ctypes.windll.winmm.timeEndPeriod)(1)
        _fine_timer = on
    except Exception:
        pass


def load_inter() -> bool:
    try:
        return ctypes.windll.gdi32.AddFontResourceExW(str(g.FONT_PATH), 0x10, 0) > 0
    except Exception:
        return False


def match_title_bar(root: tk.Tk, dark: bool):
    try:
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, pre-20H1 value
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
        # Windows 10 only repaints the caption after a frame change
        flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
        root.withdraw()
        root.deiconify()
    except Exception:
        pass


# Formatting (mirrored by the dashboard's fmtDuration)
def fmt_clock(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, total % 3600 // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_duration(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    h, m, s = total // 3600, total % 3600 // 60, total % 60
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m < 10 and s:
        return f"{m}m {s}s"
    return f"{m}m"


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {many or word + 's'}"


def reveal_in_explorer(path):
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
    except OSError:
        pass


def settings_path():
    return database.data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict):
    try:
        settings_path().write_text(json.dumps(settings), encoding="utf-8")
    except OSError:
        pass


class TrackerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.dark = system_theme() == "dark"
        self.t = THEMES["dark" if self.dark else "light"]
        self.motion = animations_enabled()
        self.has_inter = load_inter()

        self.state = "idle"  # idle | recording | saving | saved
        self.session = None
        self.mono_start = 0.0
        self.saved_duration = 0.0
        self.saved_apps = []
        self.saved_switches = 0
        self.saved_path = None
        self.message = None  # (text, link_text, link_action)

        self.poll_job = self.tick_job = self.ring_job = None
        self.ring_index = 0
        self.ring_frames = {}
        self.sweeping = False
        self.photos = {}  # keeps PhotoImages alive
        self.avatar_cache = {}
        self.names = {}
        self.icons = {}
        self.icon_requests = set()
        self.icon_results = queue.Queue()
        self.save_results = queue.Queue()
        self.save_thread = None
        self.pending_save = None

        self.db_error = None
        try:
            database.init_db()
        except Exception as exc:
            self.db_error = str(exc)
        self.ranking = []
        self.exe_paths = {}
        self.today = []
        self.today_sessions = 0
        self.refresh_history()

        self.build()
        self.render_idle()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Return>", lambda e: self.on_primary())
        root.bind("<space>", lambda e: self.on_primary())
        self.drain_icons()

    # Helpers
    def px(self, v: float) -> int:
        return round(v * self.scale)

    def font(self, size: int, weight: str = "Regular") -> tkfont.Font:
        key = ("font", size, weight)
        if key not in self.photos:
            if self.has_inter:
                family = "Inter" if weight == "Regular" else f"Inter {weight}"
            else:
                family = "Segoe UI" if weight == "Regular" else "Segoe UI Semibold"
            self.photos[key] = tkfont.Font(family=family, size=-self.px(size))
        return self.photos[key]

    def photo(self, key, pil_image):
        img = ImageTk.PhotoImage(pil_image)
        self.photos[key] = img
        return img

    def elide(self, text: str, font: tkfont.Font, max_px: int) -> str:
        if font.measure(text) <= max_px:
            return text
        while text and font.measure(text + "…") > max_px:
            text = text[:-1]
        return text.rstrip() + "…"

    def display_name(self, process: str) -> str:
        return self.names.get(process) or appinfo.fallback_name(process)

    def refresh_history(self):
        if self.db_error:
            return
        try:
            now = datetime.now().astimezone()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            totals = database.app_totals_between(midnight, now)
            self.today = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            self.ranking = database.get_all_time_ranking()
            self.exe_paths = {r["process_name"]: r["exe_path"] or "" for r in database.get_known_apps()}
            self.today_sessions = sum(
                1 for s in database.get_all_sessions()
                if s["start_time"] and database.parse_iso(s["start_time"]) >= midnight
            )
        except Exception as exc:
            self.db_error = str(exc)

    # Layout
    def build(self):
        t, px = self.t, self.px
        self.root.configure(bg=t["ground"])
        c = self.c = tk.Canvas(
            self.root, width=px(W), height=px(H), bg=t["ground"],
            highlightthickness=0, bd=0,
        )
        c.pack()

        c.create_text(px(20), px(34), text="Odin's Kin", anchor="w",
                      font=self.font(17, "SemiBold"), fill=t["ink"])

        # Dashboard link with a drawn arrow
        dash_font = self.font(15, "Medium")
        right = px(W - 20)
        arrow = px(9)
        c.create_text(right - arrow - px(6), px(34), text="Dashboard", anchor="e",
                      font=dash_font, fill=t["ink"], tags=("dash", "dash_ink"))
        ax, ay = right - arrow, px(34) + arrow // 2
        c.create_line(ax, ay, ax + arrow, ay - arrow, fill=t["ink"], width=max(1, px(1.6)),
                      capstyle="round", tags=("dash", "dash_ink"))
        c.create_line(ax + px(2.5), ay - arrow, ax + arrow, ay - arrow, ax + arrow, ay - arrow + px(6.5),
                      fill=t["ink"], width=max(1, px(1.6)), capstyle="round", joinstyle="round",
                      tags=("dash", "dash_ink"))
        c.create_rectangle(right - arrow - px(6) - dash_font.measure("Dashboard") - px(8), px(20),
                           right + px(4), px(48), outline="", fill="", tags=("dash",))
        c.tag_bind("dash", "<Enter>", lambda e: self.hover_dash(True))
        c.tag_bind("dash", "<Leave>", lambda e: self.hover_dash(False))
        c.tag_bind("dash", "<Button-1>", lambda e: self.open_dashboard())

        cx = px(W / 2)
        self.ring_item = c.create_image(cx, px(RING_CY), anchor="center")
        self.numerals_item = c.create_image(cx, px(RING_CY - 8), anchor="center")
        self.caption_item = c.create_text(cx, px(RING_CY + 30), text="", anchor="center",
                                          font=self.font(13, "Medium"), fill=t["secondary"])

        self.label_item = c.create_text(px(20), px(GROUP_LABEL_Y), anchor="w",
                                        font=self.font(15, "SemiBold"), fill=t["ink"])
        self.stats_item = c.create_text(px(W - 20), px(GROUP_LABEL_Y), anchor="e",
                                        font=self.font(13), fill=t["secondary"])
        self.group_item = c.create_image(px(SIDE), px(GROUP_Y), anchor="nw")

        # Primary button
        self.button_item = c.create_image(px(SIDE), px(BUTTON_Y), anchor="nw", tags=("button",))
        self.button_text = c.create_text(cx, px(BUTTON_Y + BUTTON_H / 2), anchor="center",
                                         font=self.font(17, "SemiBold"), tags=("button",))
        self.button_glyph = c.create_rectangle(0, 0, 0, 0, outline="", tags=("button",))
        self.button_hover = self.button_pressed = False
        c.tag_bind("button", "<Enter>", lambda e: self.set_button_state(hover=True))
        c.tag_bind("button", "<Leave>", lambda e: self.set_button_state(hover=False, pressed=False))
        c.tag_bind("button", "<ButtonPress-1>", lambda e: self.set_button_state(pressed=True))
        c.tag_bind("button", "<ButtonRelease-1>", self.on_button_release)

        self.foot_item = c.create_text(cx, px(FOOT_Y), anchor="center", font=self.font(13),
                                       fill=t["secondary"])
        self.foot_link = c.create_text(0, px(FOOT_Y), anchor="w", font=self.font(13, "SemiBold"),
                                       fill=t["ink"], tags=("foot_link",))
        c.tag_bind("foot_link", "<Button-1>", lambda e: self.message and self.message[2]())
        c.tag_bind("foot_link", "<Enter>", lambda e: c.itemconfigure(self.foot_link, fill=t["secondary"]))
        c.tag_bind("foot_link", "<Leave>", lambda e: c.itemconfigure(self.foot_link, fill=t["ink"]))

    def hover_dash(self, on: bool):
        color = self.t["secondary"] if on else self.t["ink"]
        for item in self.c.find_withtag("dash_ink"):
            self.c.itemconfigure(item, fill=color)
        self.c.configure(cursor="hand2" if on else "")

    # Ring and numerals
    def set_numerals(self, text: str, color=None):
        img, _ = g.numerals(text, self.px(44), color or self.t["ink"], weight=600)
        self.c.itemconfigure(self.numerals_item, image=self.photo("numerals", img))

    def set_caption(self, text: str):
        self.c.itemconfigure(self.caption_item, text=text)

    def segments_for(self, apps):
        """[(fraction, color)] for a ring: named hues for the top apps, the rest as 'other'."""
        total = sum(secs for _, secs in apps)
        if total <= 0:
            return []
        colors = appinfo.color_map([p for p, _ in apps], self.ranking)
        segments, other = [], 0.0
        for proc, secs in apps:
            share = secs / total
            if proc in colors and share >= 0.01:
                segments.append((share, colors[proc]))
            else:
                other += share
        if other > 0:
            segments.append((other, self.t["other"]))
        return segments

    def show_segments(self, apps, animate=False):
        size, width = self.px(RING_SIZE), self.px(RING_WIDTH)
        segments = self.segments_for(apps)
        if not segments:
            img = g.solid_ring(size, width, self.t["track"])
            self.c.itemconfigure(self.ring_item, image=self.photo("ring", img))
            return
        if not (animate and self.motion):
            img = g.segmented_ring(size, width, segments, self.t["track"])
            self.c.itemconfigure(self.ring_item, image=self.photo("ring", img))
            return

        # Draw the finished ring once; each frame only masks it and repaints one Tk image,
        # about 4 ms a frame. Frames are timed by the clock, so a slow one never stretches
        # the sweep.
        track = self.flat(g.solid_ring(size, width, self.t["track"])).convert("RGBA")
        finished = g.segmented_ring(size, width, segments)
        # The live gradient stays put and the sweep replaces it as it passes, like a clock
        # hand, rather than vanishing on click or fading through a muddy half-state
        live = g.gradient_ring(size, width, self.ring_angle((self.ring_index - 1) % RING_STEPS))
        screen = ImageTk.PhotoImage(self.flat(live))
        self.photos["ring"] = screen
        self.c.itemconfigure(self.ring_item, image=screen)
        # Windows timers tick every 15.6 ms by default, which halves the frame rate;
        # ask for 1 ms ticks only while the sweep runs
        fine_timer(True)
        self.sweeping = True
        began = time.monotonic()

        def frame():
            t = min(1.0, (time.monotonic() - began) / SWEEP_SECONDS)
            sweep = 1 - (1 - t) ** 3  # ease-out cubic
            img = track.copy()
            if t < 1:
                pie = g.pie_mask(size, sweep)
                img.alpha_composite(g.masked(live, ImageChops.invert(pie)))
                img.alpha_composite(g.masked(finished, pie))
            else:
                img.alpha_composite(finished)
            head = g.sweep_head(size, width, segments, sweep) if t < 1 else None
            if head:
                x, y, color = head
                img.alpha_composite(g.disc(width, color), (round(x - width / 2), round(y - width / 2)))
            screen.paste(img.convert("RGB"))
            if t < 1 and self.state in ("saving", "saved"):
                # Aim at the next 60 Hz deadline, whatever this frame cost
                elapsed = time.monotonic() - began
                wait = FRAME_SECONDS - elapsed % FRAME_SECONDS
                self.ring_job = self.root.after(max(1, round(wait * 1000)), frame)
            else:
                fine_timer(False)
                self.sweeping = False

        frame()

    def flat(self, img):
        """Flatten onto the window's ground. Tk takes RGB about 30x faster than RGBA,
        and the ring always sits on the plain ground anyway."""
        base = Image.new("RGBA", img.size, self.t["ground"])
        base.alpha_composite(img)
        return base.convert("RGB")

    @staticmethod
    def ring_angle(index: int) -> float:
        return 45 - index * 360 / RING_STEPS  # clockwise

    def ring_frame(self, index: int):
        if index not in self.ring_frames:
            img = g.gradient_ring(self.px(RING_SIZE), self.px(RING_WIDTH), self.ring_angle(index))
            self.ring_frames[index] = ImageTk.PhotoImage(self.flat(img))
        return self.ring_frames[index]

    def spin_ring(self):
        if self.state != "recording":
            return
        # Don't burn CPU while minimized
        if self.root.state() != "iconic":
            self.c.itemconfigure(self.ring_item, image=self.ring_frame(self.ring_index))
            self.ring_index = (self.ring_index + 1) % RING_STEPS
        if self.motion:
            self.ring_job = self.root.after(RING_FRAME_MS, self.spin_ring)

    def cancel(self, *jobs):
        for name in jobs:
            job = getattr(self, name)
            if job:
                self.root.after_cancel(job)
                setattr(self, name, None)

    # Rows
    def request_icon(self, process: str, exe: str):
        key = (process, bool(exe))
        if key in self.icon_requests or (process, True) in self.icon_requests:
            return
        self.icon_requests.add(key)

        def work():
            try:
                entry = appinfo.resolve(process, exe)
                img = appinfo.icon_image(process, exe) if entry.get("icon") else None
                self.icon_results.put((process, entry["name"], img))
            except Exception:
                self.icon_results.put((process, appinfo.fallback_name(process), None))

        threading.Thread(target=work, daemon=True).start()

    def drain_icons(self):
        if self.sweeping:  # icons can wait 0.7 s; a mid-sweep redraw drops frames
            self.root.after(100, self.drain_icons)
            return
        changed = False
        while True:
            try:
                process, name, img = self.icon_results.get_nowait()
            except queue.Empty:
                break
            self.names[process] = name
            self.icons[process] = img
            self.avatar_cache = {k: v for k, v in self.avatar_cache.items() if k[0] != process}
            changed = True
        if changed and self.state != "recording":
            self.render_rows()  # while recording, the next poll redraws
        self.root.after(250, self.drain_icons)

    def avatar(self, process: str, ring):
        key = (process, ring)
        if key not in self.avatar_cache:
            img = g.avatar(
                self.px(40), self.t["tile"], self.icons.get(process),
                letter=self.display_name(process), letter_color=self.t["secondary"],
                ring=ring, ring_color=self.t["separator"],
            )
            self.avatar_cache[key] = ImageTk.PhotoImage(img)
        return self.avatar_cache[key]

    def current_rows(self):
        """(label, stats, [(process, seconds)], live_process, live_title)"""
        if self.state == "recording" and self.session:
            apps = self.session.app_totals()
            current = self.session.current
            stats = plural(self.session.switch_count(), "switch", "switches")
            return ("This session", stats, apps,
                    current["process_name"] if current else None,
                    current["window_title"] if current else "")
        if self.state in ("saving", "saved"):
            switches = plural(self.saved_switches, "switch", "switches")
            return ("Last session", switches, self.saved_apps, None, "")
        sessions = plural(self.today_sessions, "session") if self.today_sessions else ""
        return ("Today", sessions, self.today, None, "")

    def render_rows(self):
        c, t, px = self.c, self.t, self.px
        c.delete("rows")
        label, stats, apps, live, live_title = self.current_rows()
        c.itemconfigure(self.label_item, text=label)
        c.itemconfigure(self.stats_item, text=stats)

        # Rank order; the live app keeps the last slot if it hasn't ranked in yet
        ordered = sorted(apps, key=lambda kv: -kv[1])
        shown = ordered[:MAX_ROWS]
        if live and live not in [p for p, _ in shown]:
            shown = shown[: MAX_ROWS - 1] + [kv for kv in ordered if kv[0] == live]
        group_w = W - 2 * SIDE

        if not shown:
            height = 88
            self.set_group(group_w, height)
            copy = ("Start a session, then work as usual.\nThe apps you use will line up here."
                    if self.state == "idle" else "No apps recorded.")
            c.create_text(px(W / 2), px(GROUP_Y + height / 2), text=copy, justify="center",
                          font=self.font(15), fill=t["secondary"], tags=("rows",))
            return

        # A live YouTube video gets a third line under its app's bar
        video = video_name(live_title)
        heights = [ROW_H + 16 if proc == live and video else ROW_H for proc, _ in shown]
        self.set_group(group_w, sum(heights))
        colors = appinfo.color_map([p for p, _ in apps], self.ranking)
        top = max(secs for _, secs in apps) or 1
        text_x = SIDE + 14 + 40 + 12
        right_x = W - SIDE - 16
        dur_font, name_font, sub_font = self.font(15), self.font(15, "SemiBold"), self.font(13)

        y = GROUP_Y
        for i, ((proc, secs), row_h) in enumerate(zip(shown, heights)):
            exe = (self.session.exe_for(proc) if self.session else "") or self.exe_paths.get(proc, "")
            self.request_icon(proc, exe)
            is_live = proc == live
            c.create_image(px(SIDE + 14), px(y + row_h / 2), anchor="w", tags=("rows",),
                           image=self.avatar(proc, "live" if is_live else None))
            duration = fmt_duration(secs)
            c.create_text(px(right_x), px(y + row_h / 2), text=duration, anchor="e",
                          font=dur_font, fill=t["secondary"], tags=("rows",))
            name_w = px(right_x - text_x - 12) - dur_font.measure(duration)
            c.create_text(px(text_x), px(y + 21), anchor="w", tags=("rows",), font=name_font,
                          fill=t["ink"], text=self.elide(self.display_name(proc), name_font, name_w))
            bar_w = max(4, round((right_x - text_x - 52) * secs / top))
            c.create_image(px(text_x), px(y + 38), anchor="w", tags=("rows",),
                           image=self.photo(("bar", i), g.bar(px(bar_w), px(4),
                                                              colors.get(proc, t["other"]))))
            if is_live and video:
                c.create_text(px(text_x), px(y + 55), anchor="w", font=sub_font, fill=t["secondary"],
                              text=self.elide(video, sub_font, name_w), tags=("rows",))
            y += row_h
            if i < len(shown) - 1:
                c.create_line(px(text_x), px(y), px(W - SIDE), px(y),
                              fill=t["separator"], tags=("rows",))

    def set_group(self, width, height):
        key = ("group", width, height)
        if self.photos.get("group_key") != key:
            img = g.rounded_rect(self.px(width), self.px(height), self.px(12), self.t["group"])
            self.c.itemconfigure(self.group_item, image=self.photo("group", img))
            self.photos["group_key"] = key

    # Button and footnote
    def set_button(self, style: str, text: str):
        self.button_style, self.button_label = style, text
        self.set_button_state()

    def set_button_state(self, hover=None, pressed=None):
        if hover is not None:
            self.button_hover = hover
        if pressed is not None:
            self.button_pressed = pressed
        style, t, px = self.button_style, self.t, self.px
        enabled = style != "busy"
        self.c.configure(cursor="hand2" if self.button_hover and enabled else "")
        overlay = None
        if enabled and self.button_pressed:
            overlay = (0, 0, 0, 40)
        elif enabled and self.button_hover:
            # Lighten, except the white stop pill in dark mode, which dims
            overlay = (0, 0, 0, 20) if style == "stop" and self.dark else (255, 255, 255, 30)

        key = (style, overlay)
        if self.photos.get("button_key") != key:
            w, h = px(W - 2 * SIDE), px(BUTTON_H)
            if style == "start":
                img = g.pill(w, h, stops=g.PILL_STOPS, overlay=overlay)
            elif style == "stop":
                img = g.pill(w, h, fill=t["stop_bg"], overlay=overlay)
            else:
                img = g.pill(w, h, fill=t["idle_bg"])
            self.c.itemconfigure(self.button_item, image=self.photo("button", img))
            self.photos["button_key"] = key

        fg = {"start": "#FFFFFF", "stop": t["stop_fg"], "busy": t["idle_fg"]}[style]
        self.c.itemconfigure(self.button_text, text=self.button_label, fill=fg)
        if style == "stop":
            # A drawn stop square ahead of the label
            label_w = self.font(17, "SemiBold").measure(self.button_label)
            size, gap = px(11), px(10)
            x0 = px(W / 2) - (label_w + size + gap) // 2
            y0 = px(BUTTON_Y + BUTTON_H / 2) - size // 2
            self.c.coords(self.button_glyph, x0, y0, x0 + size, y0 + size)
            self.c.itemconfigure(self.button_glyph, fill=fg, state="normal")
            self.c.coords(self.button_text, x0 + size + gap + label_w / 2, px(BUTTON_Y + BUTTON_H / 2))
        else:
            self.c.itemconfigure(self.button_glyph, state="hidden")
            self.c.coords(self.button_text, px(W / 2), px(BUTTON_Y + BUTTON_H / 2))

    def on_button_release(self, event):
        inside = self.c.find_withtag("current") and "button" in self.c.gettags("current")
        self.set_button_state(pressed=False)
        if inside:
            self.on_primary()

    def set_footnote(self, text: str, link_text: str = "", action=None):
        self.message = (text, link_text, action) if link_text else None
        c, px = self.c, self.px
        if not link_text:
            c.itemconfigure(self.foot_item, text=text)
            c.coords(self.foot_item, px(W / 2), px(FOOT_Y))
            c.itemconfigure(self.foot_link, text="", state="hidden")
            return
        lead = f"{text}  ·  " if text else ""
        lead_w = self.font(13).measure(lead)
        link_w = self.font(13, "SemiBold").measure(link_text)
        x0 = px(W / 2) - (lead_w + link_w) // 2
        c.itemconfigure(self.foot_item, text=lead)
        c.coords(self.foot_item, x0 + lead_w / 2, px(FOOT_Y))
        c.itemconfigure(self.foot_link, text=link_text, state="normal")
        c.coords(self.foot_link, x0 + lead_w, px(FOOT_Y))

    # States
    def render_idle(self):
        self.state = "idle"
        total = sum(secs for _, secs in self.today)
        self.show_segments(self.today)
        self.set_numerals(fmt_duration(total) if total else "0m")
        self.set_caption("Today")
        self.render_rows()
        self.set_button("start", "Start session")
        if self.db_error:
            self.set_footnote("Database unavailable. Sessions will save as JSON.")
        else:
            self.set_footnote("Everything stays on this PC.")

    def on_primary(self):
        if self.state in ("idle", "saved"):
            self.start()
        elif self.state == "recording":
            self.stop()

    def start(self):
        self.state = "recording"
        self.session = Session()
        self.mono_start = time.monotonic()
        self.cancel("ring_job")
        fine_timer(False)  # in case a sweep was cut short
        self.sweeping = False
        self.set_button("stop", "Stop and save")
        self.set_caption("Recording")
        self.set_footnote("Closing the window saves the session too.")
        self.poll()
        self.tick()
        self.spin_ring()

    def poll(self):
        if self.state != "recording":
            return
        title, process, exe = get_active_window()
        self.session.observe(title, process, exe)
        self.request_icon(process, exe)
        self.render_rows()
        self.poll_job = self.root.after(POLL_MS, self.poll)

    def tick(self):
        if self.state != "recording":
            return
        elapsed = time.monotonic() - self.mono_start
        self.set_numerals(fmt_clock(elapsed))
        # Land just after the next whole second so the clock never skips
        self.tick_job = self.root.after(int(1000 - (elapsed % 1) * 1000) + 15, self.tick)

    def stop(self):
        if self.state != "recording":
            return
        self.cancel("poll_job", "tick_job", "ring_job")
        self.session.stop()
        self.state = "saving"
        self.saved_duration = (self.session.ended_at - self.session.started_at).total_seconds()
        self.saved_apps = self.session.app_totals(self.session.ended_at)
        self.saved_switches = self.session.switch_count()
        payload = self.session.to_payload()

        self.set_numerals(fmt_duration(self.saved_duration))
        self.set_caption("Saving…")
        self.set_button("busy", "Saving…")
        self.render_rows()
        self.root.update_idletasks()  # paint all of that before the first frame
        self.show_segments(self.saved_apps, animate=True)

        # Write once the ring has settled: a save thread competing for the GIL is what
        # stuttered the start of the sweep. Closing the window in that gap saves at once.
        self.pending_save = payload
        self.root.after(int(SWEEP_SECONDS * 1000) + 20 if self.sweeping else 0, self.begin_save)

    def begin_save(self):
        payload, self.pending_save = self.pending_save, None
        if payload is None:
            return
        self.save_thread = threading.Thread(target=self.save_worker, args=(payload,), daemon=True)
        self.save_thread.start()
        self.root.after(100, self.check_saved)

    def save_worker(self, payload):
        """JSON first, so the session survives even if the database write fails."""
        path, error = None, None
        try:
            # Links for any YouTube videos watched, from the browser's own history
            attach_youtube_links(payload["events"], datetime.fromisoformat(payload["session"]["start"]),
                                 datetime.fromisoformat(payload["session"]["end"]))
        except Exception:
            pass
        try:
            path = export_json(payload, database.data_dir() / "sessions")
        except Exception as exc:
            error = f"JSON export failed: {exc}"
        try:
            if self.db_error:
                raise RuntimeError(self.db_error)
            database.save_session(payload)
        except Exception as exc:
            error = error or f"database: {exc}"
        self.save_results.put((path, error))

    def check_saved(self):
        # Let the ring settle first; redrawing rows and the button mid-sweep drops frames
        if self.sweeping:
            self.root.after(50, self.check_saved)
            return
        try:
            path, error = self.save_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.check_saved)
            return
        self.state = "saved"
        self.saved_path = path
        self.refresh_history()
        self.set_caption("Saved" if not error else "Saved as JSON")
        self.set_button("start", "Start new session")
        if path and error:
            self.set_footnote("Database write failed", "Show JSON", lambda: reveal_in_explorer(path))
        elif path:
            self.set_footnote("Saved", "Show in folder", lambda: reveal_in_explorer(path))
        else:
            self.set_footnote(f"Couldn't save: {error}")

    # Dashboard
    def open_dashboard(self):
        self.set_footnote("Opening the dashboard…")

        def work():
            try:
                import server

                url = server.serve_in_background()
                webbrowser.open(url)
                self.root.after(0, self.restore_footnote)
            except Exception as exc:
                self.root.after(0, lambda: self.set_footnote(f"Dashboard didn't start: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def restore_footnote(self):
        if self.state == "recording":
            self.set_footnote("Closing the window saves the session too.")
        elif self.state == "saved" and self.saved_path:
            path = self.saved_path
            self.set_footnote("Saved", "Show in folder", lambda: reveal_in_explorer(path))
        elif self.state == "idle":
            self.set_footnote("Everything stays on this PC.")

    # Lifecycle
    def on_close(self):
        if self.state == "recording":
            self.cancel("poll_job", "tick_job", "ring_job")
            self.session.stop()
            self.save_worker(self.session.to_payload())
        elif self.pending_save is not None:
            payload, self.pending_save = self.pending_save, None
            self.save_worker(payload)
        elif self.save_thread and self.save_thread.is_alive():
            self.save_thread.join(timeout=10)
        settings = load_settings()
        settings.update(x=self.root.winfo_x(), y=self.root.winfo_y())
        save_settings(settings)
        self.root.destroy()


def place_window(root: tk.Tk, width: int, height: int):
    settings = load_settings()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = settings.get("x"), settings.get("y")
    if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < sw - 80 and 0 <= y < sh - 80):
        x, y = (sw - width) // 2, max(0, (sh - height) // 2 - 20)
    root.geometry(f"{width}x{height}+{x}+{y}")


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.title("Odin's Kin")
    root.resizable(False, False)
    scale = root.winfo_fpixels("1i") / 96.0
    place_window(root, round(W * scale), round(H * scale))
    icons = [ImageTk.PhotoImage(g.app_mark(size)) for size in (64, 32, 16)]
    root.iconphoto(True, *icons)
    app = TrackerApp(root)
    match_title_bar(root, app.dark)
    root.mainloop()


if __name__ == "__main__":
    main()
