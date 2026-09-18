"""Interactive shell preloaded with every model — `make repl` (or
`python3 repl.py` directly).

Deliberately does NOT `import app` to get there, same reasoning as
migrate.py's own docstring: app.py's module-level jobs.start() reaches for
the database and starts reminder-polling the moment it's imported, and a
shell session poking at data by hand has no business running that (or
registering every Bottle route) just to get at the models. Loads .env and
puts vendor/ on sys.path the same way app.py/migrate.py do, then binds the
database and drops into `code.interact` with every model already in scope.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "vendor"))

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

if __name__ == "__main__":
    import code

    init_database()
    db.connect(reuse_if_open=True)

    banner = (
        "Scalar Pro REPL — db: {db_path}\n"
        "Preloaded: db, {models}\n"
        "Migrations are NOT applied here — run `make db-migrate` first if one is pending."
    ).format(
        db_path=os.environ.get("SQLITE_PATH", "app.db"),
        models=", ".join(m.__name__ for m in _MODELS),
    )
    code.interact(banner=banner, local=globals())
