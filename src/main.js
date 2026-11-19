import { createDeck } from "./deck.js";
import { stories } from "./stories.js";

const indexEl = document.getElementById("deck-index");
const pad = (n) => String(n).padStart(2, "0");

createDeck(document.getElementById("deck"), stories, {
  onChange: (i, n) => (indexEl.textContent = `${pad(i + 1)} / ${pad(n)}`),
});
