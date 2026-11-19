from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    """One raw entry from one source."""

    source: str
    title: str
    url: str
    published: datetime
    summary: str = ""
    weight: float = 1.0
    points: int = 0  # HN score, 0 for feeds


@dataclass
class Story:
    """A deduped story: one or more Items about the same thing."""

    items: list[Item]
    score: float = 0.0
    title: str = ""
    summary: str = ""

    @property
    def lead(self) -> Item:
        return self.items[0]

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for item in sorted(self.items, key=lambda i: -i.weight):
            if item.source not in seen:
                seen.append(item.source)
        return seen

    @property
    def published(self) -> datetime:
        return min(i.published for i in self.items)
