"""Starlette app factory/bootstrap.

Single-tenant template app — no workspace/multi-tenancy concept anywhere.
Real pip dependencies now (starlette, uvicorn, jinja2, peewee, asyncpg,
greenlet — see requirements.txt and AGENTS.md); the session, CSRF, password
hashing, mailer, and Ask-AI HTTP calls are all still hand-rolled or stdlib
(see utils.py / ai.py). asgi.py is the Bottle-shaped compatibility layer
over Starlette that the rest of this app (this file included) is written
against — see its own docstring for why it exists and how it's put
together.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler

# Every module in pages/ does `from app import app` so every route can be
# declared as `@app.route(...)` without a blueprint indirection. If this
# file is ever launched directly (`python3 app.py`), Python runs it as
# `__main__` — and that `from app import app` would otherwise import a
# *second*, separate copy of this module under the name "app", with its
# own fresh App() instance that never sees any of pages/'s routes (the one
# actually passed to uvicorn below would then only have the routes
# registered above this point). Aliasing "app" to the already-running
# module up front makes the later self-import a no-op lookup instead of a
# second execution.
sys.modules.setdefault("app", sys.modules[__name__])

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from asgi import App, Redirect, request, response
from models import db, init_database, make_database, run_migrations, status_label
from utils import (
    csrf_token,
    current_user,
    get_flashed_messages,
    notification_summary,
    open_session,
    save_session,
    url_for,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Read .env into os.environ. Dependency-free; existing shell vars win."""
    path = os.path.join(BASE_DIR, ".env")
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

LOG_DIR = os.path.join(BASE_DIR, "logs")


def _configure_logging() -> None:
    """Root logger -> logs/app.log, rotating so a long-lived self-hosted
    instance can't grow that file forever: once it hits ~1MB it's renamed to
    app.log.1 (bumping any existing .1/.2 up a slot), keeping at most 3 old
    files alongside the active one. Configured on the root logger, not
    per-module, so every `logging.getLogger(__name__)` call site — jobs.py's
    today, app.py's own below, anything added later — lands here without its
    own setup. A StreamHandler stays alongside it so `python3 app.py`/
    `make run` still show log output in the terminal during dev."""
    os.makedirs(LOG_DIR, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if os.environ.get("DEBUG", "1") == "1" else logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # peewee logs every SQL statement at DEBUG, which would otherwise drown
    # out everything else once DEBUG=1 bumps the root logger down to that
    # level — request logging (see _log_request below) is the signal
    # actually wanted in app.log, not the query stream behind it.
    logging.getLogger("peewee").setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)

# Binds the db proxy to a concrete connection object — cheap (just building
# the AsyncPostgresqlDatabase instance, no actual connection yet) and
# distinct from actually *applying* migrations (run_migrations(), right
# below). Needed here, unconditionally, before jobs.start() further down:
# its background loop reaches for `db` the moment it's running, uvicorn
# worker or dev server alike, and a bound proxy is exactly what that needs
# even on a schema-less brand new database — the alternative is jobs.py
# crashing on an uninitialized proxy on every process boot that isn't
# `python3 app.py`.
init_database()

if __name__ == "__main__":
    # Only the direct-run dev path auto-migrates, and it does so this
    # early — before jobs.start() below, not down by uvicorn.run() at the
    # bottom of this file — so that background loop never polls a table a
    # pending migration hasn't created yet. `uvicorn app:app` (see Makefile)
    # imports this module without __name__ ever equaling "__main__", so a
    # production/self-hosted deploy applies migrations as its own explicit
    # step first — `make db-migrate` — same split as admin/. Auto-migrating
    # on every uvicorn worker's own import would mean concurrent workers
    # racing to apply the same pending migration; a single, singular step
    # ahead of starting any of them avoids that outright.
    #
    # asyncio.run() here (not a bare coroutine) because there's no event
    # loop yet at import time — this runs its own, start to finish, before
    # uvicorn.run() starts the one that actually serves requests. That
    # loop is temporary and gets torn down the moment this call returns —
    # but db.obj's asyncpg connection pool (see models.py) gets lazily
    # created on whatever loop first uses it, i.e. *this* one, and using a
    # pool from a different loop than the one that created it raises. Drop
    # it and rebuild a fresh (still unconnected, so still cheap) instance
    # so the pool that actually gets used gets created fresh, on whichever
    # loop uvicorn.run() below ends up running.
    asyncio.run(run_migrations())
    db.initialize(make_database())

import jobs  # noqa: E402

DEBUG = os.environ.get("DEBUG", "1") == "1"

app = App()

# Registered here, not just under `if __name__ == '__main__'`, so the
# reminder-firing job (see jobs.py) also runs under `uvicorn app:app`. Runs
# on ASGI startup (once the event loop is actually running), not at import
# time — jobs.start() schedules a task on *this* loop, the same one that
# will serve every request, which is what playhouse.pwasyncio's connection
# pool (see models.py) needs. jobs.start() is idempotent, so this is safe
# however many times/entrypoints import this module.
app.on_startup(jobs.start)
app.on_shutdown(jobs.stop)

