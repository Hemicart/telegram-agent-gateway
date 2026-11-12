"""Small JSON state file under DATA_DIR. Writes are atomic (tmp + rename)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class PinnedFolder:
    folder_id: int
    title: str


@dataclass(frozen=True)
class GatewayState:
    api_id: int | None = None
    api_hash: str | None = None
    pinned_folder: PinnedFolder | None = None
    setup_complete: bool = False

    def with_changes(self, **changes) -> "GatewayState":
        return replace(self, **changes)


class StateStore:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> GatewayState:
        if not self._path.exists():
            return GatewayState()
        raw = json.loads(self._path.read_text())
        pinned = raw.get("pinned_folder")
        return GatewayState(
            api_id=raw.get("api_id"),
            api_hash=raw.get("api_hash"),
            pinned_folder=PinnedFolder(**pinned) if pinned else None,
            setup_complete=bool(raw.get("setup_complete", False)),
        )

    def save(self, state: GatewayState) -> None:
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(state), fh, indent=2)
        os.replace(tmp, self._path)
