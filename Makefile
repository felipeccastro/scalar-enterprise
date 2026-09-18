# Dev server. `make` (or `make run`) starts uvicorn with autoreload (picks
# up .py file changes; template edits already show up on the next request
# without a restart, via app.py's DEBUG flag — see app.py's Jinja2
# Environment's auto_reload). Same shape of command a provisioned instance
# runs under (see ../admin/launcher/provisioner.py), just invoked locally
# instead of by the launcher. WORKERS>1 needs its own process-per-worker
# flag (--workers) instead of gunicorn's; left at the single-worker default
# since jobs.py's scheduler (see app.py's app.on_startup) would otherwise
# run once per worker — see jobs.py's own docstring.
#
# Migrations are NOT applied here — uvicorn workers importing app:app never
# reach its __main__ block (see app.py), so run `make db-migrate` first.
# `python3 app.py` (this app's own dev server, not this target) still
# auto-migrates on every run, same as before.
HOST ?= 0.0.0.0
PORT ?= 8000
WORKERS ?= 1
BACKUP_DIR ?= backups
TS := $(shell date +%Y%m%d-%H%M%S)

.PHONY: run dist db-migrate backup repl test
run:
	python3 -m uvicorn app:app \
		--host $(HOST) \
		--port $(PORT) \
		--workers $(WORKERS) \
		--reload

# Apply pending migrations/ (see models.py: run_migrations()). Required
# before `make run` the first time any migration lands after a deploy —
# uvicorn workers assume the schema is already current, they don't check.
db-migrate:
	python3 migrate.py

# Interactive shell with every model already imported (see repl.py) — for
# poking at data by hand: `User.select()`, `Task.get_by_id(1)`, etc.
# Migrations are NOT applied here, same caveat as `make run` above.
repl:
	python3 repl.py

# Every user-journey test in tests/, each run under pytest in its own
# process against its own throwaway Postgres database — see
# tests/_harness.py and tests/run_all.py.
test:
	python3 tests/run_all.py

# Package a ready-to-run copy of this app — the whole directory. Unlike
# core/pro, there's no seeded database file to bundle — the database lives
# on Postgres, not in this directory — so unzip-and-run still needs its own
# `createdb scalar` (or equivalent) and `make db-migrate` before serving
# anything. .env is excluded: it's gitignored and per-install already (see
# .env.example) — without one, utils.py falls back to its documented dev
# SECRET_KEY, same as a fresh git clone.
#
# dist/ is gitignored. Unlike pro's own dist target, nothing in ../admin
# serves this yet (../admin/routes/downloads.py only knows about pro.zip
# today) — this just produces the zip locally, for now.
dist:
	mkdir -p dist
	rm -f dist/enterprise.zip
	cd .. && zip -rq enterprise/dist/enterprise.zip enterprise \
		-x 'enterprise/.env' \
		-x 'enterprise/__pycache__/*' -x 'enterprise/*/__pycache__/*' -x '*.pyc' \
		-x 'enterprise/logs/*' \
		-x 'enterprise/dist/*'
	@echo "Built dist/enterprise.zip"

# Full backup for disaster recovery / moving to a new host: the whole
# directory zipped up — including .env and uploads/, unlike `dist` above
# (a clean distributable that deliberately excludes both, see its own
# comment) — plus a consistent point-in-time dump of the Postgres database
# (backup.py, via pg_dump -F c) folded in alongside it. pg_dump is built
# for exactly this: safe to run against a live database, no locking out
# writers.
#
# Written to backups/enterprise-<timestamp>.zip so repeated runs don't
# clobber each other; backups/ is gitignored, same as dist/.
backup:
	mkdir -p $(BACKUP_DIR)
	tmp=$$(mktemp -d) && \
	mkdir -p $$tmp/enterprise && \
	python3 backup.py $$tmp/enterprise/db.dump && \
	cd .. && zip -rq enterprise/$(BACKUP_DIR)/enterprise-$(TS).zip enterprise \
		-x 'enterprise/__pycache__/*' -x 'enterprise/*/__pycache__/*' -x '*.pyc' \
		-x 'enterprise/dist/*' -x 'enterprise/$(BACKUP_DIR)/*' && \
	cd $$tmp && zip -q $(CURDIR)/$(BACKUP_DIR)/enterprise-$(TS).zip enterprise/db.dump && \
	rm -rf $$tmp
	@echo "Wrote $(BACKUP_DIR)/enterprise-$(TS).zip"

.DEFAULT_GOAL := run
