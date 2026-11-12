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
  // the opt-in tap and turn log (turnlog.js; off unless switched on under Settings): every call below does nothing while it is off
  var L = window.plTurnLog || { on: function () { return false; }, set: function () {}, add: function () {}, watch: function () {},
                                env: function () {}, state: function () {}, since: function () { return {}; } };
  var SETTINGS_KEY = "pluteus-reader";
  // spread: "single" = one centered page (the default; pleasant to read and right for books with long tables of contents),
  //         "double" = two pages side by side on a wide screen.
  // font and line "book": the book's own (its embedded fonts and spacing); anything else is the reader's choice and replaces them.
  // v: 2 since the book's own became the default. Earlier readers saved their defaults (font "theme", line "1.55") with every
  // change of any setting, so those two values in a v1 record are moved to the book's own once; a later choice is kept.
  var settings = { size: 100, font: "book", line: "book", flow: "paginated", spread: "single", v: 2 };
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); } catch (e) { /* defaults */ }
  if (saved && typeof saved === "object") Object.assign(settings, saved);
  function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* ignore */ } }
  if (saved && !saved.v) {
    if (settings.font === "theme") settings.font = "book";
    if (settings.line === "1.55") settings.line = "book";
    settings.v = 2;
    saveSettings();
  }
  if (settings.spread !== "double") settings.spread = "single";

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
    var ownFont = settings.font === "book", ownLine = settings.line === "book";
    var html = contents.document.documentElement, style = html.style;
    Object.keys(vars).forEach(function (k) { style.setProperty(k, vars[k]); });
    html.classList.toggle("pl-set-font", !ownFont);                    // reader-content.css replaces the book's font / spacing only then
    html.classList.toggle("pl-set-line", !ownLine);
    // The theme's reader-content.css can arrive late (the page is busy counting pages), and a white flash in a
    // dark scheme hurts. So the essentials are also set inline, at once; the stylesheet adds the rest.
    var body = contents.document.body;
    style.setProperty("background-color", vars["--pl-book-bg"], "important");
    // On a touch screen a tap on a word turns the page; if the text were selectable, the phone's browser would also pick the word and
    // open its "explain this word" panel (Chrome's Touch to Search). With a mouse the text stays selectable, for copying a line.
    if (window.matchMedia && matchMedia("(pointer: coarse)").matches) {
      style.setProperty("-webkit-user-select", "none", "important");
      style.setProperty("user-select", "none", "important");
      style.setProperty("-webkit-touch-callout", "none", "important");
    }
    if (body) {
      body.style.setProperty("background-color", vars["--pl-book-bg"], "important");
      body.style.setProperty("color", vars["--pl-book-fg"], "important");
      if (ownFont) body.style.removeProperty("font-family"); else body.style.setProperty("font-family", vars["--pl-book-font"], "important");
      if (ownLine) body.style.removeProperty("line-height"); else body.style.setProperty("line-height", vars["--pl-book-line"], "important");
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
  /* The saved place could not be asked for (no connection, a server error): the book then opens at its start, and saving that would
     wipe the real place. So nothing is saved until the reader moves through the book themselves (a page turn, a chapter from the list). */
  var maySave = false, userMoved = false;
  function scheduleSave() {
    if (!maySave) return;
    clearTimeout(saveTimer); saveTimer = setTimeout(sendProgress, 1500);
  }
  function flush() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; sendProgress(true); } }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);

  function showProgress() {
    var pct = lastPercent;
    $("bar").style.width = pct == null ? "0%" : Math.round(pct * 100) + "%";
    $("pct").textContent = pct == null ? "–" : Math.round(pct * 100) + "%";
  }

  var lastLoc = {};
  function where() {                                      // for the tap log: the section and page the reader is on, and how the page strip is scrolled
    var out = {};
    try { out = { sec: lastLoc.sec, page: lastLoc.page, scroll: scrollFacts() }; } catch (e) { /* the log only */ }
    return out;
  }
  function scrollFacts() {                                // the numbers behind "is there another page here?": fractions show up on some screens
    var m = rendition.manager, c = m && m.container;
    return c ? c.scrollLeft + "/" + c.scrollWidth + " w=" + c.offsetWidth + " step=" + (m.layout && m.layout.delta) : "-";
  }
  L.state(where);
  rendition.on("relocated", function (loc) {
    lastLoc = { sec: loc.start.index, page: loc.start.displayed.page + "/" + loc.start.displayed.total };
    L.add("relocated", { sec: lastLoc.sec, page: lastLoc.page, scroll: scrollFacts() });
    if (userMoved) maySave = true;                       // a place the reader went to themselves: theirs to save
    lastCfi = loc.start.cfi;
    if (book.locations.length()) lastPercent = book.locations.percentageFromCfi(lastCfi);
    showProgress();
    scheduleSave();
  });

  // Counting pages (for the percentage) parses the whole book and keeps the page busy, so it starts only once
  // the first page is on screen, and the result is remembered in this browser (keyed on the file's size + date).
  // The browser gives a site about 5 MB of this storage, and a long book's count is tens of kilobytes: so only the LOC_KEEP books opened
  // last keep theirs. A small index (key -> when last used) tells which those are; every access is guarded, the cache is a convenience.
  var locKey = "pluteus-loc:" + cfg.key, LOC_INDEX = "pluteus-loc-index", LOC_KEEP = 30;
  function locTidy(keep) {                                 // marks this book as used now and drops all but the `keep` newest counts
    try {
      var index = null, i, k;
      try { index = JSON.parse(localStorage.getItem(LOC_INDEX) || "{}"); } catch (e) { /* a damaged index: started again */ }
      if (!index || typeof index !== "object" || Array.isArray(index)) index = {};
      for (i = 0; i < localStorage.length; i++) {          // counts from before there was an index: the oldest of all
        k = localStorage.key(i);
        if (k && k.indexOf("pluteus-loc:") === 0 && typeof index[k] !== "number") index[k] = 0;
      }
      Object.keys(index).forEach(function (key) { if (localStorage.getItem(key) == null) delete index[key]; });
      if (localStorage.getItem(locKey) != null) index[locKey] = Date.now();
      Object.keys(index).sort(function (a, b) { return index[b] - index[a]; }).slice(keep)
        .forEach(function (key) { localStorage.removeItem(key); delete index[key]; });
      localStorage.setItem(LOC_INDEX, JSON.stringify(index));
    } catch (e) { /* storage blocked or full */ }
  }
  function locStore(value) {
    try { localStorage.setItem(locKey, value); }
    catch (e) {                                            // full: make room once and try again
      locTidy(Math.floor(LOC_KEEP / 3));
      try { localStorage.setItem(locKey, value); } catch (e2) { return; }
    }
    locTidy(LOC_KEEP);
  }
  function countPages() {
    book.ready.then(function () {
      var cached = null;
      try { cached = localStorage.getItem(locKey); } catch (e) { /* ignore */ }
      if (cached) { book.locations.load(cached); locTidy(LOC_KEEP); return; }
      return book.locations.generate(1600).then(function () {
        var counted = null;
        try { counted = book.locations.save(); } catch (e) { /* ignore */ }
        if (counted) locStore(counted);
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
      a.addEventListener("click", function (e) { e.preventDefault(); closeMenus(); userMoved = true; rendition.display(item.href); });
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
  /* One turn at a time. epub.js queues every turn, and the step into the next chapter is slow (that chapter is loaded and laid out
     first: a quarter of a second on a slowed-down computer, longer on a phone). A second tap made while it loaded ran afterwards and
     skipped the new chapter's first page. So a turn asked for while one is still running is dropped, whether it comes from a tap, a
     swipe, a key, the wheel or a button. Inside a chapter a turn is over in a few hundredths of a second, so quick taps still each turn. */
  var turning = false, turnGuard = null;
  function turn(forward) {
    closeMenus();
    if (turning) { L.add("turn DROPPED (one is running)", Object.assign({ fwd: forward ? 1 : 0 }, L.since())); return; }
    turning = true;
    userMoved = true;
    var began = performance.now();
    L.add("turn", Object.assign({ fwd: forward ? 1 : 0, rtl: rtl ? 1 : 0, at: lastLoc.sec + " " + lastLoc.page, scroll: scrollFacts() }, L.since()));
    root.classList.add("pl-turning");                    // visible from outside (the browser tests wait for a turn to end)
    var ended = false;
    function done(how) {
      if (ended) return;
      ended = true;
      L.add("turn done", { ms: Math.round(performance.now() - began), by: typeof how === "string" ? how : "epub.js" });
      turning = false; clearTimeout(turnGuard); root.classList.remove("pl-turning");
    }
    turnGuard = setTimeout(function () { done("TIMEOUT 4 s"); }, 4000);                  // never stuck, should epub.js not answer
    Promise.resolve(forward !== rtl ? rendition.next() : rendition.prev()).then(function () { done(); }, function () { done("error"); });
  }
  function next() { turn(true); }
  function prev() { turn(false); }
  $("next").addEventListener("click", next);
  $("prev").addEventListener("click", prev);
  // a tap on the page (tapzones.js): upper part back, lower part forward. The book's text is a frame of its own, so its taps are passed on
  // here (a link in the book still works: tapzones.js leaves taps on links alone). In the scrolling layout a tap turns nothing.
  var taps = window.plTapZones({
    stage: document.querySelector(".pl-reader-stage"), back: prev, forward: next,
    enabled: function () { return settings.flow === "paginated"; },
    menuOpen: function () { return !$("toc").hidden || !$("settings").hidden; }, closeMenus: closeMenus,
    note: function (what, detail) { L.add("tap decided", what + (detail ? "  " + detail : "")); }
  });
  rendition.hooks.content.register(function (contents) { taps.watch(contents.document); L.watch(contents.document, "book"); });
  rendition.on("click", function (e) {
    var frame = e.view && e.view.frameElement;
    if (!frame || e.defaultPrevented || e.button) { L.add("click from the book's page not used", { prevented: e.defaultPrevented ? 1 : 0, button: e.button }); return; }
    taps.tap(frame.getBoundingClientRect().top + e.clientY, e.target, "book frame");
  });

  function onKey(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;      // the browser's own: Alt+Left is Back
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    var key = e.key;
    if (key === "ArrowRight" || key === "PageDown" || (key === " " && !e.shiftKey)) { e.preventDefault(); next(); }
    else if (key === "ArrowLeft" || key === "PageUp" || (key === " " && e.shiftKey)) { e.preventDefault(); prev(); }
    else if (key === "Escape") closeMenus();
  }
  document.addEventListener("keydown", onKey);
  rendition.on("keydown", onKey);                       // keys pressed while the book page has focus

  /* The mouse wheel (and a touchpad) turns pages in the page-by-page view; one page per gesture, however many wheel events it sends.
     In the scrolling view the wheel scrolls as usual, and Ctrl + wheel stays the browser's zoom. */
  var wheelSum = 0, wheelLast = 0, wheelLock = 0;
  function onWheel(e) {
    if (e.ctrlKey || !rendition.manager) return;                                               // Ctrl + wheel is the browser's zoom; nothing before the book is shown
    if (settings.flow !== "paginated") {
      /* The scrolling layout: a wheel over the book's own page is not passed on to the box that scrolls it (the browser keeps it
         with the inner page, which has nothing to scroll), so it is forwarded here. Over the rest of the page the browser scrolls. */
      var box = e.view && e.view !== window ? document.querySelector(".epub-container") : null;
      if (box) {
        var unit = e.deltaMode === 1 ? 33 : (e.deltaMode === 2 ? box.clientHeight : 1);
        e.preventDefault();
        box.scrollBy(e.deltaX * unit, e.deltaY * unit);
      }
      return;
    }
    if (e.target && e.target.closest && e.target.closest("#toc, #settings")) return;        // the two menus scroll themselves
    e.preventDefault();
    var now = Date.now(), delta = (e.deltaY || e.deltaX || 0) * (e.deltaMode === 1 ? 33 : 1);
    if (now < wheelLock) return;
    if (now - wheelLast > 250) wheelSum = 0;                                                  // a new gesture
    wheelLast = now;
    wheelSum += delta;
    if (Math.abs(wheelSum) < 40) return;
    var forward = wheelSum > 0;
    wheelSum = 0;
    wheelLock = now + 350;
    (forward ? next : prev)();
  }
  document.addEventListener("wheel", onWheel, { passive: false });
  rendition.hooks.content.register(function (contents) {                                        // a wheel over the book page itself
    contents.document.addEventListener("wheel", onWheel, { passive: false });
  });

  // a sideways swipe on the book's page (a frame of its own, so measured in screen pixels); a pinch is no swipe (tapzones.js)
  var swipe = window.plSwipe({ forward: function () { L.add("swipe", "forward"); next(); }, back: function () { L.add("swipe", "back"); prev(); },
                               min: 50, screen: true });
  rendition.on("touchstart", swipe.start);
  rendition.on("touchend", swipe.end);

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
    $("turnlog").checked = L.on();
  }
  function changeSize(delta) {
    settings.size = Math.min(220, Math.max(60, settings.size + delta));
    saveSettings(); syncControls(); rendition.themes.fontSize(settings.size + "%");
  }
  $("size-down").addEventListener("click", function () { changeSize(-10); });
  $("size-up").addEventListener("click", function () { changeSize(10); });
  $("turnlog").addEventListener("change", function () {            // the opt-in tap log (turnlog.js): a bug report tool, off by default
    L.set(this.checked);
    L.env({ flow: settings.flow, spread: settings.spread, size: settings.size });
  });
  L.env({ flow: settings.flow, spread: settings.spread, size: settings.size });
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
    .then(function (r) {                                 // 404: nothing saved yet, which is an answer; any other failure is not
      if (r.ok) return r.json();
      if (r.status === 404) return {};
      throw new Error("status " + r.status);
    })
    .then(function (saved) { maySave = true; return saved; }, function () { return {}; })
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
