#!/usr/bin/env python3
"""BlockChores - a household cleaning schedule server.

Stdlib only (Python 3.9+).  Serves the static front end and a small JSON API
backed by a single file on disk, so it runs on a plain Debian box with no
packages to install.

    python3 server.py --host 0.0.0.0 --port 8080 --data /var/lib/blockchores
"""

import argparse
import json
import mimetypes
import os
import posixpath
import random
import re
import string
import sys
import threading
import time
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

MAX_BODY = 256 * 1024
AREAS_FALLBACK = "Misc"

# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------


def parse_date(text):
    """Parse 'YYYY-MM-DD' into a date, or return None."""
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(text).strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def iso(d):
    return d.isoformat() if d else None


def sunday_weekday(d):
    """Weekday with Sunday = 0, to match JavaScript's Date.getDay()."""
    return (d.weekday() + 1) % 7


def days_in_month(year, month):
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def day_of_month(year, month, dom):
    """Clamp a day-of-month to a real day (the 31st in a 30 day month)."""
    return date(year, month, min(dom, days_in_month(year, month)))


def add_months(d, n):
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, days_in_month(year, month)))


# --------------------------------------------------------------------------
# recurrence
# --------------------------------------------------------------------------


def clean_recurrence(raw):
    """Normalise whatever the browser sent into a recurrence we trust."""
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("type", "days")).lower()

    if kind == "weekly":
        days = []
        for value in raw.get("days") or []:
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 6 and n not in days:
                days.append(n)
        return {"type": "weekly", "days": sorted(days) or [1]}

    if kind == "monthly":
        try:
            dom = int(raw.get("day", 1))
        except (TypeError, ValueError):
            dom = 1
        return {"type": "monthly", "day": min(31, max(1, dom))}

    if kind == "once":
        return {"type": "once", "date": iso(parse_date(raw.get("date"))) or iso(date.today())}

    try:
        every = int(raw.get("every", 1))
    except (TypeError, ValueError):
        every = 1
    return {"type": "days", "every": min(3650, max(1, every))}


def compute_next_due(task):
    """When a task is next due, honouring any snooze."""
    due = base_next_due(task)
    snoozed = parse_date(task.get("snoozed_to"))
    if due and snoozed and snoozed > parse_date(due):
        return iso(snoozed)
    return due


def base_next_due(task):
    """Work out when a task is next due from its recurrence alone.

    The anchor is the last completion (so a task resets from when you actually
    did it) or, for a task that has never been done, its start date.
    """
    rec = task.get("recurrence") or {}
    kind = rec.get("type", "days")
    last = parse_date(task.get("last_completed"))
    start = parse_date(task.get("start")) or parse_date(task.get("created")) or date.today()

    if kind == "once":
        if last:
            return None
        return iso(parse_date(rec.get("date")) or start)

    if kind == "days":
        every = max(1, int(rec.get("every", 1)))
        if not last:
            return iso(start)
        return iso(last + timedelta(days=every))

    # For the calendar based schedules, find the first matching day at or
    # after `floor`: the day after the last completion, else the start date.
    floor = last + timedelta(days=1) if last else start

    if kind == "weekly":
        wanted = rec.get("days") or [1]
        cursor = floor
        for _ in range(8):
            if sunday_weekday(cursor) in wanted:
                return iso(cursor)
            cursor += timedelta(days=1)
        return iso(floor)

    if kind == "monthly":
        dom = int(rec.get("day", 1))
        candidate = day_of_month(floor.year, floor.month, dom)
        if candidate < floor:
            nxt = add_months(date(floor.year, floor.month, 1), 1)
            candidate = day_of_month(nxt.year, nxt.month, dom)
        return iso(candidate)

    return iso(start)


def describe(rec):
    """Human readable schedule, e.g. 'Every 3 days'."""
    kind = rec.get("type", "days")
    if kind == "days":
        every = int(rec.get("every", 1))
        if every == 1:
            return "Every day"
        if every == 7:
            return "Every week"
        return "Every %d days" % every
    if kind == "weekly":
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        picked = [names[d] for d in rec.get("days") or []]
        return "Weekly: " + (", ".join(picked) if picked else "Mon")
    if kind == "monthly":
        return "Monthly on day %d" % int(rec.get("day", 1))
    if kind == "once":
        return "One time (%s)" % rec.get("date", "")
    return "Every day"


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


def new_id(prefix):
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "_" + "".join(random.choice(alphabet) for _ in range(9))


DEFAULT_TASKS = [
    ("Wash the dishes", "Kitchen", {"type": "days", "every": 1}),
    ("Wipe the counters", "Kitchen", {"type": "days", "every": 1}),
    ("Take out the trash", "Kitchen", {"type": "weekly", "days": [1, 4]}),
    ("Sweep the floors", "Whole House", {"type": "days", "every": 3}),
    ("Scrub the toilet", "Bathroom", {"type": "weekly", "days": [6]}),
    ("Clean the mirror", "Bathroom", {"type": "days", "every": 7}),
    ("Change the sheets", "Bedroom", {"type": "days", "every": 14}),
    ("Vacuum the carpet", "Living Room", {"type": "weekly", "days": [6]}),
    ("Dust the shelves", "Living Room", {"type": "days", "every": 10}),
    ("Mow the lawn", "Outside", {"type": "days", "every": 10}),
    ("Deep clean the fridge", "Kitchen", {"type": "monthly", "day": 1}),
]


