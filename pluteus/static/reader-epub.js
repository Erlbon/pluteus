/* pluteus EPUB reader: epub.js does the rendering, this file is the interface around it.
   Everything visible is styled by the theme (base.css variables and .pl-* classes); the book text itself is
   styled by the theme's reader-content.css, loaded into the book's page and fed from the --pl-book-* variables. */
(function () {
  "use strict";

  var root = document.getElementById("reader");
  var $ = function (id) { return document.getElementById(id); };
  var statusEl = $("status");

  function say(text, ms) {
    statusEl.textContent = text;
    statusEl.hidden = false;
    clearTimeout(say.timer);
    if (ms) say.timer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }
  function fail(text) { say(text); }

  if (!root || typeof ePub === "undefined" || typeof JSZip === "undefined") {
    fail("The reader could not start: its scripts did not load.");
    return;
  }

  var cfg = root.dataset;
  var SETTINGS_KEY = "pluteus-reader";
  // spread: "single" = one centered page (the default; pleasant to read and right for books with long tables of contents),
  //         "double" = two pages side by side on a wide screen.
  var settings = { size: 100, font: "theme", line: "1.55", flow: "paginated", spread: "single" };
  try { Object.assign(settings, JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}")); } catch (e) { /* defaults */ }
  if (settings.spread !== "double") settings.spread = "single";
  function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* ignore */ } }

  var SPREAD_MIN_WIDTH = 900;
  function spreadMode() { return settings.spread === "double" ? "auto" : "none"; }
  root.classList.toggle("pl-single", settings.spread !== "double");      // CSS limits and centers the page width

  var book = ePub(cfg.book, { openAs: "epub" });
  var rendition = book.renderTo("viewer", {
    width: "100%", height: "100%", flow: settings.flow, spread: spreadMode(), minSpreadWidth: SPREAD_MIN_WIDTH,
    allowScriptedContent: false              // book scripts never run
  });
  var rtl = false;
  var lastCfi = "", lastPercent = null;

  /* ---- look: theme variables are copied into the book's page, and refreshed when the scheme changes ---- */
  var FONTS = {
    serif: 'Georgia, "Iowan Old Style", "Times New Roman", serif',
    sans: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
  };
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function applyVars(contents) {
    var vars = {
      "--pl-book-bg": cssVar("--pl-book-bg"), "--pl-book-fg": cssVar("--pl-book-fg"),
      "--pl-book-link": cssVar("--pl-book-link"),
      "--pl-book-font": FONTS[settings.font] || cssVar("--pl-font-reading"),
      "--pl-book-line": settings.line
    };
    var style = contents.document.documentElement.style;
    Object.keys(vars).forEach(function (k) { style.setProperty(k, vars[k]); });
    // The theme's reader-content.css can arrive late (the page is busy counting pages), and a white flash in a
    // dark scheme hurts. So the essentials are also set inline, at once; the stylesheet adds the rest.
    var body = contents.document.body;
    style.setProperty("background-color", vars["--pl-book-bg"], "important");
    if (body) {
      body.style.setProperty("background-color", vars["--pl-book-bg"], "important");
      body.style.setProperty("color", vars["--pl-book-fg"], "important");
      body.style.setProperty("font-family", vars["--pl-book-font"], "important");
      body.style.setProperty("line-height", vars["--pl-book-line"], "important");
    }
  }
  function reapplyAll() {
    rendition.getContents().forEach(applyVars);
    rendition.themes.fontSize(settings.size + "%");
  }
  rendition.hooks.content.register(applyVars);
  rendition.themes.register("pluteus", location.origin + cfg.contentCss);
  rendition.themes.select("pluteus");
  rendition.themes.fontSize(settings.size + "%");
  new MutationObserver(reapplyAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-scheme"] });
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", reapplyAll);

  /* ---- progress: where you are, saved per user on the server ---- */
  var saveTimer = null;
  function sendProgress(keepalive) {
    if (!lastCfi) return;
    fetch(cfg.progress, {
      method: "PUT", credentials: "same-origin", keepalive: !!keepalive,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locator: lastCfi, percent: lastPercent == null ? 0 : lastPercent })
    }).catch(function () { /* offline: the next page turn tries again */ });
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(sendProgress, 1500); }
  function flush() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; sendProgress(true); } }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);

  function showProgress() {
    var pct = lastPercent;
    $("bar").style.width = pct == null ? "0%" : Math.round(pct * 100) + "%";
    $("pct").textContent = pct == null ? "–" : Math.round(pct * 100) + "%";
  }

  rendition.on("relocated", function (loc) {
    lastCfi = loc.start.cfi;
    if (book.locations.length()) lastPercent = book.locations.percentageFromCfi(lastCfi);
    showProgress();
    scheduleSave();
  });

  // Counting pages (for the percentage) parses the whole book and keeps the page busy, so it starts only once
  // the first page is on screen, and the result is remembered in this browser (keyed on the file's size + date).
  var locKey = "pluteus-loc:" + cfg.key;
  function countPages() {
    book.ready.then(function () {
      var cached = null;
      try { cached = localStorage.getItem(locKey); } catch (e) { /* ignore */ }
      if (cached) { book.locations.load(cached); return; }
      return book.locations.generate(1600).then(function () {
        try { localStorage.setItem(locKey, book.locations.save()); } catch (e) { /* ignore */ }
      });
    }).then(function () {
      if (lastCfi && book.locations.length()) { lastPercent = book.locations.percentageFromCfi(lastCfi); showProgress(); scheduleSave(); }
    }).catch(function () { /* percentages stay unknown; reading still works */ });
  }

  /* ---- contents ---- */
  function closeMenus() { $("toc").hidden = true; $("settings").hidden = true; }
  function renderToc(items, ul) {
    items.forEach(function (item) {
      var li = document.createElement("li"), a = document.createElement("a");
      a.textContent = (item.label || "").trim() || "(untitled)";
      a.href = "#";
      a.addEventListener("click", function (e) { e.preventDefault(); closeMenus(); rendition.display(item.href); });
      li.appendChild(a);
      if (item.subitems && item.subitems.length) {
        var sub = document.createElement("ul"); sub.className = "pl-sub";
        renderToc(item.subitems, sub); li.appendChild(sub);
      }
      ul.appendChild(li);
    });
  }
  book.loaded.navigation.then(function (nav) { renderToc(nav.toc, $("toc-list")); });
  book.loaded.metadata.then(function (m) { rtl = m && m.direction === "rtl"; });

  /* ---- turning pages: buttons, keys, edge taps, swipes ---- */
  function next() { closeMenus(); return rtl ? rendition.prev() : rendition.next(); }
  function prev() { closeMenus(); return rtl ? rendition.next() : rendition.prev(); }
  $("next").addEventListener("click", next);
  $("prev").addEventListener("click", prev);
  $("tap-next").addEventListener("click", next);
  $("tap-prev").addEventListener("click", prev);

  function onKey(e) {
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    var key = e.key;
    if (key === "ArrowRight" || key === "PageDown" || (key === " " && !e.shiftKey)) { e.preventDefault(); next(); }
    else if (key === "ArrowLeft" || key === "PageUp" || (key === " " && e.shiftKey)) { e.preventDefault(); prev(); }
    else if (key === "Escape") closeMenus();
  }
  document.addEventListener("keydown", onKey);
  rendition.on("keydown", onKey);                       // keys pressed while the book page has focus

  var touchX = null, touchY = null;
  rendition.on("touchstart", function (e) { var t = e.changedTouches[0]; touchX = t.screenX; touchY = t.screenY; });
  rendition.on("touchend", function (e) {
    if (touchX == null) return;
    var t = e.changedTouches[0], dx = t.screenX - touchX, dy = t.screenY - touchY;
    touchX = touchY = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) (dx < 0 ? next : prev)();
  });

  /* ---- the two menus and the toolbar ---- */
  $("toc-btn").addEventListener("click", function () { var open = $("toc").hidden; closeMenus(); $("toc").hidden = !open; });
  $("settings-btn").addEventListener("click", function () { var open = $("settings").hidden; closeMenus(); $("settings").hidden = !open; });
  $("fs-btn").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (root.requestFullscreen) root.requestFullscreen();
  });

  function syncControls() {
    $("size-value").textContent = settings.size + "%";
    $("font").value = settings.font; $("line").value = settings.line; $("flow").value = settings.flow;
    $("spread").value = settings.spread;
  }
  function changeSize(delta) {
    settings.size = Math.min(220, Math.max(60, settings.size + delta));
    saveSettings(); syncControls(); rendition.themes.fontSize(settings.size + "%");
  }
  $("size-down").addEventListener("click", function () { changeSize(-10); });
  $("size-up").addEventListener("click", function () { changeSize(10); });
  $("font").addEventListener("change", function () { settings.font = this.value; saveSettings(); reapplyAll(); });
  $("line").addEventListener("change", function () { settings.line = this.value; saveSettings(); reapplyAll(); });
  $("flow").addEventListener("change", function () {
    settings.flow = this.value; saveSettings();
    var here = lastCfi;
    rendition.flow(settings.flow);
    if (here) rendition.display(here);
  });
  $("spread").addEventListener("change", function () {
    settings.spread = this.value === "double" ? "double" : "single"; saveSettings();
    var here = lastCfi;
    root.classList.toggle("pl-single", settings.spread !== "double");
    rendition.spread(spreadMode(), SPREAD_MIN_WIDTH);
    rendition.resize();                                  // the page column just changed width
    if (here) rendition.display(here);
  });
  syncControls();

  /* ---- start: ask the server where this user stopped, then open there ---- */
  fetch(cfg.progress, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .catch(function () { return {}; })
    .then(function (saved) {
      if (saved && typeof saved.percent === "number") lastPercent = saved.percent;
      return rendition.display(saved && saved.locator ? saved.locator : undefined);
    })
    .then(function () { say("", 1); setTimeout(countPages, 1500); })
    .catch(function (err) {
      var detail = err && err.message ? " (" + err.message + ")" : "";
      fail("This book could not be opened" + detail + ". You can still download it from the library.");
    });
  book.opened.catch(function () { fail("This book could not be read. You can still download it from the library."); });
})();
