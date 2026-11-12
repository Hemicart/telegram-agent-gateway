"""Single message, members and who-reacted endpoints."""

from __future__ import annotations


from telethon import errors
from telethon.tl import types

from conftest import ALLOWED_CHANNEL, ALLOWED_USER, OTHER_CHANNEL
from gateway.telegram_service import TelegramService, _member_dto

CH = -1000000000000 - ALLOWED_CHANNEL
OTHER = -1000000000000 - OTHER_CHANNEL


async def test_single_message(client, auth):
    body = await (await client.get(f"/v1/chats/{CH}/messages/42", headers=auth)).json()
    assert body["id"] == 42 and body["text"] == "m42"
    assert (await client.get(f"/v1/chats/{CH}/messages/99999", headers=auth)).status == 404


async def test_members(client, auth, tg):
    body = await (await client.get(f"/v1/chats/{CH}/members?q=lam&limit=5000", headers=auth)).json()
    assert body["members"][0]["role"] == "owner"
    assert ("members", 200, "lam") in tg.calls  # limit capped


async def test_members_not_for_dms_and_hidden_groups(client, auth, tg):
    assert (await client.get(f"/v1/chats/{ALLOWED_USER}/members", headers=auth)).status == 400
    tg.members_effect = errors.ChatAdminRequiredError(request=None)
    r = await client.get(f"/v1/chats/{CH}/members", headers=auth)
    assert r.status == 403 and (await r.json())["error"] == "members_hidden"


async def test_who_reacted(client, auth, tg):
    body = await (await client.get(f"/v1/chats/{CH}/messages/7/reactions", headers=auth)).json()
    assert body["reactions"] == [{"user_id": 2, "name": "Bob", "emoji": "🤣", "date": "2026-10-25T10:00:00Z"}]
    tg.reactors_effect = errors.BroadcastForbiddenError(request=None)
    r = await client.get(f"/v1/chats/{CH}/messages/7/reactions", headers=auth)
    assert r.status == 403 and (await r.json())["error"] == "reactors_hidden"


async def test_new_endpoints_respect_the_folder(client, auth, tg):
    for path in (f"/v1/chats/{OTHER}/messages/1", f"/v1/chats/{OTHER}/members",
                 f"/v1/chats/{OTHER}/messages/1/reactions"):
        assert (await client.get(path, headers=auth)).status == 404, path
    assert not [c for c in tg.calls if c[0] in ("members", "who_reacted", "get_message")]


def test_member_roles_from_real_types():
    user = types.User(id=5, first_name="Ann", username="ann")
    user.participant = types.ChannelParticipantAdmin(
        user_id=5, promoted_by=1, date=None, admin_rights=types.ChatAdminRights(), rank=None
    )
    assert _member_dto(user) == {"user_id": 5, "name": "Ann", "username": "ann", "bot": False, "role": "admin"}


async def test_who_reacted_maps_real_response():
    svc = TelegramService(session_path=__import__("pathlib").Path("/tmp/none"))
    resp = types.messages.MessageReactionsList(
        count=1, chats=[], users=[types.User(id=9, first_name="Bob")],
        reactions=[types.MessagePeerReaction(peer_id=types.PeerUser(9), date=None,
                                              reaction=types.ReactionEmoji(emoticon="🔥"))],
    )

    class Client:
        async def __call__(self, request):
            return resp

    svc._client = Client()
    assert await svc.who_reacted(peer=None, message_id=1, limit=10) == [
        {"user_id": 9, "name": "Bob", "emoji": "🔥", "date": None}
    ]
