"""Final-standings podium image: top 3 with stacked stock gain/loss bars."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

DESIGN_WIDTH = 840
IMAGE_WIDTH = 1200


def _scale(n: float) -> int:
    return max(1, round(n * IMAGE_WIDTH / DESIGN_WIDTH))


BANNER_HEIGHT = _scale(64)
AVATAR_SIZE = _scale(64)
BAR_HPAD = _scale(2)
MIN_BAR_WIDTH = _scale(20)
CALLOUT_LINE_GAP = _scale(6)
GUTTER_PAD = _scale(4)
COL_MIN_WIDTH = _scale(125)
COL_GAP = _scale(30)
PODIUM_SIDE_PAD = _scale(20)
AXIS_GAP = _scale(3)
BOTTOM_PAD = _scale(14)
MIN_VISIBLE_PX = _scale(2)
TARGET_FIRST_TOTAL_PX = _scale(380)
HEADER_STATS_HEIGHT = _scale(86)
STAT_LINE_GAP = (_scale(4), _scale(22), _scale(42), _scale(62))
CHART_TOP_PAD = _scale(6)
BANNER_SIDE_PAD = _scale(28)
COL_SIDE_PAD = _scale(8)
AXIS_LINE_WIDTH = _scale(2)
AVATAR_RING_WIDTH = _scale(3)
LABEL_LINE_WIDTH = max(1, _scale(1))
SEGMENT_BORDER_WIDTH = max(1, round(_scale(2) - 1.5))
LABEL_VPAD = _scale(2)
LABEL_BLOCK_GAP = _scale(2)
MIN_NAME_FONT = _scale(10)
MIN_SEGMENT_TICKER_FONT = _scale(9)
MIN_SEGMENT_PCT_FONT = _scale(8)

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

_FONT_SPEC = {
    "banner": (_scale(26), True),
    "rank": (_scale(15), True),
    "name": (_scale(17), True),
    "value": (_scale(16), False),
    "pct": (_scale(16), True),
    "segment_ticker": (_scale(18), True),
    "segment_pct": (_scale(15), True),
    "segment_small_ticker": (_scale(13), True),
    "segment_small_pct": (_scale(11), True),
}

COLORS = {
    "bg": (47, 49, 54),
    "banner": (32, 34, 37),
    "banner_text": (255, 255, 255),
    "text": (220, 221, 222),
    "muted": (163, 166, 170),
    "axis": (185, 187, 190),
    "segment_divider": (0, 0, 0),
    "gain": (67, 181, 129),
    "gain_dark": (46, 140, 98),
    "loss": (237, 66, 69),
    "loss_dark": (180, 50, 52),
    "medal_gold": (255, 215, 0),
    "medal_silver": (192, 192, 192),
    "medal_bronze": (205, 127, 50),
}


def _load_fonts() -> Dict[str, Any]:
    fonts: Dict[str, Any] = {}
    for name, (size, bold) in _FONT_SPEC.items():
        for path in (_BOLD_FONTS if bold else _REGULAR_FONTS):
            try:
                fonts[name] = ImageFont.truetype(path, size)
                break
            except (OSError, IOError):
                continue
        else:
            fonts[name] = ImageFont.load_default()
    return fonts


def _text_width(text: str, font: Any) -> float:
    return font.getlength(text)


def _font_height(font: Any) -> int:
    return int(getattr(font, "size", 12))


def _truncate(text: str, font: Any, max_width: float) -> str:
    """Only used for the banner title when the game name is extremely long."""
    if _text_width(text, font) <= max_width:
        return text
    trimmed = text
    while trimmed and _text_width(trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + "…" if trimmed else "…"


_font_size_cache: Dict[tuple[int, bool], Any] = {}


def _font_at_size(size: int, *, bold: bool = False) -> Any:
    size = max(6, int(size))
    key = (size, bold)
    if key in _font_size_cache:
        return _font_size_cache[key]
    for path in (_BOLD_FONTS if bold else _REGULAR_FONTS):
        try:
            font = ImageFont.truetype(path, size)
            _font_size_cache[key] = font
            return font
        except (OSError, IOError):
            continue
    font = ImageFont.load_default()
    _font_size_cache[key] = font
    return font


def _font_size(font: Any) -> int:
    return int(getattr(font, "size", 12))


def _fit_font(
    text: str,
    max_width: float,
    *,
    start_size: int,
    min_size: int,
    bold: bool = False,
) -> Any:
    if max_width <= 0:
        return _font_at_size(min_size, bold=bold)
    for size in range(start_size, min_size - 1, -1):
        font = _font_at_size(size, bold=bold)
        if _text_width(text, font) <= max_width:
            return font
    return _font_at_size(min_size, bold=bold)


def _fit_stacked_fonts(
    ticker: str,
    pct_text: str,
    max_width: float,
    *,
    start_ticker_size: int,
    start_pct_size: int,
    min_ticker_size: int,
    min_pct_size: int,
) -> tuple[Any, Any]:
    if max_width <= 0:
        return (
            _font_at_size(min_ticker_size, bold=True),
            _font_at_size(min_pct_size, bold=False),
        )
    for t_size in range(start_ticker_size, min_ticker_size - 1, -1):
        ratio = start_pct_size / max(start_ticker_size, 1)
        p_start = max(min_pct_size, int(round(t_size * ratio)))
        for p_size in range(p_start, min_pct_size - 1, -1):
            ticker_font = _font_at_size(t_size, bold=True)
            pct_font = _font_at_size(p_size, bold=False)
            block_w = max(_text_width(ticker, ticker_font), _text_width(pct_text, pct_font))
            if block_w <= max_width:
                return ticker_font, pct_font
    return (
        _font_at_size(min_ticker_size, bold=True),
        _font_at_size(min_pct_size, bold=False),
    )


def _split_picks(picks: Sequence[Dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    owned = [p for p in picks if p.get("status", "owned") == "owned" or "status" not in p]
    gainers = sorted(
        [p for p in owned if float(p.get("change_percent") or 0) >= 0],
        key=lambda p: float(p.get("change_percent") or 0),
        reverse=True,
    )
    losers = sorted(
        [p for p in owned if float(p.get("change_percent") or 0) < 0],
        key=lambda p: float(p.get("change_percent") or 0),
        reverse=True,
    )
    return gainers, losers


def _player_bar_range(picks: Sequence[Dict[str, Any]]) -> float:
    gainers, losers = _split_picks(picks)
    total = sum(abs(float(p.get("change_percent") or 0)) for p in gainers + losers)
    return max(total, 1.0)


def _segment_height(pct_val: float, px_per_pct: float) -> int:
    """Proportional segment height; tiny moves stay visible but don't distort scale."""
    return max(MIN_VISIBLE_PX, int(round(abs(pct_val) * px_per_pct)))


