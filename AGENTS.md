# AGENTS.md — working on this app

Guidance for whoever (human or agent) changes this project next.

## Keep DOCUMENTATION.md in sync

**Any change that adds, removes, or changes a user-visible feature, route,
model field, or config var must update [DOCUMENTATION.md](DOCUMENTATION.md)
in the same change.** That file is the single source of truth for "what
this app does" — treat a feature change without a matching doc update as
incomplete, the same way you'd treat it as incomplete without the code.

- Find (or add) the relevant section and update the **Contents** list.
- New model/field → update the **Data model reference** table.
- New env var → update the **Configuration reference** table (and
  `.env.example`).
- Skip it for pure refactors, bug fixes that just restore already-documented
  behavior, or internal-only helper changes with no user-visible effect.
- If you're unsure whether something is user-visible enough to document,
  document it — a stale README is worse than a slightly over-eager one.

## Keep Ask AI in sync with the app

**Any new record type, field, or mutation added to the UI should get a
matching tool in `ai.py`, in the same change** — a create/update(/archive)
tool mirroring the new `pages/` route's fields and validation exactly, a
`TOOLS_SCHEMA` entry, a `_DISPATCH` entry, and (if it mutates anything) a
`_MUTATING_TOOLS` entry plus a `_describe_tool_call` branch for the
confirmation banner. `SYSTEM_PROMPT` should mention it too. The point of the
chat assistant is that it's a second way to *use* the app, not a smaller
subset of it — a feature only reachable by clicking through `pages/` is a
feature Ask AI can't help with. A field that exists on the model and in the
form but not in `ai.py`'s tool signature is exactly the kind of gap that
makes the assistant tell someone "I can't do that" for something the app
plainly supports.

- Read tools (`list_*`, `get_*`, `search`) execute immediately; write tools
  always pause for human confirmation (see `_MUTATING_TOOLS` in `ai.py`) — a
  new mutation is never the exception to that.
- This is also where a new tool's `search.index_entity(...)` call goes, the
  same as every other write path — see `search.py`.

## Consider tests/ too

**A new feature or a behavior change should make you ask whether `tests/`
needs a new journey, or an existing one needs updating** — not every change
needs one (see below), but skipping the question is how the folder goes
stale and stops meaning anything.

- `tests/` holds a *small* number of high-level, readable end-to-end
  journeys (see `tests/README.md`) — not unit tests, and not one per route.
  Add a new file only for a genuinely new user-facing story (a new kind of
  record, a new page someone would actually use). A variation on a journey
  that already exists — a new field on a form already covered, a tweak to
  wording — extends that file's existing test method instead.
- If a change alters what an *existing* journey's assertions expect (a
  renamed status label, a moved button, a changed redirect target), update
  that test in the same change. A red test after a deliberate change is
  noise, not a safety net — and a green one that no longer checks anything
  real is worse than no test at all.
- Run `python3 tests/run_all.py` before considering a change done if you've
  touched a route, template, or model any journey exercises.
- Skip it for pure refactors, internal-only helpers, or anything already
  covered incidentally by an existing journey's path through the app.

## Conventions specific to this codebase

- **Zero pip dependencies beyond gunicorn** (dev-only, for autoreload).
  `bottle`/`peewee` are vendored as plain `.py` files in `vendor/`, and so is
  `peewee-migrate` (plus the slice of `playhouse` it needs) — see the schema
  bullet below and `vendor/LICENSE.peewee-migrate`. Don't add a pip
  dependency without a good reason; if you must, note it in
  `requirements.txt` the way the gunicorn line already does. If it's a
  genuinely small, MIT/BSD-equivalent library, vendoring it (a real copy in
  `vendor/`, its license alongside) is preferred over a pip dependency —
  that's the whole reason this app has no `requirements.txt` entries beyond
  gunicorn today.
