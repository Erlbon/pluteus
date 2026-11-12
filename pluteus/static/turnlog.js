/* pluteus: a log of taps, keys, swipes and page turns for a bug report (the double page turn, a last page that is skipped). OFF unless the
   reader's settings switch it on ("Tap log"); the choice is kept in this browser only. While it is off nothing is recorded or stored and
   no button is shown. While it is on, the last 600 lines are kept in memory and in this tab's session storage (so they survive a reload),
   and a small "Log" button opens a panel to Mark the moment something went wrong, Copy the text or Clear it. Nothing is ever sent
   anywhere, and no book text is recorded: only times, positions, pixel sizes and the kind of event.
   Reader scripts call window.plTurnLog: on(), set(bool), add(type, details), watch(document, name), env(), state(fn), dump(), clear(). */
(function () {
  "use strict";
  var FLAG = "pluteus-turnlog", LINES = "pluteus-turnlog-lines", MAX = 600;
  var enabled = false, lines = [], t0 = performance.now(), saveTimer = null, button = null, panel = null, box = null;
  var stateFn = null, lastInput = null;
  try { enabled = localStorage.getItem(FLAG) === "1"; } catch (e) { /* off */ }
  if (enabled) {
    try { lines = JSON.parse(sessionStorage.getItem(LINES) || "[]"); if (!Array.isArray(lines)) lines = []; } catch (e) { lines = []; }
  }

  function pad(n, width) { var s = String(n); while (s.length < width) s = " " + s; return s; }
  function text(details) {
    if (details == null) return "";
    if (typeof details !== "object") return String(details);
    return Object.keys(details).map(function (k) { return k + "=" + details[k]; }).join(" ");
  }
  function persist() {
    if (saveTimer) return;
    saveTimer = setTimeout(function () {
      saveTimer = null;
      try { sessionStorage.setItem(LINES, JSON.stringify(lines)); } catch (e) { /* full or blocked: the memory copy still works */ }
    }, 400);
  }
  function flush() {                                      // at once, when the page is being left: the last moments are the ones that matter
    if (!enabled) return;
    clearTimeout(saveTimer); saveTimer = null;
    try { sessionStorage.setItem(LINES, JSON.stringify(lines)); } catch (e) { /* ignore */ }
  }
  window.addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
  function add(type, details) {
    if (!enabled) return;
    lines.push(pad(Math.round(performance.now() - t0), 7) + " ms  " + type + (details == null || details === "" ? "" : "  " + text(details)));
    if (lines.length > MAX) lines.splice(0, lines.length - MAX);
    persist();
    if (box && panel && !panel.hidden) { box.value = lines.join("\n"); box.scrollTop = box.scrollHeight; }
  }
  function dump() { return lines.join("\n"); }
  function clear() { lines.length = 0; persist(); if (box) box.value = ""; }

  /* the facts that explain most page-turn problems: the screen's pixel ratio (a fractional one makes scroll positions fractional) and size */
  function env(extra) {
    if (!enabled) return;
    var vv = window.visualViewport;
    add("env", {
      ua: navigator.userAgent.replace(/^Mozilla\/5\.0 /, ""), dpr: window.devicePixelRatio,
      inner: window.innerWidth + "x" + window.innerHeight, screen: screen.width + "x" + screen.height,
      vv: vv ? vv.width.toFixed(1) + "x" + vv.height.toFixed(1) + "@" + vv.scale.toFixed(2) : "-",
      touch: navigator.maxTouchPoints, orient: screen.orientation ? screen.orientation.type : "-"
    });
    if (extra) add("env", extra);
  }

  /* every raw input event, as the page receives it, before any reader code looks at it (capture phase), so a single physical tap that
     arrives twice (once in the page, once from the book's frame) or a tap that arrives late shows up as it really happened */
  var TYPES = ["pointerdown", "pointerup", "pointercancel", "touchstart", "touchend", "touchcancel", "click", "dblclick", "contextmenu",
               "keydown"];
  function input(e, name) {
    if (!enabled || (panel && e.target && panel.contains && panel.contains(e.target)) || (button && e.target === button)) return;
    var now = performance.now(), d = { in: name }, t = e.target;
    d.lag = Math.round(now - e.timeStamp);                 // how long the page was busy before it could look at the event
    if (!e.isTrusted) d.synthetic = 1;
    if (e.type === "keydown") { d.key = e.key; }
    else {
      var p = e.changedTouches && e.changedTouches[0] ? e.changedTouches[0] : e;
      if (p.clientX != null) { d.x = Math.round(p.clientX); d.y = Math.round(p.clientY); }
      if (e.pointerType) d.ptr = e.pointerType;
      if (e.type === "click" || e.type === "dblclick") d.detail = e.detail;
      if (e.touches) d.fingers = e.touches.length;
    }
    if (t && t.tagName) d.on = t.tagName.toLowerCase() + (t.id ? "#" + t.id : "");
    if (e.defaultPrevented) d.prevented = 1;
    add(e.type, d);
    lastInput = { type: e.type, at: now, name: name };
  }
  function watch(doc, name) {
    if (!doc || doc.__plTurnLog) return;
    doc.__plTurnLog = true;
    TYPES.forEach(function (type) { doc.addEventListener(type, function (e) { input(e, name); }, true); });
  }
  function since() { return lastInput ? { last: lastInput.type, ago: Math.round(performance.now() - lastInput.at), from: lastInput.name } : {}; }

  /* ---- the button and the panel (only while the log is on) ---- */
  function make(tag, cls, label) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (label) el.textContent = label;
    return el;
  }
  function copy() {
    box.value = dump();
    box.focus(); box.select();
    var done = false;
    try { done = document.execCommand("copy"); } catch (e) { /* below */ }          // also works where the page is not a secure context (plain http)
    if (!done && navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(dump()).then(function () { note.textContent = "Copied."; }); return; }
    note.textContent = done ? "Copied." : "Select the text and copy it by hand.";
  }
  var note = null;
  function build() {
    if (button || !document.body) return;
    button = make("button", "pl-turnlog-btn", "Log");
    button.type = "button";
    button.title = "Tap log: mark a moment, copy the log";
    panel = make("div", "pl-turnlog");
    panel.hidden = true;
    var head = make("div", "pl-turnlog-head");
    head.appendChild(make("strong", "", "Tap log"));
    note = make("span", "pl-turnlog-note", "No book text is recorded.");
    head.appendChild(note);
    box = make("textarea", "pl-turnlog-text");
    box.readOnly = true;
    box.rows = 10;
    var row = make("div", "pl-turnlog-row");
    [["Mark", function () {
        add("MARK", stateFn ? stateFn() : "");
        note.textContent = "Marked: " + lines.length + " lines.";
      }], ["Copy", copy], ["Clear", function () { clear(); note.textContent = "Cleared."; }],
     ["Close", function () { panel.hidden = true; }]].forEach(function (b) {
      var el = make("button", "pl-btn", b[0]);
      el.type = "button";
      el.addEventListener("click", b[1]);
      row.appendChild(el);
    });
    panel.appendChild(head); panel.appendChild(box); panel.appendChild(row);
    button.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { box.value = dump(); box.scrollTop = box.scrollHeight; }
    });
    document.body.appendChild(button);
    document.body.appendChild(panel);
  }
  function unbuild() {
    [button, panel].forEach(function (el) { if (el && el.parentNode) el.parentNode.removeChild(el); });
    button = panel = box = note = null;
  }
  function set(on) {
    on = !!on;
    if (on === enabled) return;
    try { localStorage.setItem(FLAG, on ? "1" : "0"); } catch (e) { /* only for this page view */ }
    if (on) {
      enabled = true;
      build();
      watch(document, "page");
      add("log", "switched on");
      env();
    } else {
      add("log", "switched off");
      enabled = false;
      lines = [];
      try { sessionStorage.removeItem(LINES); } catch (e) { /* ignore */ }
      unbuild();
    }
  }

  window.plTurnLog = {
    on: function () { return enabled; }, set: set, add: add, watch: watch, env: env, dump: dump, clear: clear, since: since,
    state: function (fn) { stateFn = fn; }
  };
  if (enabled) {
    if (document.body) build(); else document.addEventListener("DOMContentLoaded", build);
    watch(document, "page");
    add("page loaded", location.pathname.replace(/\/[^/]*$/, "/…"));          // the folder only, never the book's name
  }
})();