def _stack_heights(
    picks: Sequence[Dict[str, Any]],
    *,
    px_per_pct: float,
) -> tuple[int, int]:
    gainers, losers = _split_picks(picks)
    gain_px = sum(
        _segment_height(float(p.get("change_percent") or 0), px_per_pct)
        for p in gainers
        if float(p.get("change_percent") or 0) > 0
    )
    loss_px = sum(
        _segment_height(float(p.get("change_percent") or 0), px_per_pct)
        for p in losers
    )
    return gain_px, loss_px


def _medal_color(rank: int) -> Tuple[int, int, int]:
    if rank == 1:
        return COLORS["medal_gold"]
    if rank == 2:
        return COLORS["medal_silver"]
    return COLORS["medal_bronze"]


def _draw_circular_avatar(
    base: Image.Image,
    avatar: Optional[Image.Image],
    center: Tuple[int, int],
    size: int,
    ring_color: Tuple[int, int, int],
) -> None:
    x, y = center
    radius = size // 2
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if avatar is None:
        placeholder = Image.new("RGB", (size, size), (64, 68, 75))
        draw = ImageDraw.Draw(placeholder)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(88, 101, 242))
    else:
        placeholder = avatar.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    base.paste(placeholder, (x - radius, y - radius), mask)
    draw = ImageDraw.Draw(base)
    draw.ellipse(
        (x - radius - _scale(2), y - radius - _scale(2), x + radius + 1, y + radius + 1),
        outline=ring_color,
        width=AVATAR_RING_WIDTH,
    )


