# AI Morning Brief

Card component prototype (spec v1): swipeable deck of news cards with an English display title, a Chinese summary, and a one-tap "Copy X Post" action.

## Run

No build step — plain HTML/CSS/ES modules.

```sh
python3 -m http.server 8000
# open http://localhost:8000
```

## Structure

- `src/card.js` — `createCard(story)` renders one card (badges, meta, title, body, copy/share).
- `src/xpost.js` — `formatXPost(story)` and `xLength(text)` (X weighted count: CJK = 2, URLs = 23); trims the summary to stay ≤ 280.
- `src/deck.js` — stacked deck: swipe left = next, right = previous; wheel/arrow keys for vertical-feed navigation.
- `src/stories.js` — placeholder data (not real news).
- `src/styles.css` — design tokens and card styles.

Story shape: `{ id, title, summary, sources: [1–2 names], publishedAt: ISO, url? }`.
