"""Add Client.website — a sample migration, kept around as the pattern for
whoever writes the next real one: a small, additive column change.

add_fields is IF-NOT-EXISTS-safe for a genuine apply, but peewee-migrate
replays already-applied migrations in "fake" mode (Database.execute_sql
mocked out) to rebuild its own model state before running a new one — a
real _column_exists probe would hit that mock, so it's skipped under fake.
"""

from peewee import CharField

from models import Client, _column_exists


def migrate(migrator, database, **kwargs):
    fake = bool(kwargs.get("fake"))
    if fake or not _column_exists("client", "website"):
        migrator.add_fields(Client, website=CharField(default=""))


def rollback(migrator, database, **kwargs):
    migrator.remove_fields(Client, "website")
