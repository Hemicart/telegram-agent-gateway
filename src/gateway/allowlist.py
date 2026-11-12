"""Folder-based allowlist (spec §8). Pure resolution + a fail-closed cache."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from telethon import utils
from telethon.tl import types

from .state import PinnedFolder

log = logging.getLogger("gateway.allowlist")

FOLDER_TYPES = (types.DialogFilter, types.DialogFilterChatlist)
BROAD_FLAGS = ("contacts", "non_contacts", "groups", "broadcasts", "bots")


class FolderError(Exception):
    """Folder config is unusable. The allowlist must be empty while this stands."""

    code = "folder_error"

    def __init__(self, message: str, *, found: "ResolvedFolder | None" = None):
        super().__init__(message)
        self.found = found


class FolderNotFound(FolderError):
    code = "folder_not_found"


class AmbiguousFolder(FolderError):
    code = "folder_ambiguous"


class FolderNeedsConfirmation(FolderError):
    code = "folder_needs_confirmation"


class InsecureFolder(FolderError):
    code = "folder_insecure"


class AllowlistUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedFolder:
    folder_id: int
    title: str
    peers: dict[int, Any]  # dialog_id -> InputPeer (carries access_hash)
    broad_flags: tuple[str, ...]


def plain_title(title: Any) -> str:
    """Folder titles are str in older layers, TextWithEntities in newer ones."""
    if isinstance(title, str):
        return title
    return getattr(title, "text", "") or ""


def _resolve(target: Any) -> ResolvedFolder:
    exclude = {utils.get_peer_id(p) for p in getattr(target, "exclude_peers", None) or []}
    peers: dict[int, Any] = {}
    for p in list(target.include_peers or []) + list(target.pinned_peers or []):
        did = utils.get_peer_id(p)  # offline: InputPeer already has the id
        if did not in exclude:
            peers[did] = p
    flags = tuple(f for f in BROAD_FLAGS if getattr(target, f, False))
    return ResolvedFolder(target.id, plain_title(target.title), peers, flags)


def resolve_folder(
    filters: list[Any], pinned: PinnedFolder | None, wanted_title: str, allow_broad: bool
) -> ResolvedFolder:
    folders = [f for f in filters if isinstance(f, FOLDER_TYPES)]

    if pinned is not None:
        target = next((f for f in folders if f.id == pinned.folder_id), None)
        if target is None:
            raise FolderNeedsConfirmation(
                f"Folder id {pinned.folder_id} ({pinned.title!r}) no longer exists"
            )
        if plain_title(target.title) != pinned.title:
            # Renamed, or the id was reused by a different folder. Fail closed.
            raise FolderNeedsConfirmation(
                f"Folder id {pinned.folder_id} is now titled {plain_title(target.title)!r}, "
                f"expected {pinned.title!r}",
                found=_resolve(target),
            )
    else:
        matches = [f for f in folders if plain_title(f.title) == wanted_title]
        if not matches:
            raise FolderNotFound(f"No folder titled {wanted_title!r}")
        if len(matches) > 1:
            raise AmbiguousFolder(f"{len(matches)} folders are titled {wanted_title!r}; rename one")
        target = matches[0]

    resolved = _resolve(target)
    if resolved.broad_flags and not allow_broad:
        raise InsecureFolder(
            "Folder includes chats by type (" + ", ".join(resolved.broad_flags) + "). "
            "Turn those toggles off and add chats one by one.",
            found=resolved,
        )
    return resolved


@dataclass(frozen=True)
class ChatInfo:
    dialog_id: int
    title: str
    kind: str
    username: str | None
    is_forum: bool


@dataclass(frozen=True)
class Snapshot:
    folder: ResolvedFolder
    chats: dict[int, ChatInfo]
    refreshed_at: float


FetchFilters = Callable[[], Awaitable[list[Any]]]
DescribePeers = Callable[[list[Any]], Awaitable[dict[int, ChatInfo]]]
OnRevoked = Callable[[set[int]], None]


class AllowlistManager:
    """Holds the last good snapshot. Every read goes through `current()`, which fails closed."""

    def __init__(
        self,
        fetch_filters: FetchFilters,
        describe_peers: DescribePeers,
        *,
        get_pinned: Callable[[], PinnedFolder | None],
        wanted_title: str,
        allow_broad: bool,
        stale_max_seconds: int,
        on_revoked: OnRevoked = lambda ids: None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._fetch_filters = fetch_filters
        self._describe_peers = describe_peers
        self._get_pinned = get_pinned
        self._wanted_title = wanted_title
        self._allow_broad = allow_broad
        self._stale_max = stale_max_seconds
        self._on_revoked = on_revoked
        self._clock = clock
        self._snapshot: Snapshot | None = None
        self._folder_error: FolderError | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def folder_error(self) -> FolderError | None:
        return self._folder_error

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _replace_snapshot(self, new: Snapshot | None) -> None:
        old_ids = set(self._snapshot.chats) if self._snapshot else set()
        new_ids = set(new.chats) if new else set()
        self._snapshot = new
        revoked = old_ids - new_ids
        if revoked:
            self._on_revoked(revoked)

    async def refresh(self) -> Snapshot:
        """Re-read the folder. Folder problems empty the allowlist immediately."""
        async with self._lock:
            try:
                filters = await self._fetch_filters()
                folder = resolve_folder(
                    filters, self._get_pinned(), self._wanted_title, self._allow_broad
                )
                known = self._snapshot.chats if self._snapshot else {}
                missing = [p for did, p in folder.peers.items() if did not in known]
                described = await self._describe_peers(missing) if missing else {}
                chats = {}
                for did in folder.peers:
                    info = known.get(did) or described.get(did)
                    chats[did] = info or ChatInfo(did, str(did), "unknown", None, False)
            except FolderError as exc:
                log.warning("Allowlist emptied: %s", exc)
                self._folder_error = exc
                self._last_error = exc.code
                self._replace_snapshot(None)
                raise
            except Exception as exc:  # network, FloodWait: keep last good until stale
                self._last_error = type(exc).__name__
                log.warning("Allowlist refresh failed (%s); keeping last good snapshot", exc)
                raise
            snap = Snapshot(folder, chats, self._clock())
            self._folder_error = None
            self._last_error = None
            self._replace_snapshot(snap)
            return snap

    def current(self) -> Snapshot:
        if self._folder_error is not None:
            raise AllowlistUnavailable(self._folder_error.code, str(self._folder_error))
        if self._snapshot is None:
            raise AllowlistUnavailable("allowlist_not_loaded", "Allowlist has not loaded yet")
        if self._clock() - self._snapshot.refreshed_at > self._stale_max:
            raise AllowlistUnavailable(
                "allowlist_stale", "Allowlist could not be refreshed recently; refusing to serve"
            )
        return self._snapshot

    def is_ok(self) -> bool:
        try:
            self.current()
            return True
        except AllowlistUnavailable:
            return False
