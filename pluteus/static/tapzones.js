/* pluteus readers (EPUB, comic, PDF, pictures): what a tap on the page does. The page is split from top to bottom, so either thumb
   reaches forward (a left hand could not reach the old right-hand edge): the upper part goes back a page, a band below it
   shows or hides the bottom bar (more room for the page), and the rest, where a thumb rests, goes forward. The top bar always stays: it
   holds the way back to the folder, and a tap that hid it left a phone with no way out but the Pluteus logo. The first 24 px under the bar do nothing,
   so a tap that misses a bar button by a little turns no page. A tap while a menu is open only closes the menu; a tap on a link, a
   button or a field, a tap that ends a drag, and a tap that clears a text selection do nothing here. Swipes and keys stay with each reader. */
(function () {
  "use strict";
  var QUIET = 24, BACK = 0.30, BARS = 0.42;

  /* opts: stage (the page area), back(), forward(), and optionally enabled() (false: taps do nothing, say while zoomed), menuOpen() and
     closeMenus(), barsChanged() (after the bottom bar was shown or hidden: the page area changed size). Returns { tap(clientY, target), watch(doc) }:
     a reader whose page is a frame of its own (the EPUB reader) passes on the taps made in there, with clientY in this window. */
  window.plTapZones = function (opts) {
    var root = document.getElementById("reader"), hadSelection = false;

    function zone(clientY) {
      var box = opts.stage.getBoundingClientRect(), y = clientY - box.top;
      if (y < QUIET || y > box.height) return "";
      var share = y / box.height;
      return share < BACK ? "back" : (share < BARS ? "bars" : "forward");
    }
    function tap(clientY, target) {
      if (target && target.closest && target.closest("a, button, input, select, textarea, label, .pl-menu")) return false;
      if (hadSelection) { hadSelection = false; return false; }
      if (opts.menuOpen && opts.menuOpen()) { opts.closeMenus(); return true; }
      var where = zone(clientY);
      if (where === "bars") {
        root.classList.toggle("pl-bare");
        if (opts.barsChanged) opts.barsChanged();
        return true;
      }
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
})();
