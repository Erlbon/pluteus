/* pluteus comic reader: one page at a time, straight from the CBZ on the server. No libraries.
   Look and feel come from the theme (base.css variables, .pl-* classes); this file only does behaviour. */
(function () {
  "use strict";

  var root = document.getElementById("reader");
  var $ = function (id) { return document.getElementById(id); };
  var statusEl = $("status");
  function say(text, ms) {
    statusEl.textContent = text; statusEl.hidden = false;
    clearTimeout(say.timer);
    if (ms) say.timer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }
  if (!root) return;

  var cfg = root.dataset;
  var count = parseInt(cfg.count, 10) || 0;
  var img = $("page"), stage = $("stage");
  var current = 0;
  if (count < 1) { say("This comic has no pages."); return; }

  /* Zoom levels: the whole page (fit to height), then 50 / 65 / 80 % of the window's width (the page scrolls down), the full
     width, and 150 / 200 / 300 % of the width (scrolls both ways). */
  var LEVELS = ["page", "0.5", "0.65", "0.8", "width", "1.5", "2", "3"];
  var LABELS = { "page": "Fit to page", "0.5": "50% of width", "0.65": "65% of width", "0.8": "80% of width", "width": "Fit to width",
                 "1.5": "150%", "2": "200%", "3": "300%" };
  var FIT_KEY = "pluteus-comic-fit";
  var fit = "page";
  try { var saved = localStorage.getItem(FIT_KEY); if (LEVELS.indexOf(saved) >= 0) fit = saved; } catch (e) { /* default */ }
  function applyFit() {
    var zoomed = fit !== "page" && fit !== "width";
    stage.classList.toggle("pl-fit-page", fit === "page");
    stage.classList.toggle("pl-fit-width", fit === "width");
    stage.classList.toggle("pl-fit-zoom", zoomed);
    if (zoomed) stage.style.setProperty("--pl-zoom", fit); else stage.style.removeProperty("--pl-zoom");
    $("tap-prev").style.pointerEvents = $("tap-next").style.pointerEvents = zoomed ? "none" : "";   // the edges must not steal a drag
    stage.scrollTop = 0;
    centre();
  }
  function centre() { stage.scrollLeft = Math.max(0, (stage.scrollWidth - stage.clientWidth) / 2); }
  function setFit(level, quiet) {
    fit = level;
    try { localStorage.setItem(FIT_KEY, fit); } catch (e) { /* ignore */ }
    applyFit();
    if (!quiet) say(LABELS[fit], 1200);
  }
  function zoomBy(step) { setFit(LEVELS[Math.max(0, Math.min(LEVELS.length - 1, LEVELS.indexOf(fit) + step))]); }

  function pageUrl(n) { return cfg.pages + (cfg.pages.indexOf("?") < 0 ? "?" : "&") + "page=" + n; }
  function clamp(n) { return Math.max(0, Math.min(count - 1, n)); }

  /* ---- progress: the page number, saved per user on the server ---- */
  var saveTimer = null;
  function percent() { return count > 1 ? (current + 1) / count : 1; }
  function sendProgress(keepalive) {
    fetch(cfg.progress, {
      method: "PUT", credentials: "same-origin", keepalive: !!keepalive,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locator: "page:" + current, percent: percent() })
    }).catch(function () { /* offline: the next page turn tries again */ });
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(function () { saveTimer = null; sendProgress(); }, 1200); }
  function flush() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; sendProgress(true); } }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);

  /* ---- showing a page ---- */
  var preloaded = {};
  function preload(n) {
    if (n < 0 || n >= count || preloaded[n]) return;
    preloaded[n] = true;
    new Image().src = pageUrl(n);
  }
  function show(n) {
    current = clamp(n);
    say("Loading…");
    img.src = pageUrl(current);
    stage.scrollTop = 0;
    $("bar").style.width = Math.round(percent() * 100) + "%";
    $("pct").textContent = (current + 1) + " / " + count;
    preload(current + 1); preload(current + 2);
    scheduleSave();
  }
  img.addEventListener("load", function () {
    statusEl.hidden = true; centre();
    if (landBottom) { stage.scrollTop = stage.scrollHeight; landBottom = false; }     // arrived by scrolling backwards: the end of the page
  });
  img.addEventListener("error", function () { say("This page could not be loaded."); });

  function next() { if (current < count - 1) show(current + 1); else say("That was the last page.", 1500); }
  function prev() { if (current > 0) show(current - 1); }
  $("next").addEventListener("click", next);
  $("prev").addEventListener("click", prev);
  $("tap-next").addEventListener("click", next);
  $("tap-prev").addEventListener("click", prev);

  var landBottom = false, lastWheel = 0, wheelAt = 0;
  function scrollable() { return stage.scrollHeight > stage.clientHeight + 1; }
  function atBottom() { return stage.scrollTop + stage.clientHeight >= stage.scrollHeight - 2; }
  function atTop() { return stage.scrollTop <= 1; }
  /* Space and Page Down scroll a tall page first and turn the page once its end is reached (Shift / Page Up: the reverse) */
  function forward() { if (scrollable() && !atBottom()) stage.scrollBy({ top: stage.clientHeight * 0.9 }); else next(); }
  function backward() { if (scrollable() && !atTop()) stage.scrollBy({ top: -stage.clientHeight * 0.9 }); else { landBottom = true; prev(); } }

  document.addEventListener("keydown", function (e) {
    var k = e.key;
    if (k === "ArrowRight") { e.preventDefault(); next(); }
    else if (k === "ArrowLeft") { e.preventDefault(); prev(); }
    else if (k === "PageDown" || (k === " " && !e.shiftKey)) { e.preventDefault(); forward(); }
    else if (k === "PageUp" || (k === " " && e.shiftKey)) { e.preventDefault(); backward(); }
    else if (k === "ArrowDown" && scrollable()) { e.preventDefault(); stage.scrollBy({ top: 100 }); }
    else if (k === "ArrowUp" && scrollable()) { e.preventDefault(); stage.scrollBy({ top: -100 }); }
    else if (k === "Home") { e.preventDefault(); show(0); }
    else if (k === "End") { e.preventDefault(); show(count - 1); }
    else if (k === "+" || k === "=") { e.preventDefault(); zoomBy(1); }
    else if (k === "-" || k === "_") { e.preventDefault(); zoomBy(-1); }
    else if (k === "0") { e.preventDefault(); setFit("page"); }
    else if (k === "f" || k === "F") { e.preventDefault(); setFit(fit === "page" ? "width" : "page"); }
  });

  /* Mouse wheel: scrolls a page that is taller than the window; a whole page (fit to page), or a push past the top or bottom
     of a tall one, turns the page. Ctrl + wheel zooms. Dragging pans a zoomed page. */
  stage.addEventListener("wheel", function (e) {
    if (e.ctrlKey) { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1 : -1); return; }
    var dy = e.deltaY;
    if (!dy || Math.abs(dy) < Math.abs(e.deltaX)) return;                              // sideways: the browser pans
    var tall = scrollable(), now = Date.now(), pause = now - lastWheel;
    lastWheel = now;
    if (tall && !((dy > 0 && atBottom()) || (dy < 0 && atTop()))) return;               // the browser scrolls it
    e.preventDefault();
    /* At the edge only a NEW push turns the page (after a pause), so the end of one long scroll or a trackpad's coasting
       never skips a page */
    if (pause < 220 || now - wheelAt < 500) return;
    wheelAt = now;
    if (dy > 0) next(); else { landBottom = tall; prev(); }
  }, { passive: false });
  var drag = null, moved = false;
  stage.addEventListener("mousedown", function (e) {
    if (!stage.classList.contains("pl-fit-zoom") || e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, left: stage.scrollLeft, top: stage.scrollTop }; moved = false;
    stage.classList.add("pl-panning");
  });
  window.addEventListener("mousemove", function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    stage.scrollLeft = drag.left - dx; stage.scrollTop = drag.top - dy;
  });
  window.addEventListener("mouseup", function () { drag = null; stage.classList.remove("pl-panning"); });
  stage.addEventListener("click", function (e) { if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; } }, true);

  /* swipe left/right on a touch screen (only when the page is not being scrolled sideways) */
  var tx = null, ty = null;
  stage.addEventListener("touchstart", function (e) { var t = e.changedTouches[0]; tx = t.clientX; ty = t.clientY; }, { passive: true });
  stage.addEventListener("touchend", function (e) {
    if (tx == null) return;
    if (stage.classList.contains("pl-fit-zoom")) { tx = ty = null; return; }          // zoomed: a swipe pans, it does not turn the page
    var t = e.changedTouches[0], dx = t.clientX - tx, dy = t.clientY - ty;
    tx = ty = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) (dx < 0 ? next : prev)();
  }, { passive: true });

  /* click the progress bar to jump to that part of the book */
  $("seek").addEventListener("click", function (e) {
    var box = this.getBoundingClientRect();
    show(Math.floor(((e.clientX - box.left) / box.width) * count));
  });

  $("fit-btn").addEventListener("click", function () { setFit(LEVELS[(LEVELS.indexOf(fit) + 1) % LEVELS.length]); });
  $("fs-btn").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (root.requestFullscreen) root.requestFullscreen();
  });

  /* ---- start where this user stopped ---- */
  applyFit();
  fetch(cfg.progress, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .catch(function () { return {}; })
    .then(function (saved) {
      var m = saved && typeof saved.locator === "string" && /^page:(\d+)$/.exec(saved.locator);
      show(m ? parseInt(m[1], 10) : 0);
    });
})();
