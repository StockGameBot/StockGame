"""Rich recurring-leaderboard image + height-budget helpers.

Each player occupies one block: a two-line stat panel on the left and a grid of
holding chips filling the right. Chips are ordered best performer first, reading
left to right and then down.
"""

from __future__ import annotations

import math
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Union

from PIL import Image, ImageDraw, ImageFont

from helpers.views import LeaderboardImageGenerator
from helpers.affiliations import load_affiliation_icon

LEADERBOARD_N_CANDIDATES = (5, 10, 15, 20, 25, 30)
DEFAULT_MAX_IMAGE_HEIGHT = 3500

CHIPS_PER_ROW = 5
CHIP_HEIGHT = 48
CHIP_GAP = 8
CHIP_BLOCK_PADDING = 20

STAT_BOX_HEIGHT = 38
STAT_BOX_GAP = 6
STAT_BOX_TOP = 38
# Name row plus a 2x2 grid of stat boxes sets the floor for every block.
MIN_PLAYER_BLOCK = STAT_BOX_TOP + STAT_BOX_HEIGHT * 2 + STAT_BOX_GAP + 8

TITLE_BLOCK = 66
HEADER_BLOCK = 32
FOOTER_BLOCK = 44

IMAGE_WIDTH = 1100
PANEL_WIDTH = 340
AFFILIATION_ICON_HEIGHT = 26
AFFILIATION_ICON_GAP = 6
RANK_ICON_GAP = 6
NAME_LINE_TOP = 9
NAME_LINE_HEIGHT = 24

