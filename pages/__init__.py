"""Every route in the app, split by feature area. Still no blueprints —
each module below does `from app import app` and decorates its own routes
directly, exactly as a single flat pages.py used to; this package just
gives each feature area its own file once the flat version grew past a
size an editing pass could comfortably hold, while keeping the same "an
AI-editing tool needs to hold it all in context" idea at the grain of one
file per feature instead of one file per app.
"""

from __future__ import annotations

from app import app
from asgi import request
from utils import (
    PUBLIC_ROUTES,
    SESSION_INDEPENDENT_PATHS,
    any_team_members_exist,
    current_user,
    redirect,
    url_for,
)


@app.hook("before_request")
async def _bootstrap_redirect() -> None:
    """Until the first owner account exists, every road leads to /register.

    /health is exempt too: a freshly provisioned, team-less instance should
    still report whether its database is reachable, not bounce a health
    check into a 200-but-meaningless /register redirect. /static/ never
    reaches a before_request hook at all — it's served by a Starlette Mount
    (see app.py), not a route these hooks apply to."""
    if request.path in ("/register", "/health"):
        return
    if not await any_team_members_exist():
        redirect(url_for("register_owner"))


@app.hook("before_request")
async def _require_login_hook() -> None:
    """Every route requires a logged-in user by default — the opposite of a
    per-route @require_login decorator, which is easy to forget on a new
    route and silently leave unprotected. PUBLIC_ROUTES (utils.py) lists
    the handful of routes a signed-out visitor genuinely needs to reach
    (register, login, accept-invite, forgot/reset password); everything
    else redirects to /login.

    Registered *after* _bootstrap_redirect above — before_request hooks run
    in registration order (see asgi.py's App.route) — so a fresh, team-less
    instance always lands on /register first, before this hook gets a
    chance to bounce it to /login instead.

    request.route_name is the name= of whichever route is about to run,
    set by asgi.py's App.route before any before_request hook runs — unlike
    Bottle, where before_request hooks fire *before* routing (hence
    SESSION_INDEPENDENT_PATHS being path-keyed rather than route-name-keyed
    below: that one's still checked ahead of a route even existing, since
    it's really about auth strategy, not this app's own routing)."""
    if request.path in SESSION_INDEPENDENT_PATHS:
        return
    if request.route_name in PUBLIC_ROUTES:
        return
    if await current_user() is None:
        redirect(url_for("login"))


# Import each feature module for its route-registration side effects only.
from . import (  # noqa: E402,F401
    attachments,
    audit,
    auth,
    chat,
    clients,
    comments,
    dashboard,
    notifications,
    settings,
    tasks,
)
