"""The parts of a Telegram message beyond text and files: links, mentions, reactions, polls,
link previews, locations and system events. Pure functions over Telethon objects."""

from __future__ import annotations

import re
from typing import Any

from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl import types


def _plain(value: Any) -> str:
    """Telegram sends some texts as TextWithEntities in newer layers."""
    return value if isinstance(value, str) else getattr(value, "text", "") or ""


def _slice(text: str, entity: Any) -> str:
    # Entity offsets count UTF-16 code units, so emoji before a link shift plain-str indexes.
    wide = add_surrogate(text)
    return del_surrogate(wide[entity.offset : entity.offset + entity.length])


def links(message: Any) -> list[dict]:
    """Every URL, including ones hidden behind words ("click here")."""
    text = getattr(message, "message", None) or ""
    out = []
    for e in getattr(message, "entities", None) or []:
        if isinstance(e, types.MessageEntityTextUrl):
            out.append({"text": _slice(text, e), "url": e.url})
        elif isinstance(e, types.MessageEntityUrl):
            url = _slice(text, e)
            out.append({"text": url, "url": url})
    return out


def mentions(message: Any) -> list[dict]:
    text = getattr(message, "message", None) or ""
    out = []
    for e in getattr(message, "entities", None) or []:
        if isinstance(e, types.MessageEntityMention):
            out.append({"text": _slice(text, e), "user_id": None})
        elif isinstance(e, types.MessageEntityMentionName):
            out.append({"text": _slice(text, e), "user_id": e.user_id})
    return out


def reactions(message: Any) -> list[dict]:
    block = getattr(message, "reactions", None)
    out = []
    for r in getattr(block, "results", None) or []:
        reaction = r.reaction
        if isinstance(reaction, types.ReactionEmoji):
            emoji = reaction.emoticon
        elif isinstance(reaction, types.ReactionCustomEmoji):
            emoji = "custom_emoji"
        elif isinstance(reaction, types.ReactionPaid):
            emoji = "star"
        else:
            continue
        out.append({"emoji": emoji, "count": r.count, "mine": r.chosen_order is not None})
    return out


def link_preview(message: Any) -> dict | None:
    media = getattr(message, "media", None)
    page = getattr(media, "webpage", None) if isinstance(media, types.MessageMediaWebPage) else None
    if not isinstance(page, types.WebPage):
        return None  # pending / empty previews carry nothing useful
    return {
        "url": page.url,
        "site_name": page.site_name,
        "title": page.title,
        "description": page.description,
    }


def _poll(media: types.MessageMediaPoll) -> dict:
    poll, results = media.poll, media.results
    by_option = {v.option: v for v in (getattr(results, "results", None) or [])}
    options = []
    for answer in poll.answers:
        votes = by_option.get(answer.option)
        options.append(
            {
                "text": _plain(answer.text),
                "votes": getattr(votes, "voters", None),
                "chosen_by_me": bool(getattr(votes, "chosen", False)),
                "correct": bool(getattr(votes, "correct", False)) if poll.quiz else None,
            }
        )
    return {
        "type": "poll",
        "question": _plain(poll.question),
        "options": options,
        "total_voters": getattr(results, "total_voters", None),
        "closed": bool(poll.closed),
        "quiz": bool(poll.quiz),
        "multiple_choice": bool(poll.multiple_choice),
    }


def inline_media(message: Any) -> dict | None:
    """Media that is data, not a file: nothing to download, everything inline."""
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        return _poll(media)
    if isinstance(media, types.MessageMediaGeo) and isinstance(media.geo, types.GeoPoint):
        return {"type": "location", "lat": media.geo.lat, "long": media.geo.long}
    if isinstance(media, types.MessageMediaDice):
        return {"type": "dice", "emoji": media.emoticon, "value": media.value}
    if isinstance(media, types.MessageMediaContact):
        # Name only: a shared phone number is someone else's personal data.
        name = " ".join(x for x in (media.first_name, media.last_name) if x)
        return {"type": "contact", "name": name or None, "user_id": media.user_id or None}
    return None


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def service_event(message: Any) -> dict | None:
    """System messages ("Alice added Bob", "pinned a message", "renamed the group")."""
    action = getattr(message, "action", None)
    if action is None:
        return None
    event: dict = {"type": _snake(type(action).__name__.removeprefix("MessageAction"))}
    for field in ("users", "user_id", "inviter_id", "title"):
        value = getattr(action, field, None)
        if value is not None:
            event[field] = _plain(value) if field == "title" else value
    return event


def counters(message: Any) -> dict:
    replies = getattr(message, "replies", None)
    return {
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies": getattr(replies, "replies", None),
    }
