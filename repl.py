"""Interactive shell preloaded with every model — `make repl` (or
`python3 repl.py` directly).

Deliberately does NOT `import app` to get there, same reasoning as
migrate.py's own docstring: app.py's module-level jobs.start() reaches for
the database and starts reminder-polling the moment it's imported, and a
shell session poking at data by hand has no business running that (or
registering every route) just to get at the models. Loads .env, then binds
the database and drops into an async-capable interactive console.

Async-capable because every query now goes through playhouse.pwasyncio
(see models.py) — `User.select().first()` still works (it's plain peewee
underneath), but doesn't hit the database until awaited, and the mutating
convenience methods (acreate/asave/adelete_instance) have no sync
equivalent at all. Plain code.interact() can't `await` at its prompt, so
this builds the same kind of top-level-await console CPython's own
`python3 -m asyncio` uses: a background thread runs the event loop,
compiled statements run on it via run_coroutine_threadsafe(), and the
result comes back to the prompt like any other expression.
"""

import ast
import asyncio
import code
import os
import threading
import types

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    import readline  # noqa: F401  # enables arrow-key history/editing below
except ImportError:
    pass  # not available on every platform; code.interact works fine without it


def _load_dotenv() -> None:
    """Same as app.py's own _load_dotenv() — kept as a small, separate copy
    here rather than imported from app.py, for the reason in this file's
    docstring above."""
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

from models import (  # noqa: E402
    Activity,
    Attachment,
    AuditLog,
    ChatMessage,
    ChatThread,
    Client,
    Comment,
    Invite,
    Notification,
    PasswordReset,
    Reminder,
    Task,
    TeamMember,
    User,
    db,
    init_database,
)

_MODELS = [
    User, TeamMember, Invite, PasswordReset,
    Client, Task, Comment, Attachment, Activity, AuditLog,
    Reminder, Notification, ChatThread, ChatMessage,
]


class AsyncConsole(code.InteractiveConsole):
    """A code.InteractiveConsole that accepts top-level `await`, running
    each compiled statement on `loop` (owned by a background thread — see
    __main__ below) via run_coroutine_threadsafe() and blocking this
    (the REPL's own) thread until it finishes, the same shape a normal
    synchronous statement's result appears at the prompt."""

    def __init__(self, locals: dict, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(locals)
        self.compile.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        self.loop = loop

    def runcode(self, source_code) -> None:
        func = types.FunctionType(source_code, self.locals)
        try:
            result = func()
        except SystemExit:
            raise
        except BaseException:
            self.showtraceback()
            return

        if not asyncio.iscoroutine(result):
            if result is not None:
                self.locals["_"] = result
            return

        future = asyncio.run_coroutine_threadsafe(result, self.loop)
        try:
            value = future.result()
        except SystemExit:
            raise
        except BaseException:
            self.showtraceback()
            return
        if value is not None:
            self.locals["_"] = value


if __name__ == "__main__":
    init_database()

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="repl-loop", daemon=True).start()
    asyncio.run_coroutine_threadsafe(db.aconnect(), loop).result()

    banner = (
        "Scalar Pro REPL — db: {db_url}\n"
        "Preloaded: db, {models}\n"
        "Top-level `await` works, e.g.: await User.aget_or_none(User.id == 1)\n"
        "Migrations are NOT applied here — run `make db-migrate` first if one is pending."
    ).format(
        db_url="{}@{}:{}".format(
            os.environ.get("PGDATABASE", "scalar"),
            os.environ.get("PGHOST", "localhost"),
            os.environ.get("PGPORT", 5432),
        ),
        models=", ".join(m.__name__ for m in _MODELS),
    )
    console = AsyncConsole(globals(), loop)
    try:
        console.interact(banner=banner)
    finally:
        asyncio.run_coroutine_threadsafe(db.aclose(), loop).result()
        loop.call_soon_threadsafe(loop.stop)
