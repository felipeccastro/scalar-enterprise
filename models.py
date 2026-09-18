"""Peewee models for pro.

Single-tenant: there is no Workspace concept at all — one instance == one
customer.

Schema changes go through migrations/ (peewee-migrate — vendored, see
vendor/peewee_migrate/ and vendor/playhouse/, plus the note on Pro's own
dependency story in AGENTS.md), applied by run_migrations() at startup (see
the bottom of this file). This is pro's one deliberate divergence from
core's zero-pip-dependency, no-migrations-framework rule — see
AGENTS.md for why the tradeoff was worth it here and core stays as-is.
"""

from __future__ import annotations

import datetime
import json
import os

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DatabaseProxy,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

# The concrete database is chosen at startup (see make_database/init_database)
# so the choice can be deferred until after .env is loaded (app.py does this).
db = DatabaseProxy()


def make_database() -> SqliteDatabase:
    """Build the concrete database. Local SQLite file, path overridable via
    SQLITE_PATH (defaults to app.db next to this file).

    Pragmas, and why each is here rather than left at SQLite's own default:
    - journal_mode=wal: readers (most requests) don't block on a writer,
      and vice versa — the default rollback journal takes an exclusive
      lock for the whole write.
    - synchronous=NORMAL: the pairing WAL mode's own docs recommend. Full
      durability on every commit (the FULL default) costs an fsync per
      write for a guarantee WAL already covers except across an actual OS
      crash/power loss — an acceptable trade for a single-tenant app.
    - foreign_keys=1: SQLite ignores FK constraints unless a connection
      turns this on itself; without it, on_delete="CASCADE"/"SET NULL" in
      models.py would be decoration, not enforced.
    - busy_timeout=5000: retry for up to 5s on a locked database instead of
      failing the request immediately. Single gunicorn worker or not (see
      ../Makefile), jobs.py's reminder-polling thread and a request handler
      both open their own connection to the same file, so brief contention
      between them is real, not hypothetical.
    - cache_size=-64000: a 64MB page cache (negative = KB, SQLite's own
      convention), up from the ~2MB default — cheap on a server built for
      this, and this database is read far more than it's written.
    """
    sqlite_path = os.environ.get(
        "SQLITE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
    )
    return SqliteDatabase(
        sqlite_path,
        pragmas={
            "journal_mode": "wal",
            "synchronous": 1,  # NORMAL
            "foreign_keys": 1,
            "busy_timeout": 5000,
            "cache_size": -64000,
        },
    )


def init_database() -> SqliteDatabase:
    """Bind the proxy to the concrete backend. Idempotent."""
    if db.obj is None:
        db.initialize(make_database())
    return db.obj


class BaseModel(Model):
    """`audit_trail = True` (opt-in per subclass, off by default here) makes
    save()/delete_instance() write an AuditLog row alongside every write —
    a full snapshot on create, a field-level diff on update, a bare marker
    on delete. See AuditLog below (the table) and pages/audit.py (the page
    that reads it back).

    `audit_exclude` names fields that never appear in a snapshot or diff
    even while audit_trail is on — a secret like User.password_hash should
    never end up sitting in a log a page renders back, hashed or not.

    `soft_delete = True` (opt-in per subclass, off by default here) makes
    delete_instance() set `deleted_at` instead of actually removing the row
    — a model that opts in needs a nullable `deleted_at` DateTimeField of
    its own (Client/Task have one; see migrations/003_soft_delete.py for
    the pattern of adding one to a future model). Query sites decide for
    themselves whether to filter it out (`Model.deleted_at.is_null(True)`,
    the same way every existing query already filters `archived_at`) —
    this only changes what delete_instance() does, not what any SELECT
    returns. `restore()` is the other half: sets `deleted_at` back to
    None. Both go through save(), so a soft delete or a restore is really
    just an update as far as audit_trail is concerned — there's nothing
    to actually log as "deleted" when nothing left the table."""

    audit_trail = False
    audit_exclude: frozenset[str] = frozenset()
    soft_delete = False

    class Meta:
        database = db

    def save(self, force_insert=False, only=None):
        if not self.audit_trail:
            return super().save(force_insert=force_insert, only=only)
        is_new = self._pk is None or force_insert
        # Computed *before* the real save(): peewee overwrites __data__ the
        # instant a field is assigned (see FieldAccessor.__set__), so the old
        # side of a diff is only ever available by asking the DB, and only
        # until this next line replaces it.
        changes = _audit_snapshot(self) if is_new else _audit_diff(self)
        result = super().save(force_insert=force_insert, only=only)
        if is_new or changes:
            _write_audit_log(self, "created" if is_new else "updated", changes)
        return result

    def delete_instance(self, recursive=False, delete_nullable=False):
        if self.soft_delete:
            self.deleted_at = datetime.datetime.now()
            return self.save()

        # Captured before the row is actually gone (cheap — everything's
        # already in memory); written after a successful delete, so a
        # failed one (an FK constraint, say) doesn't log a phantom.
        changes = _audit_deletion_snapshot(self) if self.audit_trail else {}
        result = super().delete_instance(recursive=recursive, delete_nullable=delete_nullable)
        if self.audit_trail:
            _write_audit_log(self, "deleted", changes)
        return result

    def restore(self):
        """Undo a soft delete. A no-op call on a model that never opted
        into soft_delete would be a bug at the call site, not something to
        quietly swallow — let the AttributeError from a missing
        deleted_at field surface."""
        self.deleted_at = None
        return self.save()


