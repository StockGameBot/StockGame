"""Central logging setup for StockGame.

Provides:
- Dual rotating file logs (DEBUG+ and ERROR+)
- Console output
- CRITICAL Discord DM alerts to a hardcoded admin list
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Sequence

if TYPE_CHECKING:
    from discord.ext import commands

# Hardcoded recipients for CRITICAL operational alerts (Discord user snowflakes).
CRITICAL_ALERT_USER_IDS: list[int] = [
    329374393715392520,
    1240817181692792934,
    163784331804934144,
]

LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_critical_handler: Optional["CriticalDmHandler"] = None
_configured = False


class CriticalDmHandler(logging.Handler):
    """Forward CRITICAL log records to Discord DMs once the bot is ready."""

    def __init__(self, user_ids: Sequence[int]):
        super().__init__(level=logging.CRITICAL)
        self.user_ids = list(user_ids)
        self._bot: Optional[commands.Bot] = None
        self._pending: list[str] = []
        self.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT))

    def attach_bot(self, bot: commands.Bot) -> None:
        self._bot = bot

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        bot = self._bot
        if bot is None or getattr(bot, "loop", None) is None or not bot.is_ready():
            self._pending.append(message)
            if len(self._pending) > 50:
                self._pending = self._pending[-50:]
            return

        try:
            asyncio.run_coroutine_threadsafe(self._deliver(message), bot.loop)
        except Exception:
            self.handleError(record)

    async def flush_pending(self) -> None:
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        for message in pending:
            await self._deliver(message)

    async def _deliver(self, message: str) -> None:
        bot = self._bot
        if bot is None:
            return
        body = message if len(message) <= 1800 else message[:1800] + "\n…(truncated)"
        content = f"**CRITICAL alert**\n```\n{body}\n```"
        for user_id in self.user_ids:
            try:
                user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                await user.send(content)
            except Exception as exc:
                logging.getLogger("CriticalDmHandler").error(
                    "Failed to DM CRITICAL alert to user %s: %s", user_id, exc
                )


def reset_logging_for_tests() -> None:
    """Tear down handlers so tests can reconfigure logging cleanly."""
    global _configured, _critical_handler
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _critical_handler = None
    _configured = False


def setup_app_logging(
    *,
    console_level: int = logging.INFO,
    root_level: int = logging.DEBUG,
    log_dir: str = "logs",
    force: bool = False,
) -> logging.Logger:
    """
    Configure root logging with dual files + console.

    Files:
      logs/stock_game_debug_<ts>.log  — DEBUG and above
      logs/stock_game_error_<ts>.log  — ERROR and above
    """
    global _configured, _critical_handler
    if _configured and not force:
        return logging.getLogger("DiscordBot")
    if force:
        reset_logging_for_tests()

    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y.%m.%d.%H.%M.%S.%f")
    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT)

    root = logging.getLogger()
    root.setLevel(root_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    debug_path = os.path.join(log_dir, f"stock_game_debug_{stamp}.log")
    debug_file = RotatingFileHandler(
        debug_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    debug_file.setLevel(logging.DEBUG)
    debug_file.setFormatter(formatter)
    root.addHandler(debug_file)

    error_path = os.path.join(log_dir, f"stock_game_error_{stamp}.log")
    error_file = RotatingFileHandler(
        error_path, maxBytes=5_000_000, backupCount=10, encoding="utf-8"
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    root.addHandler(error_file)

    _critical_handler = CriticalDmHandler(CRITICAL_ALERT_USER_IDS)
    root.addHandler(_critical_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _configured = True
    bootstrap = logging.getLogger("LoggingSetup")
    bootstrap.info("Logging configured. debug_log=%s error_log=%s", debug_path, error_path)
    bootstrap.debug("CRITICAL DM recipients: %s", CRITICAL_ALERT_USER_IDS)
    return logging.getLogger("DiscordBot")


def attach_critical_dm_bot(bot: commands.Bot) -> None:
    """Bind the Discord bot so CRITICAL logs can be DM'd to admins."""
    if _critical_handler is not None:
        _critical_handler.attach_bot(bot)


async def flush_critical_dm_queue() -> None:
    if _critical_handler is not None:
        await _critical_handler.flush_pending()


def get_critical_handler() -> Optional[CriticalDmHandler]:
    return _critical_handler


def log_intentional(
    logger: logging.Logger,
    message: str,
    *,
    user_id: Optional[int] = None,
    command: Optional[str] = None,
    **context: object,
) -> None:
    parts = [message]
    if command:
        parts.append(f"command={command}")
    if user_id is not None:
        parts.append(f"user={user_id}")
    for key, value in context.items():
        parts.append(f"{key}={value}")
    logger.info(" | ".join(parts))


def log_unexpected(
    logger: logging.Logger,
    message: str,
    *,
    exc: Optional[BaseException] = None,
    user_id: Optional[int] = None,
    command: Optional[str] = None,
    **context: object,
) -> None:
    parts = [message]
    if command:
        parts.append(f"command={command}")
    if user_id is not None:
        parts.append(f"user={user_id}")
    for key, value in context.items():
        parts.append(f"{key}={value}")
    if exc is not None:
        logger.error(" | ".join(parts), exc_info=exc)
    else:
        logger.error(" | ".join(parts))


DEFAULT_DISCORD_UPLOAD_LIMIT = 8 * 1024 * 1024
UPLOAD_SAFETY_MARGIN = 256 * 1024


def latest_log_path(
    kind: Literal["debug", "error"],
    log_dir: str = "logs",
) -> Optional[Path]:
    directory = Path(log_dir)
    if not directory.is_dir():
        return None
    pattern = f"stock_game_{kind}_*.log"
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def discord_upload_budget(guild_filesize_limit: Optional[int] = None) -> int:
    ceiling = DEFAULT_DISCORD_UPLOAD_LIMIT
    if guild_filesize_limit:
        ceiling = min(guild_filesize_limit, DEFAULT_DISCORD_UPLOAD_LIMIT * 3)
        ceiling = min(ceiling, 24 * 1024 * 1024)
    return max(1024, ceiling - UPLOAD_SAFETY_MARGIN)


def prepare_log_for_upload(
    path: Path,
    max_bytes: int,
) -> tuple[BytesIO, str, bool, int, int]:
    original_size = path.stat().st_size
    filename = path.name

    if original_size <= max_bytes:
        data = path.read_bytes()
        return BytesIO(data), filename, False, original_size, len(data)

    header = (
        f"[truncated: showing the last portion of {filename} "
        f"({original_size} bytes total; Discord upload limit)]\n"
    ).encode("utf-8")
    tail_budget = max(0, max_bytes - len(header))
    with path.open("rb") as handle:
        handle.seek(max(0, original_size - tail_budget))
        tail = handle.read()

    newline = tail.find(b"\n")
    if 0 <= newline < len(tail) - 1:
        tail = tail[newline + 1 :]

    payload = header + tail
    if len(payload) > max_bytes:
        payload = payload[-max_bytes:]

    out_name = f"{path.stem}_tail.log"
    return BytesIO(payload), out_name, True, original_size, len(payload)
