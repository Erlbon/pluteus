/* Cover view: a cover that cannot be loaded (the file has none) is hidden, so the type icon behind it shows instead. */
(function () {
  "use strict";
  document.addEventListener("error", function (e) {
    var img = e.target;
    if (img && img.classList && img.classList.contains("pl-tile-img")) img.classList.add("pl-gone");
  }, true);
  /* An image that failed before this script ran */
  Array.prototype.forEach.call(document.querySelectorAll("img.pl-tile-img"), function (img) {
    if (img.complete && img.naturalWidth === 0) img.classList.add("pl-gone");
  });
})();
