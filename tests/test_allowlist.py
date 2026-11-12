from __future__ import annotations

import pytest
from telethon.tl import types

from conftest import ALLOWED_CHANNEL, OTHER_CHANNEL, make_folder, peer_channel
from gateway.allowlist import (
    AllowlistUnavailable,
    AmbiguousFolder,
    FolderNeedsConfirmation,
    FolderNotFound,
    InsecureFolder,
    resolve_folder,
)
from gateway.state import PinnedFolder

CH = -1000000000000 - ALLOWED_CHANNEL


def test_resolves_by_title_offline():
    folder = resolve_folder([make_folder()], None, "Agent", allow_broad=False)
    assert folder.folder_id == 5
    assert CH in folder.peers and 2222 in folder.peers


def test_missing_and_ambiguous():
    with pytest.raises(FolderNotFound):
        resolve_folder([make_folder(title="Work")], None, "Agent", False)
    with pytest.raises(AmbiguousFolder):
        resolve_folder([make_folder(5), make_folder(6)], None, "Agent", False)


@pytest.mark.parametrize("flag", ["contacts", "non_contacts", "groups", "broadcasts", "bots"])
def test_broad_flags_refused(flag):
    with pytest.raises(InsecureFolder):
        resolve_folder([make_folder(**{flag: True})], None, "Agent", False)
    assert resolve_folder([make_folder(**{flag: True})], None, "Agent", True).broad_flags == (flag,)


def test_reused_folder_id_fails_closed():
    """User deleted 'Agent'; a new 'Family' folder got the same id."""
    pinned = PinnedFolder(5, "Agent")
    with pytest.raises(FolderNeedsConfirmation) as exc:
        resolve_folder([make_folder(5, "Family", [peer_channel(OTHER_CHANNEL)])], pinned, "Agent", False)
    assert exc.value.found.title == "Family"


def test_deleted_pinned_folder_fails_closed():
    with pytest.raises(FolderNeedsConfirmation):
        resolve_folder([make_folder(7, "Agent")], PinnedFolder(5, "Agent"), "Agent", False)


def test_exclude_and_chatlist():
    f = make_folder()
    f.exclude_peers = [peer_channel(ALLOWED_CHANNEL)]
    assert CH not in resolve_folder([f], None, "Agent", False).peers
    chatlist = types.DialogFilterChatlist(
        id=9, title=types.TextWithEntities("Agent", []), pinned_peers=[],
        include_peers=[peer_channel(ALLOWED_CHANNEL)],
    )
    assert CH in resolve_folder([chatlist], None, "Agent", False).peers


def test_default_filter_skipped():
    assert resolve_folder([types.DialogFilterDefault(), make_folder()], None, "Agent", False)


async def test_broad_flag_mid_run_empties_allowlist(ctx, tg):
    await ctx.refresh_allowlist()
    assert ctx.allowlist.is_ok()
    tg.filters = [make_folder(groups=True)]
    with pytest.raises(InsecureFolder):
        await ctx.refresh_allowlist()
    with pytest.raises(AllowlistUnavailable) as exc:
        ctx.allowlist.current()
    assert exc.value.code == "folder_insecure"


async def test_stale_after_failed_refreshes(ctx, tg):
    now = [0.0]
    ctx.allowlist._clock = lambda: now[0]
    await ctx.refresh_allowlist()
    tg.fail_filters = ConnectionError("down")
    now[0] = 100
    with pytest.raises(ConnectionError):
        await ctx.refresh_allowlist()
    assert ctx.allowlist.is_ok()  # last good kept while fresh
    now[0] = ctx.settings.allowlist_stale_max_seconds + 1
    with pytest.raises(AllowlistUnavailable) as exc:
        ctx.allowlist.current()
    assert exc.value.code == "allowlist_stale"


async def test_pin_persisted_and_confirm_rename(ctx, tg):
    await ctx.refresh_allowlist()
    assert ctx.store.load().pinned_folder == PinnedFolder(5, "Agent")
    tg.filters = [make_folder(5, "Agent v2")]
    with pytest.raises(FolderNeedsConfirmation):
        await ctx.refresh_allowlist()
    assert not ctx.allowlist.is_ok()
    await ctx.confirm_folder()
    assert ctx.allowlist.is_ok()
    assert ctx.state.pinned_folder == PinnedFolder(5, "Agent v2")


async def test_revoked_chat_purges_media_cache(ctx, tg):
    await ctx.refresh_allowlist()
    d = ctx.settings.media_cache_dir / str(CH)
    d.mkdir(parents=True)
    (d / "1.full.jpg").write_bytes(b"x")
    tg.filters = [make_folder(include=[])]
    await ctx.refresh_allowlist()
    assert not d.exists()
