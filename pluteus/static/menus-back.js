/* The Back button (or gesture) closes an open menu first, instead of leaving the page.
   It watches the reader's menus (.pl-menu): when one opens, a history entry is added; Back then removes that entry and the
   menu is closed; when a menu is closed some other way (its button, Escape, picking an item) the extra entry is removed
   again, so the history never fills up. The reader's own code does not need to know about this. */
(function () {
  "use strict";
  var menus = Array.prototype.slice.call(document.querySelectorAll(".pl-menu"));
  if (!menus.length || !window.history || !history.pushState || !window.MutationObserver) return;
  var pushed = false;

  function anyOpen() { return menus.some(function (m) { return !m.hidden; }); }

  function sync() {
    if (anyOpen() && !pushed) {
      history.pushState({ plMenu: true }, "");
      pushed = true;
    } else if (!anyOpen() && pushed) {
      pushed = false;
      history.back();                                   // take the entry we added off again
    }
  }

  var watcher = new MutationObserver(sync);
  menus.forEach(function (m) { watcher.observe(m, { attributes: true, attributeFilter: ["hidden"] }); });

  window.addEventListener("popstate", function () {
    if (!pushed) return;                                // our own history.back() above, or a real navigation: nothing to do
    pushed = false;                                     // Back was pressed with a menu open: just close the menu(s)
    menus.forEach(function (m) { m.hidden = true; });
  });
})();
