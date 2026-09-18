"""Add Client.deleted_at and Task.deleted_at — backs the generic
soft_delete mechanism on BaseModel (see models.py): delete_instance() on
either model now sets this instead of removing the row.

add_fields is IF-NOT-EXISTS-safe for a genuine apply, but peewee-migrate
replays already-applied migrations in "fake" mode (Database.execute_sql
mocked out) to rebuild its own model state before running a new one — a
real _column_exists probe would hit that mock, so it's skipped under fake
(same pattern as migrations/002_client_website.py).
"""

from peewee import DateTimeField

from models import Client, Task, _column_exists


def migrate(migrator, database, **kwargs):
    fake = bool(kwargs.get("fake"))
    if fake or not _column_exists("client", "deleted_at"):
        migrator.add_fields(Client, deleted_at=DateTimeField(null=True))
    if fake or not _column_exists("task", "deleted_at"):
        migrator.add_fields(Task, deleted_at=DateTimeField(null=True))


def rollback(migrator, database, **kwargs):
    migrator.remove_fields(Client, "deleted_at")
    migrator.remove_fields(Task, "deleted_at")
