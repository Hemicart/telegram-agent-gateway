from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telethon import errors
from telethon.tl import types

from conftest import ALLOWED_CHANNEL, OTHER_CHANNEL, make_folder
from gateway.telegram_service import TelegramService, Transcription

CH = -1000000000000 - ALLOWED_CHANNEL
OTHER = -1000000000000 - OTHER_CHANNEL


def _make_voice(tg, mid):
    file = SimpleNamespace(mime_type="audio/ogg", size=5000, name=None, width=None, height=None)
    tg.messages[mid] = SimpleNamespace(**{**vars(tg.messages[mid]), "media": object(), "file": file,
                                          "voice": object(), "document": object()})


async def test_done_is_cached_and_inlined_in_messages(client, auth, tg):
    _make_voice(tg, 200)
    r = await client.get(f"/v1/transcript/{CH}/200", headers=auth)
    body = await r.json()
    assert r.status == 200 and body["status"] == "done" and body["text"] == "hello from a voice note"
    await client.get(f"/v1/transcript/{CH}/200", headers=auth)  # cache hit
    assert [c for c in tg.calls if c[0] == "transcribe"] == [("transcribe", 200)]
    page = await (await client.get(f"/v1/chats/{CH}/messages?limit=1", headers=auth)).json()
    msg = page["messages"][0]
    assert msg["transcript"] == "hello from a voice note"
    assert msg["transcript_path"] == f"/v1/transcript/{CH}/200"


async def test_pending_returns_202_with_retry_after(client, auth, tg):
    _make_voice(tg, 50)
    tg.transcribe_effect = Transcription("pending", "", 9)
    r = await client.get(f"/v1/transcript/{CH}/50", headers=auth)
    assert r.status == 202 and r.headers["Retry-After"] == "5"
    assert (await r.json())["status"] == "pending"


async def test_no_premium_points_to_media_fallback(client, auth, tg):
    _make_voice(tg, 51)
    tg.transcribe_effect = errors.PremiumAccountRequiredError(request=None)
    body = await (await client.get(f"/v1/transcript/{CH}/51", headers=auth)).json()
    assert body["status"] == "unavailable" and body["reason"] == "telegram_premium_required"
    assert body["media_path"] == f"/v1/media/{CH}/51"


async def test_text_message_rejected(client, auth):
    r = await client.get(f"/v1/transcript/{CH}/52", headers=auth)
    assert r.status == 400 and (await r.json())["error"] == "not_audio"


async def test_outside_folder_never_reaches_telegram(client, auth, tg):
    assert (await client.get(f"/v1/transcript/{OTHER}/1", headers=auth)).status == 404
    assert not [c for c in tg.calls if c[0] in ("transcribe", "get_message")]


async def test_revoked_chat_purges_transcripts(client, auth, ctx, tg):
    _make_voice(tg, 60)
    await client.get(f"/v1/transcript/{CH}/60", headers=auth)
    assert ctx.transcripts.get(CH, 60)
    tg.filters = [make_folder(include=[])]
    await ctx.refresh_allowlist()
    assert ctx.transcripts.get(CH, 60) is None


class _FakeClient:
    def __init__(self, result):
        self.result = result

    async def __call__(self, request):
        return self.result


def _svc(result) -> TelegramService:
    svc = TelegramService(session_path=__import__("pathlib").Path("/tmp/none"))
    svc._client = _FakeClient(result)
    return svc


async def test_service_waits_for_pending_update():
    svc = _svc(types.messages.TranscribedAudio(transcription_id=7, text="", pending=True))
    task = asyncio.create_task(svc.transcribe(peer=None, message_id=1, wait_seconds=2))
    await asyncio.sleep(0.05)
    await svc._on_transcribed(SimpleNamespace(pending=False, transcription_id=7, text="final text"))
    result = await task
    assert result.status == "done" and result.text == "final text"


async def test_service_times_out_to_pending():
    svc = _svc(types.messages.TranscribedAudio(transcription_id=8, text="partial", pending=True))
    result = await svc.transcribe(peer=None, message_id=1, wait_seconds=0.05)
    assert result.status == "pending"


async def test_service_update_arrived_before_wait():
    svc = _svc(types.messages.TranscribedAudio(transcription_id=9, text="", pending=True))
    await svc._on_transcribed(SimpleNamespace(pending=False, transcription_id=9, text="early"))
    result = await svc.transcribe(peer=None, message_id=1, wait_seconds=1)
    assert result.text == "early"
