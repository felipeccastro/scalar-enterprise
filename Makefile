# Dev server. `make` (or `make run`) starts gunicorn with autoreload (picks
# up .py file changes; template/static edits already show up on the next
# request without a restart, via app.py's DEBUG flag — see app.py) and a
# worker restart every 1000 requests processed, to shake off any slow memory
# growth in a long-lived dev session. Same shape of command a provisioned
# instance runs under (see ../admin/launcher/provisioner.py), just invoked
# locally instead of by the launcher.
#
# Migrations are NOT applied here — gunicorn workers importing app:app never
# reach its __main__ block (see app.py), so run `make db-migrate` first.
# `python3 app.py` (this app's own dev server, not this target) still
# auto-migrates on every run, same as before.
HOST ?= 0.0.0.0
PORT ?= 8000
WORKERS ?= 1
BACKUP_DIR ?= backups
TS := $(shell date +%Y%m%d-%H%M%S)

.PHONY: run dist db-migrate backup repl
run:
	gunicorn app:app \
		--bind $(HOST):$(PORT) \
		--workers $(WORKERS) \
		--reload \
		--max-requests 1000

# Apply pending migrations/ (see models.py: run_migrations()). Required
# before `make run` the first time any migration lands after a deploy —
# gunicorn workers assume the schema is already current, they don't check.
db-migrate:
	python3 migrate.py

# Interactive shell with every model already imported (see repl.py) — for
# poking at data by hand: `User.select()`, `Task.get_by_id(1)`, etc.
# Migrations are NOT applied here, same caveat as `make run` above.
repl:
	python3 repl.py

# Package a ready-to-run copy of this app for the landing page's
# "Buy Once" button (../admin/routes/downloads.py serves the result) — the
# whole directory, seeded app.db included, so unzip-and-run shows the demo
# company immediately. .env is excluded: it's gitignored and per-install
# already (see .env.example) — without one, utils.py falls back to its
# documented dev SECRET_KEY, same as a fresh git clone. WAL sidecar files
# are excluded too: they're SQLite's in-flight journal, not data, and get
# rebuilt from app.db the moment anything reopens it.
#
# dist/ is gitignored — ../admin/routes/downloads.py runs this target itself
# on the first production request for pro.zip and caches the result, so
# there's nothing to remember to rebuild/commit here.
dist:
	mkdir -p dist
	rm -f dist/pro.zip
	cd .. && zip -rq pro/dist/pro.zip pro \
		-x 'pro/.env' \
		-x 'pro/__pycache__/*' -x 'pro/*/__pycache__/*' -x '*.pyc' \
		-x 'pro/app.db-shm' -x 'pro/app.db-wal' \
		-x 'pro/logs/*' \
		-x 'pro/dist/*'
	@echo "Built dist/pro.zip"

# Full backup for disaster recovery / moving to a new host: the whole
# directory zipped up — including .env and uploads/, unlike `dist` above
# (a clean distributable that deliberately excludes both, see its own
# comment) — with a consistent point-in-time snapshot of app.db standing
# in for the live file. A plain `cp` of app.db isn't safe: it runs in WAL
# mode (see models.py), so a write in flight can leave committed data
# sitting in the app.db-wal sidecar rather than app.db itself, and a raw
# copy can grab the file mid-write in a torn state. backup.py drives
# sqlite3's own Online Backup API (stdlib, no extra install) instead,
# which is built for exactly this: safe to run against a live database.
#
# Written to backups/pro-<timestamp>.zip so repeated runs don't clobber
# each other; backups/ is gitignored, same as dist/.
backup:
	mkdir -p $(BACKUP_DIR)
	tmp=$$(mktemp -d) && \
	mkdir -p $$tmp/pro && \
	python3 backup.py $$tmp/pro/app.db && \
	cd .. && zip -rq pro/$(BACKUP_DIR)/pro-$(TS).zip pro \
		-x 'pro/__pycache__/*' -x 'pro/*/__pycache__/*' -x '*.pyc' \
		-x 'pro/app.db' -x 'pro/app.db-shm' -x 'pro/app.db-wal' \
		-x 'pro/dist/*' -x 'pro/$(BACKUP_DIR)/*' && \
	cd $$tmp && zip -q $(CURDIR)/$(BACKUP_DIR)/pro-$(TS).zip pro/app.db && \
	rm -rf $$tmp
	@echo "Wrote $(BACKUP_DIR)/pro-$(TS).zip"

.DEFAULT_GOAL := run