def _gainers_for_stack(gainers: list[dict]) -> list[dict]:
    """Worst gainer near the axis, best gainer at the top of the stack."""
    return sorted(gainers, key=lambda p: float(p.get("change_percent") or 0))


def _label_fonts(seg_h: int, fonts: Dict[str, Any]) -> tuple[Any, Any]:
    min_two_line = _font_height(fonts["segment_ticker"]) + _font_height(fonts["segment_pct"]) + _scale(4)
    if seg_h >= min_two_line:
        return fonts["segment_ticker"], fonts["segment_pct"]
    return fonts["segment_small_ticker"], fonts["segment_small_pct"]


def _inside_label_width(ticker: str, pct: float, ticker_font: Any, pct_font: Any) -> float:
    pct_text = f"{pct:+.1f}%"
    return max(_text_width(str(ticker), ticker_font), _text_width(pct_text, pct_font))


def _player_bar_width(
    picks: Sequence[Dict[str, Any]],
    *,
    px_per_pct: float,
    fonts: Dict[str, Any],
) -> int:
    """Bar width = widest in-bar label + padding; only as wide as needed."""
    max_inner = 0.0
    gainers, losers = _split_picks(picks)
    for pick in gainers + losers:
        pct_val = float(pick.get("change_percent") or 0)
        seg_h = _segment_height(pct_val, px_per_pct)
        ticker_font, pct_font = _label_fonts(seg_h, fonts)
        block_h = _font_height(ticker_font) + _font_height(pct_font) + LABEL_BLOCK_GAP
        if seg_h < block_h + LABEL_VPAD:
            continue
        inner = _inside_label_width(str(pick.get("ticker") or "?"), pct_val, ticker_font, pct_font)
        max_inner = max(max_inner, inner)

    if max_inner <= 0:
        return MIN_BAR_WIDTH
    return max(MIN_BAR_WIDTH, int(max_inner + 0.999) + BAR_HPAD * 2)


@dataclass(frozen=True)
class PodiumLayout:
    block_x0: int
    block_x1: int
    col_w: int
    col_gap: int
    bar_width: int

    def col_x(self, slot: int) -> int:
        return self.block_x0 + slot * (self.col_w + self.col_gap)

    @property
    def block_width(self) -> int:
        return self.block_x1 - self.block_x0


def _compute_podium_layout(
    players: Sequence[Dict[str, Any]],
    *,
    px_per_pct: float,
    fonts: Dict[str, Any],
) -> PodiumLayout:
    bar_width = max(
        _player_bar_width(player.get("picks") or [], px_per_pct=px_per_pct, fonts=fonts)
        for player in players
    )
    col_w = max(COL_MIN_WIDTH, bar_width + 2 * GUTTER_PAD)
    block_w = 3 * col_w + 2 * COL_GAP
    block_x0 = PODIUM_SIDE_PAD
    return PodiumLayout(
        block_x0=block_x0,
        block_x1=block_x0 + block_w,
        col_w=col_w,
        col_gap=COL_GAP,
        bar_width=bar_width,
    )


def _podium_image_width(layout: PodiumLayout) -> int:
    return layout.block_x1 + PODIUM_SIDE_PAD


def _bar_bounds(
    col_x: int,
    col_w: int,
    bar_width: int,
    col_slot: int,
) -> tuple[int, int]:
    """Place bars toward the podium center; outer columns keep wider callout gutters."""
    if col_slot == 0:
        bar_x1 = col_x + col_w - GUTTER_PAD
        bar_x0 = bar_x1 - bar_width
    elif col_slot == 2:
        bar_x0 = col_x + GUTTER_PAD
        bar_x1 = bar_x0 + bar_width
    else:
        bar_x0 = col_x + (col_w - bar_width) // 2
        bar_x1 = bar_x0 + bar_width
    return bar_x0, bar_x1


