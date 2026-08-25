from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import ReviewPayload

CANVAS_W = 1080
CANVAS_H = 1350


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/arialbd.ttf',
        'DejaVuSans.ttf',
        'DejaVuSans-Bold.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_text_box(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, w: int, h: int, font: ImageFont.ImageFont, fill: str, align: str = 'left') -> None:
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


def _build_background(image: Image.Image, accent: str) -> None:
    base = Image.new('RGBA', image.size, accent)
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(image.height):
        alpha = int(20 + (y / max(1, image.height - 1)) * 80)
        draw.line([(0, y), (image.width, y)], fill=(0, 0, 0, alpha))
    composite = Image.alpha_composite(base, overlay)
    image.paste(composite.convert('RGB'), (0, 0))


def build_card_1(payload: ReviewPayload, output_path: Path) -> None:
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (11, 17, 29))
    _build_background(image, '#1f6f8b')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, CANVAS_W - 40, CANVAS_H - 40), radius=36, fill=(255, 255, 255, 30), outline=(255, 255, 255, 80))

    title = f"GUEST STORY: {payload.narrative.guest_name} - {', '.join(payload.narrative.destinations)}"
    subtitle = f"Experiencing {payload.narrative.destinations[0]} • {payload.narrative.guest_type}"
    title_font = _load_font(44, bold=True)
    sub_font = _load_font(28)
    _draw_text_box(draw, title, int(CANVAS_W * 0.06), int(CANVAS_H * 0.12), int(CANVAS_W * 0.88), int(CANVAS_H * 0.16), title_font, 'white')
    _draw_text_box(draw, subtitle, int(CANVAS_W * 0.06), int(CANVAS_H * 0.82), int(CANVAS_W * 0.88), int(CANVAS_H * 0.08), sub_font, '#f5f5f5')
    image.save(output_path)


def build_card_2(payload: ReviewPayload, output_path: Path) -> None:
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (33, 27, 20))
    _build_background(image, '#b55e1f')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, CANVAS_W - 40, CANVAS_H - 40), radius=36, fill=(255, 255, 255, 30), outline=(255, 255, 255, 80))

    tag_font = _load_font(26, bold=True)
    label_font = _load_font(28, bold=True)
    body_font = _load_font(24)
    draw.rounded_rectangle((int(CANVAS_W * 0.72), int(CANVAS_H * 0.06), int(CANVAS_W * 0.92), int(CANVAS_H * 0.12)), radius=16, fill=(255, 255, 255, 180))
    _draw_text_box(draw, 'Guide Support', int(CANVAS_W * 0.72), int(CANVAS_H * 0.06), int(CANVAS_W * 0.20), int(CANVAS_H * 0.06), tag_font, '#2f2f2f', align='center')

    guide_names = ', '.join(payload.narrative.guide_names or ['Local Guide'])
    driver_names = ', '.join(payload.narrative.driver_names or ['Driver'])
    label_text = f'Guide: {guide_names} • Driver: {driver_names}'
    _draw_text_box(draw, label_text, int(CANVAS_W * 0.06), int(CANVAS_H * 0.82), int(CANVAS_W * 0.70), int(CANVAS_H * 0.08), label_font, 'white')
    body_text = f'This review highlights the care behind the journey for {payload.narrative.guest_type}.'
    _draw_text_box(draw, body_text, int(CANVAS_W * 0.06), int(CANVAS_H * 0.66), int(CANVAS_W * 0.88), int(CANVAS_H * 0.08), body_font, '#fef3d0')
    image.save(output_path)


def build_card_3(payload: ReviewPayload, output_path: Path) -> None:
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (16, 41, 88))
    _build_background(image, '#2d6cdf')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, CANVAS_W - 40, CANVAS_H - 40), radius=36, fill=(255, 255, 255, 30), outline=(255, 255, 255, 80))

    if payload.narrative.highlight == 'blue fire':
        headline = 'Ijen Blue Fire Experience'
        body = 'A dramatic sunrise experience that turns the volcano into a once-in-a-lifetime story.'
    elif payload.narrative.highlight == 'waterfall':
        headline = 'Tumpak Sewu Adventure'
        body = 'A powerful waterfall journey with nature, views, and unforgettable photo moments.'
    else:
        headline = 'Memorable East Java Adventure'
        body = 'The journey combines comfort, scenery, and local guidance in one seamless experience.'

    headline_font = _load_font(38, bold=True)
    body_font = _load_font(26)
    _draw_text_box(draw, headline, int(CANVAS_W * 0.06), int(CANVAS_H * 0.18), int(CANVAS_W * 0.88), int(CANVAS_H * 0.16), headline_font, 'white')
    _draw_text_box(draw, body, int(CANVAS_W * 0.06), int(CANVAS_H * 0.42), int(CANVAS_W * 0.88), int(CANVAS_H * 0.20), body_font, '#eaf2ff')
    image.save(output_path)


def build_card_4(payload: ReviewPayload, output_path: Path) -> None:
    image = Image.new('RGB', (CANVAS_W, CANVAS_H), (248, 248, 248))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, CANVAS_W - 40, CANVAS_H - 40), radius=36, fill=(255, 255, 255), outline=(220, 220, 220))

    quote_font = _load_font(32)
    stars_font = _load_font(34, bold=True)
    attrib_font = _load_font(24)
    logo_font = _load_font(24, bold=True)

    _draw_text_box(draw, f'"{payload.narrative.quote_short}"', int(CANVAS_W * 0.10), int(CANVAS_H * 0.30), int(CANVAS_W * 0.80), int(CANVAS_H * 0.45), quote_font, '#2b2b2b')
    draw.text((int(CANVAS_W * 0.50), int(CANVAS_H * 0.18)), '★★★★★', font=stars_font, fill='#f5b700', anchor='mm')
    attribution = f"— {payload.narrative.guest_name} • {payload.narrative.guest_type.title()}"
    _draw_text_box(draw, attribution, int(CANVAS_W * 0.50), int(CANVAS_H * 0.78), int(CANVAS_W * 0.40), int(CANVAS_H * 0.06), attrib_font, '#555555', align='center')
    _draw_text_box(draw, 'JVTO', int(CANVAS_W * 0.50), int(CANVAS_H * 0.92), int(CANVAS_W * 0.20), int(CANVAS_H * 0.04), logo_font, '#0f5f78', align='center')
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
