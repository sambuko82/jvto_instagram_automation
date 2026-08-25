from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .agentic_extraction import extract_narrative_agentic
from .config import Settings
from .models import Narrative, ReviewPayload


def _clean_review_text(text: str) -> str:
    cleaned = text or ''
    cleaned = re.sub(r'\s*\((?:Translated by Google|Original)\)\s*', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


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

    guides = re.findall(r'\bguide\s+([A-Z][a-z]+)', text, re.I)
    drivers = re.findall(r'\bdriver\s+([A-Z][a-z]+)', text, re.I)

    destinations = []
    for token, label in [
        ('bromo', 'Bromo'),
        ('ijen', 'Ijen'),
        ('kawah ijen', 'Ijen'),
        ('tumpak sewu', 'Tumpak Sewu'),
        ('madakaripura', 'Madakaripura'),
        ('papuma', 'Papuma'),
    ]:
        if token in lowered:
            destinations.append(label)
    if not destinations:
        destinations = ['Bromo', 'Ijen']

    highlight = 'scenery'
    if 'blue fire' in lowered or 'bluefire' in lowered:
        highlight = 'blue fire'
    elif 'waterfall' in lowered or 'tumpak sewu' in lowered:
        highlight = 'waterfall'

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    quote_short = next((s for s in sentences if len(s) > 20), text)
    if len(quote_short) > 180:
        quote_short = quote_short[:177].rstrip() + '...'

    return Narrative(
        guest_name=guest_name or 'Guest',
        guest_type=guest_type,
        guide_names=[name for name in guides[:2]],
        driver_names=[name for name in drivers[:2]],
        destinations=destinations[:3],
        quote_short=quote_short,
        highlight=highlight,
    )


def extract_narrative_with_agentic(review_text: str, guest_name: str | None = None, settings: Settings | None = None) -> Narrative:
    if settings is not None and settings.agentic:
        return extract_narrative_agentic(review_text, guest_name=guest_name, settings=settings)
    return extract_narrative(review_text, guest_name=guest_name)


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

    first = reviews[0] if reviews else {}
    review_text = first.get('comment') or first.get('text') or first.get('reviewText') or ''
    reviewer = first.get('reviewer') or {}
    guest_name = reviewer.get('displayName') or reviewer.get('name') or first.get('reviewerName') or first.get('authorName') or 'Guest'
    narrative = extract_narrative_with_agentic(review_text, guest_name=guest_name, settings=settings)
    return ReviewPayload(original=first, narrative=narrative)
