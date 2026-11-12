"""aiohttp app: static pages, /health, and the API/setup route groups."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from . import api, setup_routes, transcripts
from .api import CTX
from .config import VERSION
from .context import AppContext
from .transport import https_middleware

PKG_DIR = Path(__file__).resolve().parent
STATIC_DIR = PKG_DIR / "static"
LANDING = Path(__file__).resolve().parents[2] / "landing" / "index.html"

PAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'"
    ),
}


@web.middleware
async def page_headers(request: web.Request, handler):
    resp = await handler(request)
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # revalidate, so UI fixes show up after a deploy
    if not request.path.startswith("/v1/media/"):
        for key, value in PAGE_HEADERS.items():
            resp.headers.setdefault(key, value)
    return resp


async def health(request: web.Request) -> web.Response:
    """200 while things work. 503 once set up but cut off from Telegram, so the host restarts us."""
    ctx: AppContext = request.app[CTX]
    telegram = ctx.telegram.connection_state()
    healthy = not (ctx.state.setup_complete and telegram != "connected")
    return web.json_response(
        {
            "ok": healthy,
            "version": VERSION,
            "telegram": telegram,
            "setup_complete": ctx.state.setup_complete,
            "folder_ok": ctx.allowlist.is_ok(),
        },
        status=200 if healthy else 503,
    )


async def root(request: web.Request) -> web.StreamResponse:
    """A fresh deploy opens straight into the setup wizard; afterwards / shows the docs."""
    ctx: AppContext = request.app[CTX]
    if not ctx.state.setup_complete:
        raise web.HTTPFound("/setup")
    return web.FileResponse(LANDING)


async def landing(request: web.Request) -> web.FileResponse:
    return web.FileResponse(LANDING)


async def setup_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "setup.html", headers={"Cache-Control": "no-store"})


def create_app(ctx: AppContext) -> web.Application:
    app = web.Application(
        middlewares=[
            https_middleware(ctx.settings.allow_plain_http),
            page_headers,
            api.agent_middleware,
            setup_routes.setup_middleware,
        ],
        client_max_size=64 * 1024,
    )
    app[CTX] = ctx
    app.router.add_get("/health", health)
    app.router.add_get("/", root)
    app.router.add_get("/docs", landing)
    app.router.add_get("/setup", setup_page)
    app.router.add_static("/static/", STATIC_DIR)
    api.add_routes(app)
    transcripts.add_routes(app)
    setup_routes.add_routes(app)
    return app
