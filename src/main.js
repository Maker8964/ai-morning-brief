import { createDeck } from "./deck.js";
import { stories as placeholder } from "./stories.js";

const indexEl = document.getElementById("deck-index");
const pad = (n) => String(n).padStart(2, "0");

async function loadBrief() {
  try {
    const res = await fetch("data/brief.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    const brief = await res.json();
    if (brief.stories?.length) return brief;
  } catch (err) {
    console.warn("brief.json unavailable, showing placeholder cards", err);
  }
  return { stories: placeholder };
}

const brief = await loadBrief();
createDeck(document.getElementById("deck"), brief.stories, {
  date: brief.date,
  onChange: (i, n) => (indexEl.textContent = `${pad(i + 1)} / ${pad(n)}`),
});
