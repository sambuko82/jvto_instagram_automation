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
    highlight: str = 'scenery'


@dataclass(slots=True)
class ReviewPayload:
    original: dict
    narrative: Narrative
