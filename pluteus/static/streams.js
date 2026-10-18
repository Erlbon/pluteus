/* A folder of station files: the search box and the country chips hide tiles, Random opens one of the tiles that are showing. */
(function () {
  "use strict";
  var list = document.getElementById("streams");
  if (!list) return;
  var tiles = Array.prototype.slice.call(list.querySelectorAll(".pl-stream"));
  var box = document.getElementById("stream-search"), count = document.getElementById("stream-count"), none = document.getElementById("stream-none");
  var chips = Array.prototype.slice.call(document.querySelectorAll(".pl-stream-chips .pl-chip"));
  var country = "";

  function apply() {
    var words = (box.value || "").toLowerCase().split(/\s+/).filter(Boolean), shown = 0;
    tiles.forEach(function (tile) {
      var name = tile.getAttribute("data-name") || "";
      var ok = (!country || tile.getAttribute("data-country") === country) && words.every(function (w) { return name.indexOf(w) !== -1; });
      tile.hidden = !ok;
      if (ok) shown++;
    });
    count.textContent = shown;
    none.hidden = shown !== 0;
  }

  box.addEventListener("input", apply);
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      country = chip.getAttribute("data-country") || "";
      chips.forEach(function (c) { c.classList.toggle("pl-chip-on", c === chip); });
      apply();
    });
  });
  var random = document.getElementById("stream-random");
  if (random) random.addEventListener("click", function () {
    var visible = tiles.filter(function (t) { return !t.hidden; });
    if (!visible.length) return;
    var link = visible[Math.floor(Math.random() * visible.length)].querySelector("a");
    if (link) location.href = link.href;
  });
})();
