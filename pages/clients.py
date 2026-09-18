"""Client list/detail/create/update/archive/delete."""

from __future__ import annotations

import datetime

from bottle import request

from app import app, render
from models import CLIENT_STATUSES, Client, Task
from pages._shared import _load_activity, _load_attachments, _load_comments
from utils import current_user, flash, record_activity, redirect, url_for


@app.route("/clients", method="GET", name="clients_list")
def clients_list():
    q = (request.query.get("q") or "").strip()
    showing_deleted = request.query.get("deleted") == "1"
    # Deleted is its own view, not a filter on top of the active list — a
    # soft-deleted row is the thing being looked for here, archived or not,
    # so archived_at doesn't come into it the way it does below.
    if showing_deleted:
        query = Client.select().where(Client.deleted_at.is_null(False))
    else:
        query = Client.select().where(
            Client.archived_at.is_null(True) & Client.deleted_at.is_null(True)
        )
    if q:
        query = query.where(
            Client.name.contains(q) | Client.email.contains(q) | Client.company.contains(q)
        )
    clients = list(query.order_by(Client.created_at.desc()))
    return render(
        "clients_list.html",
        clients=clients,
        statuses=CLIENT_STATUSES,
        q=q,
        showing_deleted=showing_deleted,
    )


@app.route("/clients", method="POST", name="clients_create")
def clients_create():
    name = (request.forms.get("name") or "").strip()
    if not name:
        flash("A client needs a name.", "error")
        redirect(url_for("clients_list"))
    client = Client.create(
        name=name,
        email=(request.forms.get("email") or "").strip(),
        phone=(request.forms.get("phone") or "").strip(),
        company=(request.forms.get("company") or "").strip(),
        status=request.forms.get("status") or "lead",
        notes=(request.forms.get("notes") or "").strip(),
        created_by=current_user(),
    )
    record_activity("client", client.id, current_user(), "created")
    flash(f"Added {client.name}.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>", method="GET", name="client_detail")
def client_detail(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    tasks = list(
        Task.select()
        .where((Task.client == client) & Task.archived_at.is_null(True) & Task.deleted_at.is_null(True))
        .order_by(Task.position)
    )
    return render(
        "client_detail.html",
        client=client,
        tasks=tasks,
        statuses=CLIENT_STATUSES,
        comments=_load_comments("client", client.id),
        attachments=_load_attachments("client", client.id),
        activity=_load_activity("client", client.id),
    )


@app.route("/clients/<client_id:int>", method="POST", name="client_update")
def client_update(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    client.name = (request.forms.get("name") or client.name).strip()
    client.email = (request.forms.get("email") or "").strip()
    client.phone = (request.forms.get("phone") or "").strip()
    client.company = (request.forms.get("company") or "").strip()
    client.status = request.forms.get("status") or client.status
    client.notes = (request.forms.get("notes") or "").strip()
    client.updated_at = datetime.datetime.now()
    client.save()
    record_activity("client", client.id, current_user(), "updated")
    flash("Client updated.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>/archive", method="POST", name="client_archive")
def client_archive(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is not None:
        client.archived_at = datetime.datetime.now()
        client.save()
        record_activity("client", client.id, current_user(), "archived")
        flash(f"Archived {client.name}.", "success")
    redirect(url_for("clients_list"))


@app.route("/clients/<client_id:int>/delete", method="POST", name="client_delete")
def client_delete(client_id: int):
    """Client.soft_delete = True (see models.py) means this never actually
    removes the row — delete_instance() sets deleted_at instead, so it's
    reversible from the "View deleted clients" list."""
    client = Client.select().where(Client.id == client_id).first()
    if client is not None:
        client.delete_instance()
        flash(f"Deleted {client.name}. You can restore it from the deleted clients list.", "success")
    redirect(url_for("clients_list"))


@app.route("/clients/<client_id:int>/restore", method="POST", name="client_restore")
def client_restore(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is not None:
        client.restore()
        flash(f"Restored {client.name}.", "success")
    redirect(url_for("clients_list", _query={"deleted": "1"}))
