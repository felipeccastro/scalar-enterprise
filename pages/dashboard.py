"""The "/" home dashboard: open/done counts plus recent clients and tasks."""

from __future__ import annotations

from app import app, render
from models import Client, Task


@app.route("/", method="GET", name="dashboard")
def dashboard():
    active_client = Client.archived_at.is_null(True) & Client.deleted_at.is_null(True)
    active_task = Task.archived_at.is_null(True) & Task.deleted_at.is_null(True)
    open_clients = Client.select().where(active_client).count()
    open_tasks = Task.select().where(active_task & (Task.status != "done")).count()
    done_tasks = Task.select().where(active_task & (Task.status == "done")).count()
    recent_clients = list(
        Client.select().where(active_client).order_by(Client.created_at.desc()).limit(5)
    )
    recent_tasks = list(
        Task.select().where(active_task).order_by(Task.created_at.desc()).limit(5)
    )
    return render(
        "dashboard.html",
        open_clients=open_clients,
        open_tasks=open_tasks,
        done_tasks=done_tasks,
        recent_clients=recent_clients,
        recent_tasks=recent_tasks,
    )
