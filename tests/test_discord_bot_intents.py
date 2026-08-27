import discord

import discord_bot as db


def test_bot_uses_minimal_member_cache():
    assert db.intents.members is True
    assert db.member_cache_flags == discord.MemberCacheFlags.none()
