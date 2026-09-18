"""Bottle app factory/bootstrap.

Single-tenant template app — no workspace/multi-tenancy concept anywhere.
Zero pip dependencies beyond peewee + bottle (see requirements.txt); the
session, CSRF, password hashing, mailer, and Ask-AI HTTP calls are all
hand-rolled or stdlib (see utils.py / ai.py).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# Every module in pages/ does `from app import app` so every route can be
# declared as `@app.route(...)` without a blueprint indirection. If this
# file is ever launched directly (`python3 app.py`), Python runs it as
# `__main__` — and that `from app import app` would otherwise import a
# *second*, separate copy of this module under the name "app", with its
# own fresh Bottle() instance that never sees any of pages/'s routes (the
# one actually passed to run() below would then only have the routes
# registered above this point). Aliasing "app" to the already-running
# module up front makes the later self-import a no-op lookup instead of a
# second execution.
sys.modules.setdefault("app", sys.modules[__name__])

# peewee and bottle are vendored in vendor/ as plain .py files, not
# pip-installed — this app runs with `python3 app.py` and nothing else, no
# venv/pip step required. Both are MIT-licensed; see vendor/LICENSE.peewee
# and vendor/LICENSE.bottle. peewee-migrate and the slice of playhouse it
# needs (for migrations/ — see models.py: run_migrations()) are vendored
# the same way; see vendor/LICENSE.peewee-migrate.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from bottle import Bottle, HTTPError, debug as _bottle_debug, request, response, run, static_file, template

from models import db, init_database, run_migrations, status_label
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

# Binds the db proxy to a concrete connection — cheap, and distinct from
# actually *applying* migrations (run_migrations(), right below). Needed
# here, unconditionally, before jobs.start() further down: its background
# thread reaches for `db` the moment it's running, gunicorn worker or dev
# server alike, and a bound connection is exactly what that needs even on
# a schema-less brand new database — the alternative is jobs.py crashing on
# an uninitialized proxy on every process boot that isn't `python3 app.py`.
init_database()

if __name__ == "__main__":
    # Only the direct-run dev path auto-migrates, and it does so this
    # early — before jobs.start() below, not down by run() at the bottom of
    # this file — so that background thread never polls a table a pending
    # migration hasn't created yet. `gunicorn app:app` (see Makefile)
    # imports this module without __name__ ever equaling "__main__", so a
    # production/self-hosted deploy applies migrations as its own explicit
    # step first — `make db-migrate` — same split as admin/. Auto-migrating
    # on every gunicorn worker's own import would mean concurrent workers
    # racing to apply the same pending migration; a single, singular step
    # ahead of starting any of them avoids that outright.
    run_migrations()

# Started here, not just under `if __name__ == '__main__'`, so the
# reminder-firing job (see jobs.py) also runs under `gunicorn app:app`.
# jobs.start() is idempotent and the thread is a daemon, so this is safe
# however many times/entrypoints import this module.
import jobs  # noqa: E402

jobs.start()

DEBUG = os.environ.get("DEBUG", "1") == "1"
# Set at module level (not just under `if __name__ == '__main__'`) so it
# also applies under `gunicorn app:app` — gunicorn's own --reload only
# re-execs the worker on *.py changes, but bottle's template() cache
# (app.py's render() -> bottle.template()) only skips its cache and
# re-reads a template file from disk when bottle.DEBUG is set, regardless of
# which process is serving requests. Without this, a template edit needs a
# full worker restart to show up even with --reload running.
_bottle_debug(DEBUG)

app = Bottle()

# bottle's SimpleTemplate looks in this list for template files (.html, per
# BaseTemplate's extensions list — no .tpl in this app). Also make our
# own url_for/csrf_token/current_user/flash helpers available to every
# template without each render() call having to pass them explicitly.
import bottle as _bottle_module  # noqa: E402

_bottle_module.TEMPLATE_PATH.insert(0, os.path.join(BASE_DIR, "templates"))


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


_TEMPLATE_DEFAULTS = {
    "url_for": url_for,
    "current_user": current_user,
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


def render(name: str, **kwargs) -> str:
    ctx = dict(_TEMPLATE_DEFAULTS)
    ctx.update(kwargs)
    return template(name, **ctx)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@app.hook("before_request")
def _start_request_timer() -> None:
    # Stashed on environ (request-local by construction — bottle builds a
    # fresh Request per WSGI call) rather than a module global, so concurrent
    # requests under WORKERS>1/threads can't clobber each other's start time.
    request.environ["scalar.start_time"] = time.monotonic()


@app.hook("before_request")
def _open_db() -> None:
    db.connect(reuse_if_open=True)
    # Every write a POST makes should land together: if a handler creates
    # several rows and a later one fails, the earlier ones shouldn't survive
    # as an orphaned partial write. GETs don't get one — they're read-only,
    # and holding a transaction open for a whole page render buys nothing.
    if request.method == "POST":
        db.session_start()


@app.hook("before_request")
def _open_session_hook() -> None:
    open_session()


@app.hook("after_request")
def _save_session_hook() -> None:
    save_session()


@app.hook("after_request")
def _close_db() -> None:
    """Resolve this request's transaction (see _open_db above), then close
    the connection.

    after_request runs unconditionally — after a normal response, after an
    abort()/redirect() (both just raise HTTPResponse, a controlled jump, not
    a failure), and after a genuine unhandled exception alike: Bottle's
    _handle() fires this hook from a `finally`, before the exception is
    turned into a 500 by the outer handler. Only that last case should roll
    back rather than commit, and it's the one Python guarantees
    sys.exc_info() still reports here — a caught-and-suppressed HTTPResponse
    has already cleared it by the time its own `finally` runs, verified
    against this exact try/except/finally shape."""
    if db.in_transaction():
        if sys.exc_info()[0] is not None:
            db.session_rollback()
        else:
            db.session_commit()
    if not db.is_closed():
        db.close()


@app.hook("after_request")
def _log_request() -> None:
    """Access log: one line per request, e.g. `GET /clients -> 200 (4.2ms)`.
    Runs from the same finally as _close_db above (see its comment), so for
    a genuine unhandled exception response.status_code is still whatever it
    was before the request started — bottle only applies the 500 status
    afterwards, in the outer handler that turns the exception into one (see
    _server_error, which logs that case's full traceback separately). The
    same sys.exc_info() check _close_db relies on tells us that's what's
    coming, so use 500 instead of trusting response.status_code blindly."""
    start = request.environ.get("scalar.start_time")
    elapsed_ms = (time.monotonic() - start) * 1000 if start is not None else 0.0
    status = 500 if sys.exc_info()[0] is not None else response.status_code
    logger.info("%s %s -> %s (%.1fms)", request.method, request.path, status, elapsed_ms)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.route("/health", name="health")
def _health():
    """Liveness/readiness probe for whatever's watching this process (a
    process manager, a load balancer, admin's launcher — see
    admin/launcher/provisioner.py's own _health_check, which currently just
    polls `/`). 200 only if the app can actually reach its database, not
    merely that the process is listening — a wedged/corrupted SQLite file
    or a lock that never clears would still answer `/` (it's mostly static
    HTML) while every real page silently 500s underneath it.

    Public (see PUBLIC_ROUTES in utils.py) and exempt from the
    pre-registration bootstrap redirect (see pages/__init__.py): a freshly
    provisioned, team-less instance should still report whether its
    database is reachable.

    `_open_db` (an earlier before_request hook) has already connected by
    the time this runs; SELECT 1 doesn't assume any table exists, so it
    still catches a connection failure even on a schema that somehow never
    finished migrating."""
    try:
        db.execute_sql("SELECT 1")
    except Exception as e:
        response.status = 503
        return {"status": "error", "detail": str(e)}
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


@app.route("/static/<filepath:path>", name="static")
def _static(filepath: str):
    return static_file(filepath, root=os.path.join(BASE_DIR, "static"))


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@app.error(400)
def _bad_request(error: HTTPError):
    # abort(400, "...") call sites (CSRF check, invalid status, etc.) pass a
    # specific, user-actionable message as the body — show that instead of
    # a generic one whenever it's there.
    return render("error.html", code=400, heading="Something's not right with that request",
                  message=error.body or "The request couldn't be processed. Please try again.")


@app.error(404)
def _not_found(_error: HTTPError):
    return render("error.html", code=404, heading="Not found",
                  message="That page doesn't exist, or was moved.")


@app.error(403)
def _forbidden(_error: HTTPError):
    return render("error.html", code=403, heading="You don't have access",
                  message="You're signed in, but you don't have permission to view this.")


@app.error(500)
def _server_error(_error: HTTPError):
    # By the time bottle's error_handler dispatch reaches this callback, the
    # exception has already unwound out of the except block that caught it
    # (see bottle.py: Bottle._handle) — sys.exc_info() is empty here, so
    # logger.exception() would log nothing useful. Bottle stashes the
    # original exception/traceback string on the HTTPError itself instead
    # (`_error.exception`/`_error.traceback`); use those. Only unhandled
    # exceptions carry them — an explicit `abort(500, "msg")` call site has
    # neither, hence the fallback.
    if _error.traceback:
        logger.error("Unhandled exception on %s %s\n%s", request.method, request.path, _error.traceback)
    else:
        logger.error("500 on %s %s: %s", request.method, request.path, _error.body)
    # A 500 can mean the request died mid-transaction; roll back before any
    # further query runs (the error page itself queries current_user/nav
    # data), and guard the render so a broken template can't cascade into a
    # second crash.
    try:
        if not db.is_closed():
            db.rollback()
    except Exception:
        pass
    try:
        return render("error.html", code=500, heading="Something went wrong",
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
    # Migrations are already applied by now — see the earlier
    # `if __name__ == "__main__": run_migrations()` right after
    # init_database(), well before jobs.start(). This is the same
    # `__main__` condition evaluated a second time, not a second migration
    # step; the block's just placed where run() naturally belongs.
    run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        debug=DEBUG,
        reloader=DEBUG,
    )
