"""Build the morning brief: ingest -> dedup -> rank -> summarize -> data/brief.json.

    python -m pipeline.brief [--out data/brief.json] [--limit 12] [--window 36]
"""

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .dedup import dedup, normalize_url
from .ingest import ingest
from .models import Story
from .rank import rank
from .summarize import summarize

TZ = ZoneInfo("Asia/Shanghai")
MAX_BADGES = 2


def story_to_card(story: Story) -> dict:
    lead = story.lead
    return {
        "id": hashlib.sha1(normalize_url(lead.url).encode()).hexdigest()[:10],
        "title": story.title,
        "summary": story.summary,
        "sources": story.sources[:MAX_BADGES],
        "publishedAt": story.published.astimezone(TZ).isoformat(timespec="minutes"),
        "url": lead.url,
        "score": round(story.score, 3),
    }


def build(limit: int = 12, window_hours: int = 36, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    items = ingest(now=now, window_hours=window_hours)
    stories = dedup(items)
    top = rank(stories, now=now, limit=limit)
    llm = summarize(top)
    logging.info("items=%d stories=%d picked=%d llm=%s", len(items), len(stories), len(top), llm)
    return {
        "date": now.astimezone(TZ).date().isoformat(),
        "generatedAt": now.astimezone(TZ).isoformat(timespec="seconds"),
        "summarizedBy": "llm" if llm else "source",
        "stories": [story_to_card(s) for s in top],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/brief.json")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--window", type=int, default=36, help="hours of news to consider")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    brief = build(limit=args.limit, window_hours=args.window)
    if not brief["stories"]:
        raise SystemExit("no stories ingested; keeping previous brief")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("wrote %s", out)


if __name__ == "__main__":
    main()
