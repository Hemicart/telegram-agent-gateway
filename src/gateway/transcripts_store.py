"""Disk cache for transcripts: one small JSON file per message under DATA_DIR/transcripts/<dialog_id>/."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


class TranscriptCache:
    def __init__(self, root: Path):
        self._root = root

    def _path(self, dialog_id: int, message_id: int) -> Path:
        return self._root / str(dialog_id) / f"{message_id}.json"

    def get(self, dialog_id: int, message_id: int) -> str | None:
        path = self._path(dialog_id, message_id)
        if not path.exists():
            return None
        return json.loads(path.read_text()).get("text")

    def put(self, dialog_id: int, message_id: int, text: str) -> None:
        path = self._path(dialog_id, message_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"text": text, "source": "telegram"}, fh)
        os.replace(tmp, path)

    def purge(self, dialog_ids: set[int]) -> None:
        for did in dialog_ids:
            shutil.rmtree(self._root / str(did), ignore_errors=True)
