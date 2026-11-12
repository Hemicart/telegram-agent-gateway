from __future__ import annotations

from types import SimpleNamespace

from conftest import ALLOWED_CHANNEL, OTHER_CHANNEL, make_folder

CH = -1000000000000 - ALLOWED_CHANNEL
OTHER = -1000000000000 - OTHER_CHANNEL


async def test_health_no_auth(client):
    resp = await client.get("/health")
    body = await resp.json()
    assert resp.status == 200 and body["ok"] is True and "token" not in str(body).lower()


async def test_requires_bearer(client, token):
    assert (await client.get("/v1/chats")).status == 401
    assert (await client.get("/v1/chats", headers={"Authorization": "Bearer nope"})).status == 401


async def test_list_chats(client, auth):
    body = await (await client.get("/v1/chats", headers=auth)).json()
    assert {c["dialog_id"] for c in body["chats"]} == {CH, 2222}


async def test_non_allowed_never_calls_telegram(client, auth, tg):
    for path in (f"/v1/chats/{OTHER}", f"/v1/chats/{OTHER}/messages", f"/v1/media/{OTHER}/1",
                 f"/v1/search?q=x&dialog_id={OTHER}", "/v1/chats/abc/messages"):
        resp = await client.get(path, headers=auth)
        assert resp.status == 404, path
    assert tg.history_calls() == []


async def test_messages_default_page(client, auth, tg):
    body = await (await client.get(f"/v1/chats/{CH}/messages?limit=10", headers=auth)).json()
    ids = [m["id"] for m in body["messages"]]
    assert ids == list(range(200, 190, -1))
    assert body["has_more"] is True and body["next_before_id"] == 191 and body["order"] == "desc"
    call = tg.history_calls()[0]
    assert call[2]["limit"] == 11  # limit+1
    assert call[1].channel_id == ALLOWED_CHANNEL  # InputPeer, not a bare int


async def test_before_id(client, auth):
    body = await (await client.get(f"/v1/chats/{CH}/messages?limit=5&before_id=50", headers=auth)).json()
    assert [m["id"] for m in body["messages"]] == [49, 48, 47, 46, 45]


async def test_after_id_returns_the_gap_ascending(client, auth, tg):
    body = await (await client.get(f"/v1/chats/{CH}/messages?limit=5&after_id=100", headers=auth)).json()
    assert [m["id"] for m in body["messages"]] == [101, 102, 103, 104, 105]
    assert body["order"] == "asc" and body["next_after_id"] == 105 and body["has_more"]
    assert tg.history_calls()[0][2]["reverse"] is True


async def test_last_page_has_more_false(client, auth):
    body = await (await client.get(f"/v1/chats/{CH}/messages?limit=5&after_id=197", headers=auth)).json()
    assert [m["id"] for m in body["messages"]] == [198, 199, 200] and body["has_more"] is False


async def test_mutually_exclusive_and_limit_cap(client, auth, tg):
    r = await client.get(f"/v1/chats/{CH}/messages?before_id=5&after_id=1", headers=auth)
    assert r.status == 400
    await client.get(f"/v1/chats/{CH}/messages?limit=5000", headers=auth)
    assert tg.history_calls()[-1][2]["limit"] == 101


async def test_folder_broken_returns_503(client, auth, ctx, tg):
    tg.filters = [make_folder(bots=True)]
    await ctx.try_refresh()
    resp = await client.get("/v1/chats", headers=auth)
    assert resp.status == 503 and (await resp.json())["error"] == "folder_insecure"


async def test_search_requires_dialog(client, auth):
    assert (await client.get("/v1/search?q=hello", headers=auth)).status == 400
    r = await client.get(f"/v1/search?q=hello&dialog_id={CH}", headers=auth)
    assert r.status == 200


async def test_media_svg_is_attachment_sandboxed(client, auth, tg):
    file = SimpleNamespace(mime_type="image/svg+xml", size=20, name="evil.svg", width=None, height=None)
    tg.messages[7] = SimpleNamespace(**{**vars(tg.messages[7]), "media": object(), "file": file,
                                        "document": object()})
    resp = await client.get(f"/v1/media/{CH}/7", headers=auth)
    assert resp.status == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Content-Disposition"].startswith("attachment")


async def test_media_size_cap_checked_before_download(client, auth, tg):
    file = SimpleNamespace(mime_type="video/mp4", size=10**9, name="big.mp4", width=1, height=1)
    tg.messages[8] = SimpleNamespace(**{**vars(tg.messages[8]), "media": object(), "file": file,
                                        "document": object()})
    resp = await client.get(f"/v1/media/{CH}/8", headers=auth)
    assert resp.status == 413
    assert not [c for c in tg.calls if c[0] == "download_media"]


async def test_media_cache_hit_still_checks_allowlist(client, auth, ctx, tg):
    file = SimpleNamespace(mime_type="image/jpeg", size=3, name="a.jpg", width=1, height=1)
    tg.messages[9] = SimpleNamespace(**{**vars(tg.messages[9]), "media": object(), "file": file,
                                        "photo": object()})
    assert (await client.get(f"/v1/media/{CH}/9", headers=auth)).status == 200
    tg.filters = [make_folder(include=[])]
    await ctx.refresh_allowlist()
    assert (await client.get(f"/v1/media/{CH}/9", headers=auth)).status == 404
