"""Pillow table image for recurring hedge-fund performance."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from helpers.affiliations import (
    AFFILIATION_DISPLAY,
    AFFILIATION_EMBED_ORDER,
    INDEPENDENT_KEY,
    format_dollar_gain,
    load_affiliation_icon,
)
from helpers.views import LeaderboardImageGenerator

IMAGE_WIDTH = 440
HEADER_BLOCK = 74
ROW_HEIGHT = 32
TABLE_HEADER = 30
PADDING = 12
FUND_ICON_X = 12
FUND_ICON_HEIGHT = 22
FUND_ICON_SLOT = 28
FUND_ICON_GAP = 6
FUND_TEXT_X = FUND_ICON_X + FUND_ICON_SLOT + FUND_ICON_GAP
_FUND_SHIFT = FUND_ICON_SLOT + FUND_ICON_GAP

# (label, alignment, x, width) - compact column layout
_COLUMNS = (
    ("Fund", "left", FUND_TEXT_X, 145),
    ("Members", "right", 168 + _FUND_SHIFT, 44),
    ("$ Gain", "right", 220 + _FUND_SHIFT, 92),
    ("% Gain", "right", 320 + _FUND_SHIFT, 72),
)

_FONT_PATHS = (
    "arial.ttf",
    "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)
_BOLD_PATHS = (
    "arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
) + _FONT_PATHS


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = _BOLD_PATHS if bold else _FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _fund_label(key: str) -> str:
    if key == INDEPENDENT_KEY:
        return AFFILIATION_DISPLAY[INDEPENDENT_KEY]
    return AFFILIATION_DISPLAY.get(key, key)


def _sorted_fund_rows(stats: dict[str, dict[str, float]]) -> list[tuple[str, dict[str, float]]]:
    rows = [(key, stats.get(key, {"dollars": 0.0, "percent": 0.0, "members": 0})) for key in AFFILIATION_EMBED_ORDER]
    return sorted(rows, key=lambda item: float(item[1].get("percent") or 0), reverse=True)


def _text_width(text: str, font) -> float:
    try:
        return float(font.getlength(text))
    except AttributeError:
        return float(font.getsize(text)[0])


def _draw_cell(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: int,
    width: int,
    top: int,
    height: int,
    font,
    fill,
    align: str,
) -> None:
    cy = top + height // 2
    if align == "right":
        draw.text((x + width, cy), text, fill=fill, font=font, anchor="rm")
        return
    label = text
    while label and _text_width(f"{label}…", font) > width:
        label = label[:-1]
    if label != text and label:
        label = f"{label}…"
    draw.text((x, cy), label, fill=fill, font=font, anchor="lm")


def create_affiliation_performance_image(
    *,
    overall_dollars: float,
    overall_percent: float,
    affiliation_stats: dict[str, dict[str, float]],
) -> BytesIO:
    """Render fund performance table ranked by % gain."""
    rows = _sorted_fund_rows(affiliation_stats)
    height = HEADER_BLOCK + TABLE_HEADER + len(rows) * ROW_HEIGHT + PADDING
    theme = LeaderboardImageGenerator(theme="discord_dark")
    colors = theme.colors

    img = Image.new("RGB", (IMAGE_WIDTH, height), colors["bg"])
    draw = ImageDraw.Draw(img)
    title_font = _load_font(18, bold=True)
    header_font = _load_font(14, bold=True)
    text_font = _load_font(14)
    small_font = _load_font(13)

    y = PADDING
    draw.text((PADDING, y), "Overall Performance", fill=colors["text"], font=title_font)
    overall_color = colors["positive"] if overall_dollars >= 0 else colors["negative"]
    overall_text = f"{format_dollar_gain(overall_dollars)} ({overall_percent:+.2f}%) this month"
    draw.text((PADDING, y + 26), overall_text, fill=overall_color, font=small_font)

    y = HEADER_BLOCK
    header_top = y
    draw.rectangle([0, y, IMAGE_WIDTH, y + TABLE_HEADER], fill=colors["header"])
    for label, align, col_x, col_w in _COLUMNS:
        _draw_cell(
            draw,
            label,
            x=col_x,
            width=col_w,
            top=header_top,
            height=TABLE_HEADER,
            font=header_font,
            fill=colors["text"],
            align=align,
        )
    y += TABLE_HEADER

    for index, (key, row) in enumerate(rows):
        row_top = y
        row_bg = colors["row_bg_1"] if index % 2 == 0 else colors["row_bg_2"]
        draw.rectangle([0, y, IMAGE_WIDTH, y + ROW_HEIGHT], fill=row_bg)
        dollars = float(row.get("dollars") or 0)
        percent = float(row.get("percent") or 0)
        members = int(row.get("members") or 0)
        gain_color = colors["positive"] if dollars >= 0 else colors["negative"]
        pct_color = colors["positive"] if percent >= 0 else colors["negative"]

        if key != INDEPENDENT_KEY:
            icon = load_affiliation_icon(key, FUND_ICON_HEIGHT)
            if icon is not None:
                icon_y = row_top + (ROW_HEIGHT - icon.height) // 2
                img.paste(icon, (FUND_ICON_X, icon_y), icon)

        _fund_col = _COLUMNS[0]
        _draw_cell(
            draw,
            _fund_label(key),
            x=_fund_col[2],
            width=_fund_col[3],
            top=row_top,
            height=ROW_HEIGHT,
            font=text_font,
            fill=colors["text"],
            align=_fund_col[1],
        )

        other_values = (
            (str(members), colors["text"]),
            (format_dollar_gain(dollars), gain_color),
            (f"{percent:+.2f}%", pct_color),
        )
        for (label, align, col_x, col_w), (value, fill) in zip(_COLUMNS[1:], other_values):
            _draw_cell(
                draw,
                value,
                x=col_x,
                width=col_w,
                top=row_top,
                height=ROW_HEIGHT,
                font=text_font,
                fill=fill,
                align=align,
            )
        y += ROW_HEIGHT

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
