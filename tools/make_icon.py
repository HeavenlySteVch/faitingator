# -*- coding: utf-8 -*-
"""Рисует иконку приложения: золотой ключ-камень на тёмной плитке.

Запуск:  python tools/make_icon.py
Результат: icon.ico (16–256 px) и icon.png (256 px) в корне проекта.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 1024

BG_TOP = (37, 44, 55)
BG_BOT = (16, 19, 23)
BORDER = (60, 70, 84)
GOLD_TOP = (255, 231, 163)
GOLD_MID = (243, 200, 97)
GOLD_BOT = (176, 124, 40)
SLOT = (22, 26, 33)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(size, top, mid, bottom):
    """Вертикальный градиент top -> mid -> bottom."""
    img = Image.new("RGBA", size)
    h = size[1]
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.5:
            c = lerp(top, mid, t / 0.5)
        else:
            c = lerp(mid, bottom, (t - 0.5) / 0.5)
        img.paste(c + (255,), (0, y, size[0], y + 1))
    return img


def gem_points(cx, cy, r_long, r_short):
    """Восьмиугольный «камень»: четыре длинных луча и четыре коротких."""
    pts = []
    for i in range(8):
        ang = math.radians(-90 + i * 45)
        r = r_long if i % 2 == 0 else r_short
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def build(size=S) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # --- плитка с градиентом и скруглёнными углами ---
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (6, 6, size - 6, size - 6), radius=int(size * 0.21), fill=255)
    img.paste(gradient((size, size), BG_TOP, (30, 36, 45), BG_BOT), (0, 0), mask)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, size - 6, size - 6), radius=int(size * 0.21),
                        outline=BORDER + (255,), width=max(2, size // 170))

    # --- синее свечение под камнем ---
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (size * 0.17, size * 0.17, size * 0.83, size * 0.83),
        fill=(79, 140, 247, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.07))
    img.alpha_composite(glow)

    # --- золотой камень ---
    cx = cy = size / 2
    r_long = size * 0.335
    r_short = size * 0.215
    outer = gem_points(cx, cy, r_long, r_short)

    gem_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(gem_mask).polygon(outer, fill=255)
    gold = gradient((size, size), GOLD_TOP, GOLD_MID, GOLD_BOT)
    img.paste(gold, (0, 0), gem_mask)

    d = ImageDraw.Draw(img)

    # фаски: светлая грань сверху-слева, тёмная снизу-справа
    axes = gem_points(cx, cy, r_long, r_short)
    for i in range(0, 4):
        d.line([axes[i], axes[(i + 1) % 8]], fill=(255, 244, 205, 150),
               width=max(2, size // 220))
    for i in range(4, 8):
        d.line([axes[i], axes[(i + 1) % 8]], fill=(122, 82, 20, 150),
               width=max(2, size // 220))
    for i in range(8):
        d.line([(cx, cy), axes[i]], fill=(190, 140, 45, 90),
               width=max(1, size // 420))

    # тёмная «прорезь» в центре — читается как замочная скважина ключа
    slot = gem_points(cx, cy, r_long * 0.34, r_short * 0.30)
    d.polygon(slot, fill=SLOT + (255,))
    d.line(slot + [slot[0]], fill=(90, 64, 20, 220), width=max(2, size // 300))

    # блик
    d.arc((cx - r_long * 0.72, cy - r_long * 0.78,
           cx + r_long * 0.72, cy + r_long * 0.62),
          start=200, end=330, fill=(255, 250, 225, 110),
          width=max(2, size // 200))
    return img


def main() -> None:
    big = build()
    png = ROOT / "icon.png"
    big.resize((256, 256), Image.LANCZOS).save(png)
    ico = ROOT / "icon.ico"
    big.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    # контрольная картинка для проверки глазами
    sizes = (16, 24, 32, 48, 64, 128, 256)
    width = sum(sizes) + 18 * len(sizes) + 40
    sheet = Image.new("RGBA", (width, 300), (21, 23, 27, 255))
    x = 20
    for s in sizes:
        thumb = big.resize((s, s), Image.LANCZOS)
        sheet.alpha_composite(thumb, (x, 150 - s // 2))
        x += s + 18
    sheet.save(ROOT / "tools" / "icon_preview.png")
    print("OK:", png, ico)


if __name__ == "__main__":
    main()
