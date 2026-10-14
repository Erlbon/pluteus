/* "Play all" for a folder page shown inside the shell: asks the server for every audio file of the folder and the folders below it,
   and hands the list to the player in the shell (window.parent.plShell), which keeps playing while you browse or read. */
(function () {
  "use strict";
  var shell = null;
  try { shell = window.parent !== window ? window.parent.plShell : null; } catch (e) { shell = null; }
  var main = document.querySelector("main[data-queue]");
  if (!shell || !main) return;
  var heading = main.querySelector("h1");
  if (!heading) return;
  var button = document.createElement("button");
  button.type = "button";
  button.className = "pl-btn pl-play-all";
  button.textContent = "Play all";
  button.hidden = true;
  heading.insertAdjacentElement("afterend", button);
  var items = null;
  fetch(main.dataset.queue, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : { items: [] }; })
    .then(function (data) {
      items = (data && data.items) || [];
      if (!items.length) return;
      button.textContent = "Play all (" + items.length + (data.truncated ? "+" : "") + ")";
      button.hidden = false;
    }).catch(function () { /* no button */ });
  button.addEventListener("click", function () { if (items && items.length) shell.playQueue(items, 0); });
})();
