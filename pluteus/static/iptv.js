/* pluteus live channels: plays the stream of a station file in the video element.
   - HLS (.m3u8) goes through the vendored hls.js where the browser cannot play it itself (Safari and some phones can, natively)
   - a file with several channels shows them as a list; a tap switches channel
   - the stream server must allow playback from a web page (CORS); when it does not, or is off the air, the page says so
   Without this script the page still has the video element: it plays what the browser can play. */
(function () {
  "use strict";
  var video = document.getElementById("tv"), root = document.getElementById("reader");
  if (!video || !root) return;
  var now = document.getElementById("now"), statusEl = document.getElementById("status"), statusTimer = null;
  var stations = Array.prototype.slice.call(document.querySelectorAll(".pl-station"));
  var hls = null;

  function say(text, ms) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = !text;
    clearTimeout(statusTimer);
    if (ms) statusTimer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }
  function release() {
    if (hls) { try { hls.destroy(); } catch (e) { /* ignore */ } hls = null; }
    video.removeAttribute("src");
    video.load();
  }
  function failed() {
    say("The stream did not start. Its server may be off the air, may not allow playback from a web page, or the picture is in a format this browser cannot play.", 12000);
  }
  function start(url) {
    release();
    say("Connecting…");
    if (location.protocol === "https:" && /^http:/i.test(url)) {
      say("This channel is on plain http and the browser blocks it on this secure page. Open the site over http on the local network, or use a channel with an https address.", 14000);
      return;
    }
    var hlsAddress = /[.]m3u8(?:$|[?#])/i.test(url);
    var play = function () { var p = video.play(); if (p && p.catch) p.catch(function () { say("Tap play to start", 4000); }); };
    if (hlsAddress && window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ enableWorker: false, lowLatencyMode: false });
      hls.on(window.Hls.Events.MANIFEST_PARSED, play);
      hls.on(window.Hls.Events.ERROR, function (event, data) { if (data && data.fatal) failed(); });
      hls.loadSource(url);
      hls.attachMedia(video);
    } else {
      video.src = url;                                         // native HLS (Safari, iPhone, newer Android) or a plain stream
      play();
    }
  }
  video.addEventListener("playing", function () { say(""); });
  video.addEventListener("play", function () {                  // a queue playing in the shell gives way to the picture
    try { if (window.parent !== window && window.parent.plShell) window.parent.plShell.pause(); } catch (e) { /* ignore */ }
  });
  video.addEventListener("error", function () { if (video.getAttribute("src")) failed(); });

  stations.forEach(function (button) {
    button.addEventListener("click", function () {
      stations.forEach(function (b) { b.classList.toggle("pl-now", b === button); });
      if (now) now.textContent = button.dataset.name;
      start(button.dataset.url);
    });
  });
  start(stations.length ? stations[0].dataset.url : video.dataset.url);
})();
