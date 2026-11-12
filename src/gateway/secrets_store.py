"""Owner claim, owner sessions, and the agent token (spec §12.3). Hashes only on disk.

No password to dig out of logs: the first browser that presses Start claims the server and gets a
random session cookie. Other browsers sign in (api_id + api_hash, or SETUP_PASSWORD) and get their
own session. Cookies never contain a secret: they're random IDs whose SHA-256 sits in
sessions.json, so checking one is a cheap lookup and "sign out other browsers" just drops hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("gateway.secrets")

MAX_SESSIONS = 20


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_private(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


class AlreadyClaimed(Exception):
    pass


class SecretsStore:
    def __init__(self, data_dir: Path, env_password: str | None, env_token: str | None):
        self._owner_path = data_dir / "owner.json"
        self._sessions_path = data_dir / "sessions.json"
        self._token_path = data_dir / "agent_token.json"
        self._env_password = env_password
        self._env_token = env_token
        self._sessions: list[str] = self._load_sessions()

    # --- owner ------------------------------------------------------------

    @property
    def password_required(self) -> bool:
        return self._env_password is not None

    def is_claimed(self) -> bool:
        return self._owner_path.exists()

    def claim_owner(self) -> None:
        """First caller wins: link() is atomic and fails if the file already exists."""
        tmp = self._owner_path.with_suffix(f".{secrets.token_hex(4)}.tmp")
        _write_private(tmp, {"claimed_at": int(time.time())})
        try:
            os.link(tmp, self._owner_path)
        except FileExistsError as exc:
            raise AlreadyClaimed() from exc
        finally:
            tmp.unlink(missing_ok=True)
        log.info("Server claimed by its owner")

    def check_setup_password(self, candidate: str) -> bool:
        """SETUP_PASSWORD (only when that env var is set)."""
        if not candidate or not self._env_password:
            return False
        return hmac.compare_digest(candidate.encode(), self._env_password.encode())

    # --- owner sessions -----------------------------------------------------

    def _load_sessions(self) -> list[str]:
        if not self._sessions_path.exists():
            return []
        return list(json.loads(self._sessions_path.read_text()).get("sha256", []))

    def _save_sessions(self, hashes: list[str]) -> None:
        self._sessions = hashes[-MAX_SESSIONS:]
        _write_private(self._sessions_path, {"sha256": self._sessions})

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self._save_sessions([*self._sessions, _sha256(token)])
        return token

    def check_session(self, token: str) -> bool:
        if not token or len(token) > 100:
            return False
        digest = _sha256(token)
        return any(hmac.compare_digest(digest, h) for h in self._sessions)

    def revoke_session(self, token: str) -> None:
        digest = _sha256(token)
        self._save_sessions([h for h in self._sessions if h != digest])

    def revoke_other_sessions(self, keep: str) -> int:
        digest = _sha256(keep)
        kept = [h for h in self._sessions if h == digest]
        removed = len(self._sessions) - len(kept)
        self._save_sessions(kept)
        return removed

    # --- agent token ----------------------------------------------------

    @property
    def token_from_env(self) -> bool:
        return self._env_token is not None

    def has_agent_token(self) -> bool:
        return self.token_from_env or self._token_path.exists()

    def issue_agent_token(self) -> str:
        """Create (or replace) the agent token. Returns the plaintext once; only the hash is kept."""
        if self.token_from_env:
            raise PermissionError("AGENT_API_TOKEN is set in the environment; rotate it there")
        token = secrets.token_urlsafe(32)
        _write_private(self._token_path, {"sha256": _sha256(token)})
        return token

    def check_agent_token(self, candidate: str) -> bool:
        if self._env_token:
            return hmac.compare_digest(candidate.encode(), self._env_token.encode())
        if not self._token_path.exists():
            return False
        stored = json.loads(self._token_path.read_text())["sha256"]
        return hmac.compare_digest(_sha256(candidate), stored)
