"""The single Telethon client and the login flows the setup wizard drives."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, errors, events, functions, types, utils

from .allowlist import ChatInfo
from .config import VERSION

log = logging.getLogger("gateway.telegram")

QR_ATTEMPT_SECONDS = 25
QR_GIVE_UP_SECONDS = 300


@dataclass(frozen=True)
class Transcription:
    status: str  # done | pending
    text: str
    transcription_id: int


@dataclass(frozen=True)
class QrStatus:
    status: str  # idle | pending | password_needed | done | expired | error
    url: str | None = None
    error: str | None = None


class _QrAccepted(Exception):
    """Internal: the QR token was accepted (either path)."""


def _person_name(user: Any) -> str | None:
    return " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) or None


_ROLES = (
    (types.ChannelParticipantCreator, "owner"), (types.ChatParticipantCreator, "owner"),
    (types.ChannelParticipantAdmin, "admin"), (types.ChatParticipantAdmin, "admin"),
)


def _member_dto(user: Any) -> dict:
    participant = getattr(user, "participant", None)
    role = next((name for cls, name in _ROLES if isinstance(participant, cls)), "member")
    return {
        "user_id": user.id,
        "name": _person_name(user),
        "username": getattr(user, "username", None),
        "bot": bool(getattr(user, "bot", False)),
        "role": role,
    }


def describe_entity(entity: Any) -> ChatInfo:
    did = utils.get_peer_id(entity)
    if isinstance(entity, types.User):
        name = " ".join(x for x in (entity.first_name, entity.last_name) if x) or "Deleted account"
        kind = "bot" if entity.bot else "user"
        return ChatInfo(did, name, kind, entity.username, False)
    if isinstance(entity, types.Chat):
        return ChatInfo(did, entity.title, "group", None, False)
    if isinstance(entity, types.Channel):
        kind = "supergroup" if entity.megagroup else "channel"
        return ChatInfo(did, entity.title, kind, entity.username, bool(entity.forum))
    return ChatInfo(did, str(did), "unknown", None, False)


class TelegramService:
    def __init__(self, session_path: Path):
        self._session_path = session_path
        self._client: TelegramClient | None = None
        self._api: tuple[int, str] | None = None
        self._qr_task: asyncio.Task | None = None
        self._qr_status = QrStatus("idle")
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self._on_authorized: Callable[[], Awaitable[None]] | None = None
        self._filter_handler: Callable[[Any], Awaitable[None]] | None = None
        # Telegram finishes long transcriptions later via UpdateTranscribedAudio.
        self._transcripts_done: OrderedDict[int, str] = OrderedDict()
        self._transcript_waiters: dict[int, asyncio.Future] = {}

    # --- lifecycle --------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            raise RuntimeError("Telegram is not configured")
        return self._client

    def on_authorized(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._on_authorized = callback

    def on_folders_changed(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._filter_handler = callback

    async def configure(self, api_id: int, api_hash: str) -> None:
        if self._client is not None and self._api == (api_id, api_hash):
            return
        await self.disconnect()
        # Shows up in the user's Telegram → Settings → Devices under this name.
        client = TelegramClient(
            str(self._session_path), api_id, api_hash,
            device_model="Telegram Agent Gateway", system_version="server", app_version=VERSION,
        )
        await client.connect()
        self._client = client
        self._api = (api_id, api_hash)
        if self._filter_handler:
            client.add_event_handler(
                self._filter_handler,
                events.Raw(
                    (types.UpdateDialogFilter, types.UpdateDialogFilters, types.UpdateDialogFilterOrder)
                ),
            )
        client.add_event_handler(self._on_transcribed, events.Raw(types.UpdateTranscribedAudio))
        self._lock_down_session_file()

    async def disconnect(self) -> None:
        if self._qr_task:
            self._qr_task.cancel()
        self._qr_status = QrStatus("idle")
        if self._client is not None:
            await self._client.disconnect()
        self._client = None

    def _lock_down_session_file(self) -> None:
        for suffix in (".session", ".session-journal"):
            path = Path(str(self._session_path) + suffix)
            if path.exists():
                os.chmod(path, 0o600)

    async def ensure_connected(self) -> bool:
        """Reconnect if Telethon gave up (network blip, Telegram restart). Returns True if connected."""
        if self._client is None:
            return False
        if self._client.is_connected():
            return True
        try:
            await self._client.connect()
            log.info("Reconnected to Telegram")
        except (ConnectionError, OSError) as exc:
            log.warning("Telegram reconnect failed: %s", exc)
        return self._client.is_connected()

    def connection_state(self) -> str:
        if self._client is None:
            return "not_configured"
        return "connected" if self._client.is_connected() else "disconnected"

    async def is_authorized(self) -> bool:
        if self._client is None:
            return False
        try:
            return await self._client.is_user_authorized()
        except (ConnectionError, OSError):
            return False

    async def whoami(self) -> dict | None:
        if not await self.is_authorized():
            return None
        me = await self.client.get_me()
        return {"id": me.id, "name": " ".join(x for x in (me.first_name, me.last_name) if x)}

    async def _authorized(self) -> None:
        self._lock_down_session_file()
        if self._on_authorized:
            await self._on_authorized()

    # --- QR login (primary) -------------------------------------------------

    @property
    def qr_status(self) -> QrStatus:
        return self._qr_status

    async def start_qr(self) -> QrStatus:
        if self._qr_task and not self._qr_task.done():
            return self._qr_status
        if self._qr_status.status == "password_needed":
            return self._qr_status
        try:
            qr = await self.client.qr_login()
        except errors.SessionPasswordNeededError:
            # Scanned before a restart/refresh; Telegram now only wants the 2FA password.
            self._qr_status = QrStatus("password_needed")
            return self._qr_status
        self._qr_status = QrStatus("pending", qr.url)
        self._qr_task = asyncio.create_task(self._run_qr(qr))
        return self._qr_status

    async def _run_qr(self, qr: Any) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + QR_GIVE_UP_SECONDS
        while True:
            try:
                try:
                    await qr.wait(timeout=QR_ATTEMPT_SECONDS)
                except asyncio.TimeoutError:
                    if loop.time() > deadline:
                        self._qr_status = QrStatus("expired")
                        return
                    # Token lives ~30s. A scan can land right as we refresh: then recreate()
                    # itself raises SessionPasswordNeededError or returns a success, so it sits
                    # inside the same handlers as wait() instead of crashing the task.
                    await qr.recreate()
                    resp = getattr(qr, "_resp", None)
                    if not isinstance(resp, types.auth.LoginToken):
                        await self._finish_qr(resp)  # the scan landed during the refresh
                        raise _QrAccepted() from None
                    self._qr_status = QrStatus("pending", qr.url)
                    continue
                raise _QrAccepted()  # wait() already completed the login
            except _QrAccepted:
                self._qr_status = QrStatus("done")
                await self._authorized()
                return
            except errors.SessionPasswordNeededError:
                self._qr_status = QrStatus("password_needed")
                return
            except Exception as exc:  # surfaced to the wizard, not swallowed
                log.exception("QR login failed")
                self._qr_status = QrStatus("error", error=type(exc).__name__)
                return

    async def _finish_qr(self, resp: Any) -> None:
        """Same steps Telethon's QRLogin.wait() takes after a scan (it uses these internals too)."""
        client = self.client
        if isinstance(resp, types.auth.LoginTokenMigrateTo):
            await client._switch_dc(resp.dc_id)
            resp = await client(functions.auth.ImportLoginTokenRequest(resp.token))
        if not isinstance(resp, types.auth.LoginTokenSuccess):
            raise TypeError(f"Unexpected login token response: {type(resp).__name__}")
        await client._on_login(resp.authorization.user)

    # --- phone login (fallback) ---------------------------------------------

    async def send_code(self, phone: str) -> None:
        sent = await self.client.send_code_request(phone)
        self._phone = phone
        self._phone_code_hash = sent.phone_code_hash

    async def sign_in_code(self, code: str) -> str:
        """Returns 'done' or 'password_needed'."""
        if not self._phone:
            raise ValueError("Send the login code first")
        try:
            await self.client.sign_in(self._phone, code, phone_code_hash=self._phone_code_hash)
        except errors.SessionPasswordNeededError:
            self._qr_status = QrStatus("password_needed")
            return "password_needed"
        await self._authorized()
        return "done"

    async def password_hint(self) -> str | None:
        """The hint the user set for their 2FA password, shown on the 2FA screen."""
        try:
            info = await self.client(functions.account.GetPasswordRequest())
        except errors.RPCError:
            return None
        return getattr(info, "hint", None) or None

    async def sign_in_password(self, password: str) -> None:
        await self.client.sign_in(password=password)
        self._qr_status = QrStatus("done")
        await self._authorized()

    async def log_out(self) -> None:
        if self._client is not None and await self.is_authorized():
            await self._client.log_out()

    # --- reads used by the allowlist + API ------------------------------------

    async def fetch_filters(self) -> list[Any]:
        result = await self.client(functions.messages.GetDialogFiltersRequest())
        return list(getattr(result, "filters", result))

    async def describe_peers(self, peers: list[Any]) -> dict[int, ChatInfo]:
        if not peers:
            return {}
        try:
            entities = await self.client.get_entity(peers)
            return {info.dialog_id: info for info in map(describe_entity, entities)}
        except (ValueError, errors.RPCError):
            pass  # one bad peer (left channel, deleted user) fails the batch: go one by one
        out: dict[int, ChatInfo] = {}
        for peer in peers:
            try:
                info = describe_entity(await self.client.get_entity(peer))
            except (ValueError, errors.RPCError) as exc:
                log.warning("Can't describe a folder chat (%s); listing it by id", type(exc).__name__)
                continue
            out[info.dialog_id] = info
        return out

    async def iter_messages(self, peer: Any, **kwargs) -> list[Any]:
        return [m async for m in self.client.iter_messages(peer, **kwargs)]

    async def pinned_messages(self, peer: Any, limit: int) -> list[Any]:
        return await self.iter_messages(peer, limit=limit, filter=types.InputMessagesFilterPinned)

    async def forum_topics(self, peer: Any, limit: int = 100) -> list[dict]:
        result = await self.client(
            functions.messages.GetForumTopicsRequest(
                peer=peer, offset_date=None, offset_id=0, offset_topic=0, limit=limit
            )
        )
        return [
            {
                "topic_id": t.id,
                "title": t.title,
                "closed": bool(t.closed),
                "pinned": bool(t.pinned),
                "last_message_id": t.top_message,
            }
            for t in result.topics
            if isinstance(t, types.ForumTopic)
        ]

    async def chat_details(self, peer: Any) -> dict:
        """Description and member count. One extra request, so callers cache it."""
        if isinstance(peer, types.InputPeerChannel):
            full = (await self.client(functions.channels.GetFullChannelRequest(peer))).full_chat
            return {"description": full.about or None, "members_count": full.participants_count}
        if isinstance(peer, types.InputPeerChat):
            full = (await self.client(functions.messages.GetFullChatRequest(peer.chat_id))).full_chat
            members = getattr(getattr(full, "participants", None), "participants", None)
            return {"description": full.about or None, "members_count": len(members) if members else None}
        if isinstance(peer, types.InputPeerUser):
            full = (await self.client(functions.users.GetFullUserRequest(peer))).full_user
            return {"description": full.about or None, "members_count": None}
        return {"description": None, "members_count": None}

    async def members(self, peer: Any, limit: int, search: str = "") -> list[dict]:
        """Group members with their role. Telegram may refuse for big groups or channels."""
        users = await self.client.get_participants(peer, limit=limit, search=search)
        return [_member_dto(u) for u in users]

    async def who_reacted(self, peer: Any, message_id: int, limit: int) -> list[dict]:
        """Who reacted with what. Telegram only allows this in smaller groups."""
        result = await self.client(
            functions.messages.GetMessageReactionsListRequest(peer=peer, id=message_id, limit=limit)
        )
        names = {u.id: _person_name(u) for u in result.users}
        names.update({c.id: getattr(c, "title", None) for c in result.chats})
        out = []
        for r in result.reactions:
            pid = utils.get_peer_id(r.peer_id, add_mark=False)
            emoji = getattr(r.reaction, "emoticon", None) or (
                "star" if isinstance(r.reaction, types.ReactionPaid) else "custom_emoji"
            )
            out.append({"user_id": pid, "name": names.get(pid), "emoji": emoji,
                        "date": r.date.isoformat().replace("+00:00", "Z") if r.date else None})
        return out

    async def get_message(self, peer: Any, message_id: int) -> Any | None:
        return await self.client.get_messages(peer, ids=message_id)

    async def _on_transcribed(self, update: Any) -> None:
        if update.pending:
            return
        # Updates also arrive for transcriptions the user's own apps request; keep only a few.
        self._transcripts_done[update.transcription_id] = update.text
        while len(self._transcripts_done) > 200:
            self._transcripts_done.popitem(last=False)
        waiter = self._transcript_waiters.pop(update.transcription_id, None)
        if waiter and not waiter.done():
            waiter.set_result(update.text)

    async def transcribe(self, peer: Any, message_id: int, wait_seconds: float) -> Transcription:
        """Telegram's own speech-to-text (Premium). Waits briefly for long messages to finish.

        Raises PremiumAccountRequiredError / FloodPremiumWaitError / RPCError for the caller to map.
        """
        result = await self.client(functions.messages.TranscribeAudioRequest(peer=peer, msg_id=message_id))
        tid = result.transcription_id
        if not result.pending:
            return Transcription("done", result.text, tid)
        if tid in self._transcripts_done:  # update beat us here (or an earlier wait timed out)
            return Transcription("done", self._transcripts_done.pop(tid), tid)
        waiter = self._transcript_waiters.setdefault(tid, asyncio.get_running_loop().create_future())
        try:
            text = await asyncio.wait_for(asyncio.shield(waiter), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return Transcription("pending", result.text or "", tid)
        finally:
            self._transcript_waiters.pop(tid, None)
        return Transcription("done", text, tid)

    async def download_media(self, message: Any, path: Path, thumb: bool) -> Path | None:
        selected = _pick_thumb(message) if thumb else None
        if thumb and selected is None:
            return None
        result = await self.client.download_media(message, file=str(path), thumb=selected)
        return Path(result) if result else None


def _pick_thumb(message: Any) -> Any:
    """Largest preview up to ~800px wide: good for LLM vision, far smaller than the original."""
    if getattr(message, "photo", None):
        sizes = [s for s in message.photo.sizes if getattr(s, "w", 0)]
    elif getattr(message, "document", None) and message.document.thumbs:
        sizes = [s for s in message.document.thumbs if getattr(s, "w", 0)]
    else:
        return None
    if not sizes:
        return None
    small = [s for s in sizes if s.w <= 800]
    return max(small, key=lambda s: s.w) if small else min(sizes, key=lambda s: s.w)
