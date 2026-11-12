"""Links, mentions, reactions, polls, previews, events, counters; new endpoints; never-mark-read guard."""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl import types

from conftest import ALLOWED_CHANNEL, ALLOWED_USER, fake_message
from gateway import rich
from gateway.normalize import message_dto

CH = -1000000000000 - ALLOWED_CHANNEL
TWE = types.TextWithEntities


def test_links_including_hidden_ones_after_emoji():
    text = "🚀 see here and https://x.com/a"
    msg = SimpleNamespace(message=text, entities=[
        types.MessageEntityTextUrl(offset=7, length=4, url="https://docs.example/plan"),  # 🚀 = 2 UTF-16 units
        types.MessageEntityUrl(offset=16, length=15),
    ])
    assert rich.links(msg) == [
        {"text": "here", "url": "https://docs.example/plan"},
        {"text": "https://x.com/a", "url": "https://x.com/a"},
    ]


def test_mentions():
    msg = SimpleNamespace(message="hi @bob and Ann", entities=[
        types.MessageEntityMention(offset=3, length=4),
        types.MessageEntityMentionName(offset=12, length=3, user_id=42),
    ])
    assert rich.mentions(msg) == [{"text": "@bob", "user_id": None}, {"text": "Ann", "user_id": 42}]


def test_reactions():
    msg = SimpleNamespace(reactions=types.MessageReactions(results=[
        types.ReactionCount(reaction=types.ReactionEmoji(emoticon="👍"), count=3, chosen_order=0),
        types.ReactionCount(reaction=types.ReactionCustomEmoji(document_id=1), count=1),
    ]))
    assert rich.reactions(msg) == [
        {"emoji": "👍", "count": 3, "mine": True},
        {"emoji": "custom_emoji", "count": 1, "mine": False},
    ]


def test_poll_inline_with_votes():
    poll = types.Poll(id=1, question=TWE("Ship Friday?", []), hash=0, answers=[
        types.PollAnswer(text=TWE("Yes", []), option=b"0"),
        types.PollAnswer(text=TWE("No", []), option=b"1"),
    ])
    results = types.PollResults(total_voters=5, results=[
        types.PollAnswerVoters(option=b"0", voters=4, chosen=True),
        types.PollAnswerVoters(option=b"1", voters=1),
    ])
    msg = SimpleNamespace(media=types.MessageMediaPoll(poll=poll, results=results))
    dto = rich.inline_media(msg)
    assert dto["type"] == "poll" and dto["question"] == "Ship Friday?" and dto["total_voters"] == 5
    assert dto["options"][0] == {"text": "Yes", "votes": 4, "chosen_by_me": True, "correct": None}


def test_link_preview_and_no_download():
    page = types.WebPage(id=1, url="https://a.b/post", display_url="a.b/post", hash=0,
                         site_name="A", title="Post", description="About")
    m = fake_message(5, text="https://a.b/post", media=types.MessageMediaWebPage(webpage=page))
    dto = message_dto(m, CH)
    assert dto["link_preview"] == {"url": "https://a.b/post", "site_name": "A", "title": "Post",
                                   "description": "About"}
    assert dto["media"] is None


def test_contact_has_no_phone_number():
    m = SimpleNamespace(media=types.MessageMediaContact(phone_number="+4917012345", first_name="Ann",
                                                        last_name="Lee", vcard="", user_id=7))
    assert rich.inline_media(m) == {"type": "contact", "name": "Ann Lee", "user_id": 7}


def test_location_and_dice():
    geo = SimpleNamespace(media=types.MessageMediaGeo(geo=types.GeoPoint(long=13.4, lat=52.5, access_hash=0)))
    assert rich.inline_media(geo) == {"type": "location", "lat": 52.5, "long": 13.4}
    dice = SimpleNamespace(media=types.MessageMediaDice(value=6, emoticon="🎲"))
    assert rich.inline_media(dice) == {"type": "dice", "emoji": "🎲", "value": 6}


def test_service_events():
    add = SimpleNamespace(action=types.MessageActionChatAddUser(users=[1, 2]))
    assert rich.service_event(add) == {"type": "chat_add_user", "users": [1, 2]}
    rename = SimpleNamespace(action=types.MessageActionChatEditTitle(title="Growth"))
    assert rich.service_event(rename) == {"type": "chat_edit_title", "title": "Growth"}
    assert rich.service_event(SimpleNamespace(action=types.MessageActionPinMessage()))["type"] == "pin_message"
    assert rich.service_event(SimpleNamespace(action=None)) is None


def test_full_dto_fields():
    m = fake_message(9, text="hi")
    m.edit_date = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
    m.pinned, m.grouped_id, m.views, m.forwards = True, 777, 120, 3
    m.replies = types.MessageReplies(replies=4, replies_pts=0)
    dto = message_dto(m, CH)
    assert dto["edited"] == "2026-10-25T09:00:00Z" and dto["pinned"] is True and dto["album_id"] == 777
    assert (dto["views"], dto["forwards"], dto["replies"]) == (120, 3, 4)
    assert dto["links"] == [] and dto["reactions"] == [] and dto["service"] is None


async def test_pinned_endpoint(client, auth, tg):
    tg.messages[150].pinned = True
    body = await (await client.get(f"/v1/chats/{CH}/pinned", headers=auth)).json()
    assert [m["id"] for m in body["messages"]] == [150] and body["messages"][0]["pinned"] is True


async def test_topics_only_for_forums(client, auth, ctx, tg):
    assert (await client.get(f"/v1/chats/{CH}/topics", headers=auth)).status == 400
    snap = ctx.allowlist.current()
    snap.chats[CH] = snap.chats[CH].__class__(**{**vars(snap.chats[CH]), "is_forum": True})
    body = await (await client.get(f"/v1/chats/{CH}/topics", headers=auth)).json()
    assert body["topics"][0]["title"] == "Launch"


async def test_chat_details_cached(client, auth, tg):
    for _ in range(3):
        body = await (await client.get(f"/v1/chats/{ALLOWED_USER}", headers=auth)).json()
    assert body["description"] == "Growth team chat" and body["members_count"] == 12
    assert [c for c in tg.calls if c[0] == "chat_details"] == [("chat_details",)]


def test_never_marks_read_or_goes_online():
    """Reading must never send read receipts, mark voice notes as listened, or set 'online'."""
    src = "\n".join(p.read_text() for p in pathlib.Path(__file__).parents[1].joinpath("src").rglob("*.py"))
    for forbidden in ("ReadHistoryRequest", "send_read_acknowledge", "ReadMessageContentsRequest",
                      "ReadMentionsRequest", "ReadReactionsRequest", "UpdateStatusRequest",
                      "mark_read", "ReadDiscussionRequest", "SetTypingRequest"):
        assert forbidden not in src, forbidden
