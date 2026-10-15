/* The note of a folder is fetched after the folder page is up and shown under the list, so the list never waits for it (or for a slow drive).
   The server answers with the finished, already escaped boxes; without this script the folder page simply has no note. */
(function () {
  "use strict";
  var box = document.getElementById("folder-notes");
  if (!box || !box.dataset.src) return;
  fetch(box.dataset.src, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && d.html) box.innerHTML = d.html; })
    .catch(function () { /* no note: the list is all there is */ });
})();
