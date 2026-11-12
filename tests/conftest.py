from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.allowlist import ChatInfo  # noqa: E402
from gateway.config import Settings  # noqa: E402
from gateway.context import AppContext  # noqa: E402
from gateway.secrets_store import SecretsStore  # noqa: E402
from gateway.state import StateStore  # noqa: E402
from gateway.telegram_service import QrStatus  # noqa: E402
from gateway.web import create_app  # noqa: E402

ALLOWED_CHANNEL = 1111  # dialog id -1000000001111
ALLOWED_USER = 2222
OTHER_CHANNEL = 3333


def peer_channel(cid: int) -> types.InputPeerChannel:
    return types.InputPeerChannel(channel_id=cid, access_hash=99)


def peer_user(uid: int) -> types.InputPeerUser:
    return types.InputPeerUser(user_id=uid, access_hash=77)


def make_folder(fid=5, title="Agent", include=None, **flags) -> types.DialogFilter:
    return types.DialogFilter(
        id=fid,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=[],
        include_peers=include if include is not None else [peer_channel(ALLOWED_CHANNEL), peer_user(ALLOWED_USER)],
        exclude_peers=[],
        **flags,
    )


def fake_message(mid: int, text="hi", media=None, file=None, **extra):
    return SimpleNamespace(
        id=mid,
        date=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
        out=False,
        sender_id=42,
        sender=SimpleNamespace(first_name="Alice", last_name=None, title=None),
        message=text,
        reply_to_msg_id=None,
        reply_to=None,
        fwd_from=None,
        action=None,
        media=media,
        file=file,
        photo=None, sticker=None, voice=None, video_note=None, video=None, audio=None,
        document=extra.pop("document", None),
        **extra,
    )


class FakeTelegram:
    """Same surface as TelegramService; records every Telegram call."""

    def __init__(self):
        self.filters = [make_folder()]
        self.calls: list[tuple] = []
        self.messages = {mid: fake_message(mid, f"m{mid}") for mid in range(1, 201)}
        self.fail_filters: Exception | None = None
        self.authorized = True
        self.qr_status = QrStatus("idle")
        self.download_payload = b"<svg onload=alert(1)>"
        from gateway.telegram_service import Transcription

        self.transcribe_effect = Transcription("done", "hello from a voice note", 1)

    async def fetch_filters(self):
        self.calls.append(("fetch_filters",))
        if self.fail_filters:
            raise self.fail_filters
        return list(self.filters)

    async def describe_peers(self, peers):
        from telethon import utils

        out = {}
        for p in peers:
            did = utils.get_peer_id(p)
            kind = "user" if isinstance(p, types.InputPeerUser) else "supergroup"
            out[did] = ChatInfo(did, f"Chat {did}", kind, None, False)
        return out

    async def iter_messages(self, peer, **kw):
        self.calls.append(("iter_messages", peer, kw))
        ids = sorted(self.messages, reverse=True)
        if kw.get("reverse"):
            ids = sorted(i for i in ids if i > kw.get("min_id", 0))
        elif kw.get("offset_id"):
            ids = [i for i in ids if i < kw["offset_id"] - kw.get("add_offset", 0)]
        return [self.messages[i] for i in ids[: kw.get("limit") or None]]

    async def pinned_messages(self, peer, limit):
        self.calls.append(("pinned", limit))
        return [m for m in self.messages.values() if getattr(m, "pinned", False)][:limit]

    async def forum_topics(self, peer, limit=100):
        self.calls.append(("topics",))
        return [{"topic_id": 3, "title": "Launch", "closed": False, "pinned": True, "last_message_id": 99}]

    members_effect = None
    reactors_effect = None

    async def members(self, peer, limit, search=""):
        self.calls.append(("members", limit, search))
        if isinstance(self.members_effect, Exception):
            raise self.members_effect
        return [{"user_id": 1, "name": "Alice", "username": "alice", "bot": False, "role": "owner"}]

    async def who_reacted(self, peer, message_id, limit):
        self.calls.append(("who_reacted", message_id))
        if isinstance(self.reactors_effect, Exception):
            raise self.reactors_effect
        return [{"user_id": 2, "name": "Bob", "emoji": "🤣", "date": "2026-10-25T10:00:00Z"}]

    async def chat_details(self, peer):
        self.calls.append(("chat_details",))
        return {"description": "Growth team chat", "members_count": 12}

    async def get_message(self, peer, mid):
        self.calls.append(("get_message", peer, mid))
        return self.messages.get(mid)

    async def transcribe(self, peer, message_id, wait_seconds):
        self.calls.append(("transcribe", message_id))
        effect = self.transcribe_effect
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def download_media(self, message, path, thumb):
        self.calls.append(("download_media", message.id, thumb))
        # Like Telethon: append the real extension only when the target name has none.
        out = path if path.suffix else path.with_name(path.name + ".svg")
        out.write_bytes(self.download_payload)
        return out

    configured = True

    async def configure(self, api_id, api_hash):
        return None

    async def ensure_connected(self):
        return True

    async def is_authorized(self):
        return self.authorized

    def connection_state(self):
        return "connected"

    async def whoami(self):
        return {"id": 1, "name": "Test"}

    async def disconnect(self):
        pass

    def history_calls(self):
        return [c for c in self.calls if c[0] in ("iter_messages", "get_message", "download_media")]


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        data_dir=tmp_path,
        session_path=tmp_path / "session",
        media_cache_dir=tmp_path / "media-cache",
        env_setup_password="setup-pass-123",
        env_agent_token=None,
        api_id=None,
        api_hash=None,
        allow_broad_folder=False,
        folder_title="Agent",
    )


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def ctx(settings, tg) -> AppContext:
    secrets = SecretsStore(settings.data_dir, settings.env_setup_password, settings.env_agent_token)
    return AppContext(settings, StateStore(settings.data_dir / "state.json"), secrets, tg)


@pytest.fixture
async def client(aiohttp_client, ctx):
    return await aiohttp_client(create_app(ctx))


@pytest.fixture
async def token(ctx) -> str:
    await ctx.refresh_allowlist()
    return ctx.secrets.issue_agent_token()


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}
