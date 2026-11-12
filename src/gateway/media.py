"""Media download + disk cache (spec §9.5)."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

INLINE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Every media response carries these; files come from other chat members (XSS, spec §9.5).
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cache-Control": "private, max-age=300",
}


class MediaTooLarge(Exception):
    pass


@dataclass(frozen=True)
class CachedFile:
    path: Path
    mime: str
    filename: str


def safe_filename(name: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._")
    return cleaned[:120] or fallback


def response_headers(mime: str, filename: str) -> dict[str, str]:
    disposition = "inline" if mime in INLINE_TYPES else f'attachment; filename="{filename}"'
    return {**BASE_HEADERS, "Content-Disposition": disposition}


Downloader = Callable[[Any, Path, bool], Awaitable[Path | None]]


class MediaCache:
    """Files are stored as <dialog_id>/<message_id>-<full|thumb>.<ext>.

    The stem has no dot on purpose: Telethon only appends the real extension when the target name
    has none, and the extension gives thumbnails the right MIME type.
    """

    def __init__(self, root: Path, max_bytes: int, ttl_seconds: int = 7 * 24 * 3600):
        self._root = root
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._locks: dict[tuple[int, int, str], asyncio.Lock] = {}
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, dialog_id: int) -> Path:
        return self._root / str(dialog_id)

    def _find(self, dialog_id: int, stem: str) -> Path | None:
        d = self._dir(dialog_id)
        if not d.exists():
            return None
        for path in [*d.glob(f"{stem}.*"), d / stem]:
            if not path.is_file():
                continue
            if time.time() - path.stat().st_mtime < self._ttl:
                os.utime(path)  # mark as recently used for eviction
                return path
            path.unlink(missing_ok=True)
        return None

    async def get(
        self,
        dialog_id: int,
        message: Any,
        *,
        thumb: bool,
        max_full_bytes: int,
        download: Downloader,
    ) -> CachedFile | None:
        variant = "thumb" if thumb else "full"
        file = getattr(message, "file", None)
        if not thumb:
            size = getattr(file, "size", None) or 0
            if size > max_full_bytes:  # checked before downloading
                raise MediaTooLarge(f"{size} bytes exceeds the {max_full_bytes} byte cap")
        stem = f"{message.id}-{variant}"
        key = (dialog_id, message.id, variant)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:  # two requests for the same file download it once
                cached = self._find(dialog_id, stem)
                if cached is None:
                    d = self._dir(dialog_id)
                    d.mkdir(parents=True, exist_ok=True)
                    cached = await download(message, d / stem, thumb)
                    if cached is None:
                        return None
                    if not thumb and cached.stat().st_size > max_full_bytes:  # size was unknown upfront
                        cached.unlink(missing_ok=True)
                        raise MediaTooLarge(f"File exceeds the {max_full_bytes} byte cap")
                    self._evict(keep=cached)
        finally:
            if not lock.locked():
                self._locks.pop(key, None)
        mime = (
            (getattr(file, "mime_type", None) if not thumb else None)
            or mimetypes.guess_type(cached.name)[0]
            or "application/octet-stream"
        )
        fallback = f"{dialog_id}_{message.id}{cached.suffix}"
        return CachedFile(cached, mime, safe_filename(getattr(file, "name", None), fallback))

    def purge(self, dialog_ids: set[int]) -> None:
        for did in dialog_ids:
            shutil.rmtree(self._dir(did), ignore_errors=True)

    def _evict(self, keep: Path) -> None:
        """Drop least recently used files (mtime, refreshed on every hit) until under the cap."""
        files = [p for p in self._root.rglob("*") if p.is_file() and p != keep]
        total = keep.stat().st_size + sum(p.stat().st_size for p in files)
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= self._max_bytes:
                break
            total -= path.stat().st_size
            path.unlink(missing_ok=True)
