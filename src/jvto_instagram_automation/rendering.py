from __future__ import annotations

import math
import platform
import re
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from .models import ReviewPayload

# Emoji/pictograph ranges the bundled TrueType fonts (Arial, DejaVu,
# Liberation) don't cover - a missing glyph renders as a tofu box, so strip
# these before drawing rather than showing broken boxes in review quotes.
_UNSUPPORTED_GLYPHS = re.compile(
    '['
    '\U0001F000-\U0001FFFF'
    '\U00002600-\U000027BF'
    '\U0001F1E6-\U0001F1FF'
    '\U00002B00-\U00002BFF'
    '\U0000FE00-\U0000FE0F'
    '\U0000200D'
    ']+'
)


def _sanitize_for_image_text(text: str) -> str:
    return _UNSUPPORTED_GLYPHS.sub('', text)

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
    lines = _wrap_text(draw, _sanitize_for_image_text(text), w, font)
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


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    """Fit unbreakable single-line text (e.g. a URL) into max_w, adding an
    ellipsis if needed. _wrap_text can't help here since it only breaks on
    spaces, and a URL has none - without this it just overflows the canvas.
    """
    if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + '…'
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + '…'


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

    # Only credit guide/driver names actually present in the review - a
    # fabricated "Local Guide" / "Driver" placeholder would misrepresent the
    # review as naming a team member it never mentioned.
    label_parts = []
    if narrative.guide_names:
        label_parts.append(f"Guide: {', '.join(narrative.guide_names)}")
    if narrative.driver_names:
        label_parts.append(f"Driver: {', '.join(narrative.driver_names)}")
    if label_parts:
        label_text = ' • '.join(label_parts)
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


GUEST_TYPE_LABELS = {
    'solo': 'SOLO TRAVELER',
    'couple': 'COUPLE',
    'family': 'FAMILY',
    'friends': 'FRIENDS',
    'honeymoon': 'HONEYMOON',
}


def _format_guest_type(guest_type: str) -> str:
    return GUEST_TYPE_LABELS.get(guest_type, guest_type.upper())


def _avatar_initials(guest_name: str) -> str:
    words = [w for w in guest_name.split() if w]
    if not words:
        return '?'
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _circular_mask(size: int) -> Image.Image:
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    return mask


def _load_avatar(profile_photo_url: str | None, guest_name: str, diameter: int) -> Image.Image:
    """A circular avatar: the reviewer's real Google profile photo when
    Google exposed one, otherwise a plain initials avatar in the same spot.
    Never invents a photo that isn't in the source data.
    """
    if profile_photo_url:
        try:
            url = profile_photo_url
            if 'googleusercontent.com' in url:
                # Google's own profile-photo size suffix defaults small;
                # request a square at 2x the render size instead of
                # upscaling a low-res thumbnail.
                url = f"{url.split('=')[0]}=s{diameter * 2}-c"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            photo = Image.open(BytesIO(response.content)).convert('RGB')
            side = min(photo.size)
            left = (photo.width - side) // 2
            top = (photo.height - side) // 2
            photo = photo.crop((left, top, left + side, top + side)).resize((diameter, diameter), Image.LANCZOS)
            avatar = Image.new('RGBA', (diameter, diameter))
            avatar.paste(photo, (0, 0))
            avatar.putalpha(_circular_mask(diameter))
            return avatar
        except Exception:
            pass

    avatar = Image.new('RGBA', (diameter, diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(avatar)
    draw.ellipse((0, 0, diameter, diameter), fill=(214, 235, 224, 255))
    initials = _avatar_initials(guest_name)
    font = _load_font(int(diameter * 0.36), bold=True)
    bbox = draw.textbbox((0, 0), initials, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((diameter - text_w) / 2 - bbox[0], (diameter - text_h) / 2 - bbox[1]),
        initials,
        font=font,
        fill=(27, 67, 50, 255),
    )
    return avatar


def _draw_quote_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill: str) -> None:
    # Two comma-shaped glyphs rather than a “/❝ font character - not every
    # bundled TrueType font ships one, and a missing glyph renders as an
    # empty tofu box (same reasoning as the vector star rating above).
    comma_r = size * 0.28
    for i in range(2):
        cx = x + i * (size * 0.55) + comma_r
        cy = y + comma_r
        draw.ellipse((cx - comma_r, cy - comma_r, cx + comma_r, cy + comma_r), fill=fill)
        tail = [
            (cx - comma_r * 0.9, cy + comma_r * 0.2),
            (cx + comma_r * 0.2, cy + comma_r * 0.2),
            (cx - comma_r * 0.5, cy + comma_r * 1.9),
        ]
        draw.polygon(tail, fill=fill)


def _draw_mountain_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill: str) -> None:
    draw.polygon([(x, y + size), (x + size / 2, y), (x + size, y + size)], fill=fill)


