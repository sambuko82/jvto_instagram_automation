from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Narrative:
    guest_name: str
    guest_type: str
    guide_names: list[str] = field(default_factory=list)
    driver_names: list[str] = field(default_factory=list)
    destinations: list[str] = field(default_factory=list)
    quote_short: str = ''
    quote_full: str = ''
    highlight: str = 'scenery'
    caption: str = ''
    visual_prompt: str = ''
    package: str = ''
    media_count: int = 0
    bg_photo_url: str | None = None
    # Additional real photos from the review, used as backgrounds for cards
    # 2/3 instead of solid-color filler when the review has enough of them.
    secondary_photo_urls: list[str] = field(default_factory=list)
    # Reviewer's own Google profile photo, if Google exposed one - used on
    # card 4. No fabricated avatar is drawn from it; when absent, the card
    # falls back to a plain initials avatar instead of inventing a photo.
    profile_photo_url: str | None = None
    review_url: str | None = None
    # 'specific' = review_url points at this exact review; 'profile' = general
    # business profile link used as fallback; 'none' = no link available at all.
    # Drives build_caption() so captions never overclaim what a link points to.
    review_url_kind: str = 'none'


@dataclass(slots=True)
class ReviewPayload:
    original: dict
    narrative: Narrative
