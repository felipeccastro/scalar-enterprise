"""Apply pending database migrations.

Used by `make db-migrate`. Loads .env, then applies everything in
migrations/.

Deliberately does NOT `import app` to get there, unlike this file's
counterpart in ../admin/: app.py's own module-level jobs.start() reaches
for the database the moment it's running, uvicorn worker or not — and
would race this script's own run_migrations() call below for exactly the
tables a pending migration hasn't created yet.
"""

import asyncio
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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

from models import run_migrations  # noqa: E402

if __name__ == "__main__":
    # run_migrations() is async — see models.py's own comment on why
    # (peewee-migrate needs a synchronous database; AsyncPostgresqlDatabase
    # only allows that from inside its "greenlet bridge", db.run(...)).
    # asyncio.run() here, not a bare coroutine: there's no event loop yet at
    # module scope, and this script needs exactly one, start to finish.
    asyncio.run(run_migrations())
    print("Migrations applied.")
