import { deliverImage, exportFilename, renderCardPng } from "./export.js";
import { formatXPost, xLength, X_MAX } from "./xpost.js";

const SHARE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" aria-hidden="true"><path d="M12 3v13"/><path d="M7 8l5-5 5 5"/><path d="M5 12v9h14v-9"/></svg>`;

const IMAGE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" aria-hidden="true"><rect x="3.75" y="4.75" width="16.5" height="14.5"/><path d="M3.75 16l5-5 4 4 2.5-2.5 5 5"/><circle cx="15.5" cy="9" r="1.25"/></svg>`;

const pad = (n) => String(n).padStart(2, "0");
const MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

function formatMeta(iso) {
  const d = new Date(iso);
  return `${MONTHS[d.getMonth()]} ${pad(d.getDate())} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = el("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;opacity:0;pointer-events:none";
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function localDate() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * story: { id, title, summary, sources: string[] (1–2), publishedAt: ISO, url? }
 * index: 1-based position in the brief; date: brief date (YYYY-MM-DD), both for the image file name.
 */
export function createCard(story, { index = 1, date = localDate() } = {}) {
  const card = el("article", "card");
  card.dataset.id = story.id;

  const header = el("header", "card__header");
  const badges = el("div", "card__badges");
  story.sources.slice(0, 2).forEach((s) => badges.append(el("span", "badge", s)));
  const meta = el("time", "card__meta", formatMeta(story.publishedAt));
  meta.dateTime = story.publishedAt;
  header.append(badges, meta);

  const title = el("h2", "card__title", story.title);
  const body = el("p", "card__body", story.summary);
  body.lang = "zh-CN";
  title.lang = "en";

  const footer = el("footer", "card__footer");
  const actions = el("div", "card__actions");
  const copyBtn = el("button", "btn-copy", "Copy X Post");
  copyBtn.type = "button";
  const pill = el("span", "count-pill");
  actions.append(copyBtn, pill);

  const shareBtn = el("button", "btn-share");
  shareBtn.type = "button";
  shareBtn.setAttribute("aria-label", "Share");
  shareBtn.innerHTML = SHARE_ICON;

  const saveBtn = el("button", "btn-save");
  saveBtn.type = "button";
  saveBtn.setAttribute("aria-label", "Save image");
  saveBtn.innerHTML = IMAGE_ICON;

  const icons = el("div", "card__icons");
  icons.append(saveBtn, shareBtn);
  footer.append(actions, icons);

  card.append(header, title, body, footer);

  const post = () => formatXPost(story);
  const updateCount = () => {
    const n = xLength(post());
    pill.textContent = `${n}/${X_MAX}`;
    pill.classList.toggle("is-over", n > X_MAX);
  };
  updateCount();

  let resetTimer;
  copyBtn.addEventListener("click", async () => {
    const ok = await copyText(post());
    copyBtn.textContent = ok ? "Copied" : "Copy failed";
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => (copyBtn.textContent = "Copy X Post"), 1400);
  });

  shareBtn.addEventListener("click", async () => {
    const data = { title: story.title, text: post() };
    if (story.url) data.url = story.url;
    if (navigator.share) {
      try { await navigator.share(data); } catch { /* user cancelled */ }
    } else {
      copyBtn.click();
    }
  });

  // Start rendering on press so the share sheet opens while the tap still counts as a user gesture.
  let image = null;
  const renderImage = () => (image ??= renderCardPng(card).catch((err) => {
    image = null;
    throw err;
  }));
  saveBtn.addEventListener("pointerdown", () => renderImage().catch(() => {}));
  saveBtn.addEventListener("click", async () => {
    if (saveBtn.getAttribute("aria-busy") === "true") return;
    saveBtn.setAttribute("aria-busy", "true");
    try {
      await deliverImage(await renderImage(), exportFilename(date, index));
    } catch (err) {
      console.error("Save image failed", err);
    } finally {
      saveBtn.removeAttribute("aria-busy");
    }
  });

  return { el: card, update(next) { Object.assign(story, next); title.textContent = story.title; body.textContent = story.summary; updateCount(); image = null; } };
}
