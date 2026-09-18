"""Simple in-process scheduled jobs — stdlib asyncio, no APScheduler/Celery/
cron dependency.

A background asyncio task (started from app.py's ASGI startup hook — see
start() below) wakes up every CHECK_INTERVAL seconds and runs whichever
registered jobs are due, tracking each job's next-run time in memory
(nothing persisted — a restart just resumes polling on the same schedule,
which is fine for jobs that check "is anything due?" against the DB rather
than relying on the scheduler itself to remember what it did). Good enough
for a handful of low-frequency jobs in a single-worker, single-tenant app
(see Makefile: WORKERS=1); this is not a distributed job queue, and running
with WORKERS>1 would run one of these tasks per worker process — harmless
for something idempotent, but a reminder could theoretically be picked up
by two workers in the same poll window and emailed twice. Not a concern at
the default WORKERS=1.

A task on the *same* loop that serves requests, not a separate thread with
its own loop: playhouse.pwasyncio's connection pool (see models.py) is
bound to whichever event loop first creates it, and using it from a second
loop raises. Scheduling this as a task on the one loop app.py already runs
under (via App.on_startup — see asgi.py) keeps it on that same pool.

The one job registered today is `_run_due_reminders`, which fires Reminders
created via Ask AI's create_reminder tool (see ai.py) once their remind_at
has passed: it creates an in-app Notification and emails the reminder's
owner, then marks it sent so the next poll skips it.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30  # seconds between polls — reminders don't need sub-minute precision

_jobs: list[tuple[str, Callable[[], Awaitable[None]], int]] = []  # (name, fn, interval_seconds)
_next_run: dict[str, float] = {}
_task: asyncio.Task | None = None


def register(name: str, fn: Callable[[], Awaitable[None]], *, interval_seconds: int) -> None:
    """Register an async job to run every `interval_seconds` once start()
    has been called. Call this at import time (see the bottom of this
    file) — the scheduler loop just walks whatever's registered here."""
    _jobs.append((name, fn, interval_seconds))


async def _run_due(now: float) -> None:
    from models import db

    for name, fn, interval in _jobs:
        if now < _next_run.get(name, 0):
            continue
        _next_run[name] = now + interval
        # Each run gets its own DB connection/close, the same shape as
        # app.py's before_request/after_request hooks — there's no request
        # here to hang that off of, so the job loop does it directly.
        await db.aconnect()
        try:
            await fn()
        except Exception:
            logger.exception("Scheduled job %r failed", name)
        finally:
            if not db.is_closed():
                await db.aclose()


async def _loop() -> None:
    while True:
        await _run_due(asyncio.get_running_loop().time())
        await asyncio.sleep(CHECK_INTERVAL)


async def start() -> None:
    """Schedule the background scheduler loop as a task on the currently
    running event loop — call this from an ASGI startup hook (see app.py:
    app.on_startup(jobs.start)), not at import time; there's no running
    loop yet then. Idempotent, so it's safe to call unconditionally even if
    this module ever ends up imported more than once in the same process.
    Not awaited to completion (it runs forever) — fire-and-forget, same
    shape the old background-thread version had. See stop() for the
    matching shutdown hook."""
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    """Cancel the scheduler task — call this from an ASGI shutdown hook
    (see app.py: app.on_shutdown(jobs.stop)). Without it, the task is just
    abandoned when the loop stops, which asyncio logs as "Task was
    destroyed but it is pending" — harmless (the process is exiting either
    way) but noisy, especially once per test journey."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


# ---------------------------------------------------------------------------
# The one job: fire due reminders.
# ---------------------------------------------------------------------------


async def _run_due_reminders() -> None:
    import asyncio as _asyncio

    from models import Reminder, db
    from utils import Mailer, MailerError, notify, record_activity

    due = list(
        await db.list(
            Reminder.select().where(
                Reminder.sent_at.is_null(True) & (Reminder.remind_at <= datetime.datetime.now())
            )
        )
    )
    for reminder in due:
        try:
            await notify(reminder.user, "reminder", message=reminder.message,
                         subject_type=reminder.subject_type, subject_id=reminder.subject_id)
            if reminder.subject_type and reminder.subject_id:
                # Client/Task are only ever soft-deleted (archived_at), never
                # hard-deleted, so the subject row is always still there to
                # log against.
                await record_activity(reminder.subject_type, reminder.subject_id, reminder.user,
                                       "reminder_fired", message=reminder.message)
            try:
                # Mailer is a synchronous urllib call — hop to a thread so it
                # doesn't block this loop's only thread for the length of the
                # HTTP round-trip to Resend.
                await _asyncio.to_thread(Mailer.send_reminder, email=reminder.user.email, message=reminder.message)
            except MailerError:
                # Same "best-effort, don't block the feature" stance as
                # invite/reset emails elsewhere — the in-app notification
                # above already happened, so the reminder isn't silently
                # lost just because email isn't configured or Resend is
                # down.
                logger.exception("Couldn't email reminder #%s", reminder.id)
        except Exception:
            # One bad reminder shouldn't stop the rest of this poll's batch
            # from firing.
            logger.exception("Failed to fire reminder #%s", reminder.id)
        finally:
            # Mark sent even if the block above raised or the email failed —
            # an error already logged is enough; retrying the same reminder
            # forever isn't the goal.
            reminder.sent_at = datetime.datetime.now()
            await reminder.asave()


register("run_due_reminders", _run_due_reminders, interval_seconds=CHECK_INTERVAL)