- **No app-authored JS beyond what's declaratively necessary.** Mobile nav
  uses the checkbox hack; New Client/Task modals open via
  `commandfor`/`command="show-modal"`, wired by the already-vendored
  `oat.min.js` (native browser support when present, its own click handler
  otherwise) — not custom JS. The Esc-navigation listener, the theme toggle,
  and the reminder-toast poll (`layout.html`'s `setInterval` hitting
  `/notifications/poll` — see [Scheduled jobs & reminders](DOCUMENTATION.md#scheduled-jobs--reminders))
  are the three deliberate exceptions, because there's no declarative way to
  do any of them; each is small, single-purpose, and commented inline in
  `layout.html`/`settings.html`. Follow that same shape for anything else
  that genuinely needs script — don't reach for a framework or bundler.
- **Schema changes go through `migrations/`** (peewee-migrate — vendored,
  see the dependency bullet above), applied by `run_migrations()` in
  `models.py`. This is pro's one deliberate divergence from core's
  idempotent-check-at-startup approach (`core/models.py`'s `ensure_schema()`)
  — core stays as-is; don't backport migrations there. To add a schema
  change: edit the model in `models.py`, then add
  `migrations/NNN_description.py` (next number, `migrate()` +
  `rollback()`) — see `migrations/002_client_website.py` for the pattern of
  adding a column, `migrations/001_initial.py` for creating a table.
  Run `make db-migrate` (or just restart `python3 app.py`, which auto-
  migrates on every dev run — see app.py) to apply it.
  **Migrations are NOT applied automatically under `gunicorn app:app`** —
  a production/self-hosted deploy runs `make db-migrate` as its own
  explicit step before starting the server (same split as `../admin/`);
  auto-migrating on every gunicorn worker's own import would mean
  concurrent workers racing to apply the same pending migration.
- **Bottle template gotcha:** any source line whose first non-whitespace
  character is `%` is parsed as Python — including inside an HTML comment.
  Don't write something like a `%rebase(...)` call as documentation text on
  its own line in a `.html` template; bottle will try to execute it as a
  statement and throw a `SyntaxError`. (Hit exactly this once — see the git
  history around the Esc-navigation feature.) If you need to reference
  template syntax in a comment, keep it mid-line, not line-initial.
- **Theming:** `style.css`'s `:root` tokens use CSS `light-dark()` keyed off
  the `color-scheme` property, not a `[data-theme="light"]` override block.
  Add new color tokens the same way — `--foo: light-dark(lightVal,
  darkVal);` — rather than introducing a second block to keep in sync. A
  value that can't be a plain `<color>` (e.g. a multi-part `box-shadow`)
  needs decomposing into `light-dark()`-driven intermediate custom
  properties first — see `--shadow-card` for the pattern.
- **Flash messages** (`utils.py: flash()`) always render as `oat.toast()`
  popups via `templates/_toasts.html`, never an inline banner. Route any new
  user-facing confirmation/error through `flash()`, not a hand-rolled
  message element.
- **Generic-over-subject models** (`Comment`/`Attachment`/`Activity`) key
  off `subject_type` + `subject_id` rather than a table per model. A new
  commentable/attachable thing reuses these as-is; don't add a parallel
  `TaskComment` table.
- **`BaseModel`'s opt-in flags** (`models.py`) — `audit_trail = True` and
  `soft_delete = True`, both off by default, both currently on for
  `Client`/`Task` (`audit_trail` also on `User`, `password_hash`
  excluded). A new model wanting either just sets the flag; `soft_delete`
  also needs its own nullable `deleted_at` column (a migration, see
  `migrations/003_soft_delete.py`). Don't hand-roll a parallel mechanism
  on a specific model (a bespoke `is_deleted` boolean, a one-off change
  log) — extend the shared one instead, the same reasoning as the
  generic-over-subject bullet above. `soft_delete` and `archived_at`
  (a plain column some models have, not a `BaseModel` mechanism) are
  deliberately different things: archiving is a one-way, visible status a
  person sets; soft delete is what happens whenever *anything* — this
  UI's Delete button, an Ask AI tool, a future script — tries to delete
  one of these records at all, and it's reversible (`restore()`).
  Query sites decide for themselves whether to filter `deleted_at`/
  `archived_at` out — neither mechanism touches what a `SELECT` returns
  on its own.
- **Every route requires a logged-in user by default** — enforced by a
  `before_request` hook (`_require_login_hook` in `pages/__init__.py`), not
  a per-route decorator. A new route needs *no* annotation to be protected.
  If it must be reachable by a signed-out visitor (a new auth-flow page,
  say), add its `name=` to `PUBLIC_ROUTES` in `utils.py` — forgetting this
  for a route that's supposed to be public shows up immediately as an
  unwanted redirect to `/login`, so it's a load-bearing list, not
  optional bookkeeping. A route with its own non-session auth (like
  `/internal/ai-command`'s `require_internal_secret`) goes in
  `SESSION_INDEPENDENT_PATHS` instead, keyed by path since it's checked
  before routing.

## Verifying a change

- **Templates**: bottle's `SimpleTemplate` can compile a template directly
  (no server needed) to catch syntax errors — `python3 -c "from bottle
  import SimpleTemplate; SimpleTemplate(open('templates/x.html').read())"`
  (run with `vendor/` on `sys.path`).
- **Visual/behavioral changes**: run against a scratch database —
  `SQLITE_PATH=/tmp/scratch.db PORT=8123 python3 app.py` — rather than the
  real `app.db`, so local data doesn't need resetting afterward.
- **End-to-end**: `python3 tests/run_all.py` — see [Consider tests/
  too](#consider-tests-too) above for when a change should add to or update
  what's in there rather than just running it as-is.
- **A new migration**: apply it (`SQLITE_PATH=/tmp/scratch.db make
  db-migrate`) against a scratch database seeded from before it existed
  (copy a pre-migration `app.db`, or just an empty file for a brand new
  one), then check the column/table landed as expected. Worth rolling back
  too if the migration is anything more than a straightforward additive
  column/table — `rollback()` gets a lot less exercise than `migrate()` in
  practice and is where a mistake tends to hide.
