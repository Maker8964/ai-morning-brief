"""English display headline + Chinese summary per story, via any OpenAI-compatible chat API.

Env: LLM_API_KEY (required to enable), LLM_BASE_URL (default OpenAI), LLM_MODEL.
Without a key, or if the call fails, stories fall back to their source title/excerpt.
"""

import json
import logging
import os
import re
import urllib.request

from .models import Story

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
TITLE_MAX = 60
FALLBACK_SUMMARY_MAX = 220

SYSTEM_PROMPT = f"""You write a terse AI-news morning brief for Chinese-speaking readers.
For each story you get one or more source headlines and excerpts. Return JSON:
{{"stories": [{{"id": <int>, "title": <str>, "summary": <str>}}, ...]}}
- title: English, punchy headline, max {TITLE_MAX} characters, no trailing period, no quotes, no emoji.
- summary: Simplified Chinese, 60-90 characters, 1-2 sentences, the key fact plus why it matters.
- Use only facts in the provided text. Do not invent numbers, names or dates.
- Keep product and company names in their original form (e.g. OpenAI, GPT-5)."""


def _story_payload(idx: int, story: Story) -> dict:
    return {
        "id": idx,
        "sources": [{"source": i.source, "headline": i.title, "excerpt": i.summary[:400]} for i in story.items[:4]],
    }


def _chat(messages: list[dict], api_key: str, base_url: str, model: str) -> str:
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.3, "response_format": {"type": "json_object"}}
    ).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def parse_response(content: str) -> dict[int, dict]:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
    data = json.loads(content)
    rows = data.get("stories", data) if isinstance(data, dict) else data
    out = {}
    for row in rows:
        try:
            idx = int(row["id"])
            title, summary = str(row["title"]).strip(), str(row["summary"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if title and summary:
            out[idx] = {"title": title.rstrip(".")[: TITLE_MAX + 20], "summary": summary}
    return out


def fallback(story: Story) -> None:
    lead = story.lead
    story.title = story.title or lead.title
    if not story.summary:
        excerpt = next((i.summary for i in story.items if i.summary), "") or lead.title
        story.summary = excerpt if len(excerpt) <= FALLBACK_SUMMARY_MAX else excerpt[: FALLBACK_SUMMARY_MAX - 1].rstrip() + "…"


def summarize(stories: list[Story], chat=_chat) -> bool:
    """Fills story.title/summary in place. Returns True if the LLM was used."""
    api_key = os.environ.get("LLM_API_KEY")
    used = False
    if api_key and stories:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps([_story_payload(i, s) for i, s in enumerate(stories)], ensure_ascii=False)},
        ]
        try:
            content = chat(
                messages,
                api_key,
                os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL,
                os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
            )
            for idx, row in parse_response(content).items():
                if 0 <= idx < len(stories):
                    stories[idx].title, stories[idx].summary = row["title"], row["summary"]
                    used = True
        except Exception as exc:
            log.warning("LLM summarize failed, using source text: %s", exc)
    elif not api_key:
        log.warning("LLM_API_KEY not set; using source titles/excerpts")
    for s in stories:
        fallback(s)
    return used
