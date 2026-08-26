import tempfile
from pathlib import Path

from PIL import Image

from jvto_instagram_automation.models import Narrative, ReviewPayload
from jvto_instagram_automation.rendering import CANVAS_H, CANVAS_W, create_carousel


def _sample_payload(**overrides) -> ReviewPayload:
    defaults = dict(
        guest_name='Flore Sabbah',
        guest_type='friends',
        guide_names=['Fauzi'],
        driver_names=['Lily'],
        destinations=['Bromo', 'Ijen'],
        quote_short='Absolutely fantastic trip with a wonderful guide and driver.',
        highlight='blue fire',
    )
    defaults.update(overrides)
    return ReviewPayload(original={}, narrative=Narrative(**defaults))


def test_create_carousel_generates_four_correctly_sized_cards() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        paths = create_carousel(_sample_payload(), output_dir)

        assert len(paths) == 4
        for path in paths:
            assert path.exists()
            with Image.open(path) as image:
                assert image.size == (CANVAS_W, CANVAS_H)


def test_rendering_never_crashes_without_a_review_url() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = _sample_payload(review_url=None, review_url_kind='none')
        paths = create_carousel(payload, Path(tmp_dir))
        assert len(paths) == 4


def test_rendering_embeds_qr_code_when_review_url_present() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = _sample_payload(review_url='https://maps.google.com/review/123', review_url_kind='specific')
        paths = create_carousel(payload, Path(tmp_dir))
        assert len(paths) == 4
        with Image.open(paths[3]) as image:
            assert image.size == (CANVAS_W, CANVAS_H)


def test_rendering_handles_unreachable_background_photo_gracefully() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = _sample_payload(bg_photo_url='https://example.invalid/does-not-exist.jpg')
        paths = create_carousel(payload, Path(tmp_dir))
        assert len(paths) == 4
        with Image.open(paths[0]) as image:
            assert image.size == (CANVAS_W, CANVAS_H)
