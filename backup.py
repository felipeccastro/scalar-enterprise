"""Write a consistent, point-in-time copy of app.db to the given path.

Used by `make backup` (see Makefile) to get a safe snapshot of the live
database to fold into the backup zip. Deliberately does NOT just `cp` the
file: app.db runs in WAL mode (see models.py), so a write in flight can
have committed data sitting in the app.db-wal sidecar rather than in
app.db itself, and a raw copy of just app.db can silently miss it — worse,
copying mid-write can grab the file in a torn, inconsistent state.

Uses sqlite3's own Online Backup API (stdlib `sqlite3.Connection.backup()`
— no extra install, vendored dependency, or CLI tool required) instead,
which the SQLite docs recommend for exactly this: it's safe to run against
a live, in-use database, and always produces a single, consistent file.

Standalone like migrate.py, for the same reason: doesn't import app.py, so
it can't race app.py's own startup (jobs.py's background thread, etc.).
"""

import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Same as app.py's/migrate.py's own _load_dotenv() — kept as a small,
    separate copy here for the same reason migrate.py gives for its own."""
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


def backup_database(dest_path: str) -> None:
    """Snapshot the live database (SQLITE_PATH, same default as models.py)
    to dest_path via the Online Backup API."""
    _load_dotenv()
    src_path = os.environ.get("SQLITE_PATH", os.path.join(BASE_DIR, "app.db"))
    src = sqlite3.connect(src_path)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        dest = sqlite3.connect(dest_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 backup.py <dest-path>")
    backup_database(sys.argv[1])
    print(f"Snapshotted database to {sys.argv[1]}")
