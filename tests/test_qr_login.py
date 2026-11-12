"""QR login loop: a scan that lands during the ~30s token refresh must not strand the wizard."""

from __future__ import annotations

import asyncio

from telethon import errors
from telethon.tl import types

from gateway import telegram_service as ts
from gateway.telegram_service import TelegramService


class FakeQr:
    def __init__(self, recreate_effect):
        self.url = "tg://login?token=a"
        self._recreate_effect = recreate_effect
        self._resp = None

    async def wait(self, timeout=None):
        raise asyncio.TimeoutError()

    async def recreate(self):
        effect = self._recreate_effect
        if isinstance(effect, Exception):
            raise effect
        self._resp = effect
        self.url = "tg://login?token=b"


def _service(authorized: bool) -> TelegramService:
    svc = TelegramService.__new__(TelegramService)
    TelegramService.__init__(svc, session_path=__import__("pathlib").Path("/tmp/none"))

    async def is_authorized():
        return authorized

    async def authorized_cb():
        return None

    svc.is_authorized = is_authorized
    svc._authorized = authorized_cb
    return svc


async def test_refresh_hits_2fa_goes_to_password_screen():
    svc = _service(authorized=False)
    await svc._run_qr(FakeQr(errors.SessionPasswordNeededError(request=None)))
    assert svc.qr_status.status == "password_needed"


async def test_refresh_returns_success_completes_login():
    """No 2FA, scan lands during the refresh: must log in, not ask for a password."""
    svc = _service(authorized=False)
    logged_in = []

    class Client:
        async def _on_login(self, user):
            logged_in.append(user)

    svc._client = Client()
    user = types.User(id=1)
    success = types.auth.LoginTokenSuccess(authorization=types.auth.Authorization(user=user))
    await svc._run_qr(FakeQr(success))
    assert svc.qr_status.status == "done" and logged_in == [user]


async def test_gives_up_after_deadline(monkeypatch):
    monkeypatch.setattr(ts, "QR_GIVE_UP_SECONDS", 0)  # one refresh then give up
    svc = _service(authorized=False)
    await svc._run_qr(FakeQr(types.auth.LoginToken(expires=None, token=b"b")))
    assert svc.qr_status.status == "expired"
