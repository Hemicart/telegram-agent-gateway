"""Agent API /v1 (spec §10.1). The allowlist check always runs before any Telegram call."""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web
from telethon import errors
from telethon.tl import types

from .allowlist import AllowlistUnavailable
from .context import AppContext
from .media import MediaTooLarge, response_headers
from .normalize import chat_dto, message_dto

audit = logging.getLogger("gateway.audit")
CTX = web.AppKey("ctx", AppContext)


def json_error(status: int, code: str, message: str, headers: dict | None = None) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status, headers=headers)


def _ctx(request: web.Request) -> AppContext:
    return request.app[CTX]


def _int_param(request: web.Request, name: str, *, minimum: int = 0) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"{name} must be an integer") from exc
    if value < minimum:
        raise web.HTTPBadRequest(text=f"{name} must be >= {minimum}")
    return value


def _dialog_id(request: web.Request) -> int:
    try:
        return int(request.match_info["dialog_id"])
    except ValueError as exc:
        raise web.HTTPNotFound() from exc


def _peer(ctx: AppContext, dialog_id: int) -> Any:
    """404 for anything outside the folder — never reveal whether it exists."""
    snap = ctx.allowlist.current()
    peer = snap.folder.peers.get(dialog_id)
    if peer is None:
        raise web.HTTPNotFound()
    return peer


