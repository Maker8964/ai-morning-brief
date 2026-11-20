// Renders a card to PNG (html-to-image, loaded lazily from the CDN) and hands it to
// the share sheet, a download, or an on-page preview for long-press "Save to Photos".

const HTML_TO_IMAGE = "https://cdn.jsdelivr.net/npm/html-to-image@1.11.13/+esm";
const EXPORT_WIDTH = 1080; // CSS px of the output image; ×2 pixelRatio
const PIXEL_RATIO = 2;

// WebKit (Safari, every iOS browser) often drops web fonts from the first
// foreignObject render; a throwaway first pass fixes it.
const IS_WEBKIT = /AppleWebKit/.test(navigator.userAgent) && !/Chrome|Chromium|Edg|OPR/.test(navigator.userAgent);

let libPromise;
const loadLib = () => (libPromise ??= import(HTML_TO_IMAGE));

// ---------- Font embedding ----------
// Google Fonts splits Noto Serif SC into ~100 unicode-range files; embed only the
// @font-face blocks whose range covers characters actually on the card.
const cssCache = new Map();
const fontCache = new Map();

function parseRanges(value) {
  return value.split(",").map((part) => {
    const [lo, hi] = part.trim().replace(/^U\+/i, "").split("-");
    const start = parseInt(lo.replace(/\?/g, "0"), 16);
    const end = hi ? parseInt(hi, 16) : parseInt(lo.replace(/\?/g, "F"), 16);
    return [start, end];
  });
}

function toDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function fetchFont(url) {
  if (!fontCache.has(url)) {
    fontCache.set(url, fetch(url).then((r) => r.blob()).then(toDataUrl).catch((err) => {
      fontCache.delete(url);
      throw err;
    }));
  }
  return fontCache.get(url);
}

async function fontEmbedCSS(text) {
  const codepoints = new Set([...`${text}${text.toUpperCase()}`].map((ch) => ch.codePointAt(0)));
  const hrefs = [...document.querySelectorAll('link[rel="stylesheet"][href*="fonts.googleapis.com"]')].map((l) => l.href);
  const sheets = await Promise.all(hrefs.map((href) => {
    if (!cssCache.has(href)) {
      cssCache.set(href, fetch(href).then((r) => {
        if (!r.ok) throw new Error(`font css ${r.status}`);
        return r.text();
      }).catch((err) => {
        cssCache.delete(href);
        throw err;
      }));
    }
    return cssCache.get(href);
  }));
  const faces = sheets.join("\n").match(/@font-face\s*{[^}]*}/g) || [];
  const used = faces.filter((face) => {
    const range = face.match(/unicode-range:\s*([^;}]+)/);
    if (!range) return true;
    const ranges = parseRanges(range[1]);
    for (const cp of codepoints) if (ranges.some(([lo, hi]) => cp >= lo && cp <= hi)) return true;
    return false;
  });
  const embedded = await Promise.all(used.map(async (face) => {
    const url = face.match(/url\((['"]?)([^'")]+)\1\)/)?.[2];
    return url ? face.replace(url, await fetchFont(url)) : face;
  }));
  return embedded.join("\n");
}

// ---------- Rendering ----------
export async function renderCardPng(card) {
  const [{ toBlob }] = await Promise.all([loadLib(), document.fonts.ready]);

  // offsetWidth is the layout width, unaffected by the deck's scale/translate.
  const width = card.offsetWidth;
  const clone = card.cloneNode(true);
  clone.querySelector(".card__footer")?.remove();
  ["data-depth", "aria-hidden", "style"].forEach((a) => clone.removeAttribute(a));
  clone.classList.remove("is-dragging");
  clone.style.cssText = `width:${width}px;margin:0;transform:none;opacity:1;transition:none`;

  // Outside .deck so none of the deck rules apply.
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:-100000px;top:0;pointer-events:none";
  host.append(clone);
  document.body.append(host);
  try {
    const height = clone.offsetHeight;
    const scale = EXPORT_WIDTH / width;
    const options = {
      width,
      height,
      canvasWidth: EXPORT_WIDTH,
      canvasHeight: Math.round(height * scale),
      pixelRatio: PIXEL_RATIO,
      backgroundColor: getComputedStyle(clone).backgroundColor,
      fontEmbedCSS: await fontEmbedCSS(clone.textContent),
    };
    if (IS_WEBKIT) await toBlob(clone, options);
    const blob = await toBlob(clone, options);
    if (!blob) throw new Error("render produced no image");
    return blob;
  } finally {
    host.remove();
  }
}

// ---------- Delivery ----------
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function showPreview(blob, file, canShareFile) {
  const url = URL.createObjectURL(blob);
  const overlay = document.createElement("div");
  overlay.className = "img-preview";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Card image");
  overlay.innerHTML = `
    <img class="img-preview__img" alt="" />
    <p class="img-preview__hint">Long-press the image to save to Photos</p>
    <div class="img-preview__actions"></div>`;
  overlay.querySelector("img").src = url;
  const actions = overlay.querySelector(".img-preview__actions");

  const close = () => {
    overlay.remove();
    URL.revokeObjectURL(url);
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => e.key === "Escape" && close();
  const button = (label, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "img-preview__btn";
    b.textContent = label;
    b.addEventListener("click", onClick);
    actions.append(b);
    return b;
  };

  if (canShareFile) {
    button("Share", async () => {
      try { await navigator.share({ files: [file] }); close(); } catch { /* cancelled */ }
    });
  }
  button("Download", () => download(blob, file.name));
  button("Close", close).focus();
  overlay.addEventListener("click", (e) => e.target === overlay && close());
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
}

/**
 * Touch devices: system share sheet with the PNG (lets iOS post to X or "Save Image"
 * to Photos), else an on-page preview to long-press. Desktop: plain download.
 */
export async function deliverImage(blob, filename) {
  const file = new File([blob], filename, { type: "image/png" });
  const touch = window.matchMedia("(pointer: coarse)").matches;
  const canShareFile = !!navigator.canShare?.({ files: [file] });

  if (!touch) return download(blob, filename);
  if (canShareFile) {
    try {
      await navigator.share({ files: [file] });
      return;
    } catch (err) {
      if (err.name === "AbortError") return;
      // Typically NotAllowedError: the tap's user activation expired while rendering.
      // The preview's Share button is a fresh tap.
    }
  }
  showPreview(blob, file, canShareFile);
}

export function exportFilename(date, index) {
  return `ai-brief-${date.replaceAll("-", "")}-${index}.png`;
}
