from __future__ import annotations

import math
import platform
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from .models import ReviewPayload

CANVAS_W = 1080
CANVAS_H = 1350

# OS-aware candidate paths, tried in order before falling back to Pillow's
# built-in scalable default font. Explicit paths (rather than bare filenames)
# so behavior doesn't depend on OS font-search quirks - the previous version
# silently degraded to a tiny bitmap font on non-Windows hosts with no warning.
_FONT_CANDIDATES = {
    'Windows': [
        ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf'),
    ],
    'Darwin': [
        ('/System/Library/Fonts/Supplemental/Arial.ttf', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
        ('/Library/Fonts/Arial.ttf', '/Library/Fonts/Arial Bold.ttf'),
    ],
    'Linux': [
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
        ('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    ],
}

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
_warned_fallback = False


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    cache_key = (size, bold)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    candidates = _FONT_CANDIDATES.get(platform.system(), [])
    for regular_path, bold_path in candidates:
        path = bold_path if bold else regular_path
        try:
            font = ImageFont.truetype(path, size=size)
            _font_cache[cache_key] = font
            return font
        except Exception:
            continue

    global _warned_fallback
    if not _warned_fallback:
        print(
            'jvto_instagram_automation: no system TrueType font found for this OS; '
            'falling back to Pillow default font (cards will look plainer).'
        )
        _warned_fallback = True

    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        # Pillow < 10.1 does not support a sized default font.
        font = ImageFont.load_default()
    _font_cache[cache_key] = font
    return font


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, w: int, font: ImageFont.ImageFont) -> list[str]:
    lines = []
    words = text.split()
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] <= w:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_box(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, w: int, h: int, font: ImageFont.ImageFont, fill: str, align: str = 'left') -> None:
    lines = _wrap_text(draw, text, w, font)
    line_height = max(24, int(font.getbbox('Ag')[3] * 1.15))
    total_height = len(lines) * line_height
    start_y = y + max(0, (h - total_height) // 2)
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        if align == 'center':
            text_x = x + (w - text_w) // 2
        elif align == 'right':
            text_x = x + w - text_w
        else:
            text_x = x
        draw.text((text_x, start_y + index * line_height), line, font=font, fill=fill)


def _star_points(cx: float, cy: float, outer_r: float, inner_r: float) -> list[tuple[float, float]]:
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = outer_r if i % 2 == 0 else inner_r
        points.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    return points


def _draw_star_rating(draw: ImageDraw.ImageDraw, cx: int, cy: int, count: int = 5, size: int = 40, gap: int = 12, fill: str = '#f5b700') -> None:
    # Drawn as vector polygons rather than a "★" glyph - not every bundled
    # TrueType font (Arial included, on some installs) ships that character,
    # and a missing glyph renders as an empty tofu box instead of a star.
    outer_r = size / 2
    inner_r = outer_r * 0.42
    total_w = count * size + (count - 1) * gap
    start_x = cx - total_w / 2 + outer_r
    for i in range(count):
        star_cx = start_x + i * (size + gap)
        draw.polygon(_star_points(star_cx, cy, outer_r, inner_r), fill=fill)


def _apply_glass_panel(image: Image.Image, box: tuple[int, int, int, int], radius: int, fill_rgba: tuple[int, int, int, int], outline_rgba: tuple[int, int, int, int] | None = None) -> None:
    # Pillow ignores the alpha channel of fill/outline colors when drawing on
    # an RGB image - it silently treats (255,255,255,30) as fully opaque
    # white, which was painting over the card text entirely. Draw the panel
    # on a real RGBA layer and alpha-composite it in instead.
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(box, radius=radius, fill=fill_rgba, outline=outline_rgba)
    composited = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
    image.paste(composited)


def _build_background(image: Image.Image, accent: str) -> None:
    base = Image.new('RGBA', image.size, accent)
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(image.height):
        alpha = int(20 + (y / max(1, image.height - 1)) * 80)
        draw.line([(0, y), (image.width, y)], fill=(0, 0, 0, alpha))
    composite = Image.alpha_composite(base, overlay)
    image.paste(composite.convert('RGB'), (0, 0))


def _upgrade_google_photo_url(url: str) -> str:
    # Google's review-media URLs default to a small thumbnail (e.g. 512x384)
    # unless a size directive is appended. Request an exact, high-quality
    # crop at canvas size instead of upscaling a low-res thumbnail.
    if 'googleusercontent.com' not in url:
        return url
    base = url.split('=')[0]
    return f'{base}=w{CANVAS_W}-h{CANVAS_H}-c'


def _load_background_photo(bg_url: str | None, accent: str) -> Image.Image:
    if bg_url:
        try:
            response = requests.get(_upgrade_google_photo_url(bg_url), timeout=15)
            response.raise_for_status()
            photo = Image.open(BytesIO(response.content)).convert('RGB')
            if photo.size != (CANVAS_W, CANVAS_H):
                photo = photo.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
            return photo
        except Exception:
            pass
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (11, 17, 29))
    _build_background(image, accent)
    return image


def _make_qr_overlay(review_url: str | None) -> Image.Image | None:
    if not review_url:
        return None
    try:
        import qrcode
    except Exception:
        return None
    try:
        qr = qrcode.QRCode(version=1, box_size=6, border=2)
        qr.add_data(review_url)
        qr.make(fit=True)
        return qr.make_image(fill_color='black', back_color='white').convert('RGB')
    except Exception:
        return None


def build_card_1(payload: ReviewPayload, output_path: Path) -> None:
    narrative = payload.narrative
    image = _load_background_photo(narrative.bg_photo_url, '#1f6f8b')
    draw = ImageDraw.Draw(image)
    _apply_glass_panel(image, (40, 40, CANVAS_W - 40, CANVAS_H - 40), 36, (255, 255, 255, 30), (255, 255, 255, 80))

    title = f"GUEST STORY: {narrative.guest_name} - {', '.join(narrative.destinations)}"
    subtitle = narrative.caption or f"Experiencing {narrative.destinations[0]} • {narrative.guest_type}"
    title_font = _load_font(44, bold=True)
    sub_font = _load_font(28)
    _draw_text_box(draw, title, int(CANVAS_W * 0.06), int(CANVAS_H * 0.12), int(CANVAS_W * 0.88), int(CANVAS_H * 0.16), title_font, 'white')
    _draw_text_box(draw, subtitle, int(CANVAS_W * 0.06), int(CANVAS_H * 0.82), int(CANVAS_W * 0.88), int(CANVAS_H * 0.08), sub_font, '#f5f5f5')
    image.save(output_path)


def build_card_2(payload: ReviewPayload, output_path: Path) -> None:
    narrative = payload.narrative
    photo_url = narrative.secondary_photo_urls[0] if narrative.secondary_photo_urls else None
    image = _load_background_photo(photo_url, '#b55e1f')
    if photo_url:
        # Darken the real photo so overlaid text stays readable, same
        # treatment as the plain gradient fallback used to get for free.
        overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle((0, 0, CANVAS_W, CANVAS_H), fill=(0, 0, 0, 90))
        image = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
    draw = ImageDraw.Draw(image)
    _apply_glass_panel(image, (40, 40, CANVAS_W - 40, CANVAS_H - 40), 36, (255, 255, 255, 30), (255, 255, 255, 80))

    tag_font = _load_font(26, bold=True)
    label_font = _load_font(28, bold=True)
    body_font = _load_font(24)
    draw.rounded_rectangle((int(CANVAS_W * 0.72), int(CANVAS_H * 0.06), int(CANVAS_W * 0.92), int(CANVAS_H * 0.12)), radius=16, fill=(255, 255, 255))
    _draw_text_box(draw, 'Guide Support', int(CANVAS_W * 0.72), int(CANVAS_H * 0.06), int(CANVAS_W * 0.20), int(CANVAS_H * 0.06), tag_font, '#2f2f2f', align='center')

    guide_names = ', '.join(narrative.guide_names or ['Local Guide'])
    driver_names = ', '.join(narrative.driver_names or ['Driver'])
    label_text = f'Guide: {guide_names} • Driver: {driver_names}'
    _draw_text_box(draw, label_text, int(CANVAS_W * 0.06), int(CANVAS_H * 0.82), int(CANVAS_W * 0.70), int(CANVAS_H * 0.08), label_font, 'white')
    body_text = f'This review highlights the care behind the journey for {narrative.guest_type}.'
    _draw_text_box(draw, body_text, int(CANVAS_W * 0.06), int(CANVAS_H * 0.66), int(CANVAS_W * 0.88), int(CANVAS_H * 0.08), body_font, '#fef3d0')
    image.save(output_path)


def build_card_3(payload: ReviewPayload, output_path: Path) -> None:
    narrative = payload.narrative
    photo_url = narrative.secondary_photo_urls[1] if len(narrative.secondary_photo_urls) > 1 else None
    image = _load_background_photo(photo_url, '#2d6cdf')
    if photo_url:
        overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle((0, 0, CANVAS_W, CANVAS_H), fill=(0, 0, 0, 90))
        image = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
    draw = ImageDraw.Draw(image)
    _apply_glass_panel(image, (40, 40, CANVAS_W - 40, CANVAS_H - 40), 36, (255, 255, 255, 30), (255, 255, 255, 80))

    if narrative.highlight == 'blue fire':
        headline = 'Ijen Blue Fire Experience'
        body = narrative.visual_prompt or 'A dramatic sunrise experience that turns the volcano into a once-in-a-lifetime story.'
    elif narrative.highlight == 'waterfall':
        headline = 'Tumpak Sewu Adventure'
        body = narrative.visual_prompt or 'A powerful waterfall journey with nature, views, and unforgettable photo moments.'
    else:
        headline = 'Memorable East Java Adventure'
        body = narrative.visual_prompt or 'The journey combines comfort, scenery, and local guidance in one seamless experience.'

    if len(narrative.destinations) > 1:
        headline = f'{narrative.destinations[0]} & {narrative.destinations[1]} Adventure'

    headline_font = _load_font(38, bold=True)
    body_font = _load_font(26)
    _draw_text_box(draw, headline, int(CANVAS_W * 0.06), int(CANVAS_H * 0.18), int(CANVAS_W * 0.88), int(CANVAS_H * 0.16), headline_font, 'white')
    _draw_text_box(draw, body, int(CANVAS_W * 0.06), int(CANVAS_H * 0.42), int(CANVAS_W * 0.88), int(CANVAS_H * 0.20), body_font, '#eaf2ff')
    image.save(output_path)


def build_card_4(payload: ReviewPayload, output_path: Path) -> None:
    narrative = payload.narrative
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (248, 248, 248))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, CANVAS_W - 40, CANVAS_H - 40), radius=36, fill=(255, 255, 255), outline=(220, 220, 220))

    quote_font = _load_font(32)
    attrib_font = _load_font(24)
    logo_font = _load_font(24, bold=True)
    micro_font = _load_font(18)

    _draw_text_box(draw, f'"{narrative.quote_short}"', int(CANVAS_W * 0.10), int(CANVAS_H * 0.30), int(CANVAS_W * 0.80), int(CANVAS_H * 0.42), quote_font, '#2b2b2b')
    _draw_star_rating(draw, int(CANVAS_W * 0.50), int(CANVAS_H * 0.18))
    attribution = f"— {narrative.guest_name} • {narrative.guest_type.title()}"
    _draw_text_box(draw, attribution, int(CANVAS_W * 0.50), int(CANVAS_H * 0.78), int(CANVAS_W * 0.40), int(CANVAS_H * 0.06), attrib_font, '#555555', align='center')
    _draw_text_box(draw, 'JVTO', int(CANVAS_W * 0.50), int(CANVAS_H * 0.92), int(CANVAS_W * 0.20), int(CANVAS_H * 0.04), logo_font, '#0f5f78', align='center')

    # Plain text, no emoji: not every bundled font has emoji glyphs, and a
    # missing glyph renders as a tofu box (see _draw_star_rating above).
    if narrative.review_url_kind == 'specific':
        link_note = 'Full review link in caption & our Linktree bio'
    elif narrative.review_url_kind == 'profile':
        link_note = 'See more verified reviews in our Linktree bio'
    else:
        link_note = None

    qr_overlay = _make_qr_overlay(narrative.review_url if narrative.review_url_kind != 'none' else None)
    if link_note:
        text_w = int(CANVAS_W * 0.60) if qr_overlay else int(CANVAS_W * 0.88)
        _draw_text_box(draw, link_note, int(CANVAS_W * 0.06), int(CANVAS_H * 0.955), text_w, int(CANVAS_H * 0.03), micro_font, '#777777')
    if qr_overlay:
        qr_size = 130
        qr_overlay = qr_overlay.resize((qr_size, qr_size))
        image.paste(qr_overlay, (CANVAS_W - qr_size - 60, CANVAS_H - qr_size - 60))

    image.save(output_path)


def create_carousel(payload: ReviewPayload, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    builders = [build_card_1, build_card_2, build_card_3, build_card_4]
    paths = []
    for index, builder in enumerate(builders, start=1):
        out_path = output_dir / f'card{index}.png'
        builder(payload, out_path)
        paths.append(out_path)
    return paths