def _gutter_bounds(
    side: str,
    col_slot: int,
    col_x: int,
    col_w: int,
    bar_x0: int,
    bar_x1: int,
    layout: PodiumLayout,
    canvas_width: int,
) -> tuple[int, int]:
    if side == "left":
        if col_slot == 0:
            gutter_x0 = GUTTER_PAD
        elif col_slot == 1:
            gutter_x0 = col_x - layout.col_gap + GUTTER_PAD
        else:
            gutter_x0 = col_x + GUTTER_PAD
        gutter_x1 = bar_x0 - CALLOUT_LINE_GAP
    else:
        gutter_x0 = bar_x1 + CALLOUT_LINE_GAP
        if col_slot == 2:
            gutter_x1 = canvas_width - GUTTER_PAD
        elif col_slot == 1:
            gutter_x1 = col_x + col_w + layout.col_gap - GUTTER_PAD
        else:
            gutter_x1 = col_x + col_w - GUTTER_PAD
    return gutter_x0, gutter_x1


def _header_label_width(col_slot: int, layout: PodiumLayout) -> float:
    """Keep names inside each player's column so headers don't collide."""
    del col_slot
    return layout.col_w - COL_SIDE_PAD


def _fits_inside_bar(
    seg_h: int,
    *,
    ticker: str,
    pct: float,
    ticker_font: Any,
    pct_font: Any,
) -> bool:
    """True when the label fits vertically; bar width already covers horizontal."""
    block_h = _font_height(ticker_font) + _font_height(pct_font) + LABEL_BLOCK_GAP
    return seg_h >= block_h + LABEL_VPAD


def _callout_side(col_slot: int, callout_index: int) -> str:
    if col_slot == 0:
        return "left"
    if col_slot == 2:
        return "right"
    return "left" if callout_index % 2 == 0 else "right"


def _layout_callout_tys(items: Sequence[tuple[float, float]]) -> list[float]:
    """Place labels in mid_y order without vertical overlap (keeps leader lines uncrossed)."""
    if not items:
        return []
    tys = [mid_y - block_h / 2 for mid_y, block_h in items]
    for i in range(1, len(items)):
        tys[i] = max(tys[i], tys[i - 1] + items[i - 1][1] + LABEL_VPAD)
    for i in range(len(items) - 2, -1, -1):
        tys[i] = min(tys[i], tys[i + 1] - items[i][1] - LABEL_VPAD)
    return tys


def _callout_attach_y(mid_y: int, ty: float, block_h: float) -> int:
    """Y on the label used for the leader terminus (label center keeps lines ordered)."""
    del mid_y
    return int(ty + block_h / 2)


def _draw_callout_leader(
    draw: ImageDraw.ImageDraw,
    *,
    side: str,
    bar_x: int,
    mid_y: int,
    label_x: int,
    label_w: float,
    ty: float,
    block_h: float,
    color: Tuple[int, int, int],
) -> None:
    """Leader from label to bar; diagonal when the label was nudged to avoid overlap."""
    attach_y = _callout_attach_y(mid_y, ty, block_h)
    if side == "left":
        draw.line(
            (int(label_x + label_w), attach_y, bar_x, mid_y),
            fill=color,
            width=LABEL_LINE_WIDTH,
        )
    else:
        draw.line(
            (bar_x, mid_y, int(label_x), attach_y),
            fill=color,
            width=LABEL_LINE_WIDTH,
        )


@dataclass
class _GutterCallout:
    side: str
    mid_y: int
    block_h: float
    gutter_x0: int
    gutter_x1: int
    bar_x0: int
    bar_x1: int
    ticker: str
    pct_text: str
    one_line: bool
    line_text: str
    one_line_font: Any
    ticker_font: Any
    pct_font: Any


