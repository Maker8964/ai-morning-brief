import math
from datetime import datetime, timezone

from .models import Story

HALF_LIFE_HOURS = 12.0
COVERAGE_BONUS = 0.6  # per extra distinct source


def score(story: Story, now: datetime) -> float:
    age_h = max(0.0, (now - story.published).total_seconds() / 3600)
    recency = 0.5 ** (age_h / HALF_LIFE_HOURS)
    sources = story.sources
    authority = max(i.weight for i in story.items)
    coverage = 1 + COVERAGE_BONUS * (len(sources) - 1)
    hn = max((i.points for i in story.items), default=0)
    buzz = 1 + math.log10(1 + hn) / 3  # 100 pts ≈ +0.67
    return authority * coverage * buzz * (0.3 + 0.7 * recency)


def rank(stories: list[Story], now: datetime | None = None, limit: int = 12, per_source: int = 3,
         taken: list[Story] = ()) -> list[Story]:
    """Top stories by score, capping how many any one lead source can contribute (counting `taken`)."""
    now = now or datetime.now(timezone.utc)
    for s in stories:
        s.score = score(s, now)
    picked: list[Story] = []
    counts: dict[str, int] = {}
    for s in taken:
        counts[s.lead.source] = counts.get(s.lead.source, 0) + 1
    for s in sorted(stories, key=lambda s: s.score, reverse=True):
        if len(picked) >= limit:
            break
        lead = s.lead.source
        if counts.get(lead, 0) >= per_source:
            continue
        counts[lead] = counts.get(lead, 0) + 1
        picked.append(s)
    return picked
