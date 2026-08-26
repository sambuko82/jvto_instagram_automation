from __future__ import annotations

import json
import os
import re
from typing import Any

from .config import Settings
from .models import Narrative


def _clean_review_text(text: str) -> str:
    cleaned = text or ''
    cleaned = re.sub(r'\s*\((?:Translated by Google|Original)\)\s*', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _canonicalize_destination(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        'bromo': 'Bromo',
        'kawah ijen': 'Ijen',
        'ijen': 'Ijen',
        'tumpak sewu': 'Tumpak Sewu',
        'madakaripura': 'Madakaripura',
        'papuma': 'Papuma',
    }
    return mapping.get(normalized, value.strip())


def _normalize_names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names[:3]


def _parse_json_payload(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


# Context-locked extraction rules: every field must be grounded in the
# source review text, never invented, so posted content stays credible.
EXTRACTION_INSTRUCTIONS = """You are a travel social-content strategist for JVTO.
Turn this review into rich social content, but every fact must come from the
review text itself - never invent a name, place, or detail that isn't there.

Extraction rules by field:
- guest_type: infer from wording - "family" -> family, "honeymoon" -> honeymoon,
  "alone"/"solo" -> solo, "wife"/"husband"/"couple" -> couple, else "friends".
- package: look for a trip-length pattern like "3-day"/"4D3N"/"3D2N" in the text.
  Leave empty if none is mentioned.
- guide_names / driver_names: only names that appear directly after the words
  "guide" or "driver" in the text. Do not guess a name that isn't written there.
- destinations: only East Java sites actually named in the text (Bromo, Ijen /
  Kawah Ijen, Tumpak Sewu, Madakaripura, Papuma).
- quote_short: the single most emotionally positive sentence, max 180 characters.
- quote_full: the cleaned original review text, max 300 characters.
- highlight: "blue fire" if Ijen/blue fire is mentioned, "waterfall" if a
  waterfall/Tumpak Sewu is mentioned, else "scenery".

Return strict JSON with keys: guest_name, guest_type, package, guide_names
(array), driver_names (array), destinations (array), quote_short, quote_full,
highlight, caption, visual_prompt.
"""


def _build_prompt(review_text: str, guest_name: str | None) -> str:
    return f"""{EXTRACTION_INSTRUCTIONS}
Review text:
{review_text}

Guest name:
{guest_name or 'Guest'}
"""


def extract_narrative_agentic(review_text: str, guest_name: str | None = None, settings: Settings | None = None) -> Narrative:
    from .review_parser import extract_narrative

    text = _clean_review_text(review_text)
    fallback = extract_narrative(text, guest_name=guest_name)
    if not text:
        return fallback
    if settings is not None and not settings.agentic:
        return fallback

    if not os.getenv('COMPOSIO_API_KEY'):
        return fallback

    try:
        from composio import Composio
        from composio_openai_agents import OpenAIAgentsProvider
        from agents import Agent, Runner
    except Exception:
        return fallback

    try:
        composio = Composio(api_key=os.getenv('COMPOSIO_API_KEY'), provider=OpenAIAgentsProvider())
        session = composio.create(user_id=os.getenv('COMPOSIO_USER_ID') or 'jvto_automation')
        tools = session.tools()
        agent = Agent(
            name='JVTO Review Parser',
            instructions=EXTRACTION_INSTRUCTIONS,
            tools=tools,
        )
        result = Runner.run_sync(agent, input=_build_prompt(text, guest_name))
        payload = result.final_output if hasattr(result, 'final_output') else str(result)
        decoded = _parse_json_payload(payload if isinstance(payload, str) else json.dumps(payload))
    except Exception:
        return fallback

    guest_name_value = str(decoded.get('guest_name') or guest_name or 'Guest').strip() or 'Guest'
    guest_type = str(decoded.get('guest_type') or fallback.guest_type).strip() or fallback.guest_type
    guide_names = _normalize_names(decoded.get('guide_names')) or fallback.guide_names
    driver_names = _normalize_names(decoded.get('driver_names')) or fallback.driver_names
    destinations = []
    for value in decoded.get('destinations') or []:
        if not isinstance(value, str) or not value.strip():
            continue
        canonical = _canonicalize_destination(value)
        if canonical not in destinations:
            destinations.append(canonical)
    if not destinations:
        destinations = fallback.destinations
    quote_short = str(decoded.get('quote_short') or fallback.quote_short).strip() or fallback.quote_short
    quote_full = str(decoded.get('quote_full') or fallback.quote_full).strip() or fallback.quote_full
    highlight = str(decoded.get('highlight') or fallback.highlight).strip() or fallback.highlight
    package = str(decoded.get('package') or fallback.package).strip()
    caption = str(decoded.get('caption') or quote_short).strip() or fallback.quote_short
    visual_prompt = str(decoded.get('visual_prompt') or f"A cinematic {highlight} scene for {', '.join(destinations[:2])}").strip()

    return Narrative(
        guest_name=guest_name_value,
        guest_type=guest_type,
        guide_names=guide_names,
        driver_names=driver_names,
        destinations=destinations[:3],
        quote_short=quote_short,
        quote_full=quote_full,
        highlight=highlight,
        package=package,
        caption=caption,
        visual_prompt=visual_prompt,
    )
