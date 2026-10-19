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

  /* ---- Edit logos: while it is on, a tile's camera button picks a picture and sends it to the server, which saves it beside the station file ---- */
  var edit = document.getElementById("stream-edit"), file = document.getElementById("stream-file"), status = document.getElementById("stream-status");
  if (!edit || !file) return;
  var target = null;

  function say(text) { status.textContent = text; status.hidden = !text; }
  edit.addEventListener("click", function () {
    var on = edit.getAttribute("aria-pressed") !== "true";
    edit.setAttribute("aria-pressed", on ? "true" : "false");
    list.classList.toggle("pl-editing", on);
    say(on ? "Edit logos is on: tap a camera button to set a logo. Stations do not play until you switch it off." : "");
  });
  list.addEventListener("click", function (e) {
    if (!list.classList.contains("pl-editing")) return;
    var button = e.target.closest(".pl-logo-edit");
    if (button) { target = button; file.value = ""; file.click(); }
    if (e.target.closest("a")) e.preventDefault();                            // no playing while editing
  });
  file.addEventListener("change", function () {
    var picture = file.files && file.files[0], button = target;
    if (!picture || !button) return;
    say("Saving the logo…");
    fetch(button.getAttribute("data-url"), { method: "POST", credentials: "same-origin", body: picture,
                                              headers: { "Content-Type": picture.type || "application/octet-stream" } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.body.error || "The logo could not be saved."); return; }
        var art = button.parentNode.querySelector(".pl-tile-art"), img = art.querySelector("img");
        if (!img) {
          img = document.createElement("img");
          img.className = "pl-tile-img pl-logo-img"; img.alt = "";
          art.insertBefore(img, art.firstChild);
        }
        img.classList.remove("pl-gone");
        img.src = res.body.url;
        say("Logo saved.");
      })
      .catch(function () { say("The logo could not be saved (no connection)."); });
  });
})();