def _audit_actor_id() -> int | None:
    """Best-effort current-request user id. None outside a request (a
    background thread, a script) rather than raising — audit metadata
    should never be why a write fails. Imported locally: utils.py has no
    reason to import models.py at module scope, and this would make it."""
    try:
        from utils import current_user

        user = current_user()
        return user.id if user else None
    except Exception:
        return None


def _audit_json_safe(value):
    """A raw field/column value, coerced into something json.dumps() can
    take. Dates/datetimes become ISO strings; a Model instance (a
    ForeignKeyField assigned as an object rather than a bare id) reduces to
    its own pk, so it compares equal to the plain id __data__ holds for an
    untouched relation and to what a fresh SELECT of the same column
    returns."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Model):
        return value._pk
    return value


def _audit_skip(instance: Model, name: str) -> bool:
    return name == instance._meta.primary_key.name or name in instance.audit_exclude


def _audit_snapshot(instance: Model) -> dict:
    """{field: {"old": None, "new": value}} for every non-excluded column on
    a brand new row — there's no prior value to diff against, so "old" is
    uniformly None rather than the row simply being absent from the log."""
    return {
        name: {"old": None, "new": _audit_json_safe(value)}
        for name, value in instance.__data__.items()
        if not _audit_skip(instance, name)
    }


def _audit_deletion_snapshot(instance: Model) -> dict:
    """{field: {"old": value, "new": None}} for every non-excluded column —
    the mirror of _audit_snapshot(), so a deleted row's audit entry shows
    what's gone rather than a bare "this happened" marker with nothing to
    show for it."""
    return {
        name: {"old": _audit_json_safe(value), "new": None}
        for name, value in instance.__data__.items()
        if not _audit_skip(instance, name)
    }


def _audit_diff(instance: Model) -> dict:
    """{field: {"old": ..., "new": ...}} for exactly the non-excluded fields
    that both changed *and* are actually dirty (peewee's own dirty_fields,
    which is also what determines which columns the impending UPDATE
    touches) — a reassignment back to the same value is dirty but not a
    real change, so it's fetched, compared, and dropped rather than logged
    as one. An excluded field (audit_exclude) is never even fetched to
    compare — a changed password shouldn't surface as "password_hash
    changed", either."""
    names = [f.name for f in instance.dirty_fields if not _audit_skip(instance, f.name)]
    if not names:
        return {}
    old_row = (
        type(instance)
        .select(*[instance._meta.combined[n] for n in names])
        .where(instance._pk_expr())
        .dicts()
        .first()
    )
    if not old_row:
        return {}  # the pk doesn't exist yet — force_insert masquerading as an update
    changes = {}
    for name in names:
        old_value = _audit_json_safe(old_row.get(name))
        new_value = _audit_json_safe(instance.__data__.get(name))
        if old_value != new_value:
            changes[name] = {"old": old_value, "new": new_value}
    return changes


def _write_audit_log(instance: Model, action: str, changes: dict) -> None:
    AuditLog.create(
        subject_type=type(instance).__name__.lower(),
        subject_id=instance._pk,
        action=action,
        changes_json=json.dumps(changes),
        actor=_audit_actor_id(),
    )


# ---------------------------------------------------------------------------
# Identity — no workspace, no "client" access tier. Whoever has an account
# has full access; TeamMember.role is informational (owner|admin|member).
# ---------------------------------------------------------------------------


class User(BaseModel):
    audit_trail = True
    audit_exclude = frozenset({"password_hash"})
    id = AutoField()
    email = CharField(unique=True)
    password_hash = CharField()
    name = CharField()
    created_at = DateTimeField(default=datetime.datetime.now)


