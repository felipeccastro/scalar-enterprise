"""Notifications: list, mark-one-read, mark-all-read."""

from __future__ import annotations

import datetime

from app import app, render
from asgi import request, response
from models import Notification, db
from utils import current_user, flash, notification_summary, redirect, url_for


async def _pop_reminder_toasts(user) -> list[str]:
    """Unread reminder notifications for `user`, as display strings, marked
    read in the same stroke — the toast (however it's delivered: a normal
    page load's flash(), or a /notifications/poll response) *is* the read
    receipt for a reminder, unlike assignment/comment notifications, which
    still wait for an explicit "mark read" on the /notifications page."""
    pending = await db.list(Notification.select().where(
        (Notification.user == user) & (Notification.kind == "reminder") & (Notification.read_at.is_null(True))
    ))
    messages = []
    for n in pending:
        messages.append(notification_summary(n))
        n.read_at = datetime.datetime.now()
        await n.asave()
    return messages


@app.hook("before_request")
async def _toast_due_reminders() -> None:
    """The no-JS/first-load path: surface a fired reminder as a toast on
    the next page load, for whoever hasn't got there yet via the polling
    script in layout.html (see notifications_poll below) — a page open
    from before this reminder fired, or a client with JS disabled. jobs.py
    creates the Notification row from a background thread with no request
    (and so no session) to flash() into, which is the gap both this hook
    and the poll route close, just on different triggers.

    Skips the poll route itself — otherwise this hook would always win the
    race and drain the pending notifications before notifications_poll's own
    handler ever ran, leaving the fetch() in layout.html with nothing to
    show. /static/ never reaches a before_request hook at all (see app.py).
    """
    if request.path == "/notifications/poll":
        return
    user = await current_user()
    if user is None:
        return
    for message in await _pop_reminder_toasts(user):
        flash(message, "info")


@app.route("/notifications/poll", method="GET", name="notifications_poll")
async def notifications_poll():
    """Polled every 20s by a small inline script in layout.html so a fired
    reminder pops up as a toast on a page that's already open, rather than
    only on the next navigation (see _toast_due_reminders above for that
    path). JSON, not a rendered page — {"toasts": [...]}."""
    user = await current_user()
    if user is None:
        response.status = 401
        return {"toasts": []}
    return {"toasts": await _pop_reminder_toasts(user)}


@app.route("/notifications", method="GET", name="notifications_list")
async def notifications_list():
    user = await current_user()
    notifications = await db.list(
        Notification.select().where(Notification.user == user).order_by(Notification.created_at.desc())
    )
    return await render("notifications.html", notifications=notifications)


@app.route("/notifications/{notification_id:int}/read", method="POST", name="notification_read")
async def notification_read(notification_id: int):
    user = await current_user()
    n = await db.first(Notification.select().where(
        (Notification.id == notification_id) & (Notification.user == user)
    ))
    if n is not None and n.read_at is None:
        n.read_at = datetime.datetime.now()
        await n.asave()
    redirect(url_for("notifications_list"))


@app.route("/notifications/read-all", method="POST", name="notifications_read_all")
async def notifications_read_all():
    user = await current_user()
    query = Notification.update(read_at=datetime.datetime.now()).where(
        (Notification.user == user) & (Notification.read_at.is_null(True))
    )
    await query.aexecute()
    redirect(url_for("notifications_list"))
