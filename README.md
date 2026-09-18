# Scalar Enterprise

A small single-tenant CRM template — Clients, Tasks, comments, attachments,
notifications, and a read/write **Ask AI** chat assistant — meant as a
starting point to fork and build on, not as a product of its own.

Server-rendered [Starlette](https://www.starlette.io) (ASGI, async) +
[peewee](http://docs.peewee-orm.com) over Postgres, via
[playhouse.pwasyncio](https://docs.peewee-orm.com/en/latest/peewee/asyncio.html)
— real async I/O over [asyncpg](https://magicstack.github.io/asyncpg/), not a
thread-pool shim. Templates are [Jinja2](https://jinja.palletsprojects.com).
`asgi.py` is a thin Bottle-shaped compatibility layer over Starlette
(ambient `request`/`response`, `@app.route(...)`/`@app.hook(...)`) — see its
own docstring for why it exists. Real pip dependencies throughout (see
`requirements.txt`) — unlike core/pro, nothing here is vendored. Styling
comes from the [oat.css](https://oat.style) design-system library
(vendored as static assets — a frontend/CSS concern, unrelated to the
Python dependency story above), retthemed in `static/style.css`.

- **What's built:** [DOCUMENTATION.md](DOCUMENTATION.md)
- **Working on this app?** Read [AGENTS.md](AGENTS.md) first — in particular,
  DOCUMENTATION.md needs updating alongside most changes.

## Requirements

- Python 3.10+ (the code uses `X | None` type syntax).
- A local Postgres server, with a `scalar` database created ahead of time:
  `createdb scalar` (or `psql -c 'CREATE DATABASE scalar'`). Defaults assume
  `localhost:5432`; see [Configuration](#configuration) to point elsewhere.
- `pip install -r requirements.txt`.
- Optional integrations (AI chat, outbound email) degrade gracefully when
  unconfigured — see [Configuration](#configuration) below.

## Quick start

```bash
createdb scalar         # once, if it doesn't already exist
cp .env.example .env    # optional — sensible defaults work without it
pip install -r requirements.txt
python3 app.py
```

Then open http://localhost:8000 and register the first account (it becomes
the team's owner, and gets a couple of sample clients/tasks seeded in).

### Dev server with autoreload

`python3 app.py` alone doesn't reload on code changes. For that, run it
under uvicorn instead:

```bash
make db-migrate  # apply migrations/ first — uvicorn workers assume the
                 # schema's already current, they don't check
make             # same as: make run
```

`HOST`/`PORT`/`WORKERS` are overridable, e.g. `make PORT=8080`. Template
edits show up on the next request either way, without a restart (Jinja2's
`auto_reload`, on whenever `DEBUG=1` — see app.py) — only `.py` changes need
uvicorn's `--reload` (or a manual restart, if running `python3 app.py`
directly). `python3 app.py` itself always applies any pending migration on
every run, so `make db-migrate` is only something you think about under
uvicorn.

### REPL

`make repl` (or `python3 repl.py`) drops into an interactive shell with
every model already imported — `User`, `Client`, `Task`, etc. — for poking
at data by hand. Supports top-level `await`, since queries go through
playhouse.pwasyncio (see models.py): `await User.aget_or_none(User.id == 1)`,
`await Client.acreate(name="Acme")`. Doesn't start the app itself (no
routes, no reminder-polling task), just binds the database.

## Configuration

Everything is optional — copy `.env.example` to `.env` and fill in only
what you need. Full reference in
[DOCUMENTATION.md § Configuration reference](DOCUMENTATION.md#configuration-reference).
Highlights:

- **Database** defaults to a local Postgres instance's `scalar` database on
  the standard port (`PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD`) —
  see Requirements above for creating it.
- **Ask AI** works out of the box against a local [Ollama](https://ollama.com)
  install (`OLLAMA_HOST`/`OLLAMA_MODEL`); set `OPENAI_API_KEY` to use an
  OpenAI-compatible cloud API instead.
- **Invite/password-reset emails** need `RESEND_API_KEY` — without it, the
  app shows the invite/reset link directly in the UI instead of emailing it,
  so the flow still works for local dev.
- **`SECRET_KEY`** has a dev-only default; set a real value before deploying
  anywhere real (it signs the session cookie).

## Project layout

```
app.py        # Starlette app factory, hooks, template rendering, entrypoint
asgi.py       # Bottle-shaped compatibility layer over Starlette (request/response, routing)
pages/        # every route (no blueprints — one file per feature area)
models.py     # peewee models (async, via playhouse.pwasyncio) + run_migrations()
migrations/   # schema history (peewee-migrate) — see AGENTS.md to add one
migrate.py    # `make db-migrate` entry point
repl.py       # `make repl` entry point — async-capable interactive shell, models preloaded
utils.py      # session/CSRF/password hashing/email/flash — hand-rolled, stdlib only
ai.py         # Ask AI: async tool-calling agent loop + Markdown renderer
jobs.py       # background reminder-polling task (asyncio, scheduled on ASGI startup)
templates/    # Jinja2 (.html) views
static/       # style.css (app-specific) + vendor/ (oat.css/js — frontend assets)
uploads/      # attachment storage (gitignored; see UPLOAD_FOLDER)
```

Schema changes go through `migrations/`, not an idempotent startup check —
pro's one deliberate divergence from core's zero-migrations-framework rule.
`peewee-migrate` is a real pip dependency (see Requirements above), same as
everything else here — nothing in this tier is vendored.

Every database call is async (`Model.aget()`/`.acreate()`/`instance.asave()`/
`instance.adelete_instance()`/`query.aexecute()`/`db.list()`/`db.first()`/
`db.count()`/`db.exists()` — see models.py's own docstring) — the plain sync
peewee methods still exist underneath but raise outside a "greenlet bridge"
(`db.run(...)`), which only migrate.py's own migration-runner needs.

See [DOCUMENTATION.md](DOCUMENTATION.md) for what each page/feature actually
does, and [AGENTS.md](AGENTS.md) for conventions to follow when changing any
of this.
