"""Wires settings, persisted state, secrets, Telegram and the allowlist together."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .allowlist import AllowlistManager, FolderError, FolderNeedsConfirmation
from .config import Settings
from .media import MediaCache
from .ratelimit import FailureLimiter, TokenBucket
from .secrets_store import SecretsStore
from .state import GatewayState, PinnedFolder, StateStore
from .transcripts_store import TranscriptCache

log = logging.getLogger("gateway")


@dataclass
class AppContext:
    settings: Settings
    store: StateStore
    secrets: SecretsStore
    telegram: Any  # TelegramService or a test fake with the same methods
    state: GatewayState = field(default_factory=GatewayState)
    allowlist: AllowlistManager | None = None
    media: MediaCache | None = None
    transcripts: TranscriptCache | None = None
    agent_limiter: TokenBucket | None = None
    login_limiter: FailureLimiter = field(default_factory=FailureLimiter)
    chat_details: dict[int, tuple[float, dict]] = field(default_factory=dict)
    _refresh_task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        self.state = self.store.load()
        self.media = MediaCache(self.settings.media_cache_dir, self.settings.media_cache_max_bytes)
        self.transcripts = TranscriptCache(self.settings.data_dir / "transcripts")
        self.agent_limiter = TokenBucket(self.settings.agent_rate_per_minute)
        self.allowlist = AllowlistManager(
            self.telegram.fetch_filters,
            self.telegram.describe_peers,
            get_pinned=lambda: self.state.pinned_folder,
            wanted_title=self.settings.folder_title,
            allow_broad=self.settings.allow_broad_folder,
            stale_max_seconds=self.settings.allowlist_stale_max_seconds,
            on_revoked=self._purge_revoked,
        )

    def _purge_revoked(self, dialog_ids: set[int]) -> None:
        """A chat left the folder: drop everything we cached from it."""
        self.media.purge(dialog_ids)
        self.transcripts.purge(dialog_ids)
        for did in dialog_ids:
            self.chat_details.pop(did, None)

    # --- state ------------------------------------------------------------

    def update_state(self, **changes) -> GatewayState:
        self.state = self.state.with_changes(**changes)
        self.store.save(self.state)
        return self.state

    def api_credentials(self) -> tuple[int, str] | None:
        api_id = self.settings.api_id or self.state.api_id
        api_hash = self.settings.api_hash or self.state.api_hash
        return (api_id, api_hash) if api_id and api_hash else None

    # --- allowlist ----------------------------------------------------------

    async def refresh_allowlist(self):
        snap = await self.allowlist.refresh()
        if self.state.pinned_folder is None:
            self.update_state(pinned_folder=PinnedFolder(snap.folder.folder_id, snap.folder.title))
            log.info("Pinned folder id=%s title=%r", snap.folder.folder_id, snap.folder.title)
        return snap

    async def confirm_folder(self):
        """User accepted a renamed/missing folder in /setup: re-pin and refresh."""
        err = self.allowlist.folder_error
        if isinstance(err, FolderNeedsConfirmation) and err.found is not None:
            self.update_state(pinned_folder=PinnedFolder(err.found.folder_id, err.found.title))
        else:
            self.update_state(pinned_folder=None)
        return await self.refresh_allowlist()

    async def try_refresh(self) -> None:
        """Background tick: (re)connect if needed, then re-read the folder."""
        creds = self.api_credentials()
        if not self.telegram.configured and creds:
            try:
                await self.telegram.configure(*creds)
            except (ConnectionError, OSError) as exc:
                log.warning("Telegram still unreachable: %s", exc)
                return
        if not await self.telegram.ensure_connected():
            return
        if not await self.telegram.is_authorized():
            return
        try:
            await self.refresh_allowlist()
        except FolderError:
            pass  # already logged; allowlist is empty
        except Exception:
            pass  # network: last good snapshot kept until stale

    def start_background_refresh(self) -> None:
        async def loop() -> None:
            while True:
                await self.try_refresh()
                await asyncio.sleep(self.settings.allowlist_ttl_seconds)

        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
        await self.telegram.disconnect()
