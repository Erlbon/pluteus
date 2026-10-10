/* Dark/light toggle. Runs in <head> (not deferred) so the saved choice is applied before the first paint.
   The choice lives in this browser only (localStorage); with none saved the system setting decides. */
(function () {
  var KEY = "pluteus-scheme", root = document.documentElement;
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") root.setAttribute("data-scheme", saved);
  } catch (e) { /* storage blocked: the system setting still works */ }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector(".pl-scheme");
    if (!button) return;
    button.addEventListener("click", function () {
      var now = root.getAttribute("data-scheme") ||
        (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var next = now === "dark" ? "light" : "dark";
      root.setAttribute("data-scheme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
    });
  });
})();