_REGULAR_FONTS = (
    "arial.ttf",
    "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)
_BOLD_FONTS = (
    "arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
) + _REGULAR_FONTS

# name -> (size, bold)
_FONT_SPEC = {
    "title": (26, True),
    "header": (16, True),
    "name": (17, True),
    "text": (16, False),
    "stat": (15, True),
    "label": (10, False),
    "ticker": (14, True),
    "pct": (12, False),
    "company": (11, False),
    "small": (12, False),
}


def player_block_height(
    pick_count: int,
    *,
    chip_row_height: int = CHIP_HEIGHT,
    chip_gap: int = CHIP_GAP,
    chips_per_row: int = CHIPS_PER_ROW,
    min_block: int = MIN_PLAYER_BLOCK,
) -> int:
    """Height of one player's block; the stat panel sets the floor."""
    rows = math.ceil(pick_count / chips_per_row) if pick_count > 0 else 0
    if rows <= 0:
        return min_block
    grid = rows * chip_row_height + (rows - 1) * chip_gap + CHIP_BLOCK_PADDING
    return max(min_block, grid)


def sort_picks_by_performance(picks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best performer first, so chips read top-left to bottom-right."""
    return sorted(picks, key=lambda p: float(p.get("change_percent") or 0), reverse=True)


def estimate_recurring_leaderboard_height(
    n_players: int,
    picks_per_player: Union[Sequence[int], int],
    *,
    title_block: int = TITLE_BLOCK,
    header_block: int = HEADER_BLOCK,
    footer_block: int = FOOTER_BLOCK,
    **block_kwargs: Any,
) -> int:
    """Estimate PNG height for N players given pick counts (list or uniform int)."""
    if isinstance(picks_per_player, int):
        picks = [max(0, picks_per_player)] * max(0, n_players)
    else:
        picks = list(picks_per_player)[:n_players]
        while len(picks) < n_players:
            picks.append(0)
    height = title_block + header_block + footer_block
    for count in picks:
        height += player_block_height(count, **block_kwargs)
    return height


def select_leaderboard_n(
    picks_per_player: Sequence[int],
    *,
    max_height: int = DEFAULT_MAX_IMAGE_HEIGHT,
    candidates: Sequence[int] = LEADERBOARD_N_CANDIDATES,
    target: int = 10,
    **kwargs: Any,
) -> int:
    """Pick largest N in candidates that fits height budget; prefer ``target`` when it fits."""
    available = len(picks_per_player)
    if available <= 0:
        return 0
    fitting: list[int] = []
    for n in candidates:
        use = min(n, available)
        h = estimate_recurring_leaderboard_height(use, list(picks_per_player)[:use], **kwargs)
        if h <= max_height:
            fitting.append(use)
    if not fitting:
        return min(int(candidates[0]), available)
    preferred = min(target, available)
    if preferred in fitting:
        return preferred
    at_or_below = [f for f in fitting if f <= preferred]
    if at_or_below:
        return max(at_or_below)
    return max(fitting)


class RecurringLeaderboardImageGenerator:
    """Split-panel leaderboard with holding chips for recurring channel pushes."""

    def __init__(
        self,
        width: int = IMAGE_WIDTH,
        theme: str = "discord_dark",
        panel_width: int = PANEL_WIDTH,
        chip_row_height: int = CHIP_HEIGHT,
        chips_per_row: int = CHIPS_PER_ROW,
        max_height: int = DEFAULT_MAX_IMAGE_HEIGHT,
    ):
        self.width = width
        self.theme = theme
        self.panel_width = panel_width
        self.chip_row_height = chip_row_height
        self.chips_per_row = chips_per_row
        self.max_height = max_height
        self._simple = LeaderboardImageGenerator(width=width, theme=theme)
        self.colors = dict(self._simple.colors)
        self.colors["chip_bg"] = (64, 68, 75)
        self.colors["muted"] = (185, 187, 190)
        self.fonts: Dict[str, Any] = {}
        self._load_fonts()

    def _load_fonts(self) -> None:
        for name, (size, bold) in _FONT_SPEC.items():
            for path in (_BOLD_FONTS if bold else _REGULAR_FONTS):
                try:
                    self.fonts[name] = ImageFont.truetype(path, size)
                    break
                except (OSError, IOError):
                    continue
            else:
                self.fonts[name] = ImageFont.load_default()

    # -- text helpers ----------------------------------------------------- #

    def _width(self, text: str, font: Any) -> float:
        return font.getlength(text)

    def _truncate(self, text: str, font: Any, max_width: float) -> str:
        if self._width(text, font) <= max_width:
            return text
        while text and self._width(text + "~", font) > max_width:
            text = text[:-1]
        return text + "~"

    def _wrap(self, text: str, font: Any, max_width: float, max_lines: int) -> List[str]:
        lines: List[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if self._width(candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            return []
        lines[-1] = self._truncate(lines[-1], font, max_width)
        return lines[:max_lines]

    def _change_color(self, value: float):
        return self.colors["positive"] if value >= 0 else self.colors["negative"]

    # -- drawing ---------------------------------------------------------- #

    def create_image(
        self,
        game_data: Dict[str, Any],
        players: List[Dict[str, Any]],
        *,
        target_n: int = 10,
        show_title: bool = True,
        created_at: Optional[datetime] = None,
    ) -> BytesIO:
        picks_counts = [len(p.get("picks") or []) for p in players]
        n = select_leaderboard_n(
            picks_counts,
            max_height=self.max_height,
            target=target_n,
            chip_row_height=self.chip_row_height,
            chips_per_row=self.chips_per_row,
        )
        players = players[:n]
        title_block = TITLE_BLOCK if show_title else 0
        height = estimate_recurring_leaderboard_height(
            len(players),
            [len(p.get("picks") or []) for p in players],
            title_block=title_block,
            chip_row_height=self.chip_row_height,
            chips_per_row=self.chips_per_row,
        )

        img = Image.new("RGB", (self.width, height), self.colors["bg"])
        draw = ImageDraw.Draw(img)

        y = 0
        if show_title:
            title = f"{game_data.get('name', 'Game')} (ID: {game_data.get('id', 'N/A')})"
            draw.text(
                ((self.width - self._width(title, self.fonts["title"])) / 2, 18),
                title,
                fill=self.colors["text"],
                font=self.fonts["title"],
            )
            y = TITLE_BLOCK

        draw.rectangle([0, y, self.width, y + HEADER_BLOCK], fill=self.colors["header"])
        draw.text((18, y + 7), "Investor", fill=self.colors["text"], font=self.fonts["header"])
        draw.text(
            (self.panel_width + 18, y + 7),
            "Holdings",
            fill=self.colors["text"],
            font=self.fonts["header"],
        )
        y += HEADER_BLOCK

        for idx, player in enumerate(players):
            y = self._draw_player_block(img, draw, player, idx, y, game_data)

        if created_at is not None:
            stamp = created_at.strftime("%Y-%m-%d %H:%M")
            if created_at.tzinfo is not None:
                stamp = f"{stamp} {created_at.tzname() or 'ET'}"
            else:
                stamp = f"{stamp} ET"
            footer = f"Updated · {stamp}"
        else:
            footer = "StockBot · recurring leaderboard"
        draw.text(
            (20, height - 26),
            footer,
            fill=self.colors["footer"],
            font=self.fonts["small"],
        )

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def _draw_player_block(
        self,
        img: Image.Image,
        draw: ImageDraw.ImageDraw,
        player: Dict[str, Any],
        idx: int,
        y: int,
        game_data: Dict[str, Any],
    ) -> int:
        picks = sort_picks_by_performance(list(player.get("picks") or []))
        block_h = player_block_height(
            len(picks),
            chip_row_height=self.chip_row_height,
            chips_per_row=self.chips_per_row,
        )
        row_bg = self.colors["row_bg_1"] if idx % 2 == 0 else self.colors["row_bg_2"]
        draw.rectangle([0, y, self.width, y + block_h], fill=row_bg)

        place = int(player.get("rank") or idx + 1)
        rank = f"{place}."
        rank_x = 18
        rank_w = self._width(rank, self.fonts["name"])
        name_cy = y + NAME_LINE_TOP + NAME_LINE_HEIGHT // 2
        draw.text(
            (rank_x, name_cy),
            rank,
            fill=self._simple._get_rank_color(place - 1),
            font=self.fonts["name"],
            anchor="lm",
        )
        name_x = rank_x + max(rank_w, 22) + 10
        if game_data.get("affiliations_enabled") and player.get("affiliation"):
            icon = load_affiliation_icon(str(player["affiliation"]), AFFILIATION_ICON_HEIGHT)
            if icon is not None:
                icon_x = rank_x + rank_w + RANK_ICON_GAP
                icon_y = int(name_cy - icon.height / 2)
                img.paste(icon, (int(icon_x), icon_y), icon)
                name_x = icon_x + icon.width + AFFILIATION_ICON_GAP
        name = str(player.get("display_name") or f"ID({player.get('user_id')})")
        draw.text(
            (name_x, name_cy),
            self._truncate(name, self.fonts["name"], self.panel_width - name_x - 18),
            fill=self.colors["text"],
            font=self.fonts["name"],
            anchor="lm",
        )

        value = float(player.get("current_value") or 0)
        d_chg = float(player.get("change_dollars") or 0)
        p_chg = float(player.get("change_percent") or 0)
        days = int(player.get("days_in_first") or 0)
        dollar_sign = "+" if d_chg >= 0 else "-"
        stats = (
            ("Total Value", f"${value:,.2f}", self.colors["text"]),
            ("Gain ($)", f"{dollar_sign}${abs(d_chg):,.2f}", self._change_color(d_chg)),
            ("Gain (%)", f"{p_chg:+.2f}%", self._change_color(p_chg)),
            ("Days in First", str(days), self.colors["gold"] if days else self.colors["text"]),
        )
        box_w = (self.panel_width - 36 - STAT_BOX_GAP) // 2
        for i, (label, text, color) in enumerate(stats):
            row, col = divmod(i, 2)
            self._draw_stat_box(
                draw,
                18 + col * (box_w + STAT_BOX_GAP),
                y + STAT_BOX_TOP + row * (STAT_BOX_HEIGHT + STAT_BOX_GAP),
                box_w,
                STAT_BOX_HEIGHT,
                label,
                text,
                color,
            )

        draw.line(
            [(self.panel_width, y + 8), (self.panel_width, y + block_h - 8)],
            fill=self.colors["bg"],
            width=2,
        )

        grid_width = self.width - self.panel_width - 36
        chip_w = (grid_width - CHIP_GAP * (self.chips_per_row - 1)) // self.chips_per_row
        for index, pick in enumerate(picks):
            row, col = divmod(index, self.chips_per_row)
            self._draw_chip(
                draw,
                self.panel_width + 18 + col * (chip_w + CHIP_GAP),
                y + 10 + row * (self.chip_row_height + CHIP_GAP),
                chip_w,
                self.chip_row_height,
                pick,
            )
        return y + block_h

    def _draw_stat_box(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        label: str,
        value: str,
        value_color,
    ) -> None:
        """Labelled stat tile so each number says what it is."""
        draw.rounded_rectangle([x, y, x + w, y + h], radius=5, fill=self.colors["chip_bg"])
        draw.text((x + 8, y + 5), label, fill=self.colors["muted"], font=self.fonts["label"])
        draw.text(
            (x + 8, y + 17),
            self._truncate(value, self.fonts["stat"], w - 16),
            fill=value_color,
            font=self.fonts["stat"],
        )

    def _draw_arrow(self, draw: ImageDraw.ImageDraw, x: float, y: float, up: bool, size: int = 8) -> None:
        color = self.colors["positive"] if up else self.colors["negative"]
        half = size / 2
        if up:
            draw.polygon([(x + half, y), (x, y + size), (x + size, y + size)], fill=color)
        else:
            draw.polygon([(x, y), (x + size, y), (x + half, y + size)], fill=color)

    def _draw_chip(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        pick: Dict[str, Any],
    ) -> None:
        """Ticker on the left; percent then arrow pinned to the far-right corner."""
        draw.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=self.colors["chip_bg"])
        pad = 8
        inner = w - pad * 2
        left = x + pad
        right = x + w - pad

        pct = float(pick.get("change_percent") or 0)
        pct_text = f"{pct:+.1f}%"
        pct_w = self._width(pct_text, self.fonts["pct"])
        arrow_size = 8
        gap = 5

        # Arrow always at the far-right corner; percent immediately to its left.
        arrow_x = right - arrow_size
        pct_x = arrow_x - gap - pct_w

        ticker_budget = max(pct_x - left - gap, 10)
        ticker = self._truncate(
            str(pick.get("ticker") or pick.get("stock_ticker") or "?"),
            self.fonts["ticker"],
            ticker_budget,
        )
        draw.text((left, y + 4), ticker, fill=self.colors["text"], font=self.fonts["ticker"])
        draw.text(
            (pct_x, y + 6),
            pct_text,
            fill=self._change_color(pct),
            font=self.fonts["pct"],
        )
        self._draw_arrow(draw, arrow_x, y + 8, up=pct >= 0, size=arrow_size)

        company = str(pick.get("company") or pick.get("company_name") or "")
        if company:
            for i, line in enumerate(self._wrap(company, self.fonts["company"], inner, max_lines=2)):
                draw.text(
                    (left, y + 23 + i * 12),
                    line,
                    fill=self.colors["muted"],
                    font=self.fonts["company"],
                )


_DEFAULT_RECURRING: RecurringLeaderboardImageGenerator | None = None
_TALL_RECURRING: RecurringLeaderboardImageGenerator | None = None
_TALL_MAX_HEIGHT = 8000


def get_recurring_generator(*, max_height: int | None = None) -> RecurringLeaderboardImageGenerator:
    """Process-wide generator singleton(s); fonts load once.

    Uses a second instance when ``max_height`` is the tall slash-page budget
    (``8000``); all other callers share the default instance.
    """
    global _DEFAULT_RECURRING, _TALL_RECURRING
    if max_height == _TALL_MAX_HEIGHT:
        if _TALL_RECURRING is None:
            _TALL_RECURRING = RecurringLeaderboardImageGenerator(
                theme="discord_dark",
                max_height=_TALL_MAX_HEIGHT,
            )
        return _TALL_RECURRING
    if _DEFAULT_RECURRING is None:
        _DEFAULT_RECURRING = RecurringLeaderboardImageGenerator()
    return _DEFAULT_RECURRING
