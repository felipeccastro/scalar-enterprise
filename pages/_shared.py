"""Query/redirect helpers used by more than one route module — kept here
instead of duplicated or hung off whichever module happened to need one
first.
"""

from __future__ import annotations

from models import Activity, Attachment, Comment, db
from utils import redirect, url_for


async def _load_comments(subject_type: str, subject_id: int) -> list[Comment]:
    comments = await db.list(
        Comment.select()
        .where((Comment.subject_type == subject_type) & (Comment.subject_id == subject_id))
        .order_by(Comment.created_at)
    )
    # Pre-warm the author FK: templates read c.author.name synchronously
    # (Jinja2 can't await mid-render), but a fetch this cheap once, here,
    # is simpler and safer than restructuring the query above into a join
    # just to populate the same cache peewee already maintains per-instance
    # once a relation's been loaded once (afetch, then a bare attribute
    # read, are the same cache — see models.py/pwasyncio's afetch()).
    for c in comments:
        await c.afetch(Comment.author)
    return comments


async def _load_attachments(subject_type: str, subject_id: int) -> list[Attachment]:
    return await db.list(
        Attachment.select()
        .where((Attachment.subject_type == subject_type) & (Attachment.subject_id == subject_id))
        .order_by(Attachment.created_at.desc())
    )


async def _load_activity(subject_type: str, subject_id: int, limit: int = 20) -> list[Activity]:
    activity = await db.list(
        Activity.select()
        .where((Activity.subject_type == subject_type) & (Activity.subject_id == subject_id))
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    # Pre-warm the actor FK — see _load_comments' comment above; actor is
    # nullable (a system-generated entry has none), hence the guard.
    for a in activity:
        if a.actor_id:
            await a.afetch(Activity.actor)
    return activity


# Comments/attachments/audit log entries are generic over Client/Task via
# subject_type/subject_id (see models.py) — this is what a create/delete
# redirects back to, and what the audit log links each entry's subject to.
# "user" has no detail page of its own (User.audit_trail is on too, for the
# audit log's sake, but there's no per-user page in this minimal core app)
# — it lands on the team roster on /settings instead, hence the None kwarg.
_DETAIL_ROUTE = {"client": "client_detail", "task": "task_detail", "user": "settings"}
_DETAIL_KWARG = {"client": "client_id", "task": "task_id", "user": None}


def _subject_url(subject_type: str, subject_id: int) -> str:
    route = _DETAIL_ROUTE.get(subject_type, "clients_list")
    kwarg = _DETAIL_KWARG.get(subject_type, "client_id")
    if kwarg is None:
        return url_for(route)
    return url_for(route, **{kwarg: subject_id})


def _redirect_to_subject(subject_type: str, subject_id: int):
    redirect(_subject_url(subject_type, subject_id))