class TeamMember(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="membership", unique=True, on_delete="CASCADE")
    role = CharField(default="member")  # owner | admin | member
    created_at = DateTimeField(default=datetime.datetime.now)


class Invite(BaseModel):
    """A pending "join the team" invite, emailed as a tokenized accept link."""

    id = AutoField()
    email = CharField()
    token = CharField(unique=True)
    role = CharField(default="member")
    invited_by = ForeignKeyField(User, backref="sent_invites", on_delete="CASCADE")
    accepted = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


class PasswordReset(BaseModel):
    """Single-use, time-limited token for resetting a user's password."""

    id = AutoField()
    user = ForeignKeyField(User, backref="password_resets", on_delete="CASCADE")
    token = CharField(unique=True)
    expires_at = DateTimeField()
    used = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


# ---------------------------------------------------------------------------
# Core typed models — real columns, no generic FieldDef/FieldValue.
# ---------------------------------------------------------------------------


class Client(BaseModel):
    audit_trail = True
    soft_delete = True
    id = AutoField()
    name = CharField()
    email = CharField(default="")
    phone = CharField(default="")
    company = CharField(default="")
    website = CharField(default="")  # added by migrations/002_client_website.py — see that file
    status = CharField(default="lead")  # lead | active | inactive
    notes = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    created_by = ForeignKeyField(User, backref="created_clients", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)
    # Distinct from archived_at: archiving is a deliberate "done with this"
    # action a person takes and can see reflected everywhere; deleted_at
    # exists so delete_instance() (soft_delete=True, see BaseModel) never
    # actually removes a row — added by migrations/003_soft_delete.py.
    deleted_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = ((("status",), False),)


class Task(BaseModel):
    audit_trail = True
    soft_delete = True
    id = AutoField()
    title = CharField()
    description = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    status = CharField(default="todo")  # todo | in_progress | done
    assignee = ForeignKeyField(User, backref="assigned_tasks", null=True, on_delete="SET NULL")
    client = ForeignKeyField(Client, backref="tasks", null=True, on_delete="SET NULL")
    position = IntegerField(default=0)
    created_by = ForeignKeyField(User, backref="created_tasks", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)
    deleted_at = DateTimeField(null=True)  # see Client.deleted_at above

    class Meta:
        database = db
        indexes = (
            (("status", "position"), False),
            (("client",), False),
        )


# ---------------------------------------------------------------------------
# Generic-over-subject helpers (Comment/Attachment/Activity apply to either a
# Client or a Task, keyed by subject_type + subject_id rather than a link
# table per model — one implementation instead of two).
# ---------------------------------------------------------------------------

SUBJECT_TYPES = ("client", "task")

# Shared with pages/ (form choices) and ai.py (tool-schema enums / write-tool
# validation) — defined once here so ai.py can import them without importing
# pages/ back (pages/chat.py does `import ai`, so that direction would cycle).
CLIENT_STATUSES = ("lead", "active", "inactive")
TASK_STATUSES = ("todo", "in_progress", "done")

_STATUS_LABELS = {
    "lead": "Lead", "active": "Active", "inactive": "Inactive",
    "todo": "To Do", "in_progress": "In Progress", "done": "Done",
}


def status_label(status: str) -> str:
    """Human-readable form of a Client/Task status value for display —
    e.g. "in_progress" -> "In Progress". The raw value (still what's
    stored, matched in queries, and submitted by forms/tool calls) is
    untouched; only what's shown to a person goes through this. Falls back
    to a generic underscore->title-case conversion for any status not in
    the table above, so a future status doesn't need this list updated."""
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


