/* The Libraries tab's folder picker: lists sub-folders through /libraries/browse (folders only) and fills in the form. */
(function () {
  "use strict";
  var button = document.getElementById("lib-browse"), box = document.getElementById("lib-picker");
  var pathInput = document.getElementById("lib-path"), nameInput = document.getElementById("lib-name");
  if (!button || !box || !pathInput) return;
  var url = box.dataset.browse;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function row(text, cls, onClick) {
    var b = el("button", "pl-pick" + (cls ? " " + cls : ""), text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  function suggest(path) {
    var base = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
    return base.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
  }
  function show(message) {
    box.replaceChildren(el("div", "pl-picker-head", message), row("Start from the top", "", function () { load(""); }));
    box.hidden = false;
  }

  function render(d) {
    box.replaceChildren();
    box.appendChild(el("div", "pl-picker-head", d.path || "Choose a drive or folder to start from"));
    if (d.path) {
      box.appendChild(row("Use this folder", "pl-pick-use", function () {
        pathInput.value = d.path;
        if (nameInput && !nameInput.value) nameInput.value = suggest(d.path);
        box.hidden = true;
      }));
    }
    if (d.parent !== null && d.parent !== undefined) box.appendChild(row("← Up", "", function () { load(d.parent); }));
    d.dirs.forEach(function (dir) { box.appendChild(row("📁 " + dir.name, "", function () { load(dir.path); })); });
    if (!d.dirs.length) box.appendChild(el("div", "pl-picker-head", "No sub-folders here."));
    box.hidden = false;
  }
  function load(path) {
    fetch(url + "?path=" + encodeURIComponent(path || ""), { credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) { if (res.ok) render(res.body); else show(res.body.error || "That folder cannot be shown."); })
      .catch(function () { show("The folder list could not be loaded."); });
  }
  button.addEventListener("click", function () { load(pathInput.value.trim()); });
})();
