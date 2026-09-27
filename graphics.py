"""Pillow-rendered shapes for the desktop app.

Tk's canvas can't anti-alias, so every curve (rings, pills, rounded groups, avatars)
is drawn here at SUPERSAMPLE x and scaled down. Sizes are device pixels.
"""

import math
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

SUPERSAMPLE = 4

# The Instagram spectrum, bottom-left to top-right
IG_STOPS = ["#FEDA75", "#FA7E1E", "#D62976", "#962FBF", "#4F5BD5"]
PILL_STOPS = ["#FA7E1E", "#D62976", "#962FBF"]


def resource_path(relative: str) -> Path:
    """Files bundled by PyInstaller live under sys._MEIPASS."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


FONT_PATH = resource_path("static/fonts/InterVariable.ttf")


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _strip(stops, length=256) -> Image.Image:
    colors = [_rgb(c) for c in stops]
    strip = Image.new("RGB", (length, 1))
    px = strip.load()
    for x in range(length):
        pos = x / (length - 1) * (len(colors) - 1)
        i = min(int(pos), len(colors) - 2)
        px[x, 0] = _lerp(colors[i], colors[i + 1], pos - i)
    return strip


def gradient(width: int, height: int, stops=IG_STOPS, angle: float = 45.0) -> Image.Image:
    """Linear gradient; angle 0 runs left to right, 90 bottom to top (CSS-like 'to top')."""
    diag = math.ceil(math.hypot(width, height)) + 2
    field = _strip(stops).resize((diag, diag), Image.BILINEAR)
    field = field.rotate(angle, resample=Image.BICUBIC)
    left, top = (diag - width) // 2, (diag - height) // 2
    return field.crop((left, top, left + width, top + height)).convert("RGBA")


def _down(img: Image.Image, size) -> Image.Image:
    return img.resize(size, Image.LANCZOS)


@lru_cache(maxsize=32)
def ring_mask(size: int, thickness: int) -> Image.Image:
    s = SUPERSAMPLE
    mask = Image.new("L", (size * s, size * s), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size * s - 1, size * s - 1), fill=255)
    inset = thickness * s
    d.ellipse((inset, inset, size * s - 1 - inset, size * s - 1 - inset), fill=0)
    return _down(mask, (size, size))


def _disc_mask(size: int) -> Image.Image:
    s = SUPERSAMPLE
    mask = Image.new("L", (size * s, size * s), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * s - 1, size * s - 1), fill=255)
    return _down(mask, (size, size))


def solid_ring(size: int, thickness: int, color: str) -> Image.Image:
    img = Image.new("RGBA", (size, size), color)
    img.putalpha(ring_mask(size, thickness))
    return img


def gradient_ring(size: int, thickness: int, angle: float = 45.0) -> Image.Image:
    img = gradient(size, size, IG_STOPS, angle)
    img.putalpha(ring_mask(size, thickness))
    return img


def _arcs(size, thickness, segments, gap_px=3.0):
    """[(start_deg, end_deg, color)] for each segment, clockwise from 12 o'clock (-90°).
    A segment too short for its caps comes back as a zero-length arc at its middle (a dot)."""
    radius = (size - thickness) / 2
    gap_deg = math.degrees((gap_px + thickness) / radius) if len(segments) > 1 else 0
    arcs, start = [], -90.0
    for fraction, color in segments:
        span = 360.0 * fraction
        a0, a1 = start + gap_deg / 2, start + span - gap_deg / 2
        if a1 <= a0:
            a0 = a1 = start + span / 2
        arcs.append((a0, a1, color))
        start += span
    return arcs


def segmented_ring(size, thickness, segments, track=None, gap_px=3.0) -> Image.Image:
    """segments: [(fraction, color)] summing to <= 1, drawn clockwise from 12 o'clock
    with round caps and small gaps, over an optional track ring."""
    s = SUPERSAMPLE
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    t = thickness * s
    radius = (big - t) / 2
    center = big / 2

    if track:
        d.ellipse((0, 0, big - 1, big - 1), fill=track)
        d.ellipse((t, t, big - 1 - t, big - 1 - t), fill=(0, 0, 0, 0))

    for a0, a1, color in _arcs(size, thickness, segments, gap_px):
        if a1 > a0:
            # Pillow draws a wide arc inward from the box edge
            d.arc((0, 0, big - 1, big - 1), a0, a1, fill=color, width=round(t))
        for ang in (a0, a1):
            r = math.radians(ang)
            cx, cy = center + radius * math.cos(r), center + radius * math.sin(r)
            d.ellipse((cx - t / 2, cy - t / 2, cx + t / 2, cy + t / 2), fill=color)
    return _down(img, (size, size))


# Sweep animation: the finished ring is drawn once; frames only mask it
def pie_mask(size: int, sweep: float) -> Image.Image:
    """L mask covering the first `sweep` (0..1) of the circle, clockwise from 12.
    Drawn at native size: the sweep's round head cap sits over the edge."""
    mask = Image.new("L", (size, size), 0)
    if sweep > 0:
        ImageDraw.Draw(mask).pieslice((-1, -1, size, size), -90, -90 + 360 * sweep, fill=255)
    return mask


def masked(img: Image.Image, mask: Image.Image) -> Image.Image:
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return out


