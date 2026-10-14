/* pluteus live channels: plays the stream of a station file in the video element, with the movie player's look.
   - HLS (.m3u8) goes through the vendored hls.js where the browser cannot play it itself (Safari and some phones can, natively)
   - a file with several channels shows them as a list; a tap switches channel
   - the big round button plays or leaves the broadcast (a live stream has no position: play joins it as it is now)
   - the stream server must allow playback from a web page (CORS); when it does not, or is off the air, the page says so
   Without this script the page still has the video element with the browser's own controls. */
(function () {
  "use strict";
  var video = document.getElementById("tv"), root = document.getElementById("reader");
  if (!video || !root) return;
  var now = document.getElementById("now"), statusEl = document.getElementById("status"), statusTimer = null;
  var playBtn = document.getElementById("play"), box = document.querySelector(".pl-videobox");
  var stations = Array.prototype.slice.call(document.querySelectorAll(".pl-station"));
  var hls = null, url = "", wanted = false, viaRelay = false, relay = video.dataset.relay || "";   // relay: this channel's playlist through the server ('' when it is off)
  document.documentElement.classList.add("pl-js");
  video.controls = false;

  function say(text, ms) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = !text;
    clearTimeout(statusTimer);
    if (ms) statusTimer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }
  function paint() {
    var icon = playBtn.querySelector(".pl-icon");
    if (icon) icon.className = "pl-icon " + (wanted ? "pl-i-pause" : "pl-i-play");
    playBtn.setAttribute("aria-label", wanted ? "Leave the broadcast" : "Play");
  }
  function release() {
    if (hls) { try { hls.destroy(); } catch (e) { /* ignore */ } hls = null; }
    video.removeAttribute("src");
    video.load();
  }
  function failed() {
    if (relay && !viaRelay) { viaRelay = true; start(); return; }          // the direct stream failed (no CORS, refused): once through the server
    wanted = false; paint();
    say(relay ? "The stream did not start, not even through the server. It may be off the air, or in a format this browser cannot play."
              : "The stream did not start. Its server may be off the air, may not allow playback from a web page, or the picture is in a format this browser cannot play.", 12000);
  }
  function start() {
    release();
    // a plain http channel on this secure page cannot play directly: through the server when it offers that (the relay)
    if (relay && !viaRelay && location.protocol === "https:" && /^http:/i.test(url)) viaRelay = true;
    if (location.protocol === "https:" && /^http:/i.test(url) && !(relay && viaRelay)) {
      wanted = false; paint();
      say("This channel is on plain http and the browser blocks it on this secure page. Open the site over http on the local network, or use a channel with an https address.", 14000);
      return;
    }
    say(viaRelay ? "Connecting through the server…" : "Connecting…");
    wanted = true; paint();
    var source = viaRelay && relay ? relay : url;
    var hlsAddress = /[.]m3u8(?:$|[?#])/i.test(url);
    var play = function () { var p = video.play(); if (p && p.catch) p.catch(function (e) { if (e && e.name === "NotAllowedError") { wanted = false; paint(); say("Tap play to start", 4000); } }); };
    if (hlsAddress && window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ enableWorker: false, lowLatencyMode: false });
      hls.on(window.Hls.Events.MANIFEST_PARSED, play);
      hls.on(window.Hls.Events.ERROR, function (event, data) { if (data && data.fatal) failed(); });
      hls.loadSource(source);
      hls.attachMedia(video);
    } else {
      video.src = source;                                      // native HLS (Safari, iPhone, newer Android) or a plain stream
      play();
    }
  }
  function leave() { wanted = false; release(); paint(); say(""); }
  function toggle() { if (wanted) leave(); else start(); }

  video.addEventListener("playing", function () { say(""); wanted = true; paint(); });
  video.addEventListener("error", function () { if (video.getAttribute("src") && video.error) failed(); });
  video.addEventListener("play", function () {                  // a queue playing in the shell gives way to the picture
    try { if (window.parent !== window && window.parent.plShell) window.parent.plShell.pause(); } catch (e) { /* ignore */ }
  });
  playBtn.addEventListener("click", toggle);
  video.addEventListener("click", toggle);                      // like the movie player: a click on the picture plays or pauses

  /* full screen: the whole player (picture and controls); a double click on the picture does the same */
  var fsButton = document.getElementById("fs");
  function fullscreen() {
    if (!box) return;
    var inside = document.fullscreenElement || document.webkitFullscreenElement;
    if (inside) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
    if (box.requestFullscreen) box.requestFullscreen().catch(function () { /* refused */ });
    else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  }
  if (fsButton) fsButton.addEventListener("click", fullscreen);
  video.addEventListener("dblclick", function (e) { e.preventDefault(); fullscreen(); });

  /* volume (shown on wide screens only, see base.css): remembered in this browser; the icon is a mute switch */
  var volume = document.getElementById("volume"), mute = document.getElementById("mute");
  if (volume && mute) {
    var level = 1;
    try { var saved = parseFloat(localStorage.getItem("pluteus-media-volume")); if (saved >= 0 && saved <= 1) level = saved; } catch (e) { /* storage blocked */ }
    var fill = function () { volume.style.setProperty("--p", volume.value + "%"); };
    var show = function () {
      volume.value = String(Math.round((video.muted ? 0 : video.volume) * 100)); fill();
      var icon = mute.querySelector(".pl-icon");
      if (icon) icon.className = "pl-icon " + (video.muted || video.volume === 0 ? "pl-i-mute" : "pl-i-volume");
    };
    video.volume = level; show();
    volume.addEventListener("input", function () {
      video.muted = false; video.volume = volume.value / 100;
      try { localStorage.setItem("pluteus-media-volume", String(video.volume)); } catch (e) { /* storage blocked */ }
      show();
    });
    mute.addEventListener("click", function () {
      if (video.volume === 0) { video.muted = false; video.volume = 0.5; } else video.muted = !video.muted;
      show();
    });
  }

  stations.forEach(function (button) {
    button.addEventListener("click", function () {
      stations.forEach(function (b) { b.classList.toggle("pl-now", b === button); });
      if (now) now.textContent = button.dataset.name;
      url = button.dataset.url; relay = button.dataset.relay || ""; viaRelay = false;
      start();
    });
  });
  url = stations.length ? stations[0].dataset.url : video.dataset.url;
  if (stations.length) relay = stations[0].dataset.relay || "";

  /* in the shell, with "keep the sound" on: leaving this page hands the channel to the player bar as sound only (a link there brings
     the picture back); coming back to the page takes it from the bar again, so the sound is never doubled */
  var shell = null;
  try { shell = window.parent !== window ? window.parent.plShell : null; } catch (e) { shell = null; }
  if (shell && shell.currentUrl && shell.currentUrl() === url) { try { shell.stop(); } catch (e) { /* ignore */ } }
  if (shell && video.dataset.keepSound === "1") {
    window.addEventListener("pagehide", function () {
      if (!wanted || !url) return;
      try { shell.keepTv({ title: (now ? now.textContent : "") || "TV", url: url, folder: "Live TV", page: video.dataset.page || "", logo: video.dataset.logo || "", relay: relay, viaRelay: viaRelay }); } catch (e) { /* the shell is gone */ }
    });
  }
  start();
})();
