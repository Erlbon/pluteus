/* pluteus TV mode: arrow keys (a remote's D-pad) move the focus to the nearest button or link in that direction, Enter presses it,
   Back (Escape, Backspace, the remote's Back key) goes back. Loaded by scheme.js only when TV mode is on for this browser (?tv=1 on any
   address turns it on, ?tv=0 turns it off). The readers and viewers keep their own keys. The frame and the bar around it hand the focus
   to each other, so one remote reaches both. */
(function () {
  "use strict";
  var root = document.documentElement;
  var FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
  function own() { return !!(document.body && document.body.classList.contains("pl-reader-page")); }        // a reader or viewer: its own arrow keys stay

  function visible(el) {
    if (el.hidden || el.closest("[hidden]")) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    var s = window.getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none";
  }
  function candidates() { return Array.prototype.filter.call(document.querySelectorAll(FOCUSABLE), visible); }
  function gap(aLow, aHigh, bLow, bHigh) { return aHigh < bLow ? bLow - aHigh : (bHigh < aLow ? aLow - bHigh : 0); }

  /* the best element in a direction from the current one: close along the direction, and lined up across it */
  function nearest(from, dir) {
    var a = from.getBoundingClientRect(), best = null, bestScore = Infinity;
    candidates().forEach(function (el) {
      if (el === from || from.contains(el) || el.contains(from)) return;
      var b = el.getBoundingClientRect(), along, across;
      if (dir === "right") { along = b.left - a.right; across = gap(a.top, a.bottom, b.top, b.bottom); if (b.left + b.width / 2 <= a.left + a.width / 2) return; }
      else if (dir === "left") { along = a.left - b.right; across = gap(a.top, a.bottom, b.top, b.bottom); if (b.left + b.width / 2 >= a.left + a.width / 2) return; }
      else if (dir === "down") { along = b.top - a.bottom; across = gap(a.left, a.right, b.left, b.right); if (b.top + b.height / 2 <= a.top + a.height / 2) return; }
      else { along = a.top - b.bottom; across = gap(a.left, a.right, b.left, b.right); if (b.top + b.height / 2 >= a.top + a.height / 2) return; }
      var score = Math.max(0, along) + 3 * across;
      if (score < bestScore) { bestScore = score; best = el; }
    });
    return best;
  }
  function focusEl(el) {
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e) { /* old browser */ }
  }
  /* where the focus lands when it comes into this document from the other one */
  window.plTvEnter = function (side) {
    var list = candidates();
    if (!list.length) return false;
    list.sort(function (x, y) {
      var a = x.getBoundingClientRect(), b = y.getBoundingClientRect();
      return side === "bottom" ? (b.top - a.top) || (a.left - b.left) : (a.top - b.top) || (a.left - b.left);
    });
    focusEl(list[0]);
    return true;
  };
  function handOver(dir) {
    try {
      if (window.parent !== window && dir === "up" && window.parent.plTvEnter) { window.parent.focus(); return window.parent.plTvEnter("bottom"); }
      var frame = document.getElementById("shell-frame");
      if (frame && dir === "down" && frame.contentWindow && frame.contentWindow.plTvEnter) { frame.contentWindow.focus(); return frame.contentWindow.plTvEnter("top"); }
    } catch (e) { /* the other document is not there */ }
    return false;
  }

  var DIRS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    var active = document.activeElement, tag = active && active.tagName ? active.tagName.toLowerCase() : "";
    var typing = tag === "textarea" || (tag === "input" && !/^(button|submit|checkbox|radio|range|image|reset)$/i.test(active.type || "text"));
    if ((e.key === "Escape" || e.key === "GoBack" || e.key === "BrowserBack" || (e.key === "Backspace" && !typing)) && !own()) {
      if (window.history.length > 1) { e.preventDefault(); window.history.back(); }
      return;
    }
    var dir = DIRS[e.key];
    if (!dir || own()) return;
    if (tag === "select" || tag === "video" || tag === "audio" || (tag === "input" && active.type === "range" && (dir === "left" || dir === "right"))) return;
    if (typing && (dir === "left" || dir === "right")) return;
    if (tag === "textarea") return;
    var from = active && active !== document.body && active !== document.documentElement ? active : null;
    if (!from) { if (window.plTvEnter("top")) e.preventDefault(); return; }
    var next = nearest(from, dir);
    if (next) { e.preventDefault(); focusEl(next); return; }
    if (handOver(dir)) e.preventDefault();
  });

  /* a page opened in the frame starts with the focus on something, so the remote works at once */
  function start() {
    if (!root.classList.contains("pl-tv") || own()) return;
    if (window.parent === window || (document.activeElement && document.activeElement !== document.body)) return;
    var first = document.querySelector("main .pl-list .pl-row:not(.pl-up) a.pl-title, main .pl-tile a, main .pl-station, main .pl-card a, main .pl-card button");
    if (first && visible(first)) focusEl(first); else window.plTvEnter("top");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
