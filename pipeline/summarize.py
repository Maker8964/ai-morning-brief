"""English display headline + Chinese summary per story, via any OpenAI-compatible chat API.

Env: LLM_API_KEY (required to enable), LLM_BASE_URL (default OpenAI), LLM_MODEL.
Summaries must be Simplified Chinese: English ones are retried once, then the source text is
translated; a story that still has no Chinese summary is dropped from the brief.
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

CHINESE_RULE = ("summary MUST be Simplified Chinese. Never return English text in the summary. "
                "If the source text is English, translate the key fact into Chinese.")

RETRY_PROMPT = f"{SYSTEM_PROMPT}\n- {CHINESE_RULE}"

TRANSLATE_PROMPT = f"""Translate each text into a Simplified Chinese news summary of 60-90 characters.
Return JSON: {{"stories": [{{"id": <int>, "summary": <str>}}, ...]}}
- {CHINESE_RULE}
- Keep product and company names in their original form (e.g. OpenAI, GPT-5)."""

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_WORD = re.compile(r"[A-Za-z]+")


def is_chinese(text: str) -> bool:
    """Mostly-Chinese text. Each Latin word counts as one unit, so names like OpenAI or GPT-5 are fine."""
    cjk = len(CJK.findall(text))
    words = len(LATIN_WORD.findall(text))
    return cjk >= 20 or (cjk > 0 and cjk >= words)


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


def parse_response(content: str, need_title: bool = True) -> dict[int, dict]:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
    data = json.loads(content)
    rows = data.get("stories", data) if isinstance(data, dict) else data
    out = {}
    for row in rows:
        try:
            idx = int(row["id"])
            title = str(row.get("title") or "").strip()
            summary = str(row["summary"]).strip()
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if summary and (title or not need_title):
            out[idx] = {"title": title.rstrip(".")[: TITLE_MAX + 20], "summary": summary}
    return out


def source_text(story: Story) -> str:
    excerpt = next((i.summary for i in story.items if i.summary), "") or story.lead.title
    return excerpt if len(excerpt) <= FALLBACK_SUMMARY_MAX else excerpt[: FALLBACK_SUMMARY_MAX - 1].rstrip() + "…"


def fallback(story: Story, translate=None) -> bool:
    """Fills a missing title/summary from the source; English source text is translated. False if no Chinese."""
    story.title = story.title or story.lead.title
    if story.summary:
        return True
    text = source_text(story)
    if not is_chinese(text):
        text = translate(text) if translate else ""
    if text and is_chinese(text):
        story.summary = text
        return True
    return False


def summarize(stories: list[Story], chat=_chat) -> bool:
    """Fills story.title/summary in place and removes stories left without a Chinese summary.

    Returns True if the LLM was used."""
    api_key = os.environ.get("LLM_API_KEY")
    config = (api_key, os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
    used = False

    def ask(prompt: str, payload: list, need_title: bool = True) -> dict[int, dict]:
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            return parse_response(chat(messages, *config), need_title)
        except Exception as exc:
            log.warning("LLM call failed: %s", exc)
            return {}

    def apply(rows: dict[int, dict]) -> None:
        nonlocal used
        for idx, row in rows.items():
            if not 0 <= idx < len(stories):
                continue
            story = stories[idx]
            if row["title"] and not story.title:
                story.title = row["title"]
                used = True
            if is_chinese(row["summary"]):
                story.summary = row["summary"]
                used = True
            else:
                log.warning("non-Chinese summary for story %d rejected: %.60s", idx, row["summary"])

    if api_key and stories:
        apply(ask(SYSTEM_PROMPT, [_story_payload(i, s) for i, s in enumerate(stories)]))
        missing = [i for i, s in enumerate(stories) if not s.summary]
        if missing:
            log.info("retrying Chinese summaries for %d stories", len(missing))
            apply(ask(RETRY_PROMPT, [_story_payload(i, stories[i]) for i in missing]))
    elif not api_key:
        log.warning("LLM_API_KEY not set; only Chinese source text can be used")

    def translate(text: str) -> str:
        nonlocal used
        row = ask(TRANSLATE_PROMPT, [{"id": 0, "text": text}], need_title=False).get(0)
        used = used or bool(row)
        return row["summary"] if row else ""

    kept = []
    for s in stories:
        if fallback(s, translate if api_key else None):
            kept.append(s)
        else:
            log.warning("dropping story without a Chinese summary: %s", s.lead.title)
    stories[:] = kept
    return used
