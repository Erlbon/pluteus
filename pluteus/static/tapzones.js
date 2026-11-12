/* pluteus readers (EPUB, comic, PDF, pictures): what a tap on the page does. The page is split from top to bottom, so either thumb
   reaches forward (a left hand could not reach the old right-hand edge): the upper part goes back a page, and the rest, where a thumb
   rests, goes forward. The bars always stay (a band that hid them, 0.9.20 to 0.9.24, was more in the way than useful). The first 24 px
   under the bar do nothing, so a tap that misses a bar button by a little turns no page. A tap while a menu is open only closes the menu;
   a tap on a link, a button or a field, a tap that ends a drag, and a tap that clears a text selection do nothing here. The swipe that
   turns a page is here too (plSwipe, at the end); keys stay with each reader. */
(function () {
  "use strict";
  var QUIET = 24, BACK = 0.30;

  /* opts: stage (the page area), back(), forward(), and optionally enabled() (false: taps do nothing, say while zoomed), menuOpen() and
     closeMenus(). Returns { tap(clientY, target), watch(doc) }: a reader whose page is a frame of its own (the EPUB reader) passes on
     the taps made in there, with clientY in this window. */
  window.plTapZones = function (opts) {
    var hadSelection = false;

    function zone(clientY) {
      var box = opts.stage.getBoundingClientRect(), y = clientY - box.top;
      if (y < QUIET || y > box.height) return "";
      return y / box.height < BACK ? "back" : "forward";
    }
    function tap(clientY, target) {
      if (target && target.closest && target.closest("a, button, input, select, textarea, label, .pl-menu")) return false;
      if (hadSelection) { hadSelection = false; return false; }
      if (opts.menuOpen && opts.menuOpen()) { opts.closeMenus(); return true; }
      var where = zone(clientY);
      if (!where || (opts.enabled && !opts.enabled())) return false;
      (where === "back" ? opts.back : opts.forward)();
      return true;
    }
    function watch(doc) {                                   // remember at the press whether text was selected: the tap then only clears it
      doc.addEventListener("pointerdown", function () {
        var sel = doc.getSelection ? doc.getSelection() : null;
        hadSelection = !!(sel && !sel.isCollapsed && String(sel).trim());
      }, true);
    }
    watch(document);
    opts.stage.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button) return;
      if (tap(e.clientY, e.target)) e.preventDefault();
    });
    return { tap: tap, watch: watch };
  };

  /* A sideways swipe with one finger turns a page, the same in all four readers. Two fingers are a pinch, and a finger on a page the
     browser has zoomed (pinch zoom) is moving the picture: neither is a swipe, so a gesture that had a second finger down at any time, or
     that starts or ends while the page is zoomed, turns nothing.
     opts: forward(), back(), and optionally min (pixels the finger must travel, 60), screen (true: measure in screen pixels, for touches
     that come from inside the book's own frame) and enabled() (false: swipes do nothing, say while the reader's own zoom is on).
     Returns { start(event), end(event) } for the reader's touchstart and touchend. */
  function browserZoomed() {
    var views = [window.visualViewport];
    try { if (window.top !== window) views.push(window.top.visualViewport); } catch (e) { /* another site's frame */ }
    return views.some(function (v) { return !!v && v.scale > 1.01; });
  }
  window.plSwipe = function (opts) {
    var x = null, y = null, min = opts.min || 60;
    function point(e) { var t = e.changedTouches[0]; return opts.screen ? [t.screenX, t.screenY] : [t.clientX, t.clientY]; }
    return {
      start: function (e) {
        if ((e.touches && e.touches.length > 1) || browserZoomed()) { x = y = null; return; }     // a second finger: not a swipe any more
        var p = point(e); x = p[0]; y = p[1];
      },
      end: function (e) {
        if (x == null) return;
        var p = point(e), dx = p[0] - x, dy = p[1] - y;
        x = y = null;
        if ((e.touches && e.touches.length) || browserZoomed() || (opts.enabled && !opts.enabled())) return;
        if (Math.abs(dx) > min && Math.abs(dx) > 1.5 * Math.abs(dy)) (dx < 0 ? opts.forward : opts.back)();
      }
    };
  };
})();
