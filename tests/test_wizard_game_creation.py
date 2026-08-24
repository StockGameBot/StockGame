"""Tests for guided game creation wizard helpers."""


def test_exclusive_picks_schedule_error_detects_missing_pick_date():
    import discord_bot as db

    exc = TypeError("`pick_date` required when `exclusive_picks` is enabled.")
    assert db._is_exclusive_picks_schedule_error(exc)


def test_exclusive_picks_schedule_error_detects_pick_after_start():
    import discord_bot as db

    exc = ValueError(
        "`start_date` must be after `pick_date` when `exclusive_picks` is enabled."
    )
    assert db._is_exclusive_picks_schedule_error(exc)


def test_exclusive_picks_schedule_error_ignores_other_value_errors():
    import discord_bot as db

    assert not db._is_exclusive_picks_schedule_error(ValueError("`end_date` must be after `start_date`."))


def test_exclusive_picks_schedule_error_embed_lists_current_dates():
    import discord_bot as db

    embed = db._exclusive_picks_schedule_error_embed(
        {"start_date": "2026-08-01", "pick_date": "2026-08-15"}
    )
    assert "2026-08-01" in embed.description
    assert "2026-08-15" in embed.description
    assert "exclusive picks" in embed.description.lower()
