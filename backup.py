"""Write a consistent, point-in-time snapshot of the scalar database to the
given path.

Used by `make backup` (see Makefile) to fold a live snapshot into the backup
zip. Shells out to `pg_dump` (ships with any Postgres install, client tools
included) in its custom format (-F c): a single file, restorable with
`pg_restore`, and — like every pg_dump format — built on a single consistent
MVCC snapshot, so it's safe to run against a live, in-use database without
locking writers out.

Standalone like migrate.py, for the same reason: doesn't import app.py, so
it can't race app.py's own startup (jobs.py's background thread, etc.).
"""

import os
import subprocess
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
    """Snapshot the live database (PGHOST/PGPORT/PGDATABASE/PGUSER/
    PGPASSWORD, same defaults as models.py) to dest_path via pg_dump."""
    _load_dotenv()
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    cmd = [
        "pg_dump",
        "-h", os.environ.get("PGHOST", "localhost"),
        "-p", os.environ.get("PGPORT", "5432"),
        "-d", os.environ.get("PGDATABASE", "scalar"),
        "-F", "c",
        "-f", dest_path,
    ]
    if os.environ.get("PGUSER"):
        cmd += ["-U", os.environ["PGUSER"]]
    # PGPASSWORD (if set, by _load_dotenv above or already in the
    # environment) is picked up by pg_dump itself — it's one of libpq's own
    # recognized env vars, same as PGHOST/PGPORT/PGDATABASE.
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 backup.py <dest-path>")
    backup_database(sys.argv[1])
    print(f"Snapshotted database to {sys.argv[1]}")
