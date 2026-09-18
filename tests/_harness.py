"""Shared plumbing for the user-journey tests in this folder — not a test
file itself (leading underscore, same convention as pages/_shared.py).

Each journey runs the *real* app, as a real web server, against a
throwaway SQLite database, and drives it the way a person would: look at a
page, fill in a form, submit it, see where it lands. No mocks, and no pip
installs — this app is meant to run with nothing beyond python3 and its own
vendored bottle/peewee, and these tests hold to the same rule (unittest,
urllib, wsgiref — all standard library).

A journey file is meant to run in *its own process* (`python3
tests/test_x.py`, or see run_all.py, which does exactly that for every file
in this folder) rather than be imported alongside the others: importing
app.py binds this process's database connection and starts a background
job thread, and that only makes sense to do once per process.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from wsgiref.simple_server import WSGIRequestHandler, make_server

PRO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _QuietHandler(WSGIRequestHandler):
    """The same server, minus a log line printed for every request a
    journey makes — a passing test run should be quiet."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass


class Journey(unittest.TestCase):
    """Base class for a user-journey test: boots the real app once for the
    whole file, against a fresh, empty database, then tears both down.

    Deliberately one boot per *file*, not per test method: this is a
    single-tenant app with exactly one team and one registration ever
    allowed (see pages/__init__.py's bootstrap hook), and swapping the
    database out from under a live process mid-run is asking for trouble
    (jobs.py's background thread is still holding the old connection).
    The consequence for how you write a journey: put the whole story — sign
    up, then whatever comes after — in *one* test method. If a file needs
    a second, independent scenario, give it its own file instead of a
    second test method here.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._db_fd, cls._db_path = tempfile.mkstemp(prefix="pro_journey_", suffix=".db")
        os.environ["SQLITE_PATH"] = cls._db_path

        sys.path.insert(0, PRO_DIR)
        sys.path.insert(0, os.path.join(PRO_DIR, "vendor"))

        # Migrate *before* `import app` below, not after: importing app.py
        # starts jobs.py's background thread as a side effect, unconditionally
        # (see app.py) — it reaches for the database the moment it's running,
        # and would race this database's own migration for the very tables
        # a pending one hasn't created yet. A journey plays both parts of a
        # real deploy: migrate this fresh database as its own explicit step,
        # the same one `make db-migrate` is, then serve.
        from models import run_migrations

        run_migrations()

        import app as appmod  # the real app, imported fresh in this process

        cls._server = make_server("127.0.0.1", 0, appmod.app, handler_class=_QuietHandler)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls.base_url = f"http://127.0.0.1:{cls._server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()
        os.close(cls._db_fd)
        # WAL mode (see models.py: make_database()) leaves a couple of
        # companion files alongside the main one; all three are this
        # journey's alone to clean up.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(cls._db_path + suffix)
            except FileNotFoundError:
                pass

    def setUp(self) -> None:
        self.browser = Browser(self.base_url)


class Browser:
    """A stand-in for a person at a keyboard: it remembers cookies (so a
    session survives across requests, the way it would in a real browser)
    and the page it last looked at (so it can find that page's own form
    token without a test having to know CSRF exists)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.page = ""
        self.status: int | None = None
        self.url = ""

    def visit(self, path: str) -> str:
        """Look at a page — the way clicking a link or typing an address
        into the bar does. `path` can be a bare path ("/clients") or a full
        URL, e.g. one a previous visit()/submit() landed on and handed back
        via .url — a test shouldn't have to know or care which."""
        return self._go(urllib.request.Request(self._url(path)))

    def submit(self, path: str, **fields) -> str:
        """Fill in and submit whatever form lives at `path` — the way
        clicking "Save" does, including the invisible token every form on
        the page last visited carries. Follows the redirect the app sends
        back, landing wherever a real submit would."""
        fields.setdefault("_csrf_token", self._token())
        body = urllib.parse.urlencode(fields).encode()
        return self._go(urllib.request.Request(self._url(path), data=body, method="POST"))

    def _url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else self.base_url + path

    def sees(self, text: str) -> bool:
        """Whether `text` appears anywhere on the page currently on screen."""
        return text in self.page

    def _go(self, request: urllib.request.Request) -> str:
        try:
            with self._opener.open(request) as response:
                self.page = response.read().decode("utf-8", "replace")
                self.status = response.status
                self.url = response.url
        except urllib.error.HTTPError as e:
            self.page = e.read().decode("utf-8", "replace")
            self.status = e.code
            self.url = e.url
        return self.page

    def _token(self) -> str:
        match = re.search(r'name="_csrf_token" value="([^"]*)"', self.page)
        return match.group(1) if match else ""
