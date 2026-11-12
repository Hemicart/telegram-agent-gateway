"""Runtime settings, read once from the environment (spec §16)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def _opt(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    session_path: Path
    media_cache_dir: Path
    folder_title: str
    allow_broad_folder: bool
    allowlist_ttl_seconds: int
    allowlist_stale_max_seconds: int
    max_page_size: int
    media_max_bytes: int
    media_cache_max_bytes: int
    port: int
    host: str
    log_level: str
    api_id: int | None
    api_hash: str | None
    env_setup_password: str | None
    env_agent_token: str | None
    agent_rate_per_minute: int
    allow_plain_http: bool

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        api_id_raw = _opt("TELEGRAM_API_ID")
        return cls(
            data_dir=data_dir,
            session_path=Path(os.environ.get("TELEGRAM_SESSION_PATH", data_dir / "session")),
            media_cache_dir=Path(os.environ.get("MEDIA_CACHE_DIR", data_dir / "media-cache")),
            folder_title=os.environ.get("AGENT_FOLDER_TITLE", "Agent"),
            allow_broad_folder=os.environ.get("ALLOW_BROAD_FOLDER", "0") == "1",
            allowlist_ttl_seconds=_int("ALLOWLIST_TTL_SECONDS", 60),
            allowlist_stale_max_seconds=_int("ALLOWLIST_STALE_MAX_SECONDS", 300),
            max_page_size=_int("MAX_PAGE_SIZE", 100),
            media_max_bytes=_int("MEDIA_MAX_BYTES", 20 * 1024 * 1024),
            media_cache_max_bytes=_int("MEDIA_CACHE_MAX_BYTES", 500 * 1024 * 1024),
            port=_int("PORT", 8080),
            host=os.environ.get("HOST", "0.0.0.0"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            api_id=int(api_id_raw) if api_id_raw else None,
            api_hash=_opt("TELEGRAM_API_HASH"),
            env_setup_password=_opt("SETUP_PASSWORD"),
            env_agent_token=_opt("AGENT_API_TOKEN"),
            agent_rate_per_minute=_int("AGENT_RATE_PER_MINUTE", 120),
            allow_plain_http=os.environ.get("ALLOW_PLAIN_HTTP", "0") == "1",
        )
