"""Regression tests for the October 2026 code review."""

from __future__ import annotations

from types import SimpleNamespace

from telethon import errors

from conftest import ALLOWED_CHANNEL
from gateway.telegram_service import TelegramService

CH = -1000000000000 - ALLOWED_CHANNEL


def _photo(tg, mid):
    file = SimpleNamespace(mime_type="image/jpeg", size=10, name=None, width=1, height=1)
    tg.messages[mid] = SimpleNamespace(**{**vars(tg.messages[mid]), "media": object(), "file": file,
                                          "photo": object()})


async def test_media_cache_hits_and_thumb_mime(client, auth, tg, tmp_path):
    _photo(tg, 30)
    tg.download_payload = b"\xff\xd8jpeg"

    async def telethon_like_download(message, path, thumb):
        tg.calls.append(("download_media", message.id, thumb))
        out = path if path.suffix else path.with_name(path.name + ".jpg")
        out.write_bytes(tg.download_payload)
        return out

    tg.download_media = telethon_like_download
    for _ in range(3):
        assert (await client.get(f"/v1/media/{CH}/30?thumb=1", headers=auth)).status == 200
    assert [c for c in tg.calls if c[0] == "download_media"] == [("download_media", 30, True)]
    resp = await client.get(f"/v1/media/{CH}/30?thumb=1", headers=auth)
    assert resp.headers["Content-Type"].startswith("image/jpeg")
    assert resp.headers["Content-Disposition"] == "inline"


async def test_topic_with_search_rejected(client, auth):
    r = await client.get(f"/v1/chats/{CH}/messages?topic_id=5&search=hi", headers=auth)
    assert r.status == 400


async def test_telegram_errors_come_back_as_json(client, auth, tg):
    async def boom(peer, **kw):
        raise errors.ChannelPrivateError(request=None)

    tg.iter_messages = boom
    r = await client.get(f"/v1/chats/{CH}/messages", headers=auth)
    assert r.status == 502 and (await r.json())["error"] == "telegram_error"


async def test_health_503_when_set_up_but_disconnected(client, ctx, tg):
    ctx.update_state(setup_complete=True)
    tg.connection_state = lambda: "disconnected"
    r = await client.get("/health")
    assert r.status == 503 and (await r.json())["ok"] is False


async def test_regular_video_is_not_offered_a_transcript(client, auth, tg):
    file = SimpleNamespace(mime_type="video/mp4", size=10, name="a.mp4", width=1, height=1)
    tg.messages[200] = SimpleNamespace(**{**vars(tg.messages[200]), "media": object(), "file": file,
                                           "video": object(), "document": object()})
    page = await (await client.get(f"/v1/chats/{CH}/messages?limit=1", headers=auth)).json()
    assert "transcript_path" not in page["messages"][0]
    assert (await client.get(f"/v1/transcript/{CH}/200", headers=auth)).status == 400


async def test_describe_peers_survives_one_bad_chat():
    svc = TelegramService(session_path=__import__("pathlib").Path("/tmp/none"))
    from telethon.tl import types

    good = types.InputPeerUser(user_id=5, access_hash=1)
    bad = types.InputPeerUser(user_id=6, access_hash=1)

    class Client:
        async def get_entity(self, arg):
            if isinstance(arg, list):
                raise ValueError("one of them is gone")
            if arg is bad:
                raise ValueError("gone")
            return types.User(id=5, first_name="Ann")

    svc._client = Client()
    out = await svc.describe_peers([good, bad])
    assert list(out) == [5] and out[5].title == "Ann"
