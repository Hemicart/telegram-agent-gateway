"""Setup wizard JSON API (spec §12) + admin endpoints (spec §10.2).

Auth for both = a long-lived owner session cookie (a random ID; only its hash is on disk).
The first browser gets one by pressing Start (claim). Other browsers sign in with the Telegram
api_id + api_hash used in setup (recoverable any time at my.telegram.org), or SETUP_PASSWORD if
that env var is set (also accepted as HTTP Basic for scripts). Wizard steps return 423 once
setup is complete.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import io
import logging

import qrcode
import qrcode.image.svg
from aiohttp import web
from telethon import errors

from .allowlist import FolderError
from .api import CTX, json_error
from .context import AppContext
from .normalize import chat_dto
from .secrets_store import AlreadyClaimed
from .transport import is_https

log = logging.getLogger("gateway.setup")
COOKIE = "tag_owner"
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetime at ~400 days


def _ctx(request: web.Request) -> AppContext:
    return request.app[CTX]


# Telegram's own public demo keys (desktop, mobile, web apps). Anyone knows these, so they can't
# double as the owner sign-in.
PUBLIC_API_IDS = {4, 5, 6, 8, 9, 2040, 2496, 2834, 17349, 21724}


def _ip(request: web.Request) -> str:
    """Visitor IP. Behind Render/Caddy every request comes from the proxy, so use the first
    X-Forwarded-For hop. It can be spoofed, which only lets an attacker dodge their own per-IP
    limit; the high-entropy api_hash sign-in doesn't rely on it."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    first = forwarded.split(",")[0].strip()
    return first or request.remote or "unknown"


