"""Owner claim and owner sessions (no SETUP_PASSWORD set)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gateway.context import AppContext
from gateway.secrets_store import SecretsStore
from gateway.state import StateStore
from gateway.web import create_app

H = {"X-Requested-With": "gateway"}
KEYS = {"api_id": "1234567", "api_hash": "a" * 32}


def _ctx(settings, tg, password=None):
    s = replace(settings, env_setup_password=password)
    return AppContext(s, StateStore(s.data_dir / "state.json"), SecretsStore(s.data_dir, password, None), tg)


@pytest.fixture
async def fresh(aiohttp_client, settings, tg):
    tg.authorized = False  # a brand-new server
    return await aiohttp_client(create_app(_ctx(settings, tg)))


def _cookie(resp) -> str:
    return resp.cookies["tag_owner"].value


async def test_first_browser_claims_then_others_locked_out(fresh):
    st = await (await fresh.get("/setup/api/status")).json()
    assert st == {"authed": False, "claimed": False, "password_required": False, "locked": False}
    resp = await fresh.post("/setup/api/claim", json={}, headers=H)
    assert resp.status == 200
    set_cookie = resp.headers["Set-Cookie"]
    assert "Max-Age" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    assert (await fresh.get("/setup/api/state")).status == 200

    fresh.session.cookie_jar.clear()  # a second browser
    assert (await fresh.post("/setup/api/claim", json={}, headers=H)).status == 409
    assert (await fresh.get("/setup/api/state")).status == 401
    assert (await fresh.post("/setup/api/token", headers=H)).status == 401


async def test_set_up_server_without_claim_file_is_not_claimable(aiohttp_client, settings, tg):
    """Upgrade from a version without owner.json: logged in already, must not be up for grabs."""
    client = await aiohttp_client(create_app(_ctx(settings, tg)))  # tg.authorized = True
    resp = await client.post("/setup/api/claim", json={}, headers=H)
    assert resp.status == 409


async def test_session_cookie_holds_no_secret_and_survives_restart(fresh, settings, tg, aiohttp_client):
    resp = await fresh.post("/setup/api/claim", json={}, headers=H)
    token = _cookie(resp)
    assert token not in (settings.data_dir / "sessions.json").read_text()  # only the hash is stored
    restarted = await aiohttp_client(create_app(_ctx(settings, tg)))
    assert (await restarted.get("/setup/api/state", cookies={"tag_owner": token})).status == 200
    assert (await restarted.get("/setup/api/state", cookies={"tag_owner": "forged"})).status == 401


async def test_sign_in_with_api_keys_and_sign_out_others(fresh):
    first = _cookie(await fresh.post("/setup/api/claim", json={}, headers=H))
    await fresh.post("/setup/api/credentials", json=KEYS, headers=H)
    fresh.session.cookie_jar.clear()
    resp = await fresh.post("/setup/api/login", json={"api_id": "1234567", "api_hash": "A" * 32}, headers=H)
    assert resp.status == 200
    second = _cookie(resp)
    out = await (await fresh.post("/setup/api/sign-out-others", headers=H)).json()
    assert out["signed_out"] == 1
    fresh.session.cookie_jar.clear()
    assert (await fresh.get("/setup/api/state", cookies={"tag_owner": first})).status == 401
    assert (await fresh.get("/setup/api/state", cookies={"tag_owner": second})).status == 200


async def test_wrong_api_keys_limited_per_ip_but_not_globally(fresh):
    await fresh.post("/setup/api/claim", json={}, headers=H)
    await fresh.post("/setup/api/credentials", json=KEYS, headers=H)
    fresh.session.cookie_jar.clear()
    bad = {"api_id": "1234567", "api_hash": "b" * 32}
    attacker = {**H, "X-Forwarded-For": "203.0.113.9"}
    resp = await fresh.post("/setup/api/login", json=bad, headers=attacker)
    assert resp.status == 401 and (await resp.json())["error"] == "wrong_keys"
    for _ in range(30):
        await fresh.post("/setup/api/login", json=bad, headers=attacker)
    assert (await fresh.post("/setup/api/login", json=KEYS, headers=attacker)).status == 429
    owner = {**H, "X-Forwarded-For": "198.51.100.4"}  # the real owner, elsewhere
    assert (await fresh.post("/setup/api/login", json=KEYS, headers=owner)).status == 200


async def test_public_demo_keys_rejected(fresh):
    await fresh.post("/setup/api/claim", json={}, headers=H)
    resp = await fresh.post("/setup/api/credentials", json={"api_id": "2040", "api_hash": "b" * 32}, headers=H)
    assert resp.status == 400 and "public" in (await resp.json())["message"]


async def test_claim_requires_setup_password_when_set(aiohttp_client, settings, tg):
    tg.authorized = False
    client = await aiohttp_client(create_app(_ctx(settings, tg, password="setup-pass-123")))
    assert (await client.post("/setup/api/claim", json={"password": "wrong"}, headers=H)).status == 401
    assert (await client.post("/setup/api/claim", json={"password": "setup-pass-123"}, headers=H)).status == 200
    assert (await client.get("/setup/api/state")).status == 200