def _plan_gutter_callout(
    *,
    side: str,
    gutter_x0: int,
    gutter_x1: int,
    bar_x0: int,
    bar_x1: int,
    y0: int,
    y1: int,
    ticker: str,
    pct: float,
    fonts: Dict[str, Any],
) -> _GutterCallout:
    mid_y = (y0 + y1) // 2
    seg_h = y1 - y0
    pct_text = f"{pct:+.1f}%"
    start_ticker = _font_size(fonts["segment_ticker"])
    start_pct = _font_size(fonts["segment_pct"])
    min_ticker = _font_size(fonts["segment_small_ticker"])
    min_pct = _font_size(fonts["segment_small_pct"])
    max_w = max(int(gutter_x1 - gutter_x0), 1)

    if seg_h < _font_height(fonts["segment_ticker"]) + _font_height(fonts["segment_pct"]) + _scale(4):
        line_text = f"{ticker} {pct_text}"
        one_line_font = _fit_font(
            line_text,
            max_w,
            start_size=start_pct,
            min_size=min_pct,
            bold=False,
        )
        return _GutterCallout(
            side=side,
            mid_y=mid_y,
            block_h=_font_height(one_line_font),
            gutter_x0=gutter_x0,
            gutter_x1=gutter_x1,
            bar_x0=bar_x0,
            bar_x1=bar_x1,
            ticker=str(ticker),
            pct_text=pct_text,
            one_line=True,
            line_text=line_text,
            one_line_font=one_line_font,
            ticker_font=one_line_font,
            pct_font=one_line_font,
        )

    ticker_font, pct_font = _fit_stacked_fonts(
        str(ticker),
        pct_text,
        max_w,
        start_ticker_size=start_ticker,
        start_pct_size=start_pct,
        min_ticker_size=min_ticker,
        min_pct_size=min_pct,
    )
    block_h = _font_height(ticker_font) + _font_height(pct_font) + LABEL_BLOCK_GAP
    return _GutterCallout(
        side=side,
        mid_y=mid_y,
        block_h=block_h,
        gutter_x0=gutter_x0,
        gutter_x1=gutter_x1,
        bar_x0=bar_x0,
        bar_x1=bar_x1,
        ticker=str(ticker),
        pct_text=pct_text,
        one_line=False,
        line_text="",
        one_line_font=fonts["segment_pct"],
        ticker_font=ticker_font,
        pct_font=pct_font,
    )


def _render_gutter_callout(draw: ImageDraw.ImageDraw, callout: _GutterCallout, ty: float) -> None:
    color = COLORS["text"]
    if callout.one_line:
        if callout.side == "left":
            line_w = _text_width(callout.line_text, callout.one_line_font)
            label_x = max(callout.gutter_x0, int(callout.gutter_x1 - line_w))
            _draw_callout_leader(
                draw,
                side=callout.side,
                bar_x=callout.bar_x0,
                mid_y=callout.mid_y,
                label_x=label_x,
                label_w=line_w,
                ty=ty,
                block_h=callout.block_h,
                color=color,
            )
            draw.text((label_x, ty), callout.line_text, fill=color, font=callout.one_line_font)
        else:
            label_x = int(callout.gutter_x0)
            line_w = _text_width(callout.line_text, callout.one_line_font)
            _draw_callout_leader(
                draw,
                side=callout.side,
                bar_x=callout.bar_x1,
                mid_y=callout.mid_y,
                label_x=label_x,
                label_w=line_w,
                ty=ty,
                block_h=callout.block_h,
                color=color,
            )
            draw.text((label_x, ty), callout.line_text, fill=color, font=callout.one_line_font)
        return

    ticker_w = _text_width(callout.ticker, callout.ticker_font)
    pct_w = _text_width(callout.pct_text, callout.pct_font)
    label_w = max(ticker_w, pct_w)
    bar_x = callout.bar_x0 if callout.side == "left" else callout.bar_x1
    if callout.side == "left":
        label_x = max(callout.gutter_x0, int(callout.gutter_x1 - label_w))
    else:
        label_x = int(callout.gutter_x0)
    _draw_callout_leader(
        draw,
        side=callout.side,
        bar_x=bar_x,
        mid_y=callout.mid_y,
        label_x=label_x,
        label_w=label_w,
        ty=ty,
        block_h=callout.block_h,
        color=color,
    )
    draw.text((label_x, ty), callout.ticker, fill=color, font=callout.ticker_font)
    draw.text(
        (label_x, ty + _font_height(callout.ticker_font) + LABEL_VPAD),
        callout.pct_text,
        fill=color,
        font=callout.pct_font,
    )


def _render_gutter_callouts(draw: ImageDraw.ImageDraw, callouts: list[_GutterCallout]) -> None:
    if not callouts:
        return
    ordered = sorted(callouts, key=lambda c: c.mid_y)
    tys = _layout_callout_tys([(c.mid_y, c.block_h) for c in ordered])
    for callout, ty in zip(ordered, tys):
        _render_gutter_callout(draw, callout, ty)


