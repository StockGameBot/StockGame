"""Logging setup, dual files, and CRITICAL Discord DM routing.

Unit tests mock Discord. To receive a real DM on Discord, run the live test:

  set STOCKGAME_LIVE_CRITICAL_DM=1
  python -m pytest tests/test_logging_alerts.py::test_live_critical_dm_to_allowlisted_user -v

Requires DISCORD_TOKEN in .env, and that you share a server with the bot
(and allow DMs from server members).
"""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

from helpers.logging_setup import (
    CRITICAL_ALERT_USER_IDS,
    attach_critical_dm_bot,
    flush_critical_dm_queue,
    get_critical_handler,
    latest_log_path,
    log_intentional,
    log_unexpected,
    prepare_log_for_upload,
    reset_logging_for_tests,
    setup_app_logging,
)


ALLOWED_CRITICAL_USER = 329374393715392520
EXPECTED_CRITICAL_USER_IDS = [
    329374393715392520,
    1240817181692792934,
    163784331804934144,
]


def _critical_recipient_mocks() -> dict[int, MagicMock]:
    users: dict[int, MagicMock] = {}
    for uid in EXPECTED_CRITICAL_USER_IDS:
        user = MagicMock()
        user.send = AsyncMock()
        users[uid] = user
    return users


@pytest.fixture(autouse=True)
def _clean_logging(tmp_path, monkeypatch):
    """Isolate logging config and files per test."""
    reset_logging_for_tests()
    monkeypatch.chdir(tmp_path)
    yield
    reset_logging_for_tests()


def _ready_bot(*, users: dict[int, MagicMock] | None = None):
    """Minimal bot stand-in for CriticalDmHandler."""
    users = users or {}
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.loop = asyncio.new_event_loop()
    bot.get_user.side_effect = lambda uid: users.get(uid)

    async def _fetch(uid: int):
        if uid in users:
            return users[uid]
        raise LookupError(f"unknown user {uid}")

    bot.fetch_user = AsyncMock(side_effect=_fetch)
    return bot


def test_critical_alert_recipients_are_hardcoded_allowlist_only():
    assert CRITICAL_ALERT_USER_IDS == EXPECTED_CRITICAL_USER_IDS
    assert all(uid not in (0, None) for uid in CRITICAL_ALERT_USER_IDS)
    assert len(CRITICAL_ALERT_USER_IDS) == 3


def test_dual_log_files_split_levels(tmp_path):
    setup_app_logging(log_dir=str(tmp_path / "logs"), force=True, console_level=logging.CRITICAL)
    logger = logging.getLogger("TestDualLogs")

    logger.debug("dbg-line")
    logger.info("info-line")
    logger.warning("warn-line")
    logger.error("err-line")
    logger.critical("crit-line")

    for handler in logging.getLogger().handlers:
        handler.flush()

    debug_path = latest_log_path("debug", log_dir=str(tmp_path / "logs"))
    error_path = latest_log_path("error", log_dir=str(tmp_path / "logs"))
    assert debug_path is not None and error_path is not None

    debug_text = debug_path.read_text(encoding="utf-8")
    error_text = error_path.read_text(encoding="utf-8")

    assert "dbg-line" in debug_text
    assert "info-line" in debug_text
    assert "err-line" in debug_text
    assert "crit-line" in debug_text

    assert "dbg-line" not in error_text
    assert "info-line" not in error_text
    assert "err-line" in error_text
    assert "crit-line" in error_text


