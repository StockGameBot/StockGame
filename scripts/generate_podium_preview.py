#!/usr/bin/env python3
"""Generate a sample final-standings podium PNG for visual review."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.final_standings_podium import get_podium_generator  # noqa: E402


def _placeholder_avatar(color: tuple[int, int, int], letter: str) -> Image.Image:
    size = 128
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 5, size - 5), fill=color)
    draw.text((size // 2 - 12, size // 2 - 16), letter, fill=(255, 255, 255))
    return img


def _sample_top3() -> list[dict]:
    return [
        {
            "rank": 1,
            "user_id": 1240817181692792934,
            "display_name": "MoonshotMike",
            "current_value": 10_653.00,
            "change_percent": 6.53,
            "picks": [
                {"ticker": "NVDA", "change_percent": 28.4, "status": "owned"},
                {"ticker": "TSLA", "change_percent": 15.7, "status": "owned"},
                {"ticker": "AMD", "change_percent": 12.1, "status": "owned"},
                {"ticker": "JPM", "change_percent": 9.5, "status": "owned"},
                {"ticker": "MSFT", "change_percent": 6.8, "status": "owned"},
                {"ticker": "V", "change_percent": 5.2, "status": "owned"},
                {"ticker": "KO", "change_percent": 2.1, "status": "owned"},
                {"ticker": "T", "change_percent": -1.8, "status": "owned"},
                {"ticker": "INTC", "change_percent": -4.2, "status": "owned"},
                {"ticker": "IBM", "change_percent": -8.5, "status": "owned"},
            ],
        },
        {
            "rank": 2,
            "user_id": 329374393715392520,
            "display_name": "DividendQueen",
            "current_value": 9_958.00,
            "change_percent": -0.42,
            "picks": [
                {"ticker": "TSLA", "change_percent": 15.7, "status": "owned"},
                {"ticker": "JPM", "change_percent": 9.5, "status": "owned"},
                {"ticker": "MSFT", "change_percent": 6.8, "status": "owned"},
                {"ticker": "V", "change_percent": 5.2, "status": "owned"},
                {"ticker": "KO", "change_percent": 2.1, "status": "owned"},
                {"ticker": "T", "change_percent": -1.8, "status": "owned"},
                {"ticker": "XOM", "change_percent": -6.3, "status": "owned"},
                {"ticker": "RIVN", "change_percent": -9.4, "status": "owned"},
                {"ticker": "PFE", "change_percent": -11.2, "status": "owned"},
                {"ticker": "LCID", "change_percent": -14.8, "status": "owned"},
            ],
        },
        {
            "rank": 3,
            "user_id": 163784331804934144,
            "display_name": "RiskyBusiness",
            "current_value": 9_595.00,
            "change_percent": -4.05,
            "picks": [
                {"ticker": "TSLA", "change_percent": 15.7, "status": "owned"},
                {"ticker": "JPM", "change_percent": 9.5, "status": "owned"},
                {"ticker": "V", "change_percent": 5.2, "status": "owned"},
                {"ticker": "KO", "change_percent": 2.1, "status": "owned"},
                {"ticker": "T", "change_percent": -1.8, "status": "owned"},
                {"ticker": "XOM", "change_percent": -6.3, "status": "owned"},
                {"ticker": "RIVN", "change_percent": -9.4, "status": "owned"},
                {"ticker": "LCID", "change_percent": -14.8, "status": "owned"},
                {"ticker": "NIO", "change_percent": -18.2, "status": "owned"},
                {"ticker": "PLUG", "change_percent": -22.5, "status": "owned"},
            ],
        },
    ]


def main() -> None:
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "final_standings_preview.png"

    avatars = {
        1240817181692792934: _placeholder_avatar((88, 101, 242), "M"),
        329374393715392520: _placeholder_avatar((237, 66, 69), "D"),
        163784331804934144: _placeholder_avatar((67, 181, 129), "R"),
    }

    gen = get_podium_generator()
    buf = gen.create_image("Aug 2026", _sample_top3(), avatars=avatars)
    out_path.write_bytes(buf.getvalue())
    print(f"Wrote preview to: {out_path}")


if __name__ == "__main__":
    main()