def _draw_inside_segment_label(
    draw: ImageDraw.ImageDraw,
    *,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    ticker: str,
    pct: float,
    ticker_font: Any,
    pct_font: Any,
) -> None:
    seg_h = y1 - y0
    seg_w = x1 - x0
    inner_w = max(seg_w - BAR_HPAD * 2, 1)
    pct_text = f"{pct:+.1f}%"
    ticker_font, pct_font = _fit_stacked_fonts(
        str(ticker),
        pct_text,
        inner_w,
        start_ticker_size=_font_size(ticker_font),
        start_pct_size=_font_size(pct_font),
        min_ticker_size=MIN_SEGMENT_TICKER_FONT,
        min_pct_size=MIN_SEGMENT_PCT_FONT,
    )
    ticker_w = _text_width(str(ticker), ticker_font)
    pct_w = _text_width(pct_text, pct_font)
    block_h = _font_height(ticker_font) + _font_height(pct_font) + LABEL_BLOCK_GAP
    ty = y0 + (seg_h - block_h) / 2
    tx_base = x0 + BAR_HPAD
    draw.text((tx_base + (inner_w - ticker_w) / 2, ty), str(ticker), fill=(255, 255, 255), font=ticker_font)
    draw.text(
        (tx_base + (inner_w - pct_w) / 2, ty + _font_height(ticker_font) + LABEL_VPAD),
        pct_text,
        fill=(255, 255, 255),
        font=pct_font,
    )


def _plan_segment_label(
    *,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    ticker: str,
    pct: float,
    fonts: Dict[str, Any],
    layout: PodiumLayout,
    col_x: int,
    col_w: int,
    col_slot: int,
    canvas_width: int,
    callout_index: int,
) -> _GutterCallout | None:
    """Draw in-bar label or return a gutter callout plan for batch layout."""
    seg_h = y1 - y0
    ticker_font, pct_font = _label_fonts(seg_h, fonts)
    if _fits_inside_bar(
        seg_h,
        ticker=ticker,
        pct=pct,
        ticker_font=ticker_font,
        pct_font=pct_font,
    ):
        return None

    side = _callout_side(col_slot, callout_index)
    gutter_x0, gutter_x1 = _gutter_bounds(
        side, col_slot, col_x, col_w, x0, x1, layout, canvas_width
    )
    return _plan_gutter_callout(
        side=side,
        gutter_x0=gutter_x0,
        gutter_x1=gutter_x1,
        bar_x0=x0,
        bar_x1=x1,
        y0=y0,
        y1=y1,
        ticker=ticker,
        pct=pct,
        fonts=fonts,
    )


def _draw_segment_dividers(
    draw: ImageDraw.ImageDraw,
    segments: Sequence[tuple[int, int, int, int, str, float]],
    *,
    bar_x0: int,
    bar_x1: int,
) -> None:
    """Black lines only where two stacked segments share an edge."""
    color = COLORS["segment_divider"]
    for i in range(len(segments) - 1):
        _, y0_a, _, y1_a, _, _ = segments[i]
        _, y0_b, _, y1_b, _, _ = segments[i + 1]
        if y0_a == y1_b:
            y = y0_a
        elif y1_a == y0_b:
            y = y1_a
        else:
            continue
        draw.line((bar_x0, y, bar_x1, y), fill=color, width=SEGMENT_BORDER_WIDTH)


def _header_bottom(chart_top: int) -> int:
    """Y coordinate where stock bars may begin (below avatar + stats)."""
    return chart_top + AVATAR_SIZE + HEADER_STATS_HEIGHT