def test_log_intentional_and_unexpected_helpers(tmp_path):
    log_dir = tmp_path / "logs"
    setup_app_logging(log_dir=str(log_dir), force=True, console_level=logging.CRITICAL)
    logger = logging.getLogger("HelperLogs")

    log_intentional(logger, "user joined game", user_id=42, command="join", game="G1")
    log_unexpected(
        logger,
        "unexpected failure",
        user_id=42,
        command="buy",
        exc=RuntimeError("boom"),
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    debug_text = latest_log_path("debug", log_dir=str(log_dir)).read_text(encoding="utf-8")
    error_text = latest_log_path("error", log_dir=str(log_dir)).read_text(encoding="utf-8")
    assert "user joined game" in debug_text
    assert "command=join" in debug_text
    assert "unexpected failure" in error_text
    assert "command=buy" in error_text


def test_info_and_error_do_not_dm(tmp_path):
    setup_app_logging(log_dir=str(tmp_path / "logs"), force=True, console_level=logging.CRITICAL)
    allowed = MagicMock()
    allowed.send = AsyncMock()
    bot = _ready_bot(users={ALLOWED_CRITICAL_USER: allowed})
    # Force queue path so INFO/ERROR never even schedule deliveries
    bot.is_ready.return_value = False
    attach_critical_dm_bot(bot)

    logging.getLogger("NoDm").info("info should not DM")
    logging.getLogger("NoDm").error("error should not DM")

    handler = get_critical_handler()
    assert handler is not None
    assert handler._pending == []
    allowed.send.assert_not_called()
    bot.loop.close()


def test_critical_dm_only_to_allowlisted_user(tmp_path):
    setup_app_logging(log_dir=str(tmp_path / "logs"), force=True, console_level=logging.CRITICAL)

    recipients = _critical_recipient_mocks()
    stranger = MagicMock()
    stranger.send = AsyncMock()

    bot = _ready_bot(
        users={
            **recipients,
            111111111111111111: stranger,
        }
    )
    # Queue then flush - avoids same-thread run_coroutine_threadsafe races
    bot.is_ready.return_value = False
    attach_critical_dm_bot(bot)
    handler = get_critical_handler()
    assert handler is not None
    assert handler.user_ids == EXPECTED_CRITICAL_USER_IDS

    logging.getLogger("CritPath").critical("simulated CRITICAL operational failure")
    assert len(handler._pending) == 1

    bot.is_ready.return_value = True
    bot.loop.run_until_complete(flush_critical_dm_queue())

    for user in recipients.values():
        user.send.assert_awaited()
    stranger.send.assert_not_called()
    fetch_ids = [c.args[0] for c in bot.fetch_user.await_args_list]
    get_ids = [c.args[0] for c in bot.get_user.call_args_list]
    assert set(fetch_ids + get_ids) == set(EXPECTED_CRITICAL_USER_IDS)

    content = next(iter(recipients.values())).send.await_args.args[0]
    assert "CRITICAL alert" in content
    assert "simulated CRITICAL operational failure" in content

    bot.loop.close()


def test_critical_queued_before_ready_then_flushed_only_to_allowlist(tmp_path):
    setup_app_logging(log_dir=str(tmp_path / "logs"), force=True, console_level=logging.CRITICAL)

    recipients = _critical_recipient_mocks()
    bot = MagicMock()
    bot.is_ready.return_value = False
    bot.loop = None
    bot.get_user.side_effect = lambda uid: recipients.get(uid)
    bot.fetch_user = AsyncMock(side_effect=lambda uid: recipients[uid])

    attach_critical_dm_bot(bot)
    logging.getLogger("EarlyCrit").critical("queued before bot ready")

    handler = get_critical_handler()
    assert handler is not None
    assert len(handler._pending) == 1
    for user in recipients.values():
        user.send.assert_not_called()

    bot.is_ready.return_value = True
    bot.loop = asyncio.new_event_loop()
    bot.loop.run_until_complete(flush_critical_dm_queue())

    for user in recipients.values():
        user.send.assert_awaited_once()
    assert handler._pending == []
    content = next(iter(recipients.values())).send.await_args.args[0]
    assert "queued before bot ready" in content
    bot.loop.close()


def test_critical_dm_failure_is_logged_not_raised(tmp_path):
    log_dir = tmp_path / "logs"
    setup_app_logging(log_dir=str(log_dir), force=True, console_level=logging.CRITICAL)

    recipients = _critical_recipient_mocks()
    flaky = recipients[ALLOWED_CRITICAL_USER]
    flaky.send = AsyncMock(side_effect=RuntimeError("DM blocked"))
    bot = _ready_bot(users=recipients)
    attach_critical_dm_bot(bot)

    bot.loop.run_until_complete(
        get_critical_handler()._deliver("delivery-failure-test")  # type: ignore[union-attr]
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    error_text = latest_log_path("error", log_dir=str(log_dir)).read_text(encoding="utf-8")
    assert "Failed to DM CRITICAL alert" in error_text
    assert str(ALLOWED_CRITICAL_USER) in error_text
    bot.loop.close()


def test_discord_bot_critical_paths_use_allowlisted_logging(tmp_path, monkeypatch):
    """Exercise discord_bot CRITICAL login/token messages without connecting to Discord."""
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token-for-tests")
    monkeypatch.setenv("DB_NAME", str(tmp_path / "bot_test.sqlite"))
    monkeypatch.setenv("OWNER", str(ALLOWED_CRITICAL_USER))

    from db_schema import create

    create(str(tmp_path / "bot_test.sqlite"))

    import importlib
    import sys

    sys.modules.pop("discord_bot", None)
    import discord_bot as db

    importlib.reload(db)

    recipients = _critical_recipient_mocks()
    allowed = recipients[ALLOWED_CRITICAL_USER]
    bot = _ready_bot(users=recipients)
    bot.is_ready.return_value = False
    reset_logging_for_tests()
    setup_app_logging(log_dir=str(tmp_path / "logs"), force=True, console_level=logging.CRITICAL)
    attach_critical_dm_bot(bot)

    db.logger.critical(
        "Discord login failed: invalid DISCORD_TOKEN. Check .env / secrets.",
    )
    db.logger.critical("DISCORD_TOKEN environment variable not found. Bot cannot start.")

    bot.is_ready.return_value = True
    bot.loop.run_until_complete(flush_critical_dm_queue())
    assert allowed.send.await_count == 2
    for call in allowed.send.await_args_list:
        assert "CRITICAL alert" in call.args[0]
    bot.loop.close()


def test_prepare_log_for_upload_truncates(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(b"x" * 5000 + b"\nKEEP_TAIL\n")
    buf, name, truncated, original, uploaded = prepare_log_for_upload(path, max_bytes=200)
    assert truncated is True
    assert original == path.stat().st_size
    assert uploaded <= 200
    assert b"KEEP_TAIL" in buf.getvalue()
    assert name.endswith("_tail.log")


def test_latest_log_path_none_when_missing(tmp_path):
    assert latest_log_path("debug", log_dir=str(tmp_path / "empty")) is None


@pytest.mark.live_discord
def test_live_critical_dm_to_allowlisted_user(tmp_path):
    """Opt-in: log CRITICAL with a real Discord bot and DM the allowlisted user.

    Skipped unless STOCKGAME_LIVE_CRITICAL_DM=1. Uses DISCORD_TOKEN from the
    environment / .env. You should get a Discord DM from the bot.
    """
    from pathlib import Path

    # Fixture chdirs into tmp_path; load .env from the repo root explicitly.
    # Override so the real token wins over the placeholder conftest seeds.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

    if os.getenv("STOCKGAME_LIVE_CRITICAL_DM", "").strip() != "1":
        pytest.skip(
            "Opt-in live Discord DM test. Run with STOCKGAME_LIVE_CRITICAL_DM=1 "
            "and a valid DISCORD_TOKEN (bot must share a server with the allowlisted user)."
        )

    token = (os.getenv("DISCORD_TOKEN") or "").strip()
    if not token:
        pytest.skip("DISCORD_TOKEN is required for the live CRITICAL DM test.")

    import discord
    from discord.ext import commands

    setup_app_logging(
        log_dir=str(tmp_path / "logs"),
        force=True,
        console_level=logging.INFO,
    )

    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)
    attach_critical_dm_bot(bot)

    # Queue before ready - same path as production startup failures
    live_message = (
        "LIVE TEST CRITICAL from tests/test_logging_alerts.py - "
        "safe to ignore; confirms Discord DM alerts work."
    )
    logging.getLogger("LiveCriticalDM").critical(live_message)

    handler = get_critical_handler()
    assert handler is not None
    assert len(handler._pending) >= 1

    errors: list[BaseException] = []
    delivered = {"ok": False}

    @bot.event
    async def on_ready():
        try:
            user = await bot.fetch_user(ALLOWED_CRITICAL_USER)
            assert user.id == ALLOWED_CRITICAL_USER

            # Production path: queued CRITICAL → flush while ready
            await flush_critical_dm_queue()

            # Direct send so this test fails hard if Discord rejects the DM
            # (CriticalDmHandler._deliver swallows send errors into the error log).
            await user.send(
                "**CRITICAL alert** (live pytest)\n"
                f"```\n{live_message}\n```"
            )
            delivered["ok"] = True
        except BaseException as exc:
            errors.append(exc)
        finally:
            await bot.close()

    bot.run(token, log_handler=None)

    assert not errors, (
        f"Live CRITICAL DM failed: {errors!r}\n"
        "Common causes: bot does not share a server with that user, "
        "or the user blocks DMs from server members."
    )
    assert delivered["ok"] is True
    assert handler._pending == []

    error_path = latest_log_path("error", log_dir=str(tmp_path / "logs"))
    if error_path is not None:
        error_text = error_path.read_text(encoding="utf-8")
        assert "Failed to DM CRITICAL alert" not in error_text, (
            "Handler logged a DM failure during flush:\n" + error_text
        )
