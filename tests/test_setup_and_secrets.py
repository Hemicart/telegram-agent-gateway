from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from gateway.lock import AlreadyRunning, ProcessLock
from gateway.secrets_store import AlreadyClaimed, SecretsStore

H = {"X-Requested-With": "gateway"}


async def _login(client, pw="setup-pass-123"):
    return await client.post("/setup/api/login", json={"password": pw}, headers=H)


async def test_setup_requires_password(client):
    assert (await client.get("/setup/api/state")).status == 401
    assert (await _login(client, "wrong")).status == 401
    assert (await _login(client)).status == 200
    assert (await client.get("/setup/api/state")).status == 200


async def test_csrf_header_required(client):
    resp = await client.post("/setup/api/login", json={"password": "setup-pass-123"})
    assert resp.status == 403


async def test_bruteforce_lockout(client):
    for _ in range(5):
        await _login(client, "wrong")
    assert (await _login(client)).status == 429  # even the right password, while blocked


async def test_token_issue_rotate_and_lock(client, ctx):
    await _login(client)
    first = (await (await client.post("/setup/api/token", headers=H)).json())["token"]
    assert ctx.secrets.check_agent_token(first)
    await ctx.refresh_allowlist()
    assert (await client.post("/setup/api/complete", headers=H)).status == 200
    # Wizard steps locked; admin still works.
    assert (await client.post("/setup/api/credentials", json={}, headers=H)).status == 423
    second = (await (await client.post("/admin/rotate-token", headers=H)).json())["token"]
    assert not ctx.secrets.check_agent_token(first) and ctx.secrets.check_agent_token(second)
    assert (await client.post("/setup/unlock", headers=H)).status == 200
    assert ctx.state.setup_complete is False


async def test_admin_basic_auth(client, ctx):
    import base64

    good = {"Authorization": "Basic " + base64.b64encode(b"admin:setup-pass-123").decode()}
    assert (await client.get("/admin/folder-debug")).status == 401
    assert (await client.get("/admin/folder-debug", headers=good)).status == 200


async def test_pages_and_headers(client):
    for path in ("/", "/setup"):
        resp = await client.get(path)
        assert resp.status == 200, path
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_claim_first_wins(tmp_path):
    store = SecretsStore(tmp_path, None, None)
    assert not store.is_claimed()
    store.claim_owner()
    assert store.is_claimed()
    with pytest.raises(AlreadyClaimed):
        store.claim_owner()
    assert not store.check_setup_password("anything")  # no SETUP_PASSWORD set


def test_sessions_are_hashed_and_revocable(tmp_path):
    store = SecretsStore(tmp_path, None, None)
    a, b = store.create_session(), store.create_session()
    assert a not in (tmp_path / "sessions.json").read_text()
    assert store.check_session(a) and store.check_session(b) and not store.check_session("x")
    store.revoke_session(a)
    assert not store.check_session(a) and SecretsStore(tmp_path, None, None).check_session(b)


def test_old_generated_password_file_is_ignored(tmp_path):
    (tmp_path / "setup_password.json").write_text('{"salt": "00", "hash": "00"}')
    assert not SecretsStore(tmp_path, None, None).is_claimed()


def test_env_token_cannot_be_rotated(tmp_path):
    store = SecretsStore(tmp_path, None, "env-token")
    assert store.check_agent_token("env-token")
    with pytest.raises(PermissionError):
        store.issue_agent_token()


def test_second_process_refused(tmp_path):
    lock = ProcessLock(tmp_path / "gateway.lock")
    lock.acquire()
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(__import__('pathlib').Path(__file__).parents[1] / 'src')!r})
        from gateway.lock import ProcessLock, AlreadyRunning
        try:
            ProcessLock(__import__('pathlib').Path({str(tmp_path / 'gateway.lock')!r})).acquire()
        except AlreadyRunning:
            sys.exit(3)
    """)
    assert subprocess.run([sys.executable, "-c", code]).returncode == 3
    lock.release()
    assert AlreadyRunning


async def test_root_opens_wizard_until_setup_done(client, ctx):
    resp = await client.get("/", allow_redirects=False)
    assert resp.status == 302 and resp.headers["Location"] == "/setup"
    ctx.update_state(setup_complete=True)
    assert (await client.get("/", allow_redirects=False)).status == 200
