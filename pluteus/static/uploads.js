/* The upload folder page: confirm before delete, ask for the new name on rename, and upload with a progress bar.
   Every form also works without this script (a plain POST), so nothing here is required. */
(function () {
  "use strict";

  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.dataset.confirm)) e.preventDefault();
    });
  });

  document.querySelectorAll("form[data-rename]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      var name = window.prompt("New name", f.dataset.rename);
      if (name === null || name.trim() === "" || name === f.dataset.rename) { e.preventDefault(); return; }
      f.elements.name.value = name;
    });
  });

  var form = document.querySelector("form.pl-upload");
  if (!form || !window.FormData || !window.XMLHttpRequest) return;
  var status = form.querySelector(".pl-upload-status");
  var bar = status.querySelector("i");
  var text = status.querySelector("span");
  var button = form.querySelector("button");

  function say(message) { status.hidden = false; text.textContent = message; }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("Accept", "application/json");
    button.disabled = true;
    say("Uploading… 0%");
    xhr.upload.onprogress = function (ev) {
      if (!ev.lengthComputable) return;
      var pct = Math.round((ev.loaded / ev.total) * 100);
      bar.style.width = pct + "%";
      say("Uploading… " + pct + "%");
    };
    xhr.onerror = function () { button.disabled = false; say("The upload failed. Check the connection and try again."); };
    xhr.onload = function () {
      var result = null;
      try { result = JSON.parse(xhr.responseText); } catch (err) { /* not JSON */ }
      if (xhr.status !== 200 || !result) {
        button.disabled = false;
        say(xhr.status === 413 ? "That upload is too large." : "The upload was refused (" + xhr.status + ").");
        return;
      }
      var query = [];
      if (result.saved.length) query.push("saved=" + result.saved.length);
      if (result.failed.length) query.push("bad=" + encodeURIComponent(result.failed[0].code));
      window.location.href = window.location.pathname + (query.length ? "?" + query.join("&") : "");
    };
    xhr.send(new FormData(form));
  });
})();
