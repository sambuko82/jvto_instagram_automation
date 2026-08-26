from __future__ import annotations

import json
import re
from typing import Any

from .agentic_extraction import extract_narrative_agentic
from .config import Settings
from .models import Narrative, ReviewPayload


def _clean_review_text(text: str) -> str:
    cleaned = text or ''
    # Google's translated reviews append the original-language text after an
    # "(Original)" marker. Drop it rather than keeping both languages
    # concatenated - otherwise English keyword checks (guest_type, etc.) can
    # false-positive on unrelated words in the other language (e.g. Spanish
    # "solo" meaning "only" being read as "solo traveler").
    cleaned = cleaned.split('(Original)')[0]
    cleaned = re.sub(r'\s*\(Translated by Google\)\s*', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _review_media_count(review: dict[str, Any]) -> int:
    media_items = (
        review.get('reviewMediaItems')
        or review.get('media_konten')
        or review.get('photos')
        or review.get('images')
        or []
    )
    return len(media_items) if isinstance(media_items, list) else 0


def _review_is_five_star(review: dict[str, Any]) -> bool:
    star_rating = review.get('starRating')
    bintang = review.get('bintang') or review.get('stars') or review.get('rating') or review.get('review_rate')
    if star_rating is None and bintang is None:
        # No rating field present at all - the JVTO Reviews Drive folder only
        # ever contains 5-star exports, so treat an untagged review as valid
        # rather than silently dropping it.
        return True
    return star_rating in ('FIVE', 5) or bintang == 5


def _review_url(review: dict[str, Any]) -> str | None:
    return review.get('reviewReplyUrl') or review.get('review_url') or review.get('url') or None


def get_priority_reviews(reviews: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Rank 5-star reviews by media asset count (richest documentation first).

    A review's score is media_count-weighted with a small bonus for carrying
    its own authentic reviewReplyUrl, so well-photographed AND verifiable
    reviews are prioritized for posting.
    """
    scored = []
    for review in reviews:
        if not isinstance(review, dict) or not _review_is_five_star(review):
            continue
        media_count = _review_media_count(review)
        has_url = bool(_review_url(review))
        score = media_count * 10 + (5 if has_url else 0)
        scored.append((score, media_count, review))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [review for _score, _media_count, review in scored[:limit]]


def extract_narrative(review_text: str, guest_name: str | None = None) -> Narrative:
    text = _clean_review_text(review_text)
    lowered = text.lower()

    guest_type = 'friends'
    if 'honeymoon' in lowered:
        guest_type = 'honeymoon'
    elif 'alone' in lowered or 'solo' in lowered:
        guest_type = 'solo'
    elif 'wife' in lowered or 'husband' in lowered or 'couple' in lowered:
        guest_type = 'couple'
    elif 'family' in lowered:
        guest_type = 'family'

    # Scope case-insensitivity to the literal word only - re.I on the whole
    # pattern also made [A-Z] match lowercase letters, so it captured
    # whatever word followed "guide"/"driver" (e.g. "is", "and") instead of
    # requiring an actual capitalized name.
    guides = re.findall(r'(?i:guide)\s+([A-Z][a-z]+)', text)
    drivers = re.findall(r'(?i:driver)\s+([A-Z][a-z]+)', text)

    destinations = []
    for token, label in [
        ('bromo', 'Bromo'),
        ('ijen', 'Ijen'),
        ('kawah ijen', 'Ijen'),
        ('tumpak sewu', 'Tumpak Sewu'),
        ('madakaripura', 'Madakaripura'),
        ('papuma', 'Papuma'),
    ]:
        if token in lowered and label not in destinations:
            destinations.append(label)
    if not destinations:
        destinations = ['Bromo', 'Ijen']

    highlight = 'scenery'
    if 'blue fire' in lowered or 'bluefire' in lowered:
        highlight = 'blue fire'
    elif 'waterfall' in lowered or 'tumpak sewu' in lowered:
        highlight = 'waterfall'

    package_match = re.search(r'\b(\d)\s*-?\s*day\b|\b(\d)D(\d)N\b', text, re.I)
    package = ''
    if package_match:
        if package_match.group(1):
            package = f'{package_match.group(1)}D{int(package_match.group(1)) - 1}N'
        else:
            package = f'{package_match.group(2)}D{package_match.group(3)}N'

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    quote_short = next((s for s in sentences if len(s) > 20), text)
    if len(quote_short) > 180:
        quote_short = quote_short[:177].rstrip() + '...'
    quote_full = text if len(text) <= 300 else text[:297].rstrip() + '...'

    return Narrative(
        guest_name=guest_name or 'Guest',
        guest_type=guest_type,
        guide_names=[name for name in guides[:2]],
        driver_names=[name for name in drivers[:2]],
        destinations=destinations[:3],
        quote_short=quote_short,
        quote_full=quote_full,
        highlight=highlight,
        package=package,
    )


def extract_narrative_with_agentic(review_text: str, guest_name: str | None = None, settings: Settings | None = None) -> Narrative:
    if settings is not None and settings.agentic:
        return extract_narrative_agentic(review_text, guest_name=guest_name, settings=settings)
    return extract_narrative(review_text, guest_name=guest_name)


def build_caption(payload: ReviewPayload) -> str:
    """Build the Instagram caption, phrased truthfully about the credibility link.

    review_url_kind controls the wording: 'specific' means the link points at
    this exact Google review (safe to say "read this review"); 'profile'
    means it's the general JVTO Google Maps profile (must say "see more
    reviews", not claim it verifies this one); 'none' omits the link line
    entirely rather than inventing one.
    """
    narrative = payload.narrative
    lines = [
        f"🌟 CUSTOMER STORY: {narrative.guest_name} - {' • '.join(narrative.destinations[:2])}",
        '',
        f'"{narrative.quote_full or narrative.quote_short}"',
        '',
        f"📍 Destinasi: {', '.join(narrative.destinations) or 'Bromo, Ijen'}",
    ]
    if narrative.guide_names or narrative.driver_names:
        lines.append(
            f"⛳ Tim Bertugas: Guide {', '.join(narrative.guide_names) or '-'} • Driver {', '.join(narrative.driver_names) or '-'}"
        )
    if narrative.package:
        lines.append(f"📦 Paket: {narrative.package} • {narrative.guest_type.title()} Journey")

    if narrative.review_url_kind == 'specific' and narrative.review_url:
        lines += [
            '',
            f'💯 Baca ulasan asli ini langsung di Google Maps: {narrative.review_url}',
        ]
    elif narrative.review_url_kind == 'profile' and narrative.review_url:
        lines += [
            '',
            f'💯 Lihat lebih banyak ulasan terverifikasi di profil Google Maps kami: {narrative.review_url}',
        ]
    # 'none' -> no link line, no fabricated credibility claim.

    lines += [
        '',
        '#JVTO #ExploreEastJava #MountBromo #IjenCrater #VerifiedReview',
    ]
    return '\n'.join(lines)


def load_review_payload(settings: Settings) -> ReviewPayload:
    if settings.local_json and settings.local_json.exists():
        payload = json.loads(settings.local_json.read_text(encoding='utf-8'))
    else:
        fallback_path = settings.project_root / 'data' / 'sample_review.json'
        if fallback_path.exists():
            payload = json.loads(fallback_path.read_text(encoding='utf-8'))
        else:
            payload = []

    if isinstance(payload, dict) and 'reviews' in payload:
        reviews = payload['reviews']
    elif isinstance(payload, list):
        reviews = payload
    else:
        reviews = [payload]

    if not reviews:
        raise ValueError(
            'No review data available. Pass --local-json, run --drive-export first, '
            'or ensure data/sample_review.json exists.'
        )

    priority = get_priority_reviews(reviews, limit=settings.review_priority_limit)
    first = priority[0] if priority else reviews[0]

    review_text = first.get('comment') or first.get('text') or first.get('reviewText') or ''
    reviewer = first.get('reviewer') or {}
    guest_name = reviewer.get('displayName') or reviewer.get('name') or first.get('reviewerName') or first.get('authorName') or 'Guest'
    narrative = extract_narrative_with_agentic(review_text, guest_name=guest_name, settings=settings)

    narrative.media_count = _review_media_count(first)
    review_url = _review_url(first)
    if review_url:
        narrative.review_url = review_url
        narrative.review_url_kind = 'specific'
    elif settings.google_maps_profile_url:
        narrative.review_url = settings.google_maps_profile_url
        narrative.review_url_kind = 'profile'
    else:
        narrative.review_url_kind = 'none'

    media_items = first.get('reviewMediaItems') or []
    if isinstance(media_items, list) and media_items:
        first_item = media_items[0]
        if isinstance(first_item, dict):
            narrative.bg_photo_url = first_item.get('thumbnailUrl') or first_item.get('url')

    return ReviewPayload(original=first, narrative=narrative)
