"""Initial schema: every application table, in foreign-key dependency
order. Authored for peewee-migrate (see models.py: run_migrations()).

create_model goes through peewee's create_table(safe=True), so applying
this against a database that predates migrations (any pro install seeded
before this file existed) is a no-op for the tables themselves — it just
records the migration as applied and lets everything after it in
migrations/ take over from there.
"""

from models import (
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
)

# Dependency order: a table is created after everything its foreign keys
# point at.
MODELS = [
    User,
    TeamMember,
    Invite,
    PasswordReset,
    Client,
    Task,
    Comment,
    Attachment,
    Activity,
    AuditLog,
    Notification,
    Reminder,
    ChatThread,
    ChatMessage,
]


def migrate(migrator, database, **kwargs):
    for model in MODELS:
        migrator.create_model(model)


def rollback(migrator, database, **kwargs):
    for model in reversed(MODELS):
        migrator.remove_model(model)