class Comment(BaseModel):
    id = AutoField()
    subject_type = CharField()  # "client" | "task"
    subject_id = IntegerField()
    body = TextField()  # plain text — rendered with white-space: pre-wrap
    author = ForeignKeyField(User, backref="comments", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Attachment(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    filename = CharField()
    stored_path = CharField()  # relative path under UPLOAD_FOLDER
    mime = CharField(default="application/octet-stream")
    size = IntegerField(default=0)
    uploaded_by = ForeignKeyField(User, backref="uploads", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Activity(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    actor = ForeignKeyField(User, backref="activity", null=True, on_delete="SET NULL")
    verb = CharField()  # created | updated | status_changed | commented | archived | ...
    payload_json = TextField(default="{}")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id", "created_at"), False),)


class AuditLog(BaseModel):
    """One row per audited create/update/delete — written automatically by
    BaseModel.save()/delete_instance() above for any model that opts in
    with `audit_trail = True` (Client, Task), never by a route calling
    something directly the way Activity's record_activity() is.

    `changes_json` is `{field: {"old": ..., "new": ...}}` — "old" is
    uniformly None on create, "new" is uniformly None on delete, and only
    the fields that actually changed appear on an update. See
    pages/audit.py for the page that renders this back."""

    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    action = CharField()  # created | updated | deleted
    changes_json = TextField(default="{}")
    actor = ForeignKeyField(User, backref="audit_logs", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id", "created_at"), False),)


class Reminder(BaseModel):
    """A one-shot reminder, fired by jobs.py's scheduler once `remind_at`
    passes: creates a Notification for `user` and emails them. Created only
    from Ask AI today (ai.py: create_reminder) — no manual UI, the same
    "AI-only record" shape as this app's other write tools, just applied to
    a record type that has no form of its own. Generic-over-subject like
    Comment/Attachment/Activity (optional; a reminder needn't reference a
    client or task at all)."""

    id = AutoField()
    user = ForeignKeyField(User, backref="reminders", on_delete="CASCADE")
    message = TextField()
    subject_type = CharField(null=True)  # "client" | "task" | None — see SUBJECT_TYPES
    subject_id = IntegerField(null=True)
    remind_at = DateTimeField()
    sent_at = DateTimeField(null=True)  # null = still pending; set once jobs.py fires it
    created_by = ForeignKeyField(User, backref="created_reminders", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        # jobs.py's poll queries exactly this shape: due, unsent reminders.
        indexes = ((("sent_at", "remind_at"), False),)


class Notification(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="notifications", on_delete="CASCADE")
    kind = CharField()  # assignment | comment | ...
    payload_json = TextField(default="{}")
    read_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = (
            (("user", "read_at"), False),
            (("user", "created_at"), False),
        )


# ---------------------------------------------------------------------------
# Ask-AI chat — one running thread per user, kept simple (no multi-thread UI).
# ---------------------------------------------------------------------------


class ChatThread(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="chat_threads", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)
    # Set together when the AI proposes a write and it's paused for human
    # confirmation (see ai.py: _save_pending/_clear_pending/resolve_pending);
    # null the rest of the time.
    pending_tool_calls = TextField(null=True)  # JSON: raw tool_call dicts awaiting confirmation
    pending_convo = TextField(null=True)  # JSON: full in-flight message list, incl. the proposing assistant turn
    pending_round_idx = IntegerField(null=True)  # _agent_loop's round counter, so resume doesn't reset the budget


class ChatMessage(BaseModel):
    id = AutoField()
    thread = ForeignKeyField(ChatThread, backref="messages", on_delete="CASCADE")
    role = CharField()  # user | assistant
    content = TextField()
    created_at = DateTimeField(default=datetime.datetime.now)


def seed_demo_data(owner: User) -> None:
    """A couple of realistic Clients/Tasks so a freshly-provisioned instance
    isn't an empty screen. Called once, right after the first owner registers
    (see pages/auth.py:register_owner_submit) — not from run_migrations(),
    since it needs a real User to attribute the rows to."""
    acme = Client.create(
        name="Acme Corp", email="hello@acme.example", company="Acme Corp",
        status="active", notes="Long-time client, monthly retainer.",
        created_by=owner,
    )
    northwind = Client.create(
        name="Northwind Traders", email="hi@northwind.example", company="Northwind Traders",
        status="lead", notes="Introduced last week, still evaluating.",
        created_by=owner,
    )
    Task.create(
        title="Send onboarding email", status="done",
        client=acme, assignee=owner, position=0, created_by=owner,
    )
    Task.create(
        title="Prepare proposal", description="Cover scope, timeline, and pricing.",
        status="in_progress", client=northwind, assignee=owner, position=1, created_by=owner,
    )
    Task.create(
        title="Quarterly check-in call", status="todo",
        client=acme, assignee=owner, position=2, created_by=owner,
    )


# ---------------------------------------------------------------------------
# Schema — owned by migrations/ (peewee-migrate), applied at startup.
# ---------------------------------------------------------------------------


def _column_exists(table: str, column: str) -> bool:
    """Used from within a migration's own migrate() (see
    migrations/002_client_website.py) to tell a genuine apply apart from
    peewee-migrate's "fake" replay of already-applied migrations — under
    fake, Database.execute_sql is mocked out, so a real probe here would
    hit the mock rather than the actual table."""
    cur = db.execute_sql(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def run_migrations() -> None:
    """Apply every pending migration in migrations/, in filename order. Safe
    to call on every startup — peewee-migrate tracks what's already applied
    in its own `migratehistory` table, so an already-current database is a
    no-op."""
    init_database()
    from peewee_migrate import Router

    router = Router(db.obj, migrate_dir=MIGRATIONS_DIR)
    router.run()