def _basic_password(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(auth[6:]).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    return decoded.split(":", 1)[1] if ":" in decoded else decoded


def _check_password(ctx: AppContext, request: web.Request, candidate: str) -> bool:
    """SETUP_PASSWORD check, counted by the per-IP and global lockout."""
    ip = _ip(request)
    if ctx.login_limiter.blocked(ip):
        raise web.HTTPTooManyRequests(text="Too many attempts; try again later")
    if ctx.secrets.check_setup_password(candidate):
        ctx.login_limiter.record_success(ip)
        return True
    ctx.login_limiter.record_failure(ip)
    return False


def _api_pair_ok(ctx: AppContext, api_id: str, api_hash: str) -> bool:
    creds = ctx.api_credentials()
    if creds is None or not api_id or not api_hash:
        return False
    id_ok = hmac.compare_digest(str(creds[0]).encode(), api_id.encode())
    hash_ok = hmac.compare_digest(creds[1].lower().encode(), api_hash.lower().encode())
    return id_ok and hash_ok


def _cookie_ok(request: web.Request) -> bool:
    """Cheap hash lookup; never touches a secret, so it's safe to run on every request."""
    return _ctx(request).secrets.check_session(request.cookies.get(COOKIE, ""))


def _start_session(request: web.Request, resp: web.Response) -> None:
    token = _ctx(request).secrets.create_session()
    resp.set_cookie(
        COOKIE, token, httponly=True, samesite="Strict", secure=is_https(request),
        path="/", max_age=COOKIE_MAX_AGE,
    )


def _authed(request: web.Request) -> bool:
    if _cookie_ok(request):
        return True
    basic = _basic_password(request)
    return basic is not None and _check_password(_ctx(request), request, basic)


@web.middleware
async def setup_middleware(request: web.Request, handler):
    path = request.path
    if not (path.startswith(("/setup/api/", "/admin/")) or path == "/setup/unlock"):
        return await handler(request)
    if request.method == "POST" and request.headers.get("X-Requested-With") != "gateway":
        return json_error(403, "csrf", "Missing X-Requested-With header")
    public = path in ("/setup/api/login", "/setup/api/status", "/setup/api/claim")
    try:
        if not public and not _authed(request):
            return json_error(401, "unauthorized", "Only the owner of this server can do that")
        return await handler(request)
    except SetupLocked:
        return json_error(423, "setup_locked", "Setup is complete and locked; unlock it first")
    except web.HTTPTooManyRequests as exc:
        return json_error(429, "too_many_attempts", exc.text or "Too many attempts")
    except errors.FloodWaitError as exc:
        log.warning("Telegram flood wait %ss on %s", exc.seconds, path)
        return json_error(429, "telegram_flood_wait", _flood_message(path, exc.seconds))
    except errors.RPCError as exc:
        name = type(exc).__name__
        log.warning("Telegram error on %s: %s", path, name)
        return json_error(400, "telegram_error", TELEGRAM_ERRORS.get(name, f"Telegram said: {name}"))
    except ValueError as exc:
        return json_error(400, "bad_request", str(exc))


LOCKOUT = (
    "Telegram has paused logins for this account after too many attempts. "
    "Wait about an hour without trying, then log in once. Switching between QR and phone does not help."
)

# Telegram's raw error codes are meaningless to people; say what to do instead.
TELEGRAM_ERRORS = {
    "PhonePasswordFloodError": LOCKOUT,
    "PhoneNumberFloodError": LOCKOUT,
    "PasswordHashInvalidError": "Wrong 2FA password.",
    "PhoneCodeInvalidError": "That login code is wrong. Check the latest message from Telegram and try again.",
    "PhoneCodeExpiredError": "That login code expired. Press Back, send a new code, and use the newest one.",
    "PhoneCodeEmptyError": "Enter the login code from Telegram.",
    "PhoneNumberInvalidError": "Telegram doesn't recognise this number. Use international format, e.g. +447700900123.",
    "PhoneNumberUnoccupiedError": "There's no Telegram account on this number.",
    "PhoneNumberBannedError": "Telegram has banned this phone number.",
    "AuthRestartError": "Telegram asked to restart the login. Press Back to QR code and try again.",
    "ApiIdInvalidError": "Telegram rejected the api_id / api_hash pair. Press Change setup and re-enter them from my.telegram.org.",
    "ApiIdPublishedFloodError": "This api_id was published somewhere and Telegram blocks it. Create a new app at my.telegram.org.",
    "AuthTokenExpiredError": "The QR code expired. Press New code.",
    "SessionPasswordNeededError": "Your account has a 2FA password. Enter it on the next screen.",
}


def _flood_message(path: str, seconds: int) -> str:
    wait = f"about {max(1, round(seconds / 60))} minutes" if seconds >= 90 else f"{seconds} seconds"
    if path.endswith("/password"):
        return (
            f"Too many 2FA password attempts. Telegram blocks new tries for {wait}. "
            "Wait, then enter it once more. Forgot it? Telegram → Settings → Privacy and Security "
            "→ Two-Step Verification → Forgot password."
        )
    return f"Telegram asked to slow down. Try again in {wait}."


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception as exc:
        raise ValueError("Body must be JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    return data


class SetupLocked(Exception):
    pass


def _require_unlocked(ctx: AppContext) -> None:
    if ctx.state.setup_complete:
        raise SetupLocked()


def _qr_svg(url: str) -> str:
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _folder_payload(ctx: AppContext) -> dict:
    err = ctx.allowlist.folder_error
    payload: dict = {
        "wanted_title": ctx.settings.folder_title,
        "pinned": vars(ctx.state.pinned_folder) if ctx.state.pinned_folder else None,
        "ok": ctx.allowlist.is_ok(),
        "error": err.code if err else ctx.allowlist.last_error,
        "message": str(err) if err else None,
        "found_title": err.found.title if err and err.found else None,
        "chats": [],
    }
    if payload["ok"]:
        payload["chats"] = [chat_dto(c) for c in ctx.allowlist.current().chats.values()]
    return payload


# --- wizard ------------------------------------------------------------------


async def status(request: web.Request) -> web.Response:
    """Public: only what the wizard needs to pick a screen. No secrets, no titles."""
    ctx = _ctx(request)
    return web.json_response(
        {
            "authed": _cookie_ok(request),
            "claimed": ctx.secrets.is_claimed(),
            "password_required": ctx.secrets.password_required,
            "locked": ctx.state.setup_complete,
        }
    )


async def claim(request: web.Request) -> web.Response:
    """First browser to press Start owns the server. With SETUP_PASSWORD set, it must match.

    Never claimable once it holds anything worth taking (keys saved, logged in, or set up):
    that covers servers upgraded from versions without a claim file.
    """
    ctx = _ctx(request)
    in_use = ctx.state.setup_complete or ctx.state.api_id is not None or await ctx.telegram.is_authorized()
    if in_use and not ctx.secrets.is_claimed():
        return json_error(409, "already_claimed", "This server is already set up. Sign in as the owner")
    if ctx.secrets.password_required:
        data = await _body(request)
        if not _check_password(ctx, request, str(data.get("password", ""))):
            return json_error(401, "wrong_password", "Wrong setup password")
    try:
        ctx.secrets.claim_owner()
    except AlreadyClaimed:
        return json_error(409, "already_claimed", "This server already has an owner")
    resp = web.json_response({"ok": True})
    _start_session(request, resp)
    return resp


async def login(request: web.Request) -> web.Response:
    """Another browser: sign in with the Telegram api_id + api_hash used in setup, or SETUP_PASSWORD."""
    ctx = _ctx(request)
    data = await _body(request)
    ip = _ip(request)
    api_id = str(data.get("api_id", "")).strip()
    api_hash = str(data.get("api_hash", "")).strip()
    if api_id or api_hash:
        # 128-bit secret: only the per-IP limit applies, so strangers can't lock the owner out.
        if ctx.login_limiter.blocked(ip, include_global=False):
            raise web.HTTPTooManyRequests(text="Too many attempts; try again later")
        if not _api_pair_ok(ctx, api_id, api_hash):
            ctx.login_limiter.record_failure(ip, count_global=False)
            return json_error(
                401, "wrong_keys",
                "Those don't match the api_id and api_hash this server was set up with. "
                "Look them up at my.telegram.org → API development tools.",
            )
        ctx.login_limiter.record_success(ip)
    elif not _check_password(ctx, request, str(data.get("password", "")).strip()):
        return json_error(401, "wrong_password", "Wrong setup password")
    resp = web.json_response({"ok": True})
    _start_session(request, resp)
    return resp


async def sign_out_others(request: web.Request) -> web.Response:
    removed = _ctx(request).secrets.revoke_other_sessions(keep=request.cookies.get(COOKIE, ""))
    log.info("Signed out %d other browser session(s)", removed)
    return web.json_response({"ok": True, "signed_out": removed})


async def state(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    tg = ctx.telegram
    authorized = await tg.is_authorized()
    return web.json_response(
        {
            "locked": ctx.state.setup_complete,
            "credentials": ctx.api_credentials() is not None,
            "credentials_from_env": bool(ctx.settings.api_id and ctx.settings.api_hash),
            "telegram": tg.connection_state(),
            "authorized": authorized,
            "me": await tg.whoami() if authorized else None,
            "qr": vars(tg.qr_status),
            "password_hint": await tg.password_hint() if tg.qr_status.status == "password_needed" else None,
            "folder": _folder_payload(ctx) if authorized else None,
            "token_issued": ctx.secrets.has_agent_token(),
            "token_from_env": ctx.secrets.token_from_env,
        }
    )


async def credentials(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    data = await _body(request)
    try:
        api_id = int(str(data.get("api_id", "")).strip())
    except ValueError as exc:
        raise ValueError("api_id must be a number") from exc
    api_hash = str(data.get("api_hash", "")).strip()
    if api_id in PUBLIC_API_IDS:
        raise ValueError(
            "That's one of Telegram's public demo keys. Create your own app at my.telegram.org "
            "and use its api_id and api_hash."
        )
    if len(api_hash) != 32 or any(c not in "0123456789abcdef" for c in api_hash.lower()):
        raise ValueError("api_hash should be 32 hex characters")
    await ctx.telegram.configure(api_id, api_hash)
    ctx.update_state(api_id=api_id, api_hash=api_hash)
    return web.json_response({"ok": True})


async def qr_start(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    st = await ctx.telegram.start_qr()
    return web.json_response({**vars(st), "svg": _qr_svg(st.url) if st.url else None})


async def qr_status(request: web.Request) -> web.Response:
    st = _ctx(request).telegram.qr_status
    return web.json_response({**vars(st), "svg": _qr_svg(st.url) if st.url else None})


async def phone_send(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    phone = str((await _body(request)).get("phone", "")).strip()
    if not phone.lstrip("+").isdigit() or len(phone) < 8:
        raise ValueError("Phone in international format, e.g. +447700900123")
    await ctx.telegram.send_code(phone)
    return web.json_response({"ok": True})


async def phone_code(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    code = str((await _body(request)).get("code", "")).strip()
    result = await ctx.telegram.sign_in_code(code)
    hint = await ctx.telegram.password_hint() if result == "password_needed" else None
    return web.json_response({"status": result, "password_hint": hint})


async def password(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    pw = str((await _body(request)).get("password", ""))
    try:
        await ctx.telegram.sign_in_password(pw)
    except errors.PasswordHashInvalidError:
        log.warning("Wrong 2FA password entered")
        return json_error(
            400, "wrong_2fa_password",
            "Wrong 2FA password. Careful: a few wrong tries make Telegram block attempts for up to an hour.",
        )
    return web.json_response({"ok": True})


async def folder_refresh(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    try:
        await ctx.refresh_allowlist()
    except FolderError:
        pass  # reported in the payload
    return web.json_response(_folder_payload(ctx))


async def issue_token(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    if ctx.secrets.token_from_env:
        return json_error(409, "token_from_env", "AGENT_API_TOKEN is set in the environment")
    token = ctx.secrets.issue_agent_token()
    log.info("Agent token issued/rotated")
    return web.json_response({"token": token})


async def complete(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    _require_unlocked(ctx)
    if not await ctx.telegram.is_authorized():
        raise ValueError("Log in to Telegram first")
    if not ctx.allowlist.is_ok():
        raise ValueError("Folder is not OK yet")
    if not ctx.secrets.has_agent_token():
        raise ValueError("Issue the agent token first")
    ctx.update_state(setup_complete=True)
    log.info("Setup complete; wizard locked")
    return web.json_response({"ok": True})


async def unlock(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    ctx.update_state(setup_complete=False)
    log.info("Setup unlocked")
    return web.json_response({"ok": True})


async def logout(request: web.Request) -> web.Response:
    _ctx(request).secrets.revoke_session(request.cookies.get(COOKIE, ""))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


# --- admin (works while locked) --------------------------------------------------


async def admin_folder_debug(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    payload = _folder_payload(ctx)
    if payload["ok"]:
        snap = ctx.allowlist.current()
        payload["folder_id"] = snap.folder.folder_id
        payload["peer_ids"] = sorted(snap.folder.peers)
        payload["broad_flags"] = list(snap.folder.broad_flags)
    return web.json_response(payload)


async def admin_confirm_folder(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    try:
        await ctx.confirm_folder()
    except FolderError:
        pass
    return web.json_response(_folder_payload(ctx))


def add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/setup/api/status", status)
    r.add_post("/setup/api/claim", claim)
    r.add_post("/setup/api/login", login)
    r.add_post("/setup/api/sign-out-others", sign_out_others)
    r.add_post("/setup/api/logout", logout)
    r.add_get("/setup/api/state", state)
    r.add_post("/setup/api/credentials", credentials)
    r.add_post("/setup/api/qr/start", qr_start)
    r.add_get("/setup/api/qr/status", qr_status)
    r.add_post("/setup/api/phone/send", phone_send)
    r.add_post("/setup/api/phone/code", phone_code)
    r.add_post("/setup/api/password", password)
    r.add_post("/setup/api/folder/refresh", folder_refresh)
    r.add_post("/setup/api/token", issue_token)
    r.add_post("/setup/api/complete", complete)
    r.add_post("/setup/unlock", unlock)
    r.add_post("/admin/refresh-allowlist", folder_refresh)
    r.add_post("/admin/confirm-folder", admin_confirm_folder)
    r.add_post("/admin/rotate-token", issue_token)
    r.add_get("/admin/folder-debug", admin_folder_debug)