def _draw_player_column(
    draw: ImageDraw.ImageDraw,
    fonts: Dict[str, Any],
    *,
    player: Dict[str, Any],
    layout: PodiumLayout,
    col_x: int,
    col_w: int,
    chart_top: int,
    axis_y: int,
    px_per_pct: float,
    col_slot: int,
    canvas_width: int,
    avatar: Optional[Image.Image],
    base: Image.Image,
) -> int:
    bar_x0, bar_x1 = _bar_bounds(col_x, col_w, layout.bar_width, col_slot)
    cx = col_x + col_w // 2
    medal = _medal_color(int(player.get("rank") or 1))

    _draw_circular_avatar(base, avatar, (cx, chart_top + AVATAR_SIZE // 2), AVATAR_SIZE, medal)
    draw = ImageDraw.Draw(base)

    rank = int(player.get("rank") or 1)
    rank_label = {1: "1st", 2: "2nd", 3: "3rd"}.get(rank, f"#{rank}")
    rw = _text_width(rank_label, fonts["rank"])
    rank_gap, name_gap, value_gap, pct_gap = STAT_LINE_GAP
    draw.text((cx - rw / 2, chart_top + AVATAR_SIZE + rank_gap), rank_label, fill=medal, font=fonts["rank"])

    name_text = str(player.get("display_name") or "?")
    name_font = _fit_font(
        name_text,
        _header_label_width(col_slot, layout),
        start_size=_font_size(fonts["name"]),
        min_size=MIN_NAME_FONT,
        bold=True,
    )
    nw = _text_width(name_text, name_font)
    draw.text((cx - nw / 2, chart_top + AVATAR_SIZE + name_gap), name_text, fill=COLORS["text"], font=name_font)

    value = float(player.get("current_value") or 0)
    value_text = f"${value:,.2f}"
    vw = _text_width(value_text, fonts["value"])
    draw.text((cx - vw / 2, chart_top + AVATAR_SIZE + value_gap), value_text, fill=COLORS["text"], font=fonts["value"])

    pct = float(player.get("change_percent") or 0)
    pct_text = f"{pct:+.2f}%"
    pct_color = COLORS["gain"] if pct >= 0 else COLORS["loss"]
    pw = _text_width(pct_text, fonts["pct"])
    draw.text((cx - pw / 2, chart_top + AVATAR_SIZE + pct_gap), pct_text, fill=pct_color, font=fonts["pct"])

    draw.line((bar_x0, axis_y, bar_x1, axis_y), fill=COLORS["segment_divider"], width=SEGMENT_BORDER_WIDTH)

    gainers, losers = _split_picks(player.get("picks") or [])
    segments: list[tuple[int, int, int, int, str, float]] = []
    y_cursor = axis_y
    lowest_y = axis_y
    pending_left: list[_GutterCallout] = []
    pending_right: list[_GutterCallout] = []

    for pick in _gainers_for_stack(gainers):
        pct_val = float(pick.get("change_percent") or 0)
        seg_h = _segment_height(pct_val, px_per_pct)
        y1 = y_cursor
        y0 = y_cursor - seg_h
        color = COLORS["gain"] if pct_val > 0 else COLORS["muted"]
        draw.rectangle((bar_x0, y0, bar_x1, y1), fill=color)
        segments.append((bar_x0, y0, bar_x1, y1, str(pick.get("ticker") or "?"), pct_val))
        y_cursor = y0

    y_cursor = axis_y
    for pick in losers:
        pct_val = float(pick.get("change_percent") or 0)
        seg_h = _segment_height(pct_val, px_per_pct)
        y0 = y_cursor
        y1 = y_cursor + seg_h
        draw.rectangle((bar_x0, y0, bar_x1, y1), fill=COLORS["loss"])
        segments.append((bar_x0, y0, bar_x1, y1, str(pick.get("ticker") or "?"), pct_val))
        y_cursor = y1
        lowest_y = max(lowest_y, y1)

    _draw_segment_dividers(draw, segments, bar_x0=bar_x0, bar_x1=bar_x1)

    callout_index = 0
    for x0, y0, x1, y1, ticker, pct_val in segments:
        seg_h = y1 - y0
        ticker_font, pct_font = _label_fonts(seg_h, fonts)
        if _fits_inside_bar(
            seg_h,
            ticker=ticker,
            pct=pct_val,
            ticker_font=ticker_font,
            pct_font=pct_font,
        ):
            _draw_inside_segment_label(
                draw,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                ticker=ticker,
                pct=pct_val,
                ticker_font=ticker_font,
                pct_font=pct_font,
            )
        else:
            callout = _plan_segment_label(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                ticker=ticker,
                pct=pct_val,
                fonts=fonts,
                layout=layout,
                col_x=col_x,
                col_w=col_w,
                col_slot=col_slot,
                canvas_width=canvas_width,
                callout_index=callout_index,
            )
            if callout is not None:
                if callout.side == "left":
                    pending_left.append(callout)
                else:
                    pending_right.append(callout)
                callout_index += 1

    _render_gutter_callouts(draw, pending_left)
    _render_gutter_callouts(draw, pending_right)

    return lowest_y


class FinalStandingsPodiumGenerator:
    """Render a top-3 podium with stacked stock performance bars."""

    def __init__(self, width: int = IMAGE_WIDTH):
        self.width = width
        self.fonts = _load_fonts()

    def create_image(
        self,
        game_name: str,
        top3: Sequence[Dict[str, Any]],
        *,
        avatars: Optional[Dict[int, Image.Image]] = None,
    ) -> BytesIO:
        """Build podium PNG. ``top3`` must be ordered 1st, 2nd, 3rd."""
        avatars = avatars or {}
        players = list(top3)[:3]
        while len(players) < 3:
            players.append(
                {
                    "rank": len(players) + 1,
                    "display_name": "—",
                    "current_value": 0,
                    "change_percent": 0,
                    "picks": [],
                    "user_id": 0,
                }
            )

        first = next((p for p in players if int(p.get("rank") or 0) == 1), players[0])
        first_range = _player_bar_range(first.get("picks") or [])
        px_per_pct = TARGET_FIRST_TOTAL_PX / first_range

        max_gain_px = 0
        max_loss_px = 0
        for player in players:
            gain_px, loss_px = _stack_heights(player.get("picks") or [], px_per_pct=px_per_pct)
            max_gain_px = max(max_gain_px, gain_px)
            max_loss_px = max(max_loss_px, loss_px)

        chart_top = BANNER_HEIGHT + CHART_TOP_PAD
        header_bottom = _header_bottom(chart_top)
        axis_y = header_bottom + max_gain_px
        img_height = axis_y + AXIS_GAP + max_loss_px + BOTTOM_PAD

        layout = _compute_podium_layout(
            players,
            px_per_pct=px_per_pct,
            fonts=self.fonts,
        )
        img_width = _podium_image_width(layout)

        img = Image.new("RGB", (img_width, img_height), COLORS["bg"])
        draw = ImageDraw.Draw(img)

        draw.rectangle((0, 0, img_width, BANNER_HEIGHT), fill=COLORS["banner"])
        banner = f"{game_name} Final Standings"
        banner = _truncate(banner, self.fonts["banner"], img_width - BANNER_SIDE_PAD)
        bw = _text_width(banner, self.fonts["banner"])
        draw.text(
            ((img_width - bw) / 2, (BANNER_HEIGHT - self.fonts["banner"].size) / 2),
            banner,
            fill=COLORS["banner_text"],
            font=self.fonts["banner"],
        )

        player_layout = [
            (players[1] if len(players) > 1 else players[0], 0),
            (players[0], 1),
            (players[2] if len(players) > 2 else players[0], 2),
        ]

        content_bottom = axis_y
        for player, slot in player_layout:
            col_x = layout.col_x(slot)
            col_w = layout.col_w
            uid = int(player.get("user_id") or 0)
            lowest = _draw_player_column(
                draw,
                self.fonts,
                player=player,
                layout=layout,
                col_x=col_x,
                col_w=col_w,
                chart_top=chart_top,
                axis_y=axis_y,
                px_per_pct=px_per_pct,
                col_slot=slot,
                canvas_width=img_width,
                avatar=avatars.get(uid),
                base=img,
            )
            content_bottom = max(content_bottom, lowest)

        crop_height = min(img.height, content_bottom + BOTTOM_PAD)
        if crop_height < img.height:
            img = img.crop((0, 0, img_width, crop_height))

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf


_generator: Optional[FinalStandingsPodiumGenerator] = None


def get_podium_generator() -> FinalStandingsPodiumGenerator:
    global _generator
    if _generator is None or _generator.width != IMAGE_WIDTH:
        _generator = FinalStandingsPodiumGenerator()
    return _generator
