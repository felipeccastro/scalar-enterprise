"""Task board/list/detail/create/update/archive/delete, plus the
drag-and-drop reorder endpoint the /tasks board posts to.
"""

from __future__ import annotations

import datetime

from app import app, render
from asgi import request, response
from models import TASK_STATUSES, Client, Task, TeamMember, User, db
from pages._shared import _load_activity, _load_attachments, _load_comments
from utils import abort, current_user, flash, notify, record_activity, redirect, url_for


async def _active_clients() -> list[Client]:
    return await db.list(
        Client.select()
        .where(Client.archived_at.is_null(True) & Client.deleted_at.is_null(True))
        .order_by(Client.name)
    )


async def _team_users() -> list[User]:
    members = await db.list(TeamMember.select().join(User).order_by(User.name))
    return [await m.afetch(TeamMember.user) for m in members]


@app.route("/tasks", method="GET", name="tasks_list")
async def tasks_list():
    q = (request.query.get("q") or "").strip()
    showing_deleted = request.query.get("deleted") == "1"
    # Deleted tasks get a plain list, not the kanban board below — grouping
    # by status is the wrong frame for "what did we soft-delete", and it's
    # not clear a deleted task's status column would even mean anything.
    if showing_deleted:
        query = Task.select().where(Task.deleted_at.is_null(False))
        if q:
            query = query.where(Task.title.contains(q) | Task.description.contains(q))
        return await render(
            "tasks_list.html",
            deleted_tasks=await db.list(query.order_by(Task.updated_at.desc())),
            showing_deleted=True,
            q=q,
        )
    query = Task.select().where(Task.archived_at.is_null(True) & Task.deleted_at.is_null(True))
    if q:
        query = query.where(Task.title.contains(q) | Task.description.contains(q))
    tasks = await db.list(query.order_by(Task.position, Task.id))
    # Pre-warm client/assignee FKs — the kanban cards in tasks_list.html
    # read t.client.name/t.assignee.name synchronously (Jinja2 can't await
    # mid-render); see pages/_shared.py's _load_comments for the same
    # pattern and why it's simpler than a join here.
    for t in tasks:
        if t.client_id:
            await t.afetch(Task.client)
        if t.assignee_id:
            await t.afetch(Task.assignee)
    grouped = {s: [t for t in tasks if t.status == s] for s in TASK_STATUSES}
    return await render(
        "tasks_list.html",
        grouped=grouped,
        statuses=TASK_STATUSES,
        clients=await _active_clients(),
        team_users=await _team_users(),
        q=q,
        showing_deleted=False,
    )


@app.route("/tasks", method="POST", name="tasks_create")
async def tasks_create():
    title = (request.forms.get("title") or "").strip()
    if not title:
        flash("A task needs a title.", "error")
        redirect(url_for("tasks_list"))
    client_id = request.forms.get("client_id") or ""
    assignee_id = request.forms.get("assignee_id") or ""
    user = await current_user()
    last = await db.first(Task.select().order_by(Task.position.desc()))
    task = await Task.acreate(
        title=title,
        description=(request.forms.get("description") or "").strip(),
        status=request.forms.get("status") or "todo",
        client=int(client_id) if client_id else None,
        assignee=int(assignee_id) if assignee_id else None,
        position=(last.position + 1) if last else 0,
        created_by=user,
    )
    await record_activity("task", task.id, user, "created")
    if task.assignee_id and task.assignee_id != user.id:
        assignee = await task.afetch(Task.assignee)
        await notify(assignee, "assignment", task_id=task.id, task_title=task.title)
    flash(f"Added “{task.title}”.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/{task_id:int}", method="GET", name="task_detail")
async def task_detail(task_id: int):
    task = await db.first(Task.select().where(Task.id == task_id))
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    return await render(
        "task_detail.html",
        task=task,
        statuses=TASK_STATUSES,
        clients=await _active_clients(),
        team_users=await _team_users(),
        comments=await _load_comments("task", task.id),
        attachments=await _load_attachments("task", task.id),
        activity=await _load_activity("task", task.id),
    )


@app.route("/tasks/{task_id:int}", method="POST", name="task_update")
async def task_update(task_id: int):
    task = await db.first(Task.select().where(Task.id == task_id))
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    user = await current_user()
    old_status = task.status
    task.title = (request.forms.get("title") or task.title).strip()
    task.description = (request.forms.get("description") or "").strip()
    task.status = request.forms.get("status") or task.status
    client_id = request.forms.get("client_id") or ""
    assignee_id = request.forms.get("assignee_id") or ""
    task.client = int(client_id) if client_id else None
    new_assignee_id = int(assignee_id) if assignee_id else None
    reassigned = new_assignee_id and new_assignee_id != task.assignee_id
    task.assignee = new_assignee_id
    task.updated_at = datetime.datetime.now()
    await task.asave()
    if task.status != old_status:
        await record_activity("task", task.id, user, "status_changed", old=old_status, new=task.status)
    else:
        await record_activity("task", task.id, user, "updated")
    if reassigned and task.assignee_id != user.id:
        assignee = await task.afetch(Task.assignee)
        await notify(assignee, "assignment", task_id=task.id, task_title=task.title)
    flash("Task updated.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/reorder", method="POST", name="tasks_reorder")
async def tasks_reorder():
    """Drag-and-drop endpoint for the /tasks board (see the script at the
    bottom of tasks_list.html). The board sends the *entire*, freshly
    dropped ordering of one column: every listed task gets that column's
    status and a position matching its index in the list. Only ever
    touches status/position — never title/description/etc. — so a drop
    can't clobber anything else about a task.
    """
    status = request.forms.get("status") or ""
    if status not in TASK_STATUSES:
        abort(400, "That's not a valid status.")
    task_ids = [int(v) for v in request.forms.getall("task_id") if v.isdigit()]
    user = await current_user()
    rows = await db.list(Task.select().where(Task.id.in_(task_ids)))
    tasks_by_id = {t.id: t for t in rows}
    for position, task_id in enumerate(task_ids):
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        old_status = task.status
        task.status = status
        task.position = position
        task.updated_at = datetime.datetime.now()
        await task.asave()
        if old_status != status:
            await record_activity("task", task.id, user, "status_changed", old=old_status, new=status)
    response.status = 204
    return ""


@app.route("/tasks/{task_id:int}/archive", method="POST", name="task_archive")
async def task_archive(task_id: int):
    task = await db.first(Task.select().where(Task.id == task_id))
    if task is not None:
        task.archived_at = datetime.datetime.now()
        await task.asave()
        await record_activity("task", task.id, await current_user(), "archived")
        flash(f"Archived “{task.title}”.", "success")
    redirect(url_for("tasks_list"))


@app.route("/tasks/{task_id:int}/delete", method="POST", name="task_delete")
async def task_delete(task_id: int):
    """Task.soft_delete = True (see models.py) means this never actually
    removes the row — delete_instance() sets deleted_at instead, so it's
    reversible from the "View deleted tasks" list."""
    task = await db.first(Task.select().where(Task.id == task_id))
    if task is not None:
        await task.adelete_instance()
        flash(f"Deleted “{task.title}”. You can restore it from the deleted tasks list.", "success")
    redirect(url_for("tasks_list"))


@app.route("/tasks/{task_id:int}/restore", method="POST", name="task_restore")
async def task_restore(task_id: int):
    task = await db.first(Task.select().where(Task.id == task_id))
    if task is not None:
        await task.arestore()
        flash(f"Restored “{task.title}”.", "success")
    redirect(url_for("tasks_list", _query={"deleted": "1"}))
