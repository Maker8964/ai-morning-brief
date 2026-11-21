import { createCard } from "./card.js";

const VISIBLE = 3;
const SWIPE_THRESHOLD = 0.25; // fraction of card width
const FLICK_VELOCITY = 0.5;   // px/ms
const OUT_MS = 260;

export function createDeck(root, stories, { onChange, date } = {}) {
  let index = 0;
  let busy = false;
  const n = stories.length;
  const wrap = (i) => ((i % n) + n) % n;
  const at = (i) => stories[wrap(i)];

  function render() {
    root.replaceChildren();
    const count = Math.min(VISIBLE, n);
    for (let d = count - 1; d >= 0; d--) {
      const { el } = createCard(at(index + d), { index: wrap(index + d) + 1, date });
      el.dataset.depth = d;
      if (d > 0) el.setAttribute("aria-hidden", "true");
      root.append(el);
    }
    onChange?.(wrap(index), n);
  }

  const top = () => root.querySelector('.card[data-depth="0"]');

  function flyOut(card, dir) {
    const w = root.offsetWidth;
    card.style.transform = `translateX(${dir * w * 1.5}px) rotate(${dir * 12}deg)`;
    card.style.opacity = "0";
  }

  function next() {
    if (busy || n < 2) return;
    busy = true;
    flyOut(top(), -1);
    root.querySelectorAll('.card:not([data-depth="0"])').forEach((c) => (c.dataset.depth = c.dataset.depth - 1));
    setTimeout(() => { index++; render(); busy = false; }, OUT_MS);
  }

  function prev() {
    if (busy || n < 2) return;
    busy = true;
    index--;
    render();
    const card = top();
    const w = root.offsetWidth;
    card.style.transition = "none";
    card.style.transform = `translateX(${w * 1.5}px) rotate(12deg)`;
    card.style.opacity = "0";
    card.getBoundingClientRect();
    card.style.transition = "";
    card.style.transform = "";
    card.style.opacity = "";
    setTimeout(() => (busy = false), OUT_MS);
  }

  // Pointer drag on the top card.
  let drag = null;
  root.addEventListener("pointerdown", (e) => {
    const card = e.target.closest('.card[data-depth="0"]');
    if (!card || busy || e.target.closest("button") || e.button !== 0) return;
    drag = { card, id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), dx: 0, locked: null };
  });

  root.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x0;
    const dy = e.clientY - drag.y0;
    if (drag.locked === null && Math.hypot(dx, dy) > 6) {
      drag.locked = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (drag.locked === "x") {
        drag.card.setPointerCapture(e.pointerId);
        drag.card.classList.add("is-dragging");
      }
    }
    if (drag.locked !== "x") return;
    drag.dx = dx;
    drag.card.style.transform = `translateX(${dx}px) rotate(${dx / 20}deg)`;
  });

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const { card, dx, t0, locked } = drag;
    drag = null;
    if (locked !== "x") return;
    card.classList.remove("is-dragging");
    const v = Math.abs(dx) / (performance.now() - t0);
    const passed = Math.abs(dx) > root.offsetWidth * SWIPE_THRESHOLD || v > FLICK_VELOCITY;
    if (!passed || n < 2) { card.style.transform = ""; return; }
    if (dx < 0) {
      next();
    } else {
      // Right swipe: send the card off right, then bring the previous one in.
      busy = true;
      flyOut(card, 1);
      setTimeout(() => { busy = false; prev(); }, OUT_MS);
    }
  }
  root.addEventListener("pointerup", endDrag);
  root.addEventListener("pointercancel", endDrag);

  // Vertical feed: wheel / trackpad scroll.
  let wheelLock = 0;
  root.addEventListener("wheel", (e) => {
    const now = performance.now();
    if (now < wheelLock || Math.abs(e.deltaY) < 20) return;
    wheelLock = now + 500;
    e.deltaY > 0 ? next() : prev();
  }, { passive: true });

  window.addEventListener("keydown", (e) => {
    if (["ArrowLeft", "ArrowDown", "j"].includes(e.key)) next();
    if (["ArrowRight", "ArrowUp", "k"].includes(e.key)) prev();
  });

  render();
  return { next, prev };
}
