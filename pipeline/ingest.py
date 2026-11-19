import html
import json
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser

from .models import Item
from .sources import SOURCES, Source

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AIMorningBrief/1.0; +https://github.com/Maker8964/ai-morning-brief)"
TIMEOUT = 15

AI_RE = re.compile(
    r"\b(AI|A\.I\.|AGI|LLMs?|GPT[-\w]*|ChatGPT|OpenAI|Anthropic|Claude|Gemini|DeepMind|"
    r"Llama|Mistral|DeepSeek|Qwen|Copilot|machine learning|neural|chatbots?|"
    r"artificial intelligence|generative|transformer|diffusion|Nvidia|inference|"
    r"agents?|agentic|xAI|Grok|Hugging Face|foundation models?)\b",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


def is_ai(text: str) -> bool:
    return bool(AI_RE.search(text))


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def parse_rss(source: Source, raw: bytes) -> list[Item]:
    feed = feedparser.parse(raw)
    items = []
    for e in feed.entries:
        published = _entry_time(e)
        title = clean_text(e.get("title", ""))
        url = e.get("link", "")
        if not (published and title and url):
            continue
        summary = clean_text(e.get("summary", ""))[:600]
        items.append(Item(source.name, title, url, published, summary, source.weight))
    return items


def parse_hn(source: Source, raw: bytes) -> list[Item]:
    items = []
    for hit in json.loads(raw).get("hits", []):
        title = clean_text(hit.get("title") or "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        ts = hit.get("created_at_i")
        if not (title and ts):
            continue
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
        items.append(Item(source.name, title, url, published, "", source.weight, hit.get("points") or 0))
    return items


def fetch_source(source: Source) -> list[Item]:
    try:
        raw = _fetch(source.url)
        items = parse_hn(source, raw) if source.kind == "hn" else parse_rss(source, raw)
    except Exception as exc:  # one dead or malformed feed must not kill the brief
        log.warning("fetch failed: %s (%s)", source.name, exc)
        return []
    if not source.ai_only:
        items = [i for i in items if is_ai(i.title)]
    log.info("%-16s %3d items", source.name, len(items))
    return items


def ingest(
    sources: list[Source] = SOURCES,
    now: datetime | None = None,
    window_hours: int = 36,
) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(fetch_source, sources))
    return [i for batch in batches for i in batch if cutoff <= i.published <= now + timedelta(hours=1)]
