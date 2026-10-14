/* "Play all" and "Shuffle" for a folder page shown inside the shell: asks the server for the audio files of the folder (and, as the user chose
   in Settings, the folders below it) and hands the list to the player in the shell (window.parent.plShell), which keeps playing while you browse
   or read. Shuffle asks for the list for a mix (a folder with a no-shuffle marker file and audiobooks are left out) and plays it in random order. */
(function () {
  "use strict";
  var shell = null;
  try { shell = window.parent !== window ? window.parent.plShell : null; } catch (e) { shell = null; }
  var main = document.querySelector("main[data-queue]");
  if (!shell || !main) return;
  var heading = main.querySelector("h1");
  if (!heading) return;
  function make(label) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "pl-btn pl-play-all"; b.textContent = label; b.hidden = true;
    return b;
  }
  var button = make("Play all"), shuffle = make("Shuffle");
  shuffle.style.marginLeft = "8px";
  heading.insertAdjacentElement("afterend", shuffle);
  heading.insertAdjacentElement("afterend", button);
  var items = null;
  fetch(main.dataset.queue, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : { items: [] }; })
    .then(function (data) {
      items = (data && data.items) || [];
      if (!items.length) return;
      button.textContent = "Play all (" + items.length + (data.truncated ? "+" : "") + ")";
      button.hidden = false;
      shuffle.hidden = false;
    }).catch(function () { /* no buttons */ });
  button.addEventListener("click", function () { if (items && items.length) shell.playQueue(items, 0); });
  shuffle.addEventListener("click", function () {
    shuffle.disabled = true;
    fetch(main.dataset.queue + "?mix=1", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (data) {
        var list = ((data && data.items) || []).slice();
        for (var i = list.length - 1; i > 0; i--) {                       // Fisher-Yates
          var j = Math.floor(Math.random() * (i + 1)), t = list[i]; list[i] = list[j]; list[j] = t;
        }
        if (list.length) shell.playQueue(list, 0);
      }).catch(function () { /* nothing to play */ })
      .then(function () { shuffle.disabled = false; });
  });
})();
