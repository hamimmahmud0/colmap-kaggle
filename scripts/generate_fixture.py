#!/usr/bin/env python3
"""Generate redistributable synthetic overlapping images for COLMAP smoke tests."""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    random.seed(1972)
    canvas = Image.new("RGB", (1800, 1200), "#b8c2b0")
    draw = ImageDraw.Draw(canvas)
    for index in range(260):
        x, y = random.randrange(40, 1760), random.randrange(40, 1160)
        radius = random.randrange(3, 18)
        color = tuple(random.randrange(35, 220) for _ in range(3))
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline="#202020")
    for index in range(args.count):
        angle = 2 * math.pi * index / args.count
        x = 250 + int(120 * math.cos(angle))
        y = 150 + int(80 * math.sin(angle))
        crop = canvas.crop((x, y, x + 1280, y + 900))
        crop.save(args.output / f"fixture_{index:02d}.png")


if __name__ == "__main__":
    main()
