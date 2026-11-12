from __future__ import annotations

from dataclasses import replace

import pytest

from gateway.web import create_app

REMOTE = {"Host": "203.0.113.7:8080"}
H = {"X-Requested-With": "gateway"}


@pytest.mark.parametrize("path", ["/setup", "/setup/api/state", "/admin/folder-debug", "/v1/chats"])
async def test_plain_http_from_remote_host_is_blocked(client, path):
    resp = await client.get(path, headers=REMOTE)
    assert resp.status == 403
    if path == "/setup":
        assert "Open this page over HTTPS" in await resp.text()
    else:
        assert (await resp.json())["error"] == "https_required"


async def test_login_post_blocked_before_password_is_read(client):
    resp = await client.post("/setup/api/login", json={"password": "setup-pass-123"}, headers={**REMOTE, **H})
    assert resp.status == 403


async def test_https_via_proxy_header_allowed(client):
    resp = await client.get("/setup", headers={**REMOTE, "X-Forwarded-Proto": "https"})
    assert resp.status == 200
    assert "max-age" in resp.headers["Strict-Transport-Security"]


async def test_login_cookie_secure_behind_https_proxy(client):
    resp = await client.post(
        "/setup/api/login", json={"password": "setup-pass-123"},
        headers={**REMOTE, **H, "X-Forwarded-Proto": "https"},
    )
    assert resp.status == 200
    assert "Secure" in resp.headers["Set-Cookie"]


@pytest.mark.parametrize("host", ["localhost:8099", "127.0.0.1:8099", "[::1]:8099", "gw.localhost"])
async def test_localhost_allowed_for_development(client, host):
    assert (await client.get("/setup", headers={"Host": host})).status == 200


async def test_public_pages_stay_open(client):
    assert (await client.get("/health", headers=REMOTE)).status == 200
    assert (await client.get("/docs", headers=REMOTE)).status == 200


async def test_allow_plain_http_escape_hatch(aiohttp_client, ctx):
    ctx.settings = replace(ctx.settings, allow_plain_http=True)
    client = await aiohttp_client(create_app(ctx))
    assert (await client.get("/setup", headers=REMOTE)).status == 200
