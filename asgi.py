"""Thin Bottle-shaped compatibility layer over Starlette.

The rest of this app (utils.py, every pages/*.py) leans on Bottle's ambient,
import-anywhere `request`/`response` and exception-based `abort()`/
`redirect()` control flow — used pervasively, not just at route-handler top
level. Rewriting every function in the app to thread a request object
explicitly would be a much bigger, riskier change than swapping the
framework underneath the same shape. So: `request`/`response` here are
proxies backed by a contextvars.ContextVar, populated per-request by
ContextMiddleware below — the same ergonomics as Bottle's own thread-local
globals, correctly isolated per asyncio Task instead of per-thread (Bottle's
model breaks under a shared-event-loop server; contextvars don't).

ContextMiddleware is a *raw* ASGI middleware (implements __call__(scope,
receive, send) directly), not Starlette's BaseHTTPMiddleware. That's
deliberate, not a style preference: playhouse.pwasyncio keys its connection
state off `id(asyncio.current_task())`, and BaseHTTPMiddleware's call_next()
has a documented history of running the downstream app in a separate task
from the middleware itself. A raw ASGI middleware runs everything —
middleware and the eventual route handler — in the exact same task, which
pwasyncio's design requires.

before_request/after_request hooks run *inside* each route's endpoint
wrapper (App.route), not in ContextMiddleware — they need to be inside
Starlette's ExceptionMiddleware's scope for their own abort()/redirect()
calls (raised exceptions) to actually reach the registered error/redirect
handlers instead of crashing straight out to a raw 500. ContextMiddleware
sits *outside* ExceptionMiddleware (it's just context setup, never raises
app-level control-flow exceptions), the endpoint wrapper's hook-running is
*inside* it.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.requests import Request as StarletteRequest
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class _ResponseState:
    """Per-request staging area for the outgoing response — mutated ambiently
    (`response.status = 503`, `response.set_cookie(...)`) anywhere in the
    call chain, then applied onto the real Starlette Response right before
    it goes out (see App.route's endpoint wrapper / error handlers).

    `status` starts unset (None), not defaulted to 200: a route that never
    touches `response.status` should leave whatever status the Response it
    returned/raised already carries alone (a plain `return {...}` cast to
    JSONResponse is 200 by default; RedirectResponse is already 302/303;
    etc.) — unconditionally stamping 200 over top would silently turn every
    redirect into a 200-with-a-Location-header, which no HTTP client
    follows automatically the way an actual 3xx status gets followed."""

    __slots__ = ("status", "_cookies")

    def __init__(self) -> None:
        self.status: int | None = None
        self._cookies: list[tuple[tuple, dict]] = []

    def set_cookie(self, *args: Any, **kwargs: Any) -> None:
        self._cookies.append((args, kwargs))

    def apply(self, response: Response) -> Response:
        if self.status is not None:
            response.status_code = self.status
        for args, kwargs in self._cookies:
            response.set_cookie(*args, **kwargs)
        return response


class _FormsProxy:
    """Bottle's request.forms shape: non-file fields only, .get(name, default)."""

    def __init__(self, form: Any) -> None:
        self._form = form

    def get(self, name: str, default: str = "") -> str:
        if self._form is None:
            return default
        value = self._form.get(name)
        if value is None or isinstance(value, UploadFile):
            return default
        return value

    def getall(self, name: str) -> list[str]:
        """Bottle's request.forms.getall(name): every value for a repeated
        field (e.g. multiple same-named inputs in one submit — see
        pages/tasks.py's tasks_reorder)."""
        if self._form is None:
            return []
        return [v for v in self._form.getlist(name) if not isinstance(v, UploadFile)]


class _FilesProxy:
    """Bottle's request.files shape: file fields only, .get(name) -> UploadFile | None."""

    def __init__(self, form: Any) -> None:
        self._form = form

    def get(self, name: str) -> UploadFile | None:
        if self._form is None:
            return None
        value = self._form.get(name)
        return value if isinstance(value, UploadFile) else None


class _Ctx:
    """Everything the ambient request/response proxies read from — one
    ContextVar entry per request, set by ContextMiddleware."""

    __slots__ = ("request", "environ", "response", "form", "route_name")

    def __init__(self, request: StarletteRequest) -> None:
        self.request = request
        self.environ: dict[str, Any] = {}  # Bottle's request.environ stash, same role
        self.response = _ResponseState()
        self.form: Any = None  # pre-parsed FormData, or None (see ContextMiddleware)
        self.route_name: str | None = None  # set by App.route's endpoint wrapper


_ctx_var: contextvars.ContextVar[_Ctx] = contextvars.ContextVar("scalar_ctx")


class RequestProxy:
    """Ambient accessor for "the current request" — import this once
    (`from asgi import request`) and use it anywhere a request is in
    flight, matching Bottle's own request global."""

    @property
    def _real(self) -> StarletteRequest:
        return _ctx_var.get().request

    @property
    def method(self) -> str:
        return self._real.method

    @property
    def path(self) -> str:
        return self._real.url.path

    @property
    def query(self):
        return self._real.query_params

    @property
    def headers(self):
        return self._real.headers

    @property
    def cookies(self):
        return self._real.cookies

    @property
    def remote_addr(self) -> str:
        client = self._real.client
        return client.host if client else "0.0.0.0"

    @property
    def environ(self) -> dict:
        return _ctx_var.get().environ

    @property
    def forms(self) -> _FormsProxy:
        return _FormsProxy(_ctx_var.get().form)

    @property
    def files(self) -> _FilesProxy:
        return _FilesProxy(_ctx_var.get().form)

    @property
    def route_name(self) -> str | None:
        """The name= of the currently-matched route — set before
        before_request hooks run (see App.route). Bottle's before_request
        hooks fire *before* routing, so pages/__init__.py's login-required
        hook used to re-resolve the route itself (app.match()) to get this;
        here the route is already known by the time hooks run, so it's just
        exposed directly."""
        return _ctx_var.get().route_name

    def get_header(self, name: str, default: str | None = None) -> str | None:
        return self._real.headers.get(name, default)

    def get_cookie(self, name: str, default: str | None = None) -> str | None:
        return self._real.cookies.get(name, default)

    async def json(self) -> Any:
        return await self._real.json()

    def __getattr__(self, name: str) -> Any:
        # Fallback for anything not shimmed above (path_params, url, etc.) —
        # delegate straight to the real Starlette Request.
        return getattr(self._real, name)


class ResponseProxy:
    """Ambient accessor for "the response under construction" — Bottle's
    response global. Mutating this before a handler returns/raises is what
    App.route's endpoint wrapper applies onto the real outgoing Response."""

    @property
    def _state(self) -> _ResponseState:
        return _ctx_var.get().response

    @property
    def status(self) -> int:
        # 200 when nothing's explicitly set it — same default Bottle's own
        # ambient response object started at. _state.status itself stays
        # None internally until set (see _ResponseState's own docstring for
        # why: apply() needs to tell "never touched" apart from "someone
        # set it to 200 on purpose").
        return self._state.status if self._state.status is not None else 200

    @status.setter
    def status(self, value: int) -> None:
        self._state.status = value

    @property
    def status_code(self) -> int:
        return self.status

    def set_cookie(self, *args: Any, **kwargs: Any) -> None:
        self._state.set_cookie(*args, **kwargs)


request = RequestProxy()
response = ResponseProxy()


class Redirect(Exception):
    """Raised by utils.redirect() — caught by the exception handler App
    wires up, the same "raise to short-circuit with a response" trick
    Bottle's own redirect()/abort() use (bottle.HTTPResponse is itself a
    BaseException)."""

    def __init__(self, url: str, code: int = 302) -> None:
        self.url = url
        self.code = code


def _cast_response(value: Any) -> Response:
    """Bottle's own auto-casting: dict -> JSON, str -> HTML, a Response
    passed through as-is. Keeps `return {...}` / `return render(...)`
    working unchanged in every route handler."""
    if isinstance(value, Response):
        return value
    if isinstance(value, dict):
        return JSONResponse(value)
    return HTMLResponse(value)


class App:
    """Bottle-shaped façade over a Starlette app: @app.route(...),
    @app.hook(...), @app.error(...), app.get_url(...), app.mount(...) —
    kept in this exact shape so pages/*.py and utils.py don't need to be
    rewritten in Starlette's own idiom at 40+ call sites."""

    def __init__(self) -> None:
        self.starlette = Starlette()
        self._before_hooks: list[Callable[[], Any]] = []
        self._after_hooks: list[Callable[[], Any]] = []
        self._startup_hooks: list[Callable[[], Any]] = []
        self._shutdown_hooks: list[Callable[[], Any]] = []
        self.starlette.add_exception_handler(Redirect, self._handle_redirect)
        # NOT registering a generic Exception/500 handler here: Starlette's
        # own build_middleware_stack() treats the keys 500 and Exception as
        # the *same* slot (ServerErrorMiddleware's single `handler`) — the
        # last of the two registered wins outright, silently dropping the
        # other. app.py's own @app.error(500) (-> self.error(500) below) is
        # the sole registration for that slot; genuine uncaught exceptions
        # and an HTTPException raised with no matching per-code handler both
        # end up there via Starlette's own dispatch, no extra wiring needed.

    # -- Route registration --------------------------------------------

    def route(self, path: str, method: str = "GET", name: str | None = None):
        def decorator(fn: Callable[..., Any]):
            async def endpoint(_request: StarletteRequest) -> Response:
                _ctx_var.get().route_name = name
                try:
                    for hook in self._before_hooks:
                        await hook()
                    result = await fn(**_request.path_params)
                finally:
                    for hook in self._after_hooks:
                        await hook()
                return _ctx_var.get().response.apply(_cast_response(result))

            self.starlette.router.add_route(path, endpoint, methods=[method], name=name)
            return fn

        return decorator

    def mount(self, path: str, sub_app: ASGIApp, name: str | None = None) -> None:
        self.starlette.mount(path, app=sub_app, name=name)

    def get_url(self, name: str, **kwargs: Any) -> str:
        return self.starlette.url_path_for(name, **kwargs)

    def on_startup(self, fn: Callable[[], Any]):
        """Run `fn` once the ASGI server's event loop is actually running
        (the "lifespan.startup" ASGI event — see __call__ below), not at
        import time. jobs.py's scheduler needs this: its poller has to run
        as a task on the *same* loop that serves requests, since
        playhouse.pwasyncio's connection pool (see models.py) is bound to
        whichever loop first uses it — a separate thread with its own loop
        (this app's old, pre-pwasyncio shape) would create a second pool on
        a second loop, and cross-loop use of either one raises."""
        self._startup_hooks.append(fn)
        return fn

    def on_shutdown(self, fn: Callable[[], Any]):
        """Run `fn` on the "lifespan.shutdown" ASGI event, before the loop
        itself stops — the mirror of on_startup above, e.g. for cancelling
        a task a startup hook created (see jobs.py's start()/stop())."""
        self._shutdown_hooks.append(fn)
        return fn

    # -- Hooks (before_request / after_request) --------------------------

    def hook(self, name: str):
        target = self._before_hooks if name == "before_request" else self._after_hooks

        def decorator(fn: Callable[[], Any]):
            target.append(fn)
            return fn

        return decorator

    # -- Error pages (by exact HTTP status code) -------------------------

    def error(self, code: int):
        def decorator(fn: Callable[[Exception], Any]):
            async def handler(_request: StarletteRequest, exc: Exception) -> Response:
                return _ctx_var.get().response.apply(_cast_response(await fn(exc)))

            self.starlette.add_exception_handler(code, handler)
            return fn

        return decorator

    async def _handle_redirect(self, _request: StarletteRequest, exc: Redirect) -> Response:
        response = RedirectResponse(exc.url, status_code=exc.code)
        return _ctx_var.get().response.apply(response)

    # -- ASGI entrypoint --------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._run_lifespan(receive, send)
            return
        if scope["type"] != "http":
            await self.starlette(scope, receive, send)
            return

        # The true outer layer — *above* Starlette's own ServerErrorMiddleware,
        # not registered as Starlette user middleware (which would sit
        # *inside* it — see this module's docstring: a genuinely uncaught
        # exception unwinds up through user middleware, running its
        # `finally` and tearing the context down, *before*
        # ServerErrorMiddleware gets to invoke @app.error(500)'s handler,
        # which then needs that same context still in place).
        starlette_request = StarletteRequest(scope, receive)
        ctx = _Ctx(starlette_request)
        token = _ctx_var.set(ctx)
        try:
            content_type = starlette_request.headers.get("content-type", "")
            if starlette_request.method in ("POST", "PUT", "PATCH") and content_type.startswith(_FORM_CONTENT_TYPES):
                ctx.form = await starlette_request.form()
            await self.starlette(scope, receive, send)
        finally:
            _ctx_var.reset(token)

    async def _run_lifespan(self, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message["type"] == "lifespan.startup"
        try:
            for hook in self._startup_hooks:
                await hook()
        except Exception as exc:
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return
        await send({"type": "lifespan.startup.complete"})
        message = await receive()
        assert message["type"] == "lifespan.shutdown"
        for hook in self._shutdown_hooks:
            await hook()
        await send({"type": "lifespan.shutdown.complete"})