def seed_state():
    today = iso(date.today())
    tasks = []
    for name, area, rec in DEFAULT_TASKS:
        task = {
            "id": new_id("task"),
            "name": name,
            "area": area,
            "assignee": "",
            "notes": "",
            "recurrence": clean_recurrence(rec),
            "created": today,
            "start": today,
            "last_completed": None,
            "streak": 0,
            "completions": 0,
            "archived": False,
        }
        task["next_due"] = compute_next_due(task)
        tasks.append(task)
    return {"version": 1, "tasks": tasks, "log": []}


def migrate(state):
    """Drop fields left behind by older versions of the app.

    Earlier builds scored chores with points and a running XP total.  That is
    gone, so the keys are stripped rather than left to rot in the file; the
    previous version is still on disk as chores.json.bak.
    """
    state.pop("xp", None)
    for task in state.get("tasks", []):
        task.pop("points", None)
    for entry in state.get("log", []):
        entry.pop("points", None)
    return state


class Store(object):
    """The whole app state in one JSON file, guarded by a lock."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.state = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            state = seed_state()
            self._write(state)
            return state
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (ValueError, OSError) as exc:
            sys.stderr.write("could not read %s (%s), starting fresh\n" % (self.path, exc))
            return seed_state()
        state.setdefault("tasks", [])
        state.setdefault("log", [])
        state.setdefault("version", 1)
        return migrate(state)

    def _write(self, state):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(self.path):
            try:
                os.replace(self.path, self.path + ".bak")
            except OSError:
                pass
        os.replace(tmp, self.path)

    def save(self):
        with self.lock:
            self._write(self.state)

    # -- queries ----------------------------------------------------------

    def find(self, task_id):
        for task in self.state["tasks"]:
            if task["id"] == task_id:
                return task
        return None

    def snapshot(self):
        with self.lock:
            tasks = []
            for task in self.state["tasks"]:
                view = dict(task)
                view["next_due"] = compute_next_due(task)
                view["schedule_text"] = describe(task.get("recurrence") or {})
                tasks.append(view)
            return {
                "today": iso(date.today()),
                "tasks": tasks,
                "log": self.state["log"][:120],
                "areas": sorted({(t.get("area") or AREAS_FALLBACK) for t in tasks}),
            }

    # -- mutations --------------------------------------------------------

    def upsert(self, payload, task_id=None):
        with self.lock:
            name = str(payload.get("name", "")).strip()[:120]
            if not name:
                raise ValueError("A task needs a name")

            fields = {
                "name": name,
                "area": str(payload.get("area", "")).strip()[:60] or AREAS_FALLBACK,
                "assignee": str(payload.get("assignee", "")).strip()[:60],
                "notes": str(payload.get("notes", "")).strip()[:400],
                "recurrence": clean_recurrence(payload.get("recurrence")),
            }

            task = self.find(task_id) if task_id else None
            if task is None:
                if task_id:
                    raise KeyError(task_id)
                today = iso(date.today())
                task = {
                    "id": new_id("task"),
                    "created": today,
                    "start": iso(parse_date(payload.get("start"))) or today,
                    "last_completed": None,
                    "streak": 0,
                    "completions": 0,
                    "archived": False,
                }
                self.state["tasks"].append(task)
            elif payload.get("start"):
                task["start"] = iso(parse_date(payload.get("start"))) or task.get("start")

            task.update(fields)
            task["archived"] = bool(payload.get("archived", task.get("archived", False)))
            task["next_due"] = compute_next_due(task)
            self._write(self.state)
            return task

    def delete(self, task_id):
        with self.lock:
            before = len(self.state["tasks"])
            self.state["tasks"] = [t for t in self.state["tasks"] if t["id"] != task_id]
            if len(self.state["tasks"]) == before:
                raise KeyError(task_id)
            self._write(self.state)

    def complete(self, task_id, when=None):
        with self.lock:
            task = self.find(task_id)
            if task is None:
                raise KeyError(task_id)

            done_on = parse_date(when) or date.today()
            previous = parse_date(task.get("last_completed"))
            prior_streak = int(task.get("streak", 0))
            due_before = parse_date(compute_next_due(task))

            # A streak survives if you did it on or before the day it was due.
            if previous is None or due_before is None or done_on <= due_before:
                task["streak"] = int(task.get("streak", 0)) + 1
            else:
                task["streak"] = 1

            task["last_completed"] = iso(done_on)
            task["snoozed_to"] = None
            task["completions"] = int(task.get("completions", 0)) + 1
            task["next_due"] = compute_next_due(task)

            self.state["log"].insert(0, {
                "id": new_id("log"),
                "task_id": task["id"],
                "name": task["name"],
                "area": task.get("area", AREAS_FALLBACK),
                "date": iso(done_on),
                "ts": int(time.time()),
                "previous": iso(previous),
                "previous_streak": prior_streak,
            })
            self.state["log"] = self.state["log"][:500]
            self._write(self.state)
            return task

    def undo(self, task_id):
        """Roll back the most recent completion of a task."""
        with self.lock:
            task = self.find(task_id)
            if task is None:
                raise KeyError(task_id)

            entry = None
            for i, item in enumerate(self.state["log"]):
                if item.get("task_id") == task_id:
                    entry = self.state["log"].pop(i)
                    break
            if entry is None:
                raise ValueError("Nothing to undo for this task")

            task["last_completed"] = entry.get("previous")
            task["completions"] = max(0, int(task.get("completions", 1)) - 1)
            task["streak"] = max(0, int(entry.get("previous_streak", 0)))
            task["next_due"] = compute_next_due(task)
            self._write(self.state)
            return task

    def snooze(self, task_id, days=1):
        """Push a task out by a few days without marking it done."""
        with self.lock:
            task = self.find(task_id)
            if task is None:
                raise KeyError(task_id)
            due = parse_date(compute_next_due(task))
            if due is None:
                raise ValueError("This task is already finished")
            base = max(due, date.today())
            task["snoozed_to"] = iso(base + timedelta(days=max(1, int(days))))
            task["next_due"] = compute_next_due(task)
            self._write(self.state)
            return task


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "BlockChores"
    protocol_version = "HTTP/1.1"
    store = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- plumbing ---------------------------------------------------------

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"ok": False, "error": message}, status)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("Request too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ValueError("Malformed JSON")

    # -- static -----------------------------------------------------------

    def resolve_static(self, path):
        """Map a URL path to a file inside static/, or None if it escapes."""
        path = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        if path in ("/", ""):
            path = "/index.html"
        clean = posixpath.normpath(path).lstrip("/")
        target = os.path.realpath(os.path.join(STATIC_DIR, clean))
        root = os.path.realpath(STATIC_DIR)
        if target != root and not target.startswith(root + os.sep):
            return None
        return target if os.path.isfile(target) else None

    def serve_static(self, path, with_body=True):
        target = self.resolve_static(path)
        if target is None:
            self.send_error_json(404, "Not found")
            return

        ctype, _ = mimetypes.guess_type(target)
        with open(target, "rb") as handle:
            body = handle.read()

        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # The old iPad caches aggressively; only let it keep the artwork.
        if target.endswith((".png", ".ico")):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/state":
            self.send_json({"ok": True, "state": self.store.snapshot()})
            return
        self.serve_static(self.path)

    def do_HEAD(self):
        self.serve_static(self.path, with_body=False)

    def do_POST(self):
        self.handle_write()

    def do_PUT(self):
        self.handle_write()

    def do_DELETE(self):
        self.handle_write()

    def handle_write(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.startswith("/api/"):
            self.send_error_json(404, "Not found")
            return

        try:
            payload = self.read_json()
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return

        try:
            self.route_api(path, payload)
        except KeyError:
            self.send_error_json(404, "That task no longer exists")
        except ValueError as exc:
            self.send_error_json(400, str(exc))
        except Exception as exc:  # keep the server up whatever the browser sends
            sys.stderr.write("unhandled error: %r\n" % (exc,))
            self.send_error_json(500, "Server error")

    def route_api(self, path, payload):
        parts = [p for p in path.split("/") if p][1:]  # drop 'api'

        if parts == ["tasks"]:
            task = self.store.upsert(payload)
            self.ok(task)
            return

        if len(parts) >= 2 and parts[0] == "tasks":
            task_id = parts[1]
            action = parts[2] if len(parts) > 2 else None

            if action is None:
                if self.command == "DELETE":
                    self.store.delete(task_id)
                    self.ok(None)
                else:
                    self.ok(self.store.upsert(payload, task_id))
                return
            if action == "delete":
                self.store.delete(task_id)
                self.ok(None)
                return
            if action == "complete":
                self.ok(self.store.complete(task_id, payload.get("date")))
                return
            if action == "undo":
                self.ok(self.store.undo(task_id))
                return
            if action == "snooze":
                self.ok(self.store.snooze(task_id, payload.get("days", 1)))
                return

        self.send_error_json(404, "Unknown endpoint")

    def ok(self, task):
        """Every write answers with the full state, so the UI stays in sync."""
        self.send_json({"ok": True, "task": task, "state": self.store.snapshot()})


def main():
    parser = argparse.ArgumentParser(description="BlockChores cleaning schedule server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    parser.add_argument("--data", default=os.environ.get("BLOCKCHORES_DATA",
                                                        os.path.join(BASE_DIR, "data")))
    args = parser.parse_args()

    data_path = args.data
    if os.path.isdir(data_path) or not data_path.endswith(".json"):
        data_path = os.path.join(data_path, "chores.json")

    Handler.store = Store(data_path)
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print("BlockChores serving http://%s:%d  (data: %s)" % (args.host, args.port, data_path))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
