"""OG Forge — Open Graph image generation engine.

Pure-computation image renderer built on Pillow. No external API calls.
Produces 1200x630 (or custom) social cards from structured input.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def parse_color(value: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse hex color (#rgb / #rrggbb) with fallback to default."""
    if not value:
        return default
    v = value.strip()
    if not _HEX_RE.match(v):
        return default
    v = v.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def luminance(rgb: tuple[int, int, int]) -> float:
    """Relative luminance (0-1) to decide readable text color."""
    def chan(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def readable_on(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return (255, 255, 255) if luminance(bg) < 0.55 else (17, 17, 17)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _FONT_CACHE:
        path = FONT_DIR / f"Inter-{weight}.ttf"
        if not path.exists():
            path = FONT_DIR / "Inter-Regular.ttf"
        _FONT_CACHE[key] = ImageFont.truetype(str(path), size)
    return _FONT_CACHE[key]


# ---------------------------------------------------------------------------
# Layout model
# ---------------------------------------------------------------------------

@dataclass
class CardSpec:
    title: str = "Your Title Here"
    subtitle: str = ""
    brand: str = ""
    template: str = "gradient"
    bg_color: Optional[str] = None
    bg_color2: Optional[str] = None
    accent_color: Optional[str] = None
    text_color: Optional[str] = None
    width: int = 1200
    height: int = 630
    padding: int = 80
    theme: str = "auto"  # auto | dark | light

    # derived
    bg: tuple[int, int, int] = field(init=False)
    bg2: tuple[int, int, int] = field(init=False)
    accent: tuple[int, int, int] = field(init=False)
    fg: tuple[int, int, int] = field(init=False)

    def __post_init__(self) -> None:
        self.width = max(200, min(2400, int(self.width)))
        self.height = max(200, min(2400, int(self.height)))
        self.padding = max(16, min(self.width // 4, int(self.padding)))

        # Palette resolution
        light = self.theme == "light" or (
            self.theme == "auto" and self.template in ("minimal",)
        )
        default_bg = (248, 250, 252) if light else (15, 15, 20)
        default_bg2 = (226, 232, 240) if light else (30, 30, 40)
        default_accent = (99, 102, 241)

        self.bg = parse_color(self.bg_color, default_bg)
        self.bg2 = parse_color(self.bg_color2, default_bg2)
        self.accent = parse_color(self.accent_color, default_accent)
        self.fg = parse_color(self.text_color, readable_on(self.bg))


# ---------------------------------------------------------------------------
# Text layout
# ---------------------------------------------------------------------------

def wrap_text(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit max_width. Handles long unbroken tokens."""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        words = raw_line.split(" ")
        cur = ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=f) <= max_width or not cur:
                # allow overflowing single long token
                if not cur and draw.textlength(word, font=f) > max_width:
                    # hard-split long token
                    chunk = ""
                    for ch in word:
                        if draw.textlength(chunk + ch, font=f) > max_width and chunk:
                            lines.append(chunk)
                            chunk = ch
                        else:
                            chunk += ch
                    cur = chunk
                else:
                    cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return [l for l in lines if l != ""] or [""]


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    weight: str,
    start_size: int,
    min_size: int,
    max_width: int,
    max_lines: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Shrink font until wrapped text fits in max_lines."""
    size = start_size
    while size >= min_size:
        f = font(weight, size)
        lines = wrap_text(draw, text, f, max_width)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = font(weight, min_size)
    lines = wrap_text(draw, text, f, max_width)[:max_lines]
    return f, lines


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    f: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    color: tuple[int, int, int],
    line_gap: float = 1.18,
) -> int:
    """Draw left-aligned lines starting at (x, y). Returns bottom y."""
    ascent, descent = f.getmetrics()
    line_h = int((ascent + descent) * line_gap / 2) or 1
    cy = y
    for line in lines:
        draw.text((x, cy), line, font=f, fill=color)
        cy += line_h
    return cy


# ---------------------------------------------------------------------------
# Background painters
# ---------------------------------------------------------------------------

_GRADIENT_CACHE: dict[tuple[int, int, float], Image.Image] = {}


def paint_linear_gradient(img: Image.Image, c1: tuple, c2: tuple, angle_deg: float = 25.0) -> None:
    """Fast gradient: small rotated mask, upscaled with bilinear. Mask is cached."""
    w, h = img.size
    overlay = Image.new("RGB", (w, h), c2)

    key = (w, h, angle_deg)
    mask = _GRADIENT_CACHE.get(key)
    if mask is None:
        small = 64
        grad = Image.new("L", (small, small))
        px = grad.load()
        for x in range(small):
            v = int(255 * x / (small - 1))
            for y in range(small):
                px[x, y] = v
        grad = grad.rotate(-angle_deg, resample=Image.BILINEAR, expand=False)

        aspect = w / h
        if aspect >= 1:
            cw, ch = small, max(1, int(small / aspect))
        else:
            cw, ch = max(1, int(small * aspect)), small
        left, top = (small - cw) // 2, (small - ch) // 2
        mask = grad.crop((left, top, left + cw, top + ch)).resize((w, h), Image.BILINEAR)
        if len(_GRADIENT_CACHE) > 32:
            _GRADIENT_CACHE.clear()
        _GRADIENT_CACHE[key] = mask

    img.paste(overlay, (0, 0), mask)


def paint_glow(img: Image.Image, center: tuple[int, int], color: tuple, radius: int) -> None:
    """Fast glow: render at 1/4 scale, blur, upscale."""
    w, h = img.size
    scale = 4
    sw, sh = max(2, w // scale), max(2, h // scale)
    glow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    cx, cy, r = center[0] // scale, center[1] // scale, radius // scale
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(r // 2))
    glow = glow.resize((w, h), Image.BILINEAR)
    img.paste(glow, (0, 0), glow)


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, **kw) -> None:
    draw.rounded_rectangle(box, radius=radius, **kw)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def render(spec: CardSpec) -> bytes:
    img = Image.new("RGB", (spec.width, spec.height), spec.bg)
    draw = ImageDraw.Draw(img)

    if spec.template == "gradient":
        _t_gradient(img, draw, spec)
    elif spec.template == "split":
        _t_split(img, draw, spec)
    elif spec.template == "spotlight":
        _t_spotlight(img, draw, spec)
    elif spec.template == "banner":
        _t_banner(img, draw, spec)
    else:  # minimal
        _t_minimal(img, draw, spec)

    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _t_gradient(img: Image.Image, draw: ImageDraw.ImageDraw, s: CardSpec) -> None:
    paint_linear_gradient(img, s.bg, s.bg2, angle_deg=28)
    draw = ImageDraw.Draw(img)
    # subtle accent glow bottom-right
    paint_glow(img, (s.width - 120, s.height + 60), s.accent, 380)
    draw = ImageDraw.Draw(img)

    x = s.padding
    max_w = s.width - 2 * s.padding

    # brand pill
    y = s.padding - 6
    if s.brand:
        f = font("SemiBold", 30)
        tw = draw.textlength(s.brand, font=f)
        pad_x, pad_y = 22, 12
        rounded_rect(
            draw,
            [x, y, x + tw + 2 * pad_x, y + 30 + 2 * pad_y],
            radius=999,
            fill=(*mix(s.accent, (255, 255, 255), 0.85),),
            outline=None,
        )
        draw.text((x + pad_x, y + pad_y - 1), s.brand, font=f, fill=s.accent)
        y += 30 + 2 * pad_y + 44

    # title
    start = 92 if s.width >= 1100 else int(92 * s.width / 1200)
    f_title, lines = fit_font(draw, s.title, "Bold", start, 40, max_w, 3)
    y = draw_text_block(draw, lines, f_title, x, y, s.fg)

    # subtitle
    if s.subtitle:
        y += 18
        f_sub, sub_lines = fit_font(draw, s.subtitle, "Regular", 36, 22, max_w, 2)
        sub_color = mix(s.fg, s.bg, 0.35)
        draw_text_block(draw, sub_lines, f_sub, x, y, sub_color)

    # bottom accent line
    draw.rectangle([x, s.height - s.padding // 2, x + 120, s.height - s.padding // 2 + 6], fill=s.accent)


def _t_split(img: Image.Image, draw: ImageDraw.ImageDraw, s: CardSpec) -> None:
    split = int(s.width * 0.62)
    paint_linear_gradient(img, s.bg, s.bg, angle_deg=0)
    draw = ImageDraw.Draw(img)

    # right panel
    right = Image.new("RGB", (s.width - split, s.height), s.accent)
    mask = Image.new("L", right.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [24, 24, right.width - 24, right.height - 24], radius=32, fill=255
    )
    img.paste(right, (split, 0), mask)
    draw = ImageDraw.Draw(img)

    x = s.padding
    max_w = split - s.padding - 40
    y = s.padding

    if s.brand:
        f = font("SemiBold", 28)
        draw.text((x, y), s.brand.upper(), font=f, fill=s.accent)
        y += 28 + 42

    f_title, lines = fit_font(draw, s.title, "Bold", 84, 36, max_w, 3)
    y = draw_text_block(draw, lines, f_title, x, y, s.fg)

    if s.subtitle:
        y += 16
        f_sub, sub_lines = fit_font(draw, s.subtitle, "Regular", 32, 20, max_w, 3)
        draw_text_block(draw, sub_lines, f_sub, x, y, mix(s.fg, s.bg, 0.4))

    # decorative circles on accent panel
    cx, cy = split + (s.width - split) // 2, s.height // 2
    for r, alpha in ((150, 40), (100, 60), (60, 90)):
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).ellipse(
            [cx - r, cy - r, cx + r, cy + r], fill=(*mix(s.accent, (255, 255, 255), 0.5), alpha)
        )
        img.paste(overlay, (0, 0), overlay)
    draw = ImageDraw.Draw(img)


def _t_spotlight(img: Image.Image, draw: ImageDraw.ImageDraw, s: CardSpec) -> None:
    paint_glow(img, (s.width // 2, -100), s.accent, int(s.width * 0.55))
    paint_glow(img, (s.width // 4, s.height), mix(s.accent, s.bg2, 0.5), int(s.width * 0.35))
    draw = ImageDraw.Draw(img)

    max_w = s.width - 2 * s.padding

    if s.brand:
        f = font("SemiBold", 30)
        tw = draw.textlength(s.brand, font=f)
        draw.text(((s.width - tw) / 2, s.padding - 8), s.brand, font=f, fill=s.accent)

    # centered title
    start = 96 if s.width >= 1100 else int(96 * s.width / 1200)
    f_title, lines = fit_font(draw, s.title, "Bold", start, 44, max_w, 3)
    ascent, descent = f_title.getmetrics()
    line_h = int((ascent + descent) * 1.18)
    block_h = line_h * len(lines)
    y = (s.height - block_h) // 2 + (12 if s.brand else 0)
    for line in lines:
        tw = draw.textlength(line, font=f_title)
        draw.text(((s.width - tw) / 2, y), line, font=f_title, fill=s.fg)
        y += line_h

    if s.subtitle:
        y += 20
        f_sub, sub_lines = fit_font(draw, s.subtitle, "Regular", 34, 22, max_w, 2)
        for line in sub_lines:
            tw = draw.textlength(line, font=f_sub)
            draw.text(((s.width - tw) / 2, y), line, font=f_sub, fill=mix(s.fg, s.bg, 0.35))
            y += int(f_sub.getmetrics()[0] * 1.4)


def _t_banner(img: Image.Image, draw: ImageDraw.ImageDraw, s: CardSpec) -> None:
    bar_h = 14
    draw.rectangle([0, 0, s.width, bar_h], fill=s.accent)

    x = s.padding
    max_w = s.width - 2 * s.padding
    y = s.padding + bar_h + 16

    if s.brand:
        f = font("SemiBold", 28)
        draw.text((x, y), s.brand.upper(), font=f, fill=s.accent)
        y += 28 + 40

    f_title, lines = fit_font(draw, s.title, "SemiBold", 80, 36, max_w, 3)
    y = draw_text_block(draw, lines, f_title, x, y, s.fg)

    if s.subtitle:
        y += 16
        f_sub, sub_lines = fit_font(draw, s.subtitle, "Regular", 32, 20, max_w, 2)
        draw_text_block(draw, sub_lines, f_sub, x, y, mix(s.fg, s.bg, 0.4))


def _t_minimal(img: Image.Image, draw: ImageDraw.ImageDraw, s: CardSpec) -> None:
    # thin border frame
    inset = s.padding // 2
    draw.rounded_rectangle(
        [inset, inset, s.width - inset, s.height - inset],
        radius=24,
        outline=mix(s.fg, s.bg, 0.75),
        width=2,
    )

    max_w = s.width - 4 * inset
    y = s.height // 3

    if s.brand:
        f = font("SemiBold", 28)
        tw = draw.textlength(s.brand, font=f)
        draw.text(((s.width - tw) / 2, y), s.brand.upper(), font=f, fill=s.accent)
        y += 28 + 36

    f_title, lines = fit_font(draw, s.title, "SemiBold", 84, 40, max_w, 3)
    for line in lines:
        tw = draw.textlength(line, font=f_title)
        draw.text(((s.width - tw) / 2, y), line, font=f_title, fill=s.fg)
        ascent, descent = f_title.getmetrics()
        y += int((ascent + descent) * 1.18)

    if s.subtitle:
        y += 18
        f_sub, sub_lines = fit_font(draw, s.subtitle, "Regular", 32, 20, max_w, 2)
        for line in sub_lines:
            tw = draw.textlength(line, font=f_sub)
            draw.text(((s.width - tw) / 2, y), line, font=f_sub, fill=mix(s.fg, s.bg, 0.4))
            ascent, descent = f_sub.getmetrics()
            y += int((ascent + descent) * 1.4)


TEMPLATES = ["gradient", "split", "spotlight", "banner", "minimal"]
