"""The audit log page — a global feed of AuditLog rows (see models.py:
BaseModel.save()/delete_instance(), which write these automatically for any
model with `audit_trail = True`, currently Client and Task).

Distinct from Activity/record_activity(): that's a hand-written, one-line-
per-call log a route calls explicitly ("commented", "archived", ...); this
is unattended and field-level, written by the model layer itself regardless
of which route (or the Ask-AI tools, or a future script) made the change.
"""

from __future__ import annotations

import json

from bottle import request

from app import app, render
from models import AuditLog
from pages._shared import _subject_url

PAGE_SIZE = 50


@app.route("/audit", method="GET", name="audit_log")
def audit_log():
    page = request.query.get("page") or "1"
    page = int(page) if page.isdigit() and int(page) > 0 else 1
    query = AuditLog.select().order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    total = query.count()
    rows = list(query.paginate(page, PAGE_SIZE))
    return render(
        "audit_log.html",
        entries=[_present(e) for e in rows],
        page=page,
        has_prev=page > 1,
        has_next=page * PAGE_SIZE < total,
        total=total,
    )


def _present(entry: AuditLog) -> dict:
    try:
        changes = json.loads(entry.changes_json or "{}")
    except (ValueError, TypeError):
        changes = {}
    return {
        "subject_type": entry.subject_type,
        "subject_id": entry.subject_id,
        # The subject may well be gone by the time this renders (that's
        # exactly what a "deleted" entry means) — _subject_url still builds
        # a link; the detail route's own missing-row handling takes it from
        # there rather than this page having to know.
        "url": _subject_url(entry.subject_type, entry.subject_id),
        "action": entry.action,
        "actor": entry.actor.name if entry.actor_id else "System",
        "created_at": entry.created_at,
        "changes": changes,
    }
