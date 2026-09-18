"""Comments — generic over Client/Task via subject_type/subject_id."""

from __future__ import annotations

from app import app
from asgi import request
from models import Comment, Task, db
from pages._shared import _redirect_to_subject
from utils import current_user, flash, notify, record_activity, redirect, url_for


@app.route("/comments", method="POST", name="comment_create")
async def comment_create():
    subject_type = request.forms.get("subject_type") or ""
    subject_id = int(request.forms.get("subject_id") or 0)
    body = (request.forms.get("body") or "").strip()
    if subject_type not in ("client", "task") or not subject_id or not body:
        flash("Couldn't add that comment.", "error")
        redirect(url_for("dashboard"))
    user = await current_user()
    await Comment.acreate(subject_type=subject_type, subject_id=subject_id, body=body, author=user)
    await record_activity(subject_type, subject_id, user, "commented")
    if subject_type == "task":
        task = await db.first(Task.select().where(Task.id == subject_id))
        if task is not None and task.assignee_id and task.assignee_id != user.id:
            assignee = await task.afetch(Task.assignee)
            await notify(assignee, "comment", task_id=task.id, task_title=task.title)
    _redirect_to_subject(subject_type, subject_id)


@app.route("/comments/{comment_id:int}/delete", method="POST", name="comment_delete")
async def comment_delete(comment_id: int):
    comment = await db.first(Comment.select().where(Comment.id == comment_id))
    if comment is not None:
        subject_type, subject_id = comment.subject_type, comment.subject_id
        if comment.author_id == (await current_user()).id:
            await comment.adelete_instance()
            flash("Comment deleted.", "success")
        else:
            flash("You can only delete your own comments.", "error")
        _redirect_to_subject(subject_type, subject_id)
    redirect(url_for("dashboard"))