def build_card_4(payload: ReviewPayload, output_path: Path) -> None:
    """The testimonial card, styled like a Google Reviews screenshot: real
    star rating, the reviewer's own Google profile photo (or an initials
    avatar when Google didn't expose one), their quote, and a working QR
    code back to the real review - never a fabricated link or photo.
    """
    narrative = payload.narrative
    deep_green = '#1b4332'
    gold = '#f5b700'
    google_blue = '#4285f4'

    image = Image.new('RGB', (CANVAS_W, CANVAS_H), '#f5f6f4')
    draw = ImageDraw.Draw(image)

    g_font = _load_font(46, bold=True)
    brand_font = _load_font(40, bold=True)
    draw.text((int(CANVAS_W * 0.08), int(CANVAS_H * 0.055)), 'G', font=g_font, fill=google_blue)
    g_bbox = draw.textbbox((0, 0), 'G', font=g_font)
    draw.text(
        (int(CANVAS_W * 0.08) + (g_bbox[2] - g_bbox[0]) + 14, int(CANVAS_H * 0.06)),
        'Google reviews',
        font=brand_font,
        fill='#3c4043',
    )
    _draw_star_rating(draw, int(CANVAS_W * 0.08) + 100, int(CANVAS_H * 0.145), gap=10, size=32, fill=gold)

    avatar_d = 220
    avatar_cx, avatar_cy = int(CANVAS_W * 0.78), int(CANVAS_H * 0.18)
    avatar = _load_avatar(narrative.profile_photo_url, narrative.guest_name, avatar_d)
    image.paste(avatar, (avatar_cx - avatar_d // 2, avatar_cy - avatar_d // 2), avatar)

    card_box = (int(CANVAS_W * 0.05), int(CANVAS_H * 0.23), int(CANVAS_W * 0.95), int(CANVAS_H * 0.82))
    draw.rounded_rectangle(card_box, radius=36, fill='white', outline='#e4e4e4')

    _draw_quote_mark(draw, int(CANVAS_W * 0.09), int(CANVAS_H * 0.27), 64, gold)

    headline = f"{narrative.guest_name.upper()} · {_format_guest_type(narrative.guest_type)}"
    headline_font = _load_font(40, bold=True)
    _draw_text_box(draw, headline, int(CANVAS_W * 0.09), int(CANVAS_H * 0.38), int(CANVAS_W * 0.82), int(CANVAS_H * 0.08), headline_font, deep_green)

    quote_font = _load_font(28)
    _draw_text_box(draw, narrative.quote_full or narrative.quote_short, int(CANVAS_W * 0.09), int(CANVAS_H * 0.46), int(CANVAS_W * 0.70), int(CANVAS_H * 0.32), quote_font, '#4a4a4a')

    footer_top = int(CANVAS_H * 0.84)
    draw.rectangle((0, footer_top, CANVAS_W, CANVAS_H), fill=deep_green)
    _draw_mountain_mark(draw, int(CANVAS_W * 0.08), footer_top + int(CANVAS_H * 0.015), 30, gold)
    brand_lines_font = _load_font(26, bold=True)
    draw.text((int(CANVAS_W * 0.08) + 40, footer_top + int(CANVAS_H * 0.008)), 'JAVA VOLCANO', font=brand_lines_font, fill='white')
    draw.text((int(CANVAS_W * 0.08) + 40, footer_top + int(CANVAS_H * 0.008) + 30), 'TOUR OPERATOR', font=brand_lines_font, fill='white')

    url_font = _load_font(20)
    if narrative.review_url_kind != 'none' and narrative.review_url:
        url_max_w = int(CANVAS_W * 0.62)
        url_text = _truncate_to_width(draw, narrative.review_url, url_font, url_max_w)
        draw.text((int(CANVAS_W * 0.08), footer_top + int(CANVAS_H * 0.075)), url_text, font=url_font, fill='#cfe3d8')

    qr_overlay = _make_qr_overlay(narrative.review_url if narrative.review_url_kind != 'none' else None)
    if qr_overlay:
        qr_size = 110
        qr_x, qr_y = CANVAS_W - qr_size - int(CANVAS_W * 0.08), footer_top + int(CANVAS_H * 0.01)
        draw.rectangle((qr_x - 8, qr_y - 8, qr_x + qr_size + 8, qr_y + qr_size + 8), fill='white')
        image.paste(qr_overlay.resize((qr_size, qr_size)), (qr_x, qr_y))
        caption_font = _load_font(16)
        _draw_text_box(draw, 'Scan for review', qr_x - 20, qr_y + qr_size + 12, qr_size + 40, int(CANVAS_H * 0.03), caption_font, '#cfe3d8', align='center')

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
