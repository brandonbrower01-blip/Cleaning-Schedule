#!/usr/bin/env python3
"""Tests for the BlockChores server.  Run: python3 -m unittest discover tests"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


def make_task(recurrence, last_completed=None, start=None, **extra):
    task = {
        "id": "task_test",
        "name": "Test",
        "area": "Kitchen",
        "points": 10,
        "recurrence": server.clean_recurrence(recurrence),
        "created": start or "2026-01-01",
        "start": start or "2026-01-01",
        "last_completed": last_completed,
        "streak": 0,
        "completions": 0,
        "archived": False,
    }
    task.update(extra)
    return task


class RecurrenceTests(unittest.TestCase):

    def test_interval_waits_from_last_completion(self):
        task = make_task({"type": "days", "every": 3}, last_completed="2026-03-10")
        self.assertEqual(server.compute_next_due(task), "2026-03-13")

    def test_new_interval_task_is_due_on_its_start_date(self):
        task = make_task({"type": "days", "every": 3}, start="2026-03-01")
        self.assertEqual(server.compute_next_due(task), "2026-03-01")

    def test_missed_task_stays_overdue_rather_than_skipping_ahead(self):
        # completed long ago: the next due date is still in the past
        task = make_task({"type": "days", "every": 1}, last_completed="2026-03-01")
        self.assertEqual(server.compute_next_due(task), "2026-03-02")

    def test_weekly_picks_the_next_listed_weekday(self):
        # 2026-03-10 is a Tuesday; next Thursday (4) is the 12th
        task = make_task({"type": "weekly", "days": [4]}, last_completed="2026-03-10")
        self.assertEqual(server.compute_next_due(task), "2026-03-12")

    def test_weekly_wraps_into_the_following_week(self):
        # completed Thursday, only Monday (1) scheduled -> next Monday
        task = make_task({"type": "weekly", "days": [1]}, last_completed="2026-03-12")
        self.assertEqual(server.compute_next_due(task), "2026-03-16")

    def test_weekly_never_returns_the_completion_day_itself(self):
        # Tuesday is in the list, but it was just done: expect next Tuesday
        task = make_task({"type": "weekly", "days": [2]}, last_completed="2026-03-10")
        self.assertEqual(server.compute_next_due(task), "2026-03-17")

    def test_monthly_rolls_to_next_month(self):
        task = make_task({"type": "monthly", "day": 1}, last_completed="2026-03-01")
        self.assertEqual(server.compute_next_due(task), "2026-04-01")

    def test_monthly_clamps_to_a_short_month(self):
        task = make_task({"type": "monthly", "day": 31}, last_completed="2026-01-31")
        self.assertEqual(server.compute_next_due(task), "2026-02-28")

    def test_monthly_handles_a_leap_year(self):
        task = make_task({"type": "monthly", "day": 30}, last_completed="2028-01-30")
        self.assertEqual(server.compute_next_due(task), "2028-02-29")

    def test_one_time_task_disappears_once_done(self):
        task = make_task({"type": "once", "date": "2026-03-04"})
        self.assertEqual(server.compute_next_due(task), "2026-03-04")
        task["last_completed"] = "2026-03-04"
        self.assertIsNone(server.compute_next_due(task))

    def test_snooze_overrides_an_earlier_due_date(self):
        task = make_task({"type": "days", "every": 1}, last_completed="2026-03-10")
        task["snoozed_to"] = "2026-03-15"
        self.assertEqual(server.compute_next_due(task), "2026-03-15")

    def test_snooze_in_the_past_is_ignored(self):
        task = make_task({"type": "days", "every": 30}, last_completed="2026-03-10")
        task["snoozed_to"] = "2026-03-11"
        self.assertEqual(server.compute_next_due(task), "2026-04-09")

    def test_bad_input_is_normalised_rather_than_crashing(self):
        rec = server.clean_recurrence({"type": "weekly", "days": ["2", 9, None, 2]})
        self.assertEqual(rec, {"type": "weekly", "days": [2]})
        self.assertEqual(server.clean_recurrence({"type": "days", "every": -5}),
                         {"type": "days", "every": 1})
        self.assertEqual(server.clean_recurrence({"type": "monthly", "day": 99})["day"], 31)
        self.assertEqual(server.clean_recurrence("nonsense"), {"type": "days", "every": 1})

    def test_schedule_descriptions(self):
        self.assertEqual(server.describe({"type": "days", "every": 1}), "Every day")
        self.assertEqual(server.describe({"type": "days", "every": 7}), "Every week")
        self.assertEqual(server.describe({"type": "weekly", "days": [1, 4]}), "Weekly: Mon, Thu")
        self.assertEqual(server.describe({"type": "monthly", "day": 3}), "Monthly on day 3")


class StoreTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.Store(os.path.join(self.dir.name, "chores.json"))

    def tearDown(self):
        self.dir.cleanup()

    def add(self, **kwargs):
        payload = {"name": "Mop the floor", "area": "Kitchen", "points": 10,
                   "recurrence": {"type": "days", "every": 2}}
        payload.update(kwargs)
        return self.store.upsert(payload)

    def test_seeded_with_starter_tasks(self):
        self.assertTrue(len(self.store.state["tasks"]) > 5)

    def test_a_task_needs_a_name(self):
        with self.assertRaises(ValueError):
            self.add(name="   ")

    def test_complete_awards_xp_and_logs_it(self):
        task = self.add()
        before = self.store.state["xp"]
        self.store.complete(task["id"])
        self.assertEqual(self.store.state["xp"], before + 10)
        self.assertEqual(self.store.state["log"][0]["task_id"], task["id"])
        self.assertEqual(self.store.find(task["id"])["last_completed"],
                         date.today().isoformat())

    def test_undo_restores_xp_due_date_and_streak(self):
        task = self.add()
        before_xp = self.store.state["xp"]
        before_due = server.compute_next_due(task)

        self.store.complete(task["id"])
        self.store.undo(task["id"])

        restored = self.store.find(task["id"])
        self.assertEqual(self.store.state["xp"], before_xp)
        self.assertIsNone(restored["last_completed"])
        self.assertEqual(restored["streak"], 0)
        self.assertEqual(server.compute_next_due(restored), before_due)
        self.assertEqual([e for e in self.store.state["log"] if e["task_id"] == task["id"]], [])

    def test_undo_after_a_broken_streak_restores_the_old_streak(self):
        task = self.add(recurrence={"type": "days", "every": 1})
        today = date.today()
        self.store.complete(task["id"], (today - timedelta(days=30)).isoformat())
        self.store.complete(task["id"], (today - timedelta(days=29)).isoformat())
        self.assertEqual(self.store.find(task["id"])["streak"], 2)

        # a very late completion resets the streak to 1
        self.store.complete(task["id"], today.isoformat())
        self.assertEqual(self.store.find(task["id"])["streak"], 1)

        # undoing it must give the streak of 2 back, not zero
        self.store.undo(task["id"])
        self.assertEqual(self.store.find(task["id"])["streak"], 2)

    def test_undo_with_nothing_to_undo(self):
        task = self.add()
        with self.assertRaises(ValueError):
            self.store.undo(task["id"])

    def test_streak_grows_when_done_on_time_and_resets_when_late(self):
        task = self.add(recurrence={"type": "days", "every": 1})
        today = date.today()

        self.store.complete(task["id"], (today - timedelta(days=2)).isoformat())
        self.assertEqual(self.store.find(task["id"])["streak"], 1)

        # due the next day, done the next day: the streak holds
        self.store.complete(task["id"], (today - timedelta(days=1)).isoformat())
        self.assertEqual(self.store.find(task["id"])["streak"], 2)

        # then a long gap: back to one
        self.store.complete(task["id"], (today + timedelta(days=9)).isoformat())
        self.assertEqual(self.store.find(task["id"])["streak"], 1)

    def test_completing_clears_a_snooze(self):
        task = self.add()
        self.store.snooze(task["id"], 5)
        self.assertIsNotNone(self.store.find(task["id"])["snoozed_to"])
        self.store.complete(task["id"])
        self.assertIsNone(self.store.find(task["id"])["snoozed_to"])

    def test_snooze_pushes_the_due_date_out(self):
        task = self.add(recurrence={"type": "days", "every": 1})
        due = server.compute_next_due(self.store.find(task["id"]))
        self.store.snooze(task["id"], 2)
        pushed = server.compute_next_due(self.store.find(task["id"]))
        self.assertTrue(pushed > due)

    def test_update_keeps_history_and_identity(self):
        task = self.add()
        self.store.complete(task["id"])
        updated = self.store.upsert({"name": "Mop it well", "area": "Hall", "points": 25,
                                     "recurrence": {"type": "weekly", "days": [0]}},
                                    task["id"])
        self.assertEqual(updated["id"], task["id"])
        self.assertEqual(updated["completions"], 1)
        self.assertEqual(updated["points"], 25)

    def test_delete_removes_the_task(self):
        task = self.add()
        self.store.delete(task["id"])
        self.assertIsNone(self.store.find(task["id"]))
        with self.assertRaises(KeyError):
            self.store.delete(task["id"])

    def test_state_survives_a_restart(self):
        task = self.add(name="Feed the chickens")
        self.store.complete(task["id"])
        reopened = server.Store(self.store.path)
        self.assertEqual(reopened.find(task["id"])["name"], "Feed the chickens")
        self.assertEqual(reopened.state["xp"], self.store.state["xp"])

    def test_snapshot_shape(self):
        snap = self.store.snapshot()
        for key in ("today", "tasks", "log", "xp", "areas"):
            self.assertIn(key, snap)
        self.assertIn("schedule_text", snap["tasks"][0])
        self.assertIn("next_due", snap["tasks"][0])


class ApiTests(unittest.TestCase):
    """Drive the real HTTP server the way the iPad will."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        server.Handler.store = server.Store(os.path.join(cls.dir.name, "chores.json"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.dir.cleanup()

    def call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode())

    def test_index_is_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as res:
            body = res.read().decode()
        self.assertEqual(res.status, 200)
        self.assertIn("BlockChores", body)

    def test_static_assets_are_served(self):
        for path, expected in (("/css/style.css", "text/css"),
                               ("/js/app.js", "javascript"),
                               ("/icons/icon-152.png", "image/png")):
            with urllib.request.urlopen(self.base + path, timeout=10) as res:
                self.assertEqual(res.status, 200, path)
                self.assertIn(expected, res.headers.get("Content-Type", ""), path)

    def test_directory_traversal_is_refused(self):
        status, _ = self.call("GET", "/../server.py")
        self.assertEqual(status, 404)

    def test_full_task_lifecycle(self):
        status, created = self.call("POST", "/api/tasks", {
            "name": "Polish the diamonds", "area": "Vault", "points": 40,
            "recurrence": {"type": "days", "every": 5}})
        self.assertEqual(status, 200)
        task_id = created["task"]["id"]
        self.assertIn("Vault", created["state"]["areas"])

        status, done = self.call("POST", "/api/tasks/%s/complete" % task_id, {})
        self.assertEqual(status, 200)
        self.assertEqual(done["task"]["completions"], 1)

        status, undone = self.call("POST", "/api/tasks/%s/undo" % task_id, {})
        self.assertEqual(status, 200)
        self.assertEqual(undone["task"]["completions"], 0)

        status, _ = self.call("POST", "/api/tasks/%s/snooze" % task_id, {"days": 3})
        self.assertEqual(status, 200)

        status, edited = self.call("POST", "/api/tasks/" + task_id, {
            "name": "Polish the emeralds", "area": "Vault", "points": 40,
            "recurrence": {"type": "monthly", "day": 2}})
        self.assertEqual(edited["task"]["name"], "Polish the emeralds")

        status, _ = self.call("POST", "/api/tasks/%s/delete" % task_id, {})
        self.assertEqual(status, 200)
        status, after = self.call("GET", "/api/state")
        self.assertNotIn(task_id, [t["id"] for t in after["state"]["tasks"]])

    def test_delete_verb_also_works(self):
        _, created = self.call("POST", "/api/tasks", {"name": "Temp", "area": "X",
                                                      "recurrence": {"type": "days", "every": 1}})
        status, _ = self.call("DELETE", "/api/tasks/" + created["task"]["id"])
        self.assertEqual(status, 200)

    def test_errors_come_back_as_json(self):
        status, body = self.call("POST", "/api/tasks", {"name": ""})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

        status, body = self.call("POST", "/api/tasks/nope_1/complete", {})
        self.assertEqual(status, 404)

        status, body = self.call("POST", "/api/wat", {})
        self.assertEqual(status, 404)

    def test_malformed_json_is_rejected_cleanly(self):
        req = urllib.request.Request(self.base + "/api/tasks", data=b"{not json",
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected a 400")
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, 400)


if __name__ == "__main__":
    unittest.main()
