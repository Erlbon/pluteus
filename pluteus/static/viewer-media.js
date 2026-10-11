/* pluteus audio/video viewer: resumes where this user stopped (position saved per user on the server). */
(function () {
  "use strict";
  var root = document.getElementById("reader"), media = document.getElementById("media");
  if (!root || !media) return;
  var progressUrl = root.dataset.progress;
  var timer = null, lastSent = -1;

  function send(done, keepalive) {
    var t = media.currentTime || 0, d = media.duration || 0;
    if (!done && Math.abs(t - lastSent) < 1) return;
    lastSent = t;
    fetch(progressUrl, {
      method: "PUT", credentials: "same-origin", keepalive: !!keepalive,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locator: "t:" + (done ? 0 : t.toFixed(1)), percent: done ? 1 : (d > 0 ? t / d : 0) })
    }).catch(function () { /* offline: the next save tries again */ });
  }

  fetch(progressUrl, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .catch(function () { return {}; })
    .then(function (saved) {
      var m = saved && typeof saved.locator === "string" && /^t:(\d+(?:\.\d+)?)$/.exec(saved.locator);
      var at = m ? parseFloat(m[1]) : 0;
      if (at > 3) {
        var seek = function () { try { media.currentTime = at; } catch (e) { /* not seekable yet */ } };
        if (media.readyState >= 1) seek(); else media.addEventListener("loadedmetadata", seek, { once: true });
      }
    });

  media.addEventListener("play", function () { clearInterval(timer); timer = setInterval(function () { send(false); }, 5000); });
  media.addEventListener("pause", function () { clearInterval(timer); if (!media.ended) send(false); });
  media.addEventListener("ended", function () { clearInterval(timer); send(true); });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden" && !media.paused) send(false, true); });
  window.addEventListener("pagehide", function () { if (!media.paused) send(false, true); });
})();
