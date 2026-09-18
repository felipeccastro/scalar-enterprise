# Scalar Core — Feature Documentation

This describes what's actually built, for a person (or an agent) picking up
this project without prior context. Code comments cover *why* a given line
is written the way it is; this file covers *what the product does*, page by
page. Keep it in sync with the code — see [AGENTS.md](AGENTS.md).

## Contents

- [Overview](#overview)
- [Accounts, team & access](#accounts-team--access)
- [Dashboard](#dashboard)
- [Clients](#clients)
- [Tasks](#tasks)
- [Soft delete](#soft-delete)
- [Comments, attachments & activity](#comments-attachments--activity)
- [Audit log](#audit-log)
- [Notifications](#notifications)
- [Ask AI (chat assistant)](#ask-ai-chat-assistant)
- [Scheduled jobs & reminders](#scheduled-jobs--reminders)
- [Settings & appearance](#settings--appearance)
- [Error pages](#error-pages)
- [Logging](#logging)
- [Navigation & keyboard](#navigation--keyboard)
- [Email](#email)
- [Interface & behavior](#interface--behavior)
- [Data model reference](#data-model-reference)
- [Configuration reference](#configuration-reference)

## Overview

A small single-tenant CRM: **Clients** and **Tasks**, with comments,
file attachments, an activity log, in-app notifications, and a read/write
**Ask AI** chat assistant. "Single-tenant" means one running instance serves
one team — there's no workspace/org switcher; whoever signs up first is the
owner, and everyone else joins by invite.

Server-rendered [Bottle](https://bottlepy.org) + [peewee](http://docs.peewee-orm.com)
over SQLite, styled with the [oat.css](https://oat.style) design-system
library. No build step, no Node, no pip install for the app itself — bottle
and peewee are vendored as plain `.py` files in `vendor/`. See the
[README](README.md) for how to run it.

## Accounts, team & access

- **First run**: `/register` creates the first user, who becomes **owner**,
  and seeds two sample clients and three sample tasks so the app isn't an
  empty screen. Once any team member exists, `/register` redirects to
  `/login` — there's no open sign-up after that.
- **Roles**: `owner`, `admin`, `member` (`TeamMember.role`). The only
  role-gated action is **sending an invite** (owner/admin — `@require_role`
  in `pages/auth.py`). Everything else (Clients/Tasks CRUD, comments,
  attachments) is open to any signed-in team member; roles are otherwise
  informational (shown in the Settings team table).
- **Inviting**: an owner/admin enters an email on the Settings page, which
  emails a tokenized `/invite/<token>` accept link (see [Email](#email) for
  what happens if email isn't configured). The invite's role defaults to
  `member`. Accepting sets a name + password and logs the new user in.
- **Password reset**: `/forgot-password` emails a time-limited
  `/reset-password/<token>` link. Always shows the same "if that email has
  an account…" message either way, so the form can't be used to enumerate
  registered emails.
- **Sessions**: a single signed, HMAC'd cookie (see
  [Interface & behavior](#interface--behavior)) — no server-side session
  store.

## Dashboard

`/` — three stat tiles (open clients, open tasks, done tasks) and two
"recent" lists (5 most recently created clients / tasks, each with its
status badge), linking into the respective detail pages.

## Clients

`/clients` — a searchable table (name/email/company, via `?q=`) of
active clients (not archived, not deleted — see below). **New client**
opens a modal dialog (title bar, ✕ close, Cancel/Add buttons) with name
(required), email, phone, company, status (lead/active/inactive), and
freeform notes. Clicking a row opens `/clients/<id>`:

- Edit form for every field above, a file-upload **Attachments** section,
  and the shared **Comments**/**Activity** sidebar (see below).
- **Archive**: sets `archived_at`. A deliberate, one-way "done with this"
  action — no unarchive button, but the record (and everything that
  references it) is otherwise untouched.
- **Delete**: a *soft* delete (see [Soft delete](#soft-delete) below) —
  sets `deleted_at` rather than removing the row, and is reversible. The
  button flips to **Restore** once a client is deleted.
- Lists that client's own tasks isn't shown on this page directly — tasks
  reference their client from the Tasks side (`Task.client`).

"View deleted clients" (bottom of `/clients`) lists exactly the
soft-deleted ones — archived or not, since deletion is the more final
state being looked at here — each with its own **Restore** button, no
need to open the detail page.

## Tasks

`/tasks` — a kanban board grouped by status (**To Do** / **In Progress** /
**Done**), each column headed by a dot + count, over active (not archived,
not deleted) tasks. **New task** opens the same kind of modal dialog:
title (required), client (optional, from a select of active clients),
assignee (optional, from the team), status, description. Cards can be
dragged between columns (plain HTML5 drag-and-drop, no library) to change
status without opening the task; on drop, the whole column's new order is
POSTed to `/tasks/reorder`, which only ever touches `status`/`position`,
so it can't clobber anything else about a task. Clicking a card opens
`/tasks/<id>`:

- Edit form for every field above, **Attachments**, and the shared
  **Comments**/**Activity** sidebar.
- **Archive** / **Delete** (flips to **Restore**) — same two distinct
  actions as Clients, above.
- Reassigning a task to someone other than yourself sends them an
  **assignment** notification (see [Notifications](#notifications)).

"View deleted tasks" (bottom of `/tasks`) is a plain list, not the kanban
board — grouping by status is the wrong frame for "what did we
soft-delete" — with the same per-row **Restore**.

## Soft delete

A generic mechanism on `BaseModel` (`models.py`), not specific to Clients
or Tasks: a model opts in with `soft_delete = True` and its own nullable
`deleted_at` column, and from then on `delete_instance()` never actually
removes the row — it sets `deleted_at` and saves, same as any other
update. `restore()` is the other half (`deleted_at` back to `None`).
Currently on: `Client`, `Task`.

Distinct from **Archive**: archiving is a deliberate, one-way, visible
status a person sets; a soft delete is what happens whenever *anything* —
this UI's Delete button, an Ask AI tool call, a future script — tries to
delete one of these records at all. Because it goes through `save()`, a
delete or a restore shows up in the [Audit log](#audit-log) as an
ordinary field update (`deleted_at` changing), not a fabricated "deleted"
entry for a row that's still actually there.

Query sites decide for themselves whether to filter `deleted_at` out —
same as every existing query already filters `archived_at` — this
mechanism only changes what `delete_instance()` does, not what any
`SELECT` returns.
- Saving records an `updated` (or `status_changed`, if the status field
  changed) activity entry and shows a **"Task updated."** toast.

Search (`?q=`) matches title or description.

## Comments, attachments & activity

Shared across Clients and Tasks (`Comment`/`Attachment`/`Activity` are keyed
by `subject_type` + `subject_id` rather than having a separate table per
model):

- **Comments**: plain text (rendered with `white-space: pre-wrap`, not
  Markdown — only Ask AI's replies get Markdown rendering). Any signed-in
  user can comment; only the comment's own author can delete it.
- **Attachments**: uploaded to local disk under `UPLOAD_FOLDER`
  (default `./uploads/<subject_type>/<subject_id>/`), 10 MB max per file.
  Download streams the file back with its original filename; delete removes
  both the DB row and the file.
- **Activity**: an append-only log (`created` / `updated` / `status_changed`
  / `archived` / …) shown oldest-first-hidden, newest-first-shown in the
  detail page's sidebar. Written by `record_activity()` — see
  [AGENTS.md](AGENTS.md) if you're adding a new mutation that should log one.

## Audit log

`/audit` — a paginated (50/page), global, newest-first feed of every
create/update/delete on a model with `audit_trail = True` (`Client`,
`Task`, and `User`). Unlike Activity above, nobody calls anything to write
one of these — `BaseModel.save()`/`delete_instance()` in `models.py` write
an `AuditLog` row automatically:

- **Created**: every field's value, `old` uniformly `None`.
- **Updated**: only the fields that actually changed, each as
  `{"old": ..., "new": ...}` — a field reassigned to the same value it
  already had doesn't show up as a change.
- **Deleted**: every field's value, `new` uniformly `None` — what the row
  looked like right before it was gone.

Each entry links to its subject (`Client`/`Task` land on their detail page;
`User` has none of its own, so it lands on `/settings`'s team roster
instead) and names the actor, best-effort from the current session
(`None`/"System" outside a request — seeding, a script).
`audit_exclude` on a model (`User.audit_exclude = {"password_hash"}`) keeps
named fields out of every entry entirely, never fetched or serialized —
add a model to the audit trail with a secret field, add it there too.

## Notifications

`/notifications` — a flat, newest-first list. Three kinds today:
**assignment** (you were made a task's assignee), **comment** (someone
commented on something — not currently wired to any route, but the
`notify()` helper and template already handle the kind), and **reminder**
(see [Scheduled jobs & reminders](#scheduled-jobs--reminders)). Unread rows get a
dot marker; "Mark all read" and per-row "Mark read" are both one POST.
There's no unread-count badge in the sidebar nav yet.

## Ask AI (chat assistant)

`/chat` — a single running thread per user (no multi-thread UI; `ChatThread`
is 1:1 with `User`). Two interchangeable backends, picked automatically:

- **OpenAI-compatible cloud API** if `OPENAI_API_KEY` is set
  (`OPENAI_MODEL`, `OPENAI_BASE_URL` configurable).
- **Local Ollama** otherwise (`OLLAMA_HOST`, `OLLAMA_MODEL` configurable) —
  the page shows a "Backend: local Ollama" notice with the env vars to check
  if nothing answers.

It's a tool-calling agent (`ai.py`) over both **read** tools (`list_clients`,
`get_client`, `list_tasks`, `get_task`, `search`) and **write** tools
(`create_client`, `update_client`, `archive_client`, `delete_client`,
`restore_client`, `create_task`, `update_task`, `archive_task`,
`delete_task`, `restore_task`, `create_reminder` — see
[Scheduled jobs & reminders](#scheduled-jobs--reminders)) — up to
`MAX_TOOL_ROUNDTRIPS` (6) per message. `delete_*`/`restore_*` are the soft
delete from [Soft delete](#soft-delete), not a real removal; `archive_*`
is separate and one-way, no matching restore tool.
Read tools execute immediately; a write tool call **pauses the turn** and
shows a confirmation banner ("The assistant wants to: …") with Confirm/
Cancel buttons before anything is actually written — `ChatThread.pending_*`
persists the paused state across requests so refreshing the page doesn't
lose it. Confirming re-enters the same tool loop with the write executed
(and its own activity/notification side effects, same as doing it by hand);
cancelling drops it and tells the model so.

Model replies are rendered through a small hand-rolled Markdown-to-HTML
renderer (`ai.py: render_markdown`) — the only place in the app that
happens; everything else (comments, descriptions, notes) is plain text.

## Scheduled jobs & reminders

`jobs.py` runs a single background thread (started from `app.py` at process
startup) that polls the database every 30 seconds and runs whichever
registered jobs are due — a small stdlib-only (`threading` + `time`)
scheduler, not a task queue.

The one job today is firing **reminders**: a `Reminder` (message, `remind_at`,
optionally linked to a client or task) is created only via Ask AI's
`create_reminder` tool — "remind me about Acme in 2 days" or "remind me about
this task in 10 minutes" — there's no manual form for it. Once `remind_at`
passes, the job creates a **reminder** notification (see
[Notifications](#notifications)) for whoever asked, emails them the reminder
text, and logs a `reminder_fired` activity entry if it was linked to a client
or task. There's no page to browse or cancel a reminder before it fires.

A fired reminder pops up as a toast wherever the person is in the app, not
just on their next click: a small polling script in `layout.html` hits
`GET /notifications/poll` every 20 seconds and shows whatever comes back via
the same `ot.toast()` used for flash messages. A page that's already open
gets the toast within that window; `pages/notifications.py`'s
`_toast_due_reminders` `before_request` hook is the no-JS/first-load
fallback for everyone else. Either path marks the reminder's notification
read — for a reminder, seeing the toast *is* the read receipt, unlike
assignment/comment notifications, which still wait for an explicit "mark
read" on the /notifications page.

## Settings & appearance

`/settings`:

- **Appearance** — a Dark/Light toggle. See
  [Interface & behavior](#interface--behavior) for how it's implemented.
- **Change password** (or **Set a password**, if the account somehow has
  none — the form always asks for the current password since there's no
  alternate sign-in method here to fall back on) — collapsed behind a
  `<details>`/`<summary>` disclosure. A failed or successful submit
  redirects to `#change-password` so the disclosure re-opens instead of
  hiding its own result.
- **Team** — every member's name/email/role, plus (owner/admin only) an
  invite form and a list of pending invite emails, laid out side by side
  (same `.detail-cols` two-column grid as the client/task detail pages;
  stacks on narrow screens).
- **Environment** (`/settings/env`, owner only) — a form for this instance's
  `.env` file: API keys, email sending, dev-server settings, etc. The form
  is generated from `.env.example` itself (section headers + one commented
  `# KEY=example` line per setting are parsed into fields; no separate list
  to keep in sync), pre-filled from `os.environ`. Saving rewrites `.env`,
  commenting a field back out if it's left blank, and updates `os.environ`
  for the current process — though settings read once at import time
  (ports, model names, file paths) still need a restart to take effect.
  Key-/secret-/token-/password-shaped vars render as masked fields with a
  Show/Hide toggle. The first owner is sent here (`?onboarding=1`, adds a
  "Skip for now" link to the dashboard) right after `/register` — everyone
  else reaches it from the Settings page.

## Error pages

`app.py`'s 404/403/500 handlers all render the same `error.html`, styled as
a small card (icon + monospace `ERR_<code>` tag, heading, message, a
divider, then actions) instead of a bare heading and link. The icon and its
tinted badge vary by code — a compass for 404, a padlock for 403, an alert
triangle for 500 — reusing this app's existing status-color tokens
(muted/warning/danger) rather than introducing new colors. A **Go home**
button is always shown; 500 also gets a **Try again** button that reloads
the same URL. Deliberately dependency-free (no icon library, no JS): the
500 handler already rolls back the DB and guards the render itself against
a second crash (see its comment in `app.py`), so the page most likely to
render right after something broke shouldn't lean on anything else that
could fail with it.

## Logging

`app.py`'s `_configure_logging()` sends every `logging.getLogger(...)` call
in the app (an access-log line per request — see below — jobs.py's
reminder-job failures, app.py's own 500 handler) to `logs/app.log`, plus the
console so `python3 app.py`/`make run` still show it during dev. Rotating:
once `app.log` hits ~1MB it's rolled to `.1` (bumping any existing `.1`/`.2`
up a slot), keeping at most 3 old files alongside the active one — a
long-lived self-hosted instance can't fill its disk from an ever-growing
log. Level is `DEBUG` when the `DEBUG` env var is on, `INFO` otherwise (see
[Configuration reference](#configuration-reference)); peewee's own
per-query DEBUG logging is explicitly silenced to `WARNING` regardless, so
turning `DEBUG` on doesn't drown the log in SQL statements. `logs/` is
gitignored and excluded from `make dist`.

An `after_request` hook (`_log_request`) writes one line per request —
`GET /clients -> 200 (4.2ms)` — status and timing included, sourced from
`response.status_code` in the normal case and hardcoded to 500 for a
genuine unhandled exception, whose status bottle only applies to
`response` *after* this hook already ran (see the hook's own comment).

## Navigation & keyboard

- **Esc** goes "back" a level: a detail page (`/clients/<id>`,
  `/tasks/<id>`) goes to its list; a list page (`/clients`, `/tasks`) goes
  to the dashboard. Opt-in per page via `<body data-esc-back>`; pages that
  don't set it (dashboard, chat, settings, notifications) aren't affected.
  Suppressed while a modal dialog is open or while typing in a field.
- **Mobile nav**: below 860px, the sidebar becomes an off-canvas drawer
  (hamburger button, backdrop, slide-in) — pure CSS (`:checked` on a hidden
  checkbox), no JS.

## Email

Resend's HTTP API (`utils.py: Mailer`), used for invite, password-reset,
and reminder emails. Without `RESEND_API_KEY` set, sending raises
`MailerError`; the invite/reset call sites catch that and fall back to
putting the link directly in a flash message ("Share this link instead: …")
so the flow still works without email configured — handy for local dev.
`jobs.py`'s reminder job instead just logs the failure and moves on (there's
no request/flash to fall back to from a background thread) — the in-app
notification still goes out either way.

## Interface & behavior

- **Theme**: dark by default; the Settings toggle writes
  `localStorage['scalar-core:theme']` and sets `document.documentElement
  .style.colorScheme`. `style.css`'s `:root` tokens are all defined with CSS
  `light-dark()`, which resolves off that `color-scheme` value — flipping it
  is the entire retheme, no second `[data-theme]` override block to keep in
  sync. A tiny inline script in `layout.html`'s `<head>` applies the saved
  preference before the stylesheets load, so there's no flash of the wrong
  theme. Light mode isn't a flat inversion of dark: the brand indigo
  (`--primary`) stays constant, but the pale accent colors
  (`--danger`/`--success`/`--warning`) get deepened for legibility as text
  on a white background, and each one's `-foreground` pairing (used for
  solid-fill buttons like **Archive**) flips alongside it.
- **Installable**: a manifest (`static/manifest.json`) plus favicon/
  apple-touch-icon links and `theme-color`/`apple-mobile-web-app-*` meta
  tags in `layout.html`'s `<head>` make this installable as a standalone app
  from a browser's "Install"/"Add to Home Screen" prompt — no service
  worker, just enough for the OS to treat it as an app icon. Same brand mark
  as admin's own (`admin/static/favicon.svg` + `icon-*.png`, regenerated via
  `admin/scripts/make_icons.py`), copied in rather than shared since each
  app serves its own `static/`.
- **Toasts**: every flashed message (`utils.py: flash()`) renders as an
  `oat.toast()` popup (top-right, colored by category) instead of an inline
  banner — see `templates/_toasts.html`. Embeds the message as a JS string
  literal via `app.js_string()`, which escapes `</script>`-breakout
  characters, since some messages interpolate user-supplied text (a
  filename, a task title).
- **Modals**: New Client/New Task open a native `<dialog>` (oat.css themes
  it as a centered card with a dimmed backdrop) via the declarative
  `commandfor`/`command="show-modal"` attributes — `oat.min.js` wires that
  up (using the browser's native support when present, its own click
  handler otherwise), so there's still no app-authored interaction JS for
  it.
- **Sidebar**: sticky + viewport-height, so the user name/logout footer at
  its bottom stays in view regardless of how tall the current page's
  content is.
- **View transitions**: `@view-transition { navigation: auto }` opts every
  same-origin navigation into the browser's cross-document transition
  (Chromium only; a no-op elsewhere). The default root crossfade is
  disabled (`::view-transition-old/new(root) { animation: none }`) — its
  `mix-blend-mode: plus-lighter` blend flashes white on a dark theme
  whenever the two page snapshots don't match exactly.
- **Chat layout**: the Ask AI page is a `flex-direction: column` page
  (`.chat-page`) sized to the viewport minus the shell's own padding, with
  the message log as the one `flex: 1` scrolling region — not a fixed `vh`
  guess, so it adapts to whatever else is on the page (the Ollama notice,
  the pending-confirmation banner) instead of clipping or leaving dead
  space.

## Data model reference

All models in `models.py`; schema changes are applied by migrations
(`migrations/`, peewee-migrate — vendored) via `run_migrations()` — see
[AGENTS.md](AGENTS.md) for when that runs and how to add one. This is
pro's one deliberate divergence from `core/`, which still applies schema
idempotently at startup with no migrations directory at all.

| Model | Purpose |
|---|---|
| `User` | Account: email, password hash, name. Audited (`audit_trail = True`, `password_hash` excluded — see [Audit log](#audit-log)). |
| `TeamMember` | 1:1 with `User`. `role`: owner / admin / member. |
| `Invite` | Pending join invite: email, token, role, accepted flag. |
| `PasswordReset` | Single-use, time-limited reset token. |
| `Client` | name, email, phone, company, `website` (added by `migrations/002_client_website.py` — a demo field for the migration pattern, not wired into any form yet), status, notes, archived via `archived_at`. Audited, and soft-deleted via `deleted_at` — see [Soft delete](#soft-delete). |
| `Task` | title, description, status, `assignee`/`client` (both optional FKs), `position` (board ordering), archived via `archived_at`. Audited, and soft-deleted via `deleted_at` — see [Soft delete](#soft-delete). |
| `Comment` | Generic over `subject_type`+`subject_id` (client/task). Plain text. |
| `Attachment` | Generic over subject. Local-disk file, metadata in DB. |
| `Activity` | Generic over subject. Append-only log: verb + JSON payload. |
| `AuditLog` | Generic over subject. Written automatically, not by a route calling something — see [Audit log](#audit-log). |
| `Notification` | Per-user. `kind` + JSON payload, `read_at`. |
| `Reminder` | message, `remind_at`, optional client/task link, `sent_at` (null until `jobs.py` fires it). Created only via Ask AI. |
| `ChatThread` | 1:1 with `User`. Holds `pending_*` fields for an in-flight write confirmation. |
| `ChatMessage` | user/assistant turns in a thread. |

## Configuration reference

See `.env.example` for the full list with defaults; the short version. All
of these can also be edited from the app itself at `/settings/env` (owner
only) — see [Settings & appearance](#settings--appearance).

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Signs the session cookie. Set a real value in production. |
| `SQLITE_PATH` | DB file location (default `app.db` next to `models.py`). |
| `UPLOAD_FOLDER` | Attachment storage root (default `./uploads`). |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | Ask AI, cloud backend. |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Ask AI, local backend (used when `OPENAI_API_KEY` is unset). |
| `RESEND_API_KEY` / `RESEND_FROM` | Invite/reset emails. Unset = flash a shareable link instead. |
| `HOST` / `PORT` / `DEBUG` | Dev server (see `Makefile`/`app.py`). |
