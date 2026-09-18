"""Shared plumbing for the user-journey tests in this folder — not a test
file itself (leading underscore, same convention as pages/_shared.py).

Each journey runs the *real* app, as a real ASGI server (uvicorn, on a
background thread's own event loop — see Journey.setUpClass), against a
throwaway Postgres database (created and dropped around the test, see
_create_test_database/_drop_test_database below), and drives it the way a
person would: look at a page, fill in a form, submit it, see where it
lands. Browser (below) talks to that server over real HTTP via stdlib
urllib — server-implementation-agnostic, so it didn't need to change at
all when the app itself moved from a WSGI dev server to uvicorn. asyncpg
(not psycopg2 — see requirements.txt) creates/drops the throwaway database
directly, ahead of anything peewee does.

Requires a reachable Postgres server (PGHOST/PGPORT/PGUSER/PGPASSWORD, same
defaults as models.py) with permission to CREATE DATABASE / DROP DATABASE —
the same server the app itself would run against, not a separate test-only
instance.

A journey file is meant to run in *its own process* (`python3
tests/test_x.py`, or see run_all.py, which does exactly that for every file
in this folder) rather than be imported alongside the others: importing
app.py binds this process's database connection and starts a background
job thread, and that only makes sense to do once per process.
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import os
import re
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

import asyncpg
import uvicorn

PRO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv() -> None:
    """Same as app.py's/migrate.py's own _load_dotenv() — kept as a small,
    separate copy here for the same reason migrate.py gives for its own.
    Needed here (unlike before Postgres) because _create_test_database()
    below runs ahead of `import app`, the thing that would otherwise load
    .env for us — PGUSER/PGPASSWORD have to be in os.environ before it
    connects."""
    path = os.path.join(PRO_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()


async def _pg_admin_connect():
    """A connection to the "postgres" maintenance database — CREATE
    DATABASE / DROP DATABASE can't run against the database being
    created/dropped itself."""
    return await asyncpg.connect(
        database="postgres",
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        user=os.environ.get("PGUSER") or None,
        password=os.environ.get("PGPASSWORD") or None,
    )


async def _create_test_database(name: str) -> None:
    conn = await _pg_admin_connect()
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_test_database(name: str) -> None:
    conn = await _pg_admin_connect()
    try:
        # DROP DATABASE fails while any session is attached — the app's
        # own connections close per-request (see app.py's
        # before_request/after_request hooks) and jobs.py's poller closes
        # between iterations, but this journey's background job thread
        # (daemon, outlives tearDownClass) could still be mid-poll and
        # holding one open. Force the issue rather than let a slow teardown
        # fail run_all.py.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


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
        cls._db_name = f"scalar_journey_{uuid.uuid4().hex[:16]}"

        # A dedicated event loop on its own thread, kept alive for this
        # whole test file — every request the ASGI server below handles
        # runs as a task on *this* loop, matching production's shape (one
        # loop, one task per request), which is what playhouse.pwasyncio's
        # per-task connection state (see models.py) requires.
        cls._loop = asyncio.new_event_loop()
        cls._loop_thread = threading.Thread(target=cls._loop.run_forever, name="journey-loop", daemon=True)
        cls._loop_thread.start()

        def run(coro):
            return asyncio.run_coroutine_threadsafe(coro, cls._loop).result()

        run(_create_test_database(cls._db_name))
        os.environ["PGDATABASE"] = cls._db_name

        sys.path.insert(0, PRO_DIR)

        # Migrate *before* `import app` below, not after: importing app.py
        # starts jobs.py's background thread as a side effect, unconditionally
        # (see app.py) — it reaches for the database the moment it's running,
        # and would race this database's own migration for the very tables
        # a pending one hasn't created yet. A journey plays both parts of a
        # real deploy: migrate this fresh database as its own explicit step,
        # the same one `make db-migrate` is, then serve.
        from models import run_migrations

        run(run_migrations())

        import app as appmod  # the real app, imported fresh in this process

        config = uvicorn.Config(appmod.app, host="127.0.0.1", port=0, log_config=None, access_log=False)
        cls._server = uvicorn.Server(config)
        cls._serve_future = asyncio.run_coroutine_threadsafe(cls._server.serve(), cls._loop)
        for _ in range(500):  # up to 5s
            if cls._server.started:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("uvicorn didn't start within 5s")
        port = cls._server.servers[0].sockets[0].getsockname()[1]
        cls.base_url = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.should_exit = True
        cls._serve_future.result(timeout=5)
        asyncio.run_coroutine_threadsafe(_drop_test_database(cls._db_name), cls._loop).result()
        cls._loop.call_soon_threadsafe(cls._loop.stop)
        cls._loop_thread.join(timeout=5)

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
