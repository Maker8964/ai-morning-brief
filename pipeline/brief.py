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
from .summarize import is_valid, summarize

TZ = ZoneInfo("Asia/Shanghai")
MAX_BADGES = 2
WIDER_WINDOW_HOURS = 48
BACKFILL_ROUNDS = 2

log = logging.getLogger(__name__)


class ShortBrief(RuntimeError):
    """Fewer than `limit` stories survived even after backfill and a wider window."""


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


def _urls(story: Story) -> set[str]:
    return {normalize_url(i.url) for i in story.items}


def _fill(picked: list[Story], stories: list[Story], limit: int, tried: set[str], now: datetime) -> tuple[int, bool]:
    """Summarizes the next-ranked untried stories to top `picked` up to `limit`, backfilling dropped ones.

    The per-source cap counts only stories already picked, so a rejected story frees its slot.

    Returns (stories attempted, whether the LLM was used)."""
    attempted, llm = 0, False
    for _ in range(BACKFILL_ROUNDS):
        untried = [s for s in stories if not _urls(s) & tried]
        batch = rank(untried, now=now, limit=limit - len(picked), taken=picked)
        if not batch:
            break
        for s in batch:
            tried |= _urls(s)
        attempted += len(batch)
        llm = summarize(batch) or llm
        picked.extend(s for s in batch if is_valid(s, s.summary))
    return attempted, llm


def build(limit: int = 12, window_hours: int = 36, now: datetime | None = None) -> dict:
    """Raises ShortBrief unless exactly `limit` stories end up with a valid summary."""
    now = now or datetime.now(timezone.utc)
    picked: list[Story] = []
    tried: set[str] = set()
    llm = False
    attempted = 0
    stages = []
    for window in dict.fromkeys([window_hours, max(window_hours, WIDER_WINDOW_HOURS)]):
        if len(picked) >= limit:
            break
        if stages:
            log.warning("only %d/%d stories; retrying with a %dh window", len(picked), limit, window)
        items = ingest(now=now, window_hours=window)
        stories = dedup(items)
        before = len(picked)
        n, used = _fill(picked, stories, limit, tried, now)
        attempted, llm = attempted + n, llm or used
        stages.append(f"window={window}h items={len(items)} stories={len(stories)} "
                      f"attempted={n} kept={len(picked) - before}")
        log.info("%s picked=%d/%d llm=%s", stages[-1], len(picked), limit, llm)
    if len(picked) < limit:
        short = "summarize" if attempted >= limit else "ingest/dedup/rank"
        raise ShortBrief(f"brief short: {len(picked)}/{limit} stories, {short} stage came up short ({'; '.join(stages)})")
    return {
        "date": now.astimezone(TZ).date().isoformat(),
        "generatedAt": now.astimezone(TZ).isoformat(timespec="seconds"),
        "summarizedBy": "llm" if llm else "source",
        "stories": [story_to_card(s) for s in picked[:limit]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/brief.json")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--window", type=int, default=36, help="hours of news to consider")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        brief = build(limit=args.limit, window_hours=args.window)
    except ShortBrief as exc:
        log.error("%s; keeping previous %s", exc, args.out)
        raise SystemExit(1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("wrote %s", out)


if __name__ == "__main__":
    main()
