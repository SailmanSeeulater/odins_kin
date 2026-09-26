"""Run with: python -m unittest discover tests"""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import privacy  # noqa: E402
from privacy import attach_youtube_links, canonical_youtube_url, redact_title, video_name  # noqa: E402

T0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


class RedactionTests(unittest.TestCase):
    def test_non_browser_titles_are_dropped(self):
        for process, title in [
            ("powershell.exe", r"PS C:\Users\me\secret-project> git push"),
            ("WindowsTerminal.exe", "ssh prod-db-01"),
            ("Code.exe", "passwords.txt - notes - Visual Studio Code"),
            ("OUTLOOK.EXE", "Offer letter - Inbox - Outlook"),
            ("Code.exe", "Great talk - YouTube - Visual Studio Code"),  # YouTube-looking, wrong app
        ]:
            self.assertEqual(redact_title(process, title), "", title)

    def test_browser_pages_other_than_youtube_are_dropped(self):
        self.assertEqual(redact_title("chrome.exe", "Inbox (3) - me@example.com - Gmail - Google Chrome"), "")
        self.assertEqual(redact_title("chrome.exe", "YouTube - Google Chrome"), "")  # home page, no video

    def test_youtube_videos_are_kept_without_browser_noise(self):
        cases = {
            ("chrome.exe", "(3) Tkinter tips - YouTube - Google Chrome"): "Tkinter tips - YouTube",
            ("msedge.exe", "Tkinter tips - YouTube and 2 more pages - Personal - Microsoft Edge"): "Tkinter tips - YouTube",
            ("firefox.exe", "Tkinter tips - YouTube — Mozilla Firefox"): "Tkinter tips - YouTube",
            ("chrome.exe", "Lo-fi mix - YouTube Music - Google Chrome"): "Lo-fi mix - YouTube Music",
            ("chrome.exe", "Part 1 - Part 2 - YouTube - Google Chrome"): "Part 1 - Part 2 - YouTube",
        }
        for (process, title), expected in cases.items():
            self.assertEqual(redact_title(process, title), expected, title)

    def test_private_windows_are_dropped(self):
        self.assertEqual(redact_title("msedge.exe", "Video - YouTube - [InPrivate] - Microsoft Edge"), "")
        self.assertEqual(redact_title("chrome.exe", "Video - YouTube - Google Chrome (Incognito)"), "")
        self.assertEqual(redact_title("firefox.exe", "Video - YouTube — Mozilla Firefox Private Browsing"), "")

    def test_redaction_is_idempotent(self):
        once = redact_title("chrome.exe", "(1) Video - YouTube - Google Chrome")
        self.assertEqual(redact_title("chrome.exe", once), once)
        self.assertEqual(video_name(once), "Video")


class UrlTests(unittest.TestCase):
    def test_canonical_links_drop_tracking(self):
        self.assertEqual(
            canonical_youtube_url("https://www.youtube.com/watch?v=abcDEF12345&pp=xyz&t=42s"),
            "https://www.youtube.com/watch?v=abcDEF12345",
        )
        self.assertEqual(canonical_youtube_url("https://youtu.be/abcDEF12345?si=track"),
                         "https://www.youtube.com/watch?v=abcDEF12345")
        self.assertEqual(canonical_youtube_url("https://www.youtube.com/shorts/abcDEF12345"),
                         "https://www.youtube.com/shorts/abcDEF12345")
        self.assertEqual(canonical_youtube_url("https://music.youtube.com/watch?v=abcDEF12345&list=RD"),
                         "https://music.youtube.com/watch?v=abcDEF12345")

    def test_non_youtube_or_non_video_links_are_rejected(self):
        for url in [
            "https://evil.example/watch?v=abcDEF12345",
            "https://www.youtube.com/results?search_query=x",
            "javascript:alert(1)",
            "https://www.youtube.com/watch?v=<script>",
        ]:
            self.assertEqual(canonical_youtube_url(url), "", url)


def fake_chromium_history(folder: Path, rows):
    """A History file with Chromium's urls/visits schema."""
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "History")
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    for i, (when, url, title) in enumerate(rows, start=1):
        conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (i, url, title))
        conn.execute("INSERT INTO visits VALUES (?, ?, ?)",
                     (i, i, int((when - epoch).total_seconds() * 1_000_000)))
    conn.commit()
    conn.close()


class HistoryTests(unittest.TestCase):
    def test_links_come_from_browser_history_youtube_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "User Data"
            fake_chromium_history(root / "Default", [
                (T0 + timedelta(minutes=1), "https://www.youtube.com/watch?v=abcDEF12345&pp=1", "(2) Tkinter tips - YouTube"),
                (T0 + timedelta(minutes=2), "https://bank.example/account", "My bank"),
                (T0 - timedelta(days=3), "https://www.youtube.com/watch?v=oldOLD12345", "Old video - YouTube"),
            ])
            original = privacy.CHROMIUM
            privacy.CHROMIUM = {"chrome.exe": [root]}
            try:
                visits = privacy.youtube_visits("chrome.exe", T0, T0 + timedelta(hours=1))
            finally:
                privacy.CHROMIUM = original
        self.assertEqual([(t.minute, url) for t, _, url in visits],
                         [(1, "https://www.youtube.com/watch?v=abcDEF12345")])

    def test_events_are_matched_to_their_visit(self):
        events = [
            {"start_time": (T0 + timedelta(minutes=1)).isoformat(), "process_name": "chrome.exe",
             "window_title": "Tkinter tips - YouTube", "url": None},
            {"start_time": (T0 + timedelta(minutes=9)).isoformat(), "process_name": "Code.exe",
             "window_title": "", "url": None},
            {"start_time": (T0 + timedelta(minutes=20)).isoformat(), "process_name": "chrome.exe",
             "window_title": "Tkinter tips - YouTube", "url": None},
        ]
        calls = []

        def visits_for(process, since, until):
            calls.append(process)
            return [
                (T0 + timedelta(seconds=30), "(1) Tkinter tips - YouTube", "https://www.youtube.com/watch?v=first111111"),
                (T0 + timedelta(minutes=19), "Tkinter tips - YouTube", "https://www.youtube.com/watch?v=second22222"),
            ]

        found = attach_youtube_links(events, T0, T0 + timedelta(minutes=30), visits_for=visits_for)
        self.assertEqual(found, 2)
        self.assertEqual(calls, ["chrome.exe"])  # only browsers that showed YouTube are read
        self.assertTrue(events[0]["url"].endswith("first111111"))
        self.assertIsNone(events[1]["url"])
        self.assertTrue(events[2]["url"].endswith("second22222"))

    def test_sessions_without_youtube_never_read_history(self):
        def visits_for(*_):
            raise AssertionError("history must not be read")

        events = [{"start_time": T0.isoformat(), "process_name": "chrome.exe", "window_title": "", "url": None}]
        self.assertEqual(attach_youtube_links(events, T0, T0, visits_for=visits_for), 0)


if __name__ == "__main__":
    unittest.main()