# Jinja2 templates, autoescape on for .html (matches Bottle's own
# default-escaped {{ }} — an explicit `| safe` opts a value out, same shape
# as Bottle's {{! }}). auto_reload=DEBUG re-reads a template file from disk
# when it changes instead of using Jinja2's compiled-template cache, the
# same role bottle.DEBUG played for its own template() cache.
_jinja_env = Environment(
    loader=FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    autoescape=select_autoescape(["html"]),
    auto_reload=DEBUG,
)


def asset_version(filepath: str) -> int:
    """A static asset's mtime, used as a `?v=` cache-busting query string in
    layout.html. Bumps itself automatically whenever the file changes — no
    version number to remember to increment — so a browser that already
    cached an old style.css (no Cache-Control is set on /static/, so this is
    otherwise left to each browser's own heuristics) fetches the new one
    instead of silently reusing a stale copy after a deploy/restart."""
    try:
        return int(os.path.getmtime(os.path.join(BASE_DIR, "static", filepath)))
    except OSError:
        return 0


def js_string(value: str) -> str:
    """json.dumps, with the characters that could break out of a <script>
    block (a message containing literal "</script>", say — some flashed
    messages interpolate user-supplied data like a filename or task title)
    escaped as \\uXXXX. Safe to inline unquoted inside a <script> tag."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# DB-free — safe to call live from inside a template, unlike current_user()
# (see render() below, which resolves that one eagerly instead).
_TEMPLATE_DEFAULTS = {
    "url_for": url_for,
    "csrf_token": csrf_token,
    "get_flashed_messages": get_flashed_messages,
    "asset_version": asset_version,
    "status_label": status_label,
    "notification_summary": notification_summary,
    # Exposed so layout.html can highlight the current section in the
    # sidebar nav (`request.path.startswith(...)`) without every route
    # having to pass its own "active nav" flag through render().
    "request": request,
    # For safely embedding a flashed message as a JS string literal in
    # _toasts.html's bootstrap <script>.
    "js_string": js_string,
}


async def render(name: str, **kwargs) -> str:
    """current_user() touches the database (see utils.py) and is awaited
    here, once, up front — Jinja2 templates can't `await` mid-render, so
    layout.html (the only template that needs it) gets it as a plain value
    in the context instead of calling it live the way every other
    DB-free template global above is."""
    ctx = dict(_TEMPLATE_DEFAULTS)
    ctx["current_user"] = await current_user()
    ctx.update(kwargs)
    return _jinja_env.get_template(name).render(**ctx)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@app.hook("before_request")
async def _start_request_timer() -> None:
    # Stashed on request.environ (request-local by construction — see
    # asgi.py's _Ctx, one per request/task) rather than a module global, so
    # concurrent requests can't clobber each other's start time.
    request.environ["scalar.start_time"] = time.monotonic()


@app.hook("before_request")
async def _open_db() -> None:
    await db.aconnect()
    # Every write a POST/PUT/PATCH/DELETE makes should land together: if a
    # handler creates several rows and a later one fails, the earlier ones
    # shouldn't survive as an orphaned partial write. GETs don't get one —
    # they're read-only, and holding a transaction open for a whole page
    # render buys nothing. The transaction object itself is stashed on
    # request.environ so _close_db (an after_request hook, run separately —
    # see this module's own async_atomic usage) can commit/roll it back.
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # db.atomic() (not db.obj.atomic()) would go through DatabaseProxy's
        # own hardcoded atomic()/transaction()/savepoint()/manual_commit()
        # methods, which always build plain peewee._atomic — unlike every
        # other method here, these four are NOT proxied through to
        # db.obj's own (async-aware, for AsyncPostgresqlDatabase) versions
        # via Proxy.__getattr__, since DatabaseProxy defines its own.
        txn = db.obj.atomic()
        await txn.__aenter__()
        request.environ["_txn"] = txn


@app.hook("before_request")
async def _open_session_hook() -> None:
    open_session()


@app.hook("after_request")
async def _save_session_hook() -> None:
    save_session()


def _propagating_error() -> BaseException | None:
    """The exception currently unwinding through this after_request hook,
    *unless* it's a controlled jump (redirect()/abort() — see utils.py)
    rather than a genuine failure. Unlike Bottle, where HTTPResponse is
    caught by the framework's own routing before after-hooks ever see it
    (clearing sys.exc_info() by the time they run), asgi.py's App.route
    runs after_request hooks from a bare `finally` — sys.exc_info() still
    reports a Redirect/HTTPException here exactly as it would a real bug,
    so this excludes them explicitly instead of relying on it alone."""
    exc = sys.exc_info()[1]
    if isinstance(exc, (Redirect, HTTPException)):
        return None
    return exc


@app.hook("after_request")
async def _close_db() -> None:
    """Resolve this request's transaction (see _open_db above), then close
    the connection.

    after_request runs unconditionally — after a normal response, after an
    abort()/redirect() (both just raise, a controlled jump — see
    _propagating_error above), and after a genuine unhandled exception
    alike. Only that last case should roll back rather than commit: every
    route in this app that writes something then redirects (which is most
    of them) would otherwise have that write silently rolled back by its
    own redirect()."""
    txn = request.environ.get("_txn")
    if txn is not None:
        error = _propagating_error()
        exc_info = (type(error), error, error.__traceback__) if error is not None else (None, None, None)
        await txn.__aexit__(*exc_info)
    if not db.is_closed():
        await db.aclose()


@app.hook("after_request")
async def _log_request() -> None:
    """Access log: one line per request, e.g. `GET /clients -> 200 (4.2ms)`.
    Runs from the same finally as _close_db above (see its comment), so for
    a genuine unhandled exception response.status_code is still whatever it
    was before the request started — the actual error status only gets
    applied to the real outgoing response afterwards, by asgi.py's error
    handlers. The same _propagating_error() check _close_db relies on tells
    us that's what's coming, so use 500 instead of trusting response.status
    blindly (redirect()/abort() report their own real status normally,
    same as any other response)."""
    start = request.environ.get("scalar.start_time")
    elapsed_ms = (time.monotonic() - start) * 1000 if start is not None else 0.0
    status = 500 if _propagating_error() is not None else response.status
    logger.info("%s %s -> %s (%.1fms)", request.method, request.path, status, elapsed_ms)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.route("/health", name="health")
async def _health():
    """Liveness/readiness probe for whatever's watching this process (a
    process manager, a load balancer, admin's launcher — see
    admin/launcher/provisioner.py's own _health_check, which currently just
    polls `/`). 200 only if the app can actually reach its database, not
    merely that the process is listening — an unreachable Postgres server
    or an exhausted connection pool would still answer `/` (it's mostly
    static HTML) while every real page silently 500s underneath it.

    Public (see PUBLIC_ROUTES in utils.py) and exempt from the
    pre-registration bootstrap redirect (see pages/__init__.py): a freshly
    provisioned, team-less instance should still report whether its
    database is reachable.

    `_open_db` (an earlier before_request hook) has already connected by
    the time this runs; SELECT 1 doesn't assume any table exists, so it
    still catches a connection failure even on a schema that somehow never
    finished migrating."""
    try:
        await db.aexecute_sql("SELECT 1")
    except Exception as e:
        response.status = 503
        return {"status": "error", "detail": str(e)}
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@app.error(400)
async def _bad_request(error: HTTPException):
    # abort(400, "...") call sites (CSRF check, invalid status, etc.) pass a
    # specific, user-actionable message as the body — show that instead of
    # a generic one whenever it's there.
    return await render("error.html", code=400, heading="Something's not right with that request",
                         message=error.detail or "The request couldn't be processed. Please try again.")


@app.error(404)
async def _not_found(_error: HTTPException):
    return await render("error.html", code=404, heading="Not found",
                         message="That page doesn't exist, or was moved.")


@app.error(403)
async def _forbidden(_error: HTTPException):
    return await render("error.html", code=403, heading="You don't have access",
                         message="You're signed in, but you don't have permission to view this.")


@app.error(500)
async def _server_error(error: Exception):
    # Starlette's ServerErrorMiddleware hands us the raw exception here
    # (not an HTTPException wrapper the way Bottle's HTTPError was) —
    # format the traceback ourselves for the log line.
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    logger.error("Unhandled exception on %s %s\n%s", request.method, request.path, tb)
    # A 500 can mean the request died mid-transaction — a defensive second
    # cleanup attempt, in case _close_db (an after_request hook, which
    # already ran once by the time this handler fires) didn't fully
    # complete itself (e.g. it's what raised). db.in_transaction() and
    # is_closed() are both plain local-state checks, safe to call without
    # the greenlet bridge queries otherwise need. Guarded so a broken
    # render() below can't cascade into a second crash.
    try:
        if db.in_transaction():
            txn = request.environ.get("_txn")
            if txn is not None:
                await txn.__aexit__(*sys.exc_info())
        if not db.is_closed():
            await db.aclose()
    except Exception:
        pass
    try:
        return await render("error.html", code=500, heading="Something went wrong",
                             message="An unexpected error occurred. Please try again.")
    except Exception:
        return (
            "<!doctype html><meta charset=utf-8><title>500</title>"
            "<h1>Something went wrong</h1><p>Please try again.</p>"
        )


# Route registration lives in the pages/ package, imported for its side
# effects only — every view there does `from app import app` and decorates
# directly (no blueprints; pages/ splits routes by feature area rather than
# introducing that indirection). Must be imported after `app`/`render`/hooks
# exist above.
import pages  # noqa: E402,F401


if __name__ == "__main__":
    import uvicorn

    # Migrations are already applied by now — see the earlier
    # `if __name__ == "__main__": asyncio.run(run_migrations())` right
    # after init_database(), well before jobs.start(). This is the same
    # `__main__` condition evaluated a second time, not a second migration
    # step; the block's just placed where uvicorn.run() naturally belongs.
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        reload=False,  # `make run` (uvicorn's own --reload, see Makefile) is the autoreload path, not this
        log_config=None,  # this app configures logging itself (see _configure_logging above)
    )
