# AI Morning Brief

A daily deck of AI news cards: English display title, Chinese summary, one-tap "Copy X Post".

```
ingest (RSS + HN) -> dedup -> rank -> summarize (LLM) -> data/brief.json -> card deck
```

## Run

```sh
pip install -r requirements.txt
LLM_API_KEY=... python3 -m pipeline.brief      # writes data/brief.json
python3 -m http.server 8000                    # open http://localhost:8000
python3 -m unittest                            # tests
```

The frontend has no build step. It loads `data/brief.json` and falls back to placeholder cards if it is missing.

## Pipeline

- `pipeline/sources.py` — feed list with per-source weight. General feeds (TechCrunch, Bloomberg, HN) keep only items whose title matches AI keywords.
- `pipeline/ingest.py` — fetches all sources in parallel; keeps the last 36 h (`--window`). A failing feed is logged and skipped.
- `pipeline/dedup.py` — clusters items about the same story: same normalized URL, or IDF-weighted headline overlap ≥ 0.5 with ≥ 2 shared words. The most central headline leads; a card shows up to 2 source badges.
- `pipeline/rank.py` — score = source weight × coverage (+60% per extra source) × HN buzz × recency (12 h half-life); top 12, max 3 per lead source.
- `pipeline/summarize.py` — one call to any OpenAI-compatible chat API for an English headline (≤ 60 chars) and a 60–90 character Chinese summary. Without `LLM_API_KEY`, or on error, it uses the source headline and excerpt.

| Env var | Default |
| --- | --- |
| `LLM_API_KEY` | unset (source-text fallback) |
| `LLM_BASE_URL` | `https://api.openai.com/v1` |
| `LLM_MODEL` | `gpt-4o-mini` |

## Schedule

`.github/workflows/brief.yml` runs daily at 06:00 Asia/Shanghai (and on manual dispatch), then commits `data/brief.json`. Set the `LLM_API_KEY` repo secret, and optionally the `LLM_BASE_URL` / `LLM_MODEL` repo variables.

## Frontend

- `src/card.js` — `createCard(story)` renders one card (badges, meta, title, body, copy/share).
- `src/xpost.js` — `formatXPost(story)` and `xLength(text)` (X weighted count: CJK = 2, URLs = 23); trims the summary to stay ≤ 280.
- `src/deck.js` — stacked deck: swipe left = next, right = previous; wheel/arrow keys for vertical-feed navigation.
- `src/stories.js` — placeholder cards used when `data/brief.json` is missing.
- `src/styles.css` — design tokens and card styles.

Story shape: `{ id, title, summary, sources: [1–2 names], publishedAt: ISO, url? }`.
