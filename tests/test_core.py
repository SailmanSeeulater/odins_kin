"""Run with: python -m unittest discover tests"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Point storage at a throwaway folder before anything imports database
_tmp = tempfile.TemporaryDirectory()
os.environ["ODINSKIN_DATA_DIR"] = _tmp.name
os.environ.pop("ODINSKIN_DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import appinfo  # noqa: E402
import database  # noqa: E402
from app import fmt_clock, fmt_duration  # noqa: E402
from tracker import Session, export_json  # noqa: E402

T0 = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def sample_session():
    s = Session(T0)
    s.observe("app.py - Code", "Code.exe", r"C:\Code.exe", at(0))
    s.observe("app.py - Code", "Code.exe", r"C:\Code.exe", at(30))  # same window: no new event
    s.observe("Docs - Chrome", "chrome.exe", r"C:\chrome.exe", at(60))
    s.observe("app.py - Code", "Code.exe", r"C:\Code.exe", at(90))
    s.stop(at(150))
    return s


class SessionTests(unittest.TestCase):
    def test_focus_changes_open_and_close_events(self):
        s = sample_session()
        self.assertEqual(len(s.events), 3)
        self.assertEqual([e["duration_seconds"] for e in s.events], [60, 30, 60])
        self.assertTrue(all(e["end_time"] for e in s.events))
        self.assertEqual(s.switch_count(), 2)

    def test_app_totals_rank_biggest_first(self):
        self.assertEqual(sample_session().app_totals(), [("Code.exe", 120), ("chrome.exe", 30)])

    def test_open_event_counts_up_to_now(self):
        s = Session(T0)
        s.observe("x", "a.exe", "", at(0))
        self.assertEqual(s.app_totals(at(42)), [("a.exe", 42)])

    def test_new_session_starts_empty(self):
        # The old app kept one global event list, so session 2 re-saved session 1's events
        sample_session()
        self.assertEqual(Session(T0).events, [])

    def test_payload_shape_matches_json_export(self):
        payload = sample_session().to_payload()
        self.assertEqual(payload["session"]["total_events"], 3)
        self.assertEqual(payload["time_per_app_seconds"], {"Code.exe": 120, "chrome.exe": 30})
        self.assertEqual(set(payload["events"][0]), {
            "start_time", "end_time", "duration_seconds", "window_title", "process_name", "exe_path", "url",
        })

    def test_titles_are_redacted_before_they_are_stored(self):
        s = Session(T0)
        s.observe(r"PS C:\secret> deploy", "powershell.exe", "", at(0))
        s.observe("Talk - YouTube - Google Chrome", "chrome.exe", "", at(10))
        self.assertEqual([e["window_title"] for e in s.events], ["", "Talk - YouTube"])

    def test_hidden_title_changes_do_not_split_events(self):
        s = Session(T0)
        s.observe("a.py - proj - Visual Studio Code", "Code.exe", "", at(0))
        s.observe("b.py - proj - Visual Studio Code", "Code.exe", "", at(5))
        self.assertEqual(len(s.events), 1)

    def test_export_json_writes_a_file(self):
        out = export_json(sample_session().to_payload(), Path(_tmp.name) / "exports")
        self.assertTrue(out.exists())
        self.assertIn("activity_session_", out.name)


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.session_id = database.save_session(sample_session().to_payload())

    def test_round_trip(self):
        sessions = {s["id"]: s for s in database.get_all_sessions()}
        saved = sessions[self.session_id]
        self.assertEqual(saved["summary"], {"Code.exe": 120, "chrome.exe": 30})
        events = database.get_session_events(self.session_id)
        self.assertEqual([e["process_name"] for e in events], ["Code.exe", "chrome.exe", "Code.exe"])
        self.assertTrue(all(e["window_title"] == "" for e in events))  # sample titles aren't YouTube

    def test_totals_clip_at_range_edges(self):
        totals = database.app_totals_between(at(45), at(100))
        self.assertAlmostEqual(totals["Code.exe"], 15 + 10)
        self.assertAlmostEqual(totals["chrome.exe"], 30)

    def test_ranking_and_known_apps(self):
        self.assertEqual(database.get_all_time_ranking()[0], "Code.exe")
        known = {r["process_name"]: r["exe_path"] for r in database.get_known_apps()}
        self.assertEqual(known["chrome.exe"], r"C:\chrome.exe")


class ColorTests(unittest.TestCase):
    def test_all_time_leaders_keep_their_hue(self):
        colors = appinfo.color_map(["b", "a"], ["a", "b", "c"])
        self.assertEqual(colors["a"], appinfo.APP_COLORS[0])
        self.assertEqual(colors["b"], appinfo.APP_COLORS[1])

    def test_newcomers_borrow_unused_hues(self):
        colors = appinfo.color_map(["new", "a"], ["a", "b"])
        self.assertEqual(colors["a"], appinfo.APP_COLORS[0])
        self.assertEqual(colors["new"], appinfo.APP_COLORS[1])  # b isn't on screen, so its hue is free

    def test_overflow_is_other(self):
        many = [f"p{i}" for i in range(10)]
        self.assertEqual(len(appinfo.color_map(many, [])), len(appinfo.APP_COLORS))


class FormatTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(fmt_duration(13.4), "13s")
        self.assertEqual(fmt_duration(252), "4m 12s")
        self.assertEqual(fmt_duration(47 * 60 + 5), "47m")
        self.assertEqual(fmt_duration(3600), "1h")
        self.assertEqual(fmt_duration(2 * 3600 + 14 * 60), "2h 14m")

    def test_clock(self):
        self.assertEqual(fmt_clock(767), "12:47")
        self.assertEqual(fmt_clock(3767), "1:02:47")


if __name__ == "__main__":
    unittest.main()
