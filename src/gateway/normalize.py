"""Telethon Message -> agent DTO (spec §18.1)."""

from __future__ import annotations

from typing import Any

from . import rich


def _iso(dt: Any) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def media_kind(message: Any) -> str | None:
    """Kind of downloadable file, or None (no media, or inline data like polls / link previews)."""
    if not getattr(message, "media", None):
        return None
    if rich.inline_media(message) is not None or rich.link_preview(message) is not None:
        return None
    for kind in ("photo", "sticker", "voice", "video_note", "video", "audio"):
        if getattr(message, kind, None):
            return kind
    if getattr(message, "document", None):
        return "document"
    return "other"


def media_dto(message: Any, dialog_id: int) -> dict | None:
    inline = rich.inline_media(message)
    if inline is not None:
        return inline
    kind = media_kind(message)
    if kind is None:
        return None
    file = getattr(message, "file", None)
    return {
        "type": kind,
        "mime": getattr(file, "mime_type", None),
        "size": getattr(file, "size", None),
        "file_name": getattr(file, "name", None),
        "width": getattr(file, "width", None),
        "height": getattr(file, "height", None),
        "download_path": f"/v1/media/{dialog_id}/{message.id}",
    }


def sender_name(message: Any) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    title = getattr(sender, "title", None)
    if title:
        return title
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    return " ".join(p for p in parts if p) or None


def topic_id(message: Any) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None or not getattr(reply, "forum_topic", False):
        return None
    return getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)


def fwd_dto(message: Any) -> dict | None:
    fwd = getattr(message, "fwd_from", None)
    if fwd is None:
        return None
    return {"from_name": getattr(fwd, "from_name", None), "date": _iso(getattr(fwd, "date", None))}


def message_dto(message: Any, dialog_id: int) -> dict:
    return {
        "id": message.id,
        "dialog_id": dialog_id,
        "date": _iso(message.date),
        "out": bool(getattr(message, "out", False)),
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": sender_name(message),
        "text": getattr(message, "message", None) or None,
        "reply_to_msg_id": getattr(message, "reply_to_msg_id", None),
        "topic_id": topic_id(message),
        "fwd_from": fwd_dto(message),
        "edited": _iso(getattr(message, "edit_date", None)),
        "pinned": bool(getattr(message, "pinned", False)),
        "album_id": getattr(message, "grouped_id", None),
        "links": rich.links(message),
        "mentions": rich.mentions(message),
        "reactions": rich.reactions(message),
        "link_preview": rich.link_preview(message),
        **rich.counters(message),
        "service": rich.service_event(message),
        "media": media_dto(message, dialog_id),
    }


def chat_dto(info: Any) -> dict:
    return {
        "dialog_id": info.dialog_id,
        "title": info.title,
        "kind": info.kind,
        "username": info.username,
        "is_forum": info.is_forum,
    }
