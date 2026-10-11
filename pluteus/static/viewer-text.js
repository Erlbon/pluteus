/* pluteus text viewer: text size and line wrapping, remembered in this browser. */
(function () {
  "use strict";
  var text = document.getElementById("text");
  if (!text) return;
  var SIZE = "pluteus-text-size", WRAP = "pluteus-text-wrap";
  var size = 13, wrap = false;
  try {
    size = Math.min(32, Math.max(10, parseInt(localStorage.getItem(SIZE), 10) || 13));
    wrap = localStorage.getItem(WRAP) === "1";
  } catch (e) { /* defaults */ }

  function apply() {
    text.style.fontSize = size + "px";
    text.classList.toggle("pl-wrap", wrap);
    document.getElementById("wrap-btn").setAttribute("aria-pressed", wrap ? "true" : "false");
  }
  function remember() { try { localStorage.setItem(SIZE, String(size)); localStorage.setItem(WRAP, wrap ? "1" : "0"); } catch (e) { /* ignore */ } }

  document.getElementById("size-down").addEventListener("click", function () { size = Math.max(10, size - 1); remember(); apply(); });
  document.getElementById("size-up").addEventListener("click", function () { size = Math.min(32, size + 1); remember(); apply(); });
  document.getElementById("wrap-btn").addEventListener("click", function () { wrap = !wrap; remember(); apply(); });
  apply();
  text.focus({ preventScroll: true });                   // so the arrow and page keys scroll the text at once
})();
