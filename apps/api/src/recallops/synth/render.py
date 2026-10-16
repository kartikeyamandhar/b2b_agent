"""Programmatic photos for synthetic claims. No real consumer imagery, no real brand marks, no people.

Each photo is a seeded scene: procedural background, object placement, rotation, blur, and JPEG quality are
drawn from the photo's own RNG stream, which is independent of the claim's violations, so image quality never
correlates with the label. Fonts: Pillow's bundled default font only (no system fonts, no downloads).
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 768, 576
Tier = Literal["clean", "mild", "hard"]
Family = Literal["cut_cord", "cut_material", "mark_text", "disassemble", "generic"]


@dataclass(frozen=True)
class Style:
    tier: Tier
    rotation: float
    blur: float
    quality: int
    hue: tuple[int, int, int]
    offset: tuple[int, int]
    scale: float


def draw_style(rng: random.Random) -> Style:
    tiers: list[Tier] = ["clean", "mild", "hard"]
    tier: Tier = rng.choices(tiers, weights=[0.6, 0.3, 0.1])[0]
    rotation = {"clean": 2.0, "mild": 6.0, "hard": 12.0}[tier]
    blur = {"clean": 0.0, "mild": 0.8, "hard": 1.6}[tier]
    quality = {"clean": 90, "mild": 70, "hard": 45}[tier]
    return Style(
        tier=tier,
        rotation=rng.uniform(-rotation, rotation),
        blur=rng.uniform(0, blur),
        quality=quality,
        hue=(rng.randint(40, 215), rng.randint(40, 215), rng.randint(40, 215)),
        offset=(rng.randint(-80, 80), rng.randint(-60, 60)),
        scale=rng.uniform(0.8, 1.15),
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _background(rng: random.Random, style: Style) -> Image.Image:
    base = Image.new("RGB", (WIDTH, HEIGHT), style.hue)
    draw = ImageDraw.Draw(base)
    for _ in range(rng.randint(30, 60)):
        x, y = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        r = rng.randint(10, 90)
        red, green, blue = (max(0, min(255, c + rng.randint(-45, 45))) for c in style.hue)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(red, green, blue))
    return base.filter(ImageFilter.GaussianBlur(6))


def _finish(image: Image.Image, style: Style) -> bytes:
    rotated = image.rotate(style.rotation, resample=Image.Resampling.BICUBIC, fillcolor=style.hue)
    if style.blur > 0:
        rotated = rotated.filter(ImageFilter.GaussianBlur(style.blur))
    out = io.BytesIO()
    rotated.save(out, format="JPEG", quality=style.quality, optimize=False)
    return out.getvalue()


def render_label(rng: random.Random, style: Style, lines: list[str]) -> bytes:
    """A product rating sticker with the identifying fields among distractor fields."""
    image = _background(rng, style)
    draw = ImageDraw.Draw(image)
    w, h = int(420 * style.scale), int(280 * style.scale)
    x0 = (WIDTH - w) // 2 + style.offset[0]
    y0 = (HEIGHT - h) // 2 + style.offset[1]
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=12, fill=(244, 244, 238), outline=(40, 40, 40), width=3)
    size = max(16, int(24 * style.scale))
    font = _font(size)
    y = y0 + 18
    for line in lines:
        draw.text((x0 + 22, y), line, fill=(15, 15, 15), font=font)
        y += size + 10
    for i in range(rng.randint(18, 30)):  # barcode-like stripes
        bx = x0 + 22 + i * 7
        draw.rectangle((bx, y0 + h - 46, bx + rng.randint(1, 4), y0 + h - 16), fill=(20, 20, 20))
    return _finish(image, style)


def render_destruction(rng: random.Random, style: Style, family: Family, complies: bool) -> bytes:
    """A product silhouette that shows, or fails to show, the instructed destruction."""
    image = _background(rng, style)
    draw = ImageDraw.Draw(image)
    w, h = int(300 * style.scale), int(220 * style.scale)
    x0 = (WIDTH - w) // 2 + style.offset[0]
    y0 = (HEIGHT - h) // 2 + style.offset[1] - 40
    body = (90, 90, 96) if family != "cut_material" else (170, 60, 70)
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=18, fill=body, outline=(20, 20, 20), width=4)
    cord_y = y0 + h // 2
    cord = [(x0 + w, cord_y), (x0 + w + 90, cord_y + 60), (x0 + w + 170, cord_y + 140)]
    if family == "cut_cord" and complies:
        draw.line(cord[:2], fill=(10, 10, 10), width=9)
        draw.line([(cord[1][0] + 22, cord[1][1] + 18), cord[2]], fill=(10, 10, 10), width=9)
    else:
        draw.line(cord, fill=(10, 10, 10), width=9)
    if family == "cut_material" and complies:
        draw.line((x0 + w // 2, y0 - 10, x0 + w // 2 + 12, y0 + h + 10), fill=style.hue, width=14)
    if family in ("mark_text", "generic") and complies:
        draw.text((x0 + 24, y0 + h // 3), "RECALLED", fill=(250, 250, 250), font=_font(max(26, int(44 * style.scale))))
    if family == "disassemble" and complies:
        draw.rectangle((x0 - 60, y0 + h + 30, x0 + 40, y0 + h + 70), fill=body, outline=(20, 20, 20), width=3)
        draw.rectangle((x0 + w - 40, y0 + h + 40, x0 + w + 70, y0 + h + 80), fill=body, outline=(20, 20, 20), width=3)
    return _finish(image, style)


def render_receipt(rng: random.Random, style: Style, retailer: str, purchased: str, amount_cents: int) -> bytes:
    """A plain placeholder receipt, watermarked as synthetic; no logos, no real formatting."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (236, 236, 230))
    draw = ImageDraw.Draw(image)
    font = _font(22)
    lines = [
        "SYNTHETIC TEST RECEIPT",
        f"Store: {retailer}",
        f"Date: {purchased}",
        "Item: recalled product",
        f"Total: ${amount_cents / 100:.2f}",
        f"Ref: {rng.randint(100000, 999999)}",
    ]
    for i, line in enumerate(lines):
        draw.text((60 + style.offset[0] // 4, 60 + i * 44), line, fill=(30, 30, 30), font=font)
    draw.text((300, 470), "SYNTHETIC", fill=(200, 120, 120), font=_font(48))
    return _finish(image, style)


def instruction_family(instruction: str | None) -> Family:
    text = (instruction or "").lower()
    if "cut" in text and ("cord" in text or "plug" in text):
        return "cut_cord"
    if "cut" in text:
        return "cut_material"
    if "disassembl" in text:
        return "disassemble"
    if "recall" in text or "marker" in text or "write" in text:
        return "mark_text"
    return "generic"
