// X (Twitter) weighted length, per twitter-text v3 config:
// code points in these ranges count 1, everything else (CJK, emoji…) counts 2,
// and every URL counts as 23 regardless of length.
export const X_MAX = 280;
const URL_WEIGHT = 23;
const LIGHT_RANGES = [
  [0x0000, 0x10ff],
  [0x2000, 0x200d],
  [0x2010, 0x201f],
  [0x2032, 0x2037],
];
const URL_RE = /https?:\/\/\S+/g;

const charWeight = (cp) =>
  LIGHT_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi) ? 1 : 2;

export function xLength(text) {
  let len = 0;
  const stripped = text.replace(URL_RE, () => {
    len += URL_WEIGHT;
    return "";
  });
  for (const ch of stripped) len += charWeight(ch.codePointAt(0));
  return len;
}

function compose(story, summary) {
  const parts = [story.title.toUpperCase(), summary];
  const via = `via ${story.sources.join(" / ")}`;
  parts.push(story.url ? `${via} ${story.url}` : via);
  return parts.join("\n\n");
}

// Formats a story as an X post; trims the summary (never title/sources) to fit.
export function formatXPost(story) {
  let summary = story.summary;
  let post = compose(story, summary);
  if (xLength(post) <= X_MAX) return post;

  const chars = Array.from(summary);
  while (chars.length && xLength(post) > X_MAX) {
    chars.pop();
    summary = chars.join("").replace(/[，。、；：,.;:\s]+$/u, "") + "…";
    post = compose(story, summary);
  }
  return post;
}