def sweep_head(size, thickness, segments, sweep, gap_px=3.0):
    """(x, y, color) of the sweep's leading edge while it's inside a segment, else None."""
    angle = -90.0 + 360.0 * sweep
    for a0, a1, color in _arcs(size, thickness, segments, gap_px):
        if a0 < angle < a1:
            radius = (size - thickness) / 2
            r = math.radians(angle)
            return size / 2 + radius * math.cos(r), size / 2 + radius * math.sin(r), color
    return None


@lru_cache(maxsize=16)
def disc(diameter: int, color: str) -> Image.Image:
    img = Image.new("RGBA", (diameter, diameter), color)
    img.putalpha(_disc_mask(diameter))
    return img


def rounded_rect(width, height, radius, fill, border=None) -> Image.Image:
    s = SUPERSAMPLE
    img = Image.new("RGBA", (width * s, height * s), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, width * s - 1, height * s - 1), radius=radius * s, fill=fill,
        outline=border, width=s if border else 0,
    )
    return _down(img, (width, height))


def pill(width, height, fill=None, stops=None, overlay=None, text_color=None) -> Image.Image:
    """A capsule. fill is a color, or stops draws a left-to-right gradient.
    overlay=(r, g, b, a) tints it for hover and press states."""
    s = SUPERSAMPLE
    mask = Image.new("L", (width * s, height * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * s - 1, height * s - 1), radius=height * s // 2, fill=255
    )
    mask = _down(mask, (width, height))
    body = gradient(width, height, stops, 0) if stops else Image.new("RGBA", (width, height), fill)
    if overlay:
        body = Image.alpha_composite(body, Image.new("RGBA", (width, height), overlay))
    body.putalpha(mask)
    return body


def bar(width, height, color) -> Image.Image:
    width = max(width, height)
    return pill(width, height, fill=color)


def avatar(size, tile, icon=None, letter="", letter_color="#8E8E93", ring=None, ring_color=None):
    """An Instagram-style avatar: the app icon on a round tile, inside an optional ring.
    ring: None, 'live' (gradient) or 'seen' (ring_color)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring_w = max(2, round(size * 0.055))
    gap = max(2, round(size * 0.05))
    if ring == "live":
        img = Image.alpha_composite(img, gradient_ring(size, ring_w))
    elif ring == "seen":
        img = Image.alpha_composite(img, solid_ring(size, max(1, ring_w - 1), ring_color))

    inner = size - 2 * (ring_w + gap) if ring else size
    offset = (size - inner) // 2
    disc = Image.new("RGBA", (inner, inner), tile)
    disc.putalpha(_disc_mask(inner))
    img.alpha_composite(disc, (offset, offset))

    if icon is not None:
        icon_px = round(inner * 0.62)
        glyph = icon.resize((icon_px, icon_px), Image.LANCZOS)
        img.alpha_composite(glyph, ((size - icon_px) // 2, (size - icon_px) // 2))
    elif letter:
        font = inter(round(inner * 0.42), 600)
        d = ImageDraw.Draw(img)
        d.text((size / 2, size / 2), letter[:1].upper(), font=font, fill=letter_color, anchor="mm")
    return img


@lru_cache(maxsize=32)
def inter(px: int, weight: int = 400, display: bool = False) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), px)
    try:
        font.set_variation_by_axes([32 if display else 14, weight])
    except Exception:
        pass
    return font


def numerals(text: str, px: int, color: str, weight: int = 600, tracking: float = -0.02):
    """Display numerals with fixed-width digits (so a ticking timer never jitters)
    and tight tracking. Pillow here has no OpenType layout, so digits are placed by hand."""
    s = 2
    font = inter(px * s, weight, display=True)
    digit_w = max(font.getlength(d) for d in "0123456789")
    track = tracking * px * s

    def advance(ch):
        return (digit_w if ch.isdigit() else font.getlength(ch)) + track

    width = math.ceil(sum(advance(ch) for ch in text) - track) + 4 * s
    ascent, descent = font.getmetrics()
    img = Image.new("RGBA", (width, ascent + descent), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 2 * s
    for ch in text:
        w = font.getlength(ch)
        slot = digit_w if ch.isdigit() else w
        d.text((x + (slot - w) / 2, 0), ch, font=font, fill=color)
        x += advance(ch)
    bbox = img.getbbox() or (0, 0, 1, 1)
    # Keep the full line height so baselines line up; trim only horizontally
    img = img.crop((bbox[0], 0, bbox[2], ascent + descent))
    return img.resize((max(1, img.width // s), max(1, img.height // s)), Image.LANCZOS), ascent / s


def app_mark(size: int) -> Image.Image:
    """Odin's Kin mark: a story ring around the Ansuz rune, the rune of Odin."""
    s = SUPERSAMPLE
    big = size * s
    ring_w = max(2, round(size * 0.09))
    gap = max(1, round(size * 0.05))
    img = gradient_ring(size, ring_w).resize((big, big), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    inset = (ring_w + gap) * s
    d.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill="#111114")

    stroke = max(2, round(size * 0.075)) * s
    cx, top, bottom = big * 0.43, big * 0.29, big * 0.72
    arm = big * 0.19

    def line(x0, y0, x1, y1):
        d.line((x0, y0, x1, y1), fill="#FFFFFF", width=stroke)
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill="#FFFFFF")

    line(cx, top, cx, bottom)
    line(cx, top, cx + arm, top + arm * 0.9)
    line(cx, top + arm * 0.95, cx + arm, top + arm * 1.85)
    return _down(img, (size, size))
