"""Voice / video-note transcripts via Telegram's own speech-to-text (Premium), cached on disk.

Without Premium the endpoint says so and points at the media file, so the agent can fall back to
transcribing the audio itself (e.g. Whisper).
"""

from __future__ import annotations

import logging

from aiohttp import web
from telethon import errors

from .api import CTX, _dialog_id, _peer, json_error
from .context import AppContext
from .normalize import media_kind

log = logging.getLogger("gateway.transcripts")
audit = logging.getLogger("gateway.audit")

TRANSCRIBABLE = {"voice", "video_note"}  # what Telegram can transcribe
WAIT_SECONDS = 15
RETRY_AFTER = 5


def _fallback(dialog_id: int, message_id: int, reason: str, message: str) -> web.Response:
    return web.json_response(
        {
            "dialog_id": dialog_id,
            "message_id": message_id,
            "status": "unavailable",
            "reason": reason,
            "message": message,
            "media_path": f"/v1/media/{dialog_id}/{message_id}",
        }
    )


async def get_transcript(request: web.Request) -> web.Response:
    ctx: AppContext = request.app[CTX]
    did = _dialog_id(request)
    peer = _peer(ctx, did)  # allowlist first, also for cache hits
    try:
        message_id = int(request.match_info["message_id"])
    except ValueError:
        raise web.HTTPNotFound() from None

    cached = ctx.transcripts.get(did, message_id)
    if cached is not None:
        return web.json_response(
            {"dialog_id": did, "message_id": message_id, "status": "done", "text": cached, "source": "telegram"}
        )

    message = await ctx.telegram.get_message(peer, message_id)
    if message is None:
        raise web.HTTPNotFound()
    if media_kind(message) not in TRANSCRIBABLE:
        return json_error(400, "not_audio", "Only voice messages and video notes can be transcribed")

    try:
        result = await ctx.telegram.transcribe(peer, message_id, WAIT_SECONDS)
    except errors.PremiumAccountRequiredError:
        return _fallback(
            did, message_id, "telegram_premium_required",
            "This Telegram account has no Premium, so Telegram won't transcribe. "
            "Download media_path and transcribe the audio yourself.",
        )
    except errors.FloodPremiumWaitError as exc:
        return _fallback(
            did, message_id, "telegram_trial_used_up",
            f"Free transcriptions are used up for now (resets in ~{exc.seconds // 60} min). "
            "Download media_path and transcribe the audio yourself.",
        )
    except errors.RPCError as exc:
        log.warning("Transcription failed: %s", type(exc).__name__)
        return _fallback(
            did, message_id, "telegram_transcription_failed",
            "Telegram couldn't transcribe this one. Download media_path and transcribe the audio yourself.",
        )

    audit.info("transcript dialog_id=%s message_id=%s status=%s", did, message_id, result.status)
    if result.status == "pending":
        return web.json_response(
            {"dialog_id": did, "message_id": message_id, "status": "pending", "text": result.text or None},
            status=202,
            headers={"Retry-After": str(RETRY_AFTER)},
        )
    ctx.transcripts.put(did, message_id, result.text)
    return web.json_response(
        {"dialog_id": did, "message_id": message_id, "status": "done", "text": result.text, "source": "telegram"}
    )


def add_routes(app: web.Application) -> None:
    app.router.add_get("/v1/transcript/{dialog_id}/{message_id}", get_transcript)