@web.middleware
async def agent_middleware(request: web.Request, handler):
    if not request.path.startswith("/v1/"):
        return await handler(request)
    ctx = _ctx(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token or not ctx.secrets.check_agent_token(token):
        return json_error(401, "unauthorized", "Missing or invalid bearer token")
    if not ctx.agent_limiter.allow():
        return json_error(429, "rate_limited", "Too many requests", {"Retry-After": "5"})
    try:
        return await handler(request)
    except AllowlistUnavailable as exc:
        return json_error(503, exc.code, str(exc))
    except errors.FloodWaitError as exc:
        return json_error(
            429, "telegram_flood_wait", f"Telegram asked to wait {exc.seconds}s",
            {"Retry-After": str(exc.seconds)},
        )
    except web.HTTPNotFound:
        return json_error(404, "not_found", "Not found")
    except web.HTTPBadRequest as exc:
        return json_error(400, "bad_request", exc.text or "Bad request")
    except ConnectionError:
        return json_error(503, "telegram_disconnected", "Gateway is not connected to Telegram")
    except errors.RPCError as exc:
        audit.warning("telegram error on %s: %s", request.path, type(exc).__name__)
        return json_error(502, "telegram_error", f"Telegram refused: {type(exc).__name__}")


async def list_chats(request: web.Request) -> web.Response:
    snap = _ctx(request).allowlist.current()
    audit.info("list_chats count=%d", len(snap.chats))
    return web.json_response({"chats": [chat_dto(c) for c in snap.chats.values()]})


CHAT_DETAILS_TTL = 600


async def get_chat(request: web.Request) -> web.Response:
    """Chat metadata plus description and member count (cached ~10 min: one extra Telegram call)."""
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    cached = ctx.chat_details.get(did)
    if cached is None or time.monotonic() - cached[0] > CHAT_DETAILS_TTL:
        cached = (time.monotonic(), await ctx.telegram.chat_details(peer))
        ctx.chat_details[did] = cached
    return web.json_response({**chat_dto(ctx.allowlist.current().chats[did]), **cached[1]})


async def list_pinned(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    limit = min(_int_param(request, "limit", minimum=1) or 20, ctx.settings.max_page_size)
    rows = await ctx.telegram.pinned_messages(peer, limit)
    audit.info("pinned dialog_id=%s returned=%d", did, len(rows))
    return web.json_response(
        {"dialog_id": did, "messages": [_with_transcript(ctx, message_dto(m, did)) for m in rows]}
    )


def _message_id(request: web.Request) -> int:
    try:
        return int(request.match_info["message_id"])
    except ValueError:
        raise web.HTTPNotFound() from None


async def get_one_message(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    message = await ctx.telegram.get_message(peer, _message_id(request))
    if message is None:
        raise web.HTTPNotFound()
    return web.json_response(_with_transcript(ctx, message_dto(message, did)))


async def list_members(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    if ctx.allowlist.current().chats[did].kind in ("user", "bot"):
        return json_error(400, "not_a_group", "Members only exist for groups and channels")
    limit = min(_int_param(request, "limit", minimum=1) or 100, 200)
    search = request.query.get("q", "").strip()[:64]
    try:
        members = await ctx.telegram.members(peer, limit, search)
    except (errors.ChatAdminRequiredError, errors.ChannelPrivateError):
        return json_error(403, "members_hidden", "Telegram hides the member list of this chat")
    audit.info("members dialog_id=%s returned=%d", did, len(members))
    return web.json_response({"dialog_id": did, "members": members})


async def list_who_reacted(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    mid = _message_id(request)
    limit = min(_int_param(request, "limit", minimum=1) or 50, 100)
    try:
        reactions = await ctx.telegram.who_reacted(peer, mid, limit)
    except (errors.BroadcastForbiddenError, errors.ChatAdminRequiredError, errors.MsgIdInvalidError):
        return json_error(403, "reactors_hidden", "Telegram doesn't show who reacted here (big group or channel)")
    return web.json_response({"dialog_id": did, "message_id": mid, "reactions": reactions})


async def list_topics(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    if not ctx.allowlist.current().chats[did].is_forum:
        return json_error(400, "not_a_forum", "This chat has no topics")
    topics = await ctx.telegram.forum_topics(peer)
    return web.json_response({"dialog_id": did, "topics": topics})


def _with_transcript(ctx: AppContext, dto: dict) -> dict:
    """Voice / video notes: point at the transcript endpoint, and inline it if already cached."""
    media = dto.get("media")
    if not media or media["type"] not in ("voice", "video_note"):
        return dto
    did, mid = dto["dialog_id"], dto["id"]
    return {
        **dto,
        "transcript": ctx.transcripts.get(did, mid),
        "transcript_path": f"/v1/transcript/{did}/{mid}",
    }


def _history_kwargs(request: web.Request, limit: int) -> tuple[dict, str]:
    before = _int_param(request, "before_id", minimum=1)
    after = _int_param(request, "after_id", minimum=0)
    around = _int_param(request, "around_id", minimum=1)
    if sum(x is not None for x in (before, after, around)) > 1:
        raise web.HTTPBadRequest(text="before_id, after_id and around_id are mutually exclusive")
    if after is not None:
        # reverse=True is required: min_id alone returns the newest page and skips the gap.
        kwargs, order = {"limit": limit + 1, "min_id": after, "reverse": True}, "asc"
    elif around is not None:
        kwargs, order = {"limit": limit, "offset_id": around, "add_offset": -(limit // 2)}, "desc"
    else:
        kwargs, order = {"limit": limit + 1, "offset_id": before or 0}, "desc"
    topic = _int_param(request, "topic_id", minimum=1)
    search = request.query.get("search", "").strip()
    media_only = request.query.get("media_only") in ("1", "true")
    if topic is not None:
        if search or media_only:
            # Telethon serves topics via GetReplies, which silently ignores search and filters.
            raise web.HTTPBadRequest(text="topic_id can't be combined with search or media_only")
        kwargs["reply_to"] = topic
    if search:
        kwargs["search"] = search[:256]
    if media_only:
        kwargs["filter"] = types.InputMessagesFilterPhotoVideo
    return kwargs, order


async def list_messages(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)
    limit = _int_param(request, "limit", minimum=1) or 50
    limit = min(limit, ctx.settings.max_page_size)
    kwargs, order = _history_kwargs(request, limit)
    rows = await ctx.telegram.iter_messages(peer, **kwargs)
    has_more = len(rows) > limit if "add_offset" not in kwargs else False
    page = rows[:limit]
    audit.info("messages dialog_id=%s params=%s returned=%d", did, dict(request.query), len(page))
    ids = [m.id for m in page]
    body = {
        "dialog_id": did,
        "order": order,
        "messages": [_with_transcript(ctx, message_dto(m, did)) for m in page],
        "has_more": has_more,
        "next_before_id": min(ids) if ids and order == "desc" else None,
        "next_after_id": max(ids) if ids and order == "asc" else None,
    }
    return web.json_response(body)


async def search(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    q = request.query.get("q", "").strip()
    raw_did = request.query.get("dialog_id")
    if not q:
        return json_error(400, "bad_request", "q is required")
    if not raw_did:
        return json_error(400, "bad_request", "dialog_id is required in v1 (no cross-chat search)")
    try:
        did = int(raw_did)
    except ValueError:
        raise web.HTTPNotFound() from None
    peer = _peer(ctx, did)
    limit = min(_int_param(request, "limit", minimum=1) or 20, ctx.settings.max_page_size)
    rows = await ctx.telegram.iter_messages(peer, limit=limit, search=q[:256])
    audit.info("search dialog_id=%s returned=%d", did, len(rows))
    return web.json_response(
        {"dialog_id": did, "messages": [_with_transcript(ctx, message_dto(m, did)) for m in rows]}
    )


async def get_media(request: web.Request) -> web.StreamResponse:
    ctx = _ctx(request)
    did = _dialog_id(request)
    peer = _peer(ctx, did)  # runs on cache hits too
    try:
        message_id = int(request.match_info["message_id"])
    except ValueError:
        raise web.HTTPNotFound() from None
    message = await ctx.telegram.get_message(peer, message_id)
    if message is None or not getattr(message, "media", None):
        raise web.HTTPNotFound()
    thumb = request.query.get("thumb") in ("1", "true")
    try:
        cached = await ctx.media.get(
            did, message, thumb=thumb, max_full_bytes=ctx.settings.media_max_bytes,
            download=ctx.telegram.download_media,
        )
    except MediaTooLarge as exc:
        return json_error(413, "media_too_large", f"{exc}. Use ?thumb=1")
    if cached is None:
        raise web.HTTPNotFound()
    audit.info("media dialog_id=%s message_id=%s thumb=%s", did, message_id, thumb)
    headers = {**response_headers(cached.mime, cached.filename), "Content-Type": cached.mime}
    return web.FileResponse(cached.path, headers=headers)


def add_routes(app: web.Application) -> None:
    app.router.add_get("/v1/chats", list_chats)
    app.router.add_get("/v1/chats/{dialog_id}", get_chat)
    app.router.add_get("/v1/chats/{dialog_id}/messages", list_messages)
    app.router.add_get("/v1/chats/{dialog_id}/pinned", list_pinned)
    app.router.add_get("/v1/chats/{dialog_id}/members", list_members)
    app.router.add_get("/v1/chats/{dialog_id}/messages/{message_id}", get_one_message)
    app.router.add_get("/v1/chats/{dialog_id}/messages/{message_id}/reactions", list_who_reacted)
    app.router.add_get("/v1/chats/{dialog_id}/topics", list_topics)
    app.router.add_get("/v1/media/{dialog_id}/{message_id}", get_media)
    app.router.add_get("/v1/search", search)
