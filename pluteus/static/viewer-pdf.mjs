/* pluteus PDF viewer: pdf.js (vendored, nothing from a CDN) draws one page at a time onto a canvas.
   The file is fetched with range requests, so a big PDF shows its first page before it has finished downloading.
   Look and feel come from the theme; this file only does behaviour. */
import * as pdfjs from "./vendor/pdfjs/pdf.min.mjs";

const root = document.getElementById("reader");
const $ = (id) => document.getElementById(id);
const stage = document.querySelector(".pl-reader-stage");
const canvas = $("canvas");
const statusEl = $("status");
const cfg = root.dataset;

function say(text, ms) {
  statusEl.textContent = text;
  statusEl.hidden = false;
  clearTimeout(say.timer);
  if (ms) say.timer = setTimeout(() => { statusEl.hidden = true; }, ms);
}

pdfjs.GlobalWorkerOptions.workerSrc = new URL("./vendor/pdfjs/pdf.worker.min.mjs", import.meta.url).href;
const FONTS = new URL("./vendor/pdfjs/standard_fonts/", import.meta.url).href;
const WASM = new URL("./vendor/pdfjs/wasm/", import.meta.url).href;       // JPEG 2000, JBIG2 and colour-profile decoders

/* ---- settings remembered in this browser ---- */
const KEY = "pluteus-pdf";
let fit = "width", invert = false, zoom = 1;
try {
  const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
  if (saved.fit === "page" || saved.fit === "width") fit = saved.fit;
  invert = saved.invert === true;
} catch (e) { /* defaults */ }
const remember = () => { try { localStorage.setItem(KEY, JSON.stringify({ fit, invert })); } catch (e) { /* ignore */ } };
root.classList.toggle("pl-inverted", invert);

let pdf = null, count = 0, current = 0, renderTask = null, token = 0;
const clamp = (n) => Math.max(0, Math.min(count - 1, n));

/* ---- progress: the page number, saved per user on the server ---- */
let saveTimer = null;
const fraction = () => (count > 1 ? (current + 1) / count : 1);
function sendProgress(keepalive) {
  fetch(cfg.progress, {
    method: "PUT", credentials: "same-origin", keepalive: !!keepalive,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locator: "page:" + current, percent: fraction() })
  }).catch(() => { /* offline: the next page turn tries again */ });
}
const scheduleSave = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => { saveTimer = null; sendProgress(false); }, 1200); };
const flush = () => { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; sendProgress(true); } };
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
window.addEventListener("pagehide", flush);

/* ---- drawing a page ---- */
async function show(n) {
  if (!pdf) return;
  current = clamp(n);
  $("bar").style.width = Math.round(fraction() * 100) + "%";
  $("pct").textContent = (current + 1) + " / " + count;
  scheduleSave();
  const mine = ++token;
  if (renderTask) {                                   // one render at a time on the canvas
    renderTask.cancel();
    try { await renderTask.promise; } catch (e) { /* cancelled */ }
    renderTask = null;
  }
  if (mine !== token) return;
  try {
    const page = await pdf.getPage(current + 1);
    if (mine !== token) return;
    const base = page.getViewport({ scale: 1 });
    const availW = Math.max(100, stage.clientWidth - 24), availH = Math.max(100, stage.clientHeight - 24);
    const fitted = fit === "width" ? availW / base.width : Math.min(availW / base.width, availH / base.height);
    const css = Math.max(0.1, fitted * zoom);
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const viewport = page.getViewport({ scale: css * dpr });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width = Math.floor(viewport.width / dpr) + "px";
    canvas.style.height = Math.floor(viewport.height / dpr) + "px";
    renderTask = page.render({ canvas, viewport });
    await renderTask.promise;
    statusEl.hidden = true;
    stage.scrollTop = 0;
    if (current + 1 < count) pdf.getPage(current + 2).catch(() => {});      // warm up the next page
  } catch (e) {
    if (e && e.name === "RenderingCancelledException") return;
    say("This page could not be drawn.");
  }
}

const next = () => { if (current < count - 1) show(current + 1); else say("That was the last page.", 1500); };
const prev = () => { if (current > 0) show(current - 1); };
$("next").addEventListener("click", next);
$("prev").addEventListener("click", prev);
$("tap-next").addEventListener("click", next);
$("tap-prev").addEventListener("click", prev);

document.addEventListener("keydown", (e) => {
  const k = e.key;
  if (k === "ArrowRight" || k === "PageDown") { e.preventDefault(); next(); }
  else if (k === "ArrowLeft" || k === "PageUp") { e.preventDefault(); prev(); }
  else if (k === "Home") { e.preventDefault(); show(0); }
  else if (k === "End") { e.preventDefault(); show(count - 1); }
  else if (k === "+" || k === "=") { e.preventDefault(); changeZoom(0.25); }
  else if (k === "-") { e.preventDefault(); changeZoom(-0.25); }
});

let tx = null, ty = null;
stage.addEventListener("touchstart", (e) => { const t = e.changedTouches[0]; tx = t.clientX; ty = t.clientY; }, { passive: true });
stage.addEventListener("touchend", (e) => {
  if (tx == null) return;
  const t = e.changedTouches[0], dx = t.clientX - tx, dy = t.clientY - ty;
  tx = ty = null;
  if (zoom <= 1 && Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) (dx < 0 ? next : prev)();
}, { passive: true });

$("seek").addEventListener("click", function (e) {
  const box = this.getBoundingClientRect();
  show(Math.floor(((e.clientX - box.left) / box.width) * count));
});

function changeZoom(delta) {
  zoom = Math.min(4, Math.max(0.5, Math.round((zoom + delta) * 100) / 100));
  say(Math.round(zoom * 100) + "%", 900);
  show(current);
}
$("plus-btn").addEventListener("click", () => changeZoom(0.25));
$("minus-btn").addEventListener("click", () => changeZoom(-0.25));
$("fit-btn").addEventListener("click", () => {
  fit = fit === "width" ? "page" : "width"; zoom = 1; remember();
  say(fit === "width" ? "Fit to width" : "Fit to page", 1200);
  show(current);
});
$("invert-btn").addEventListener("click", () => {
  invert = !invert; remember(); root.classList.toggle("pl-inverted", invert);
});
$("fs-btn").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen(); else if (root.requestFullscreen) root.requestFullscreen();
});
let resizeTimer = null;
new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => show(current), 250); }).observe(stage);

/* ---- open the document, then go to where this user stopped ---- */
(async () => {
  try {
    pdf = await pdfjs.getDocument({ url: cfg.src, standardFontDataUrl: FONTS, wasmUrl: WASM }).promise;
  } catch (e) {
    const locked = e && e.name === "PasswordException";
    say(locked ? "This PDF is password-protected. Download it to open it elsewhere."
               : "This PDF could not be opened" + (e && e.message ? " (" + e.message + ")" : "") + ". You can still download it.");
    return;
  }
  count = pdf.numPages;
  let start = 0;
  try {
    const r = await fetch(cfg.progress, { credentials: "same-origin" });
    const saved = r.ok ? await r.json() : {};
    const m = saved && typeof saved.locator === "string" && /^page:(\d+)$/.exec(saved.locator);
    if (m) start = parseInt(m[1], 10);
  } catch (e) { /* start at the first page */ }
  show(start);
})();
