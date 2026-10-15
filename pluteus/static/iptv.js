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
  var hls = null, ts = null, tsToken = 0, mpegtsLoading = null, url = "", wanted = false, viaRelay = video.dataset.always === "1", always = video.dataset.always === "1", relay = video.dataset.relay || "";   // relay: this channel's playlist through the server ('' when it is off)
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
  /* an MPEG-TS channel (a panel such as Dispatcharr serves raw transport streams): mpegts.js, loaded when first needed */
  function needMpegts() {
    if (window.mpegts) return Promise.resolve();
    if (!mpegtsLoading) mpegtsLoading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = video.dataset.mpegts; s.onload = resolve; s.onerror = function () { mpegtsLoading = null; reject(); };
      document.head.appendChild(s);
    });
    return mpegtsLoading;
  }
  function release() {
    tsToken++;
    if (ts) { try { ts.pause(); ts.unload(); ts.detachMediaElement(); ts.destroy(); } catch (e) { /* ignore */ } ts = null; }
    if (hls) { try { hls.destroy(); } catch (e) { /* ignore */ } hls = null; }
    video.removeAttribute("src");
    video.load();
  }
  function failed() {
    if (relay && !viaRelay && !always) { viaRelay = true; start(); return; }          // the direct stream failed (no CORS, refused): once through the server
    wanted = false; paint();
    say(relay ? "The stream did not start, not even through the server. It may be off the air, or in a format this browser cannot play."
              : "The stream did not start. Its server may be off the air, may not allow playback from a web page, or the picture is in a format this browser cannot play.", 12000);
  }
  /* How far behind the live edge to play, to ride out a jittery stream: 0 (the smallest delay), 8 or 20 seconds. Playback waits until that much is buffered,
     and after a stall waits again until the cushion is rebuilt. Kept in this browser; 8 seconds unless chosen otherwise. */
  var holding = 0;                                           // a token: a newer hold (or a leave) cancels an older one
  function bufferSeconds() {
    try { var v = parseInt(localStorage.getItem("pluteus-tv-buffer"), 10); if (v === 0 || v === 8 || v === 20) return v; } catch (e) { /* storage blocked */ }
    return 8;
  }
  function ahead() {
    try { if (video.buffered.length) return Math.max(0, video.buffered.end(video.buffered.length - 1) - video.currentTime); } catch (e) { /* no buffer yet */ }
    return 0;
  }
  function holdUntilBuffered(seconds, then) {
    var mine = ++holding, began = Date.now();
    video.pause();
    var timer = setInterval(function () {
      var have = ahead();
      if (mine !== holding || !wanted) { clearInterval(timer); return; }
      if (have >= seconds || Date.now() - began > (seconds * 3 + 10) * 1000) { clearInterval(timer); say(""); then(); return; }     // (a source slower than real time must not hold it for ever)
      say("Buffering " + Math.floor(have) + " of " + seconds + " s…");
    }, 250);
  }
  function start() {
    holding++;
    release();
    if (always && !relay) {                                    // a channel that needs a stored login or header: only the server can fetch it
      wanted = false; paint();
      say("This channel needs a stored login or header, which only the server relay can use. Switch the relay on (relay = true) and add the stream under Settings, My streams.", 14000);
      return;
    }
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
    var tsAddress = !hlsAddress && !!video.dataset.mpegts && !/[.](?:mp4|webm|ogv|mp3|aac|ogg)(?:$|[?#])/i.test(url);       // not HLS: an MPEG transport stream
    var play = function () { var p = video.play(); if (p && p.catch) p.catch(function (e) { if (e && e.name === "NotAllowedError") { wanted = false; paint(); say("Tap play to start", 4000); } }); };
    if (tsAddress) {
      var mine = tsToken;
      needMpegts().then(function () {
        if (mine !== tsToken || !wanted) return;
        if (!window.mpegts || !window.mpegts.isSupported()) { failed(); return; }
        var cushion = bufferSeconds();
        ts = window.mpegts.createPlayer({ type: "mpegts", isLive: true, url: source },
                                        { enableWorker: false, liveBufferLatencyChasing: false, autoCleanupSourceBuffer: true,
                                          autoCleanupMaxBackwardDuration: 60, autoCleanupMinBackwardDuration: 30 });
        ts.on(window.mpegts.Events.ERROR, function () { if (mine === tsToken) failed(); });
        ts.attachMediaElement(video); ts.load();
        if (cushion > 0) holdUntilBuffered(cushion, play); else play();
      }, function () { failed(); });
    } else if (hlsAddress && window.Hls && window.Hls.isSupported()) {
      var behind = bufferSeconds();                              // play this far behind the live edge (hls.js default: three segments)
      hls = new window.Hls(behind > 0 ? { enableWorker: false, lowLatencyMode: false, liveSyncDuration: behind, maxBufferLength: Math.max(30, behind * 2) }
                                      : { enableWorker: false, lowLatencyMode: false });
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
  /* Stream info (for a spotty channel): what the player knows about the stream right now. It tells the places apart: the speed the data arrives at, how much
     is buffered ahead, how many frames were dropped, and how often (and how long) playback waited for data. */
  var infoBtn = document.getElementById("info"), infoBox = document.getElementById("tvinfo"), infoTimer = null, stalls = 0, stallMs = 0, waitingSince = 0;
  video.addEventListener("waiting", function () {
    if (!waitingSince && wanted) { waitingSince = Date.now(); stalls++; }
    var cushion = bufferSeconds();
    if (ts && wanted && cushion > 0 && !video.paused) holdUntilBuffered(cushion, function () { var p = video.play(); if (p && p.catch) p.catch(function () { /* the controls start it */ }); });     // rebuild the cushion before going on
  });
  video.addEventListener("playing", function () { if (waitingSince) { stallMs += Date.now() - waitingSince; waitingSince = 0; } });
  function infoText() {
    var quality = video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null, lines = [];
    lines.push("Coming from: " + (viaRelay ? "this server (the relay), which fetched it" : "the stream's own server, straight to this browser"));
    lines.push("Picture: " + (video.videoWidth ? video.videoWidth + " x " + video.videoHeight : "none yet") + (video.paused ? " (paused)" : " (playing)"));
    lines.push("Buffered ahead: " + ahead().toFixed(1) + " s (the buffer setting asks for " + bufferSeconds() + " s)");
    if (quality) lines.push("Frames: " + quality.totalVideoFrames + " shown, " + quality.droppedVideoFrames + " dropped by the browser");
    if (ts && ts.statisticsInfo) {
      var st = ts.statisticsInfo;
      lines.push("Data arriving at: " + Math.round(st.speed || 0) + " kB/s (a standard-definition channel needs about 400 to 1000, high definition 1000 to 2500)");
      lines.push("Decoded frames: " + (st.decodedFrames || 0) + ", dropped by the player: " + (st.droppedFrames || 0));
    }
    if (hls) lines.push("Estimated bandwidth: " + Math.round((hls.bandwidthEstimate || 0) / 1000) + " kbit/s" + (hls.latency ? ", behind live by " + hls.latency.toFixed(1) + " s" : ""));
    lines.push("Times playback waited for data: " + stalls + ", " + (stallMs / 1000).toFixed(1) + " s in all");
    lines.push("Tip: if data arrives well below that speed, or often waits, the weak link is between the stream and this browser. The admin page's relay box says which side of the server.");
    return lines.join("\n");
  }
  if (infoBtn && infoBox) {
    infoBtn.hidden = false;
    /* A/B: the same channel directly from its server, or through this server (the relay), to see which one is smoother. Only offered when both are possible:
       the server offers the relay for this channel, the channel does not need it, and a direct stream is allowed on this page. */
    var viaBtn = document.createElement("button");
    viaBtn.type = "button"; viaBtn.className = "pl-btn pl-btn-small"; viaBtn.hidden = true;
    infoBtn.parentNode.appendChild(document.createTextNode(" "));
    infoBtn.parentNode.appendChild(viaBtn);
    function paintVia() {
      var possible = !!relay && !always && !(location.protocol === "https:" && /^http:/i.test(url));
      viaBtn.hidden = infoBox.hidden || !possible;
      bufferSel.hidden = infoBox.hidden;
      viaBtn.textContent = viaRelay ? "Play directly instead" : "Play through the server instead";
    }
    viaBtn.addEventListener("click", function () { viaRelay = !viaRelay; start(); paintVia(); });
    /* the buffer: how far behind live to play. A bigger cushion rides out a jittery stream, at the cost of a longer wait at the start and of being later than live. */
    var bufferSel = document.createElement("select");
    bufferSel.setAttribute("aria-label", "Buffer");
    [["0", "Buffer: live, smallest delay"], ["8", "Buffer: smooth, about 8 s behind"], ["20", "Buffer: extra smooth, about 20 s behind"]].forEach(function (c) {
      var o = document.createElement("option"); o.value = c[0]; o.textContent = c[1]; bufferSel.appendChild(o);
    });
    bufferSel.value = String(bufferSeconds());
    bufferSel.hidden = true;
    infoBtn.parentNode.appendChild(document.createTextNode(" "));
    infoBtn.parentNode.appendChild(bufferSel);
    bufferSel.addEventListener("change", function () {
      try { localStorage.setItem("pluteus-tv-buffer", bufferSel.value); } catch (e) { /* storage blocked */ }
      if (wanted) start();
    });
    infoBtn.addEventListener("click", function () {
      var open = infoBox.hidden;
      infoBox.hidden = !open; infoBtn.setAttribute("aria-expanded", String(open));
      paintVia();
      clearInterval(infoTimer);
      if (open) { infoBox.textContent = infoText(); infoTimer = setInterval(function () { infoBox.textContent = infoText(); paintVia(); }, 1000); }
    });
  }
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
      url = button.dataset.url; relay = button.dataset.relay || ""; always = button.dataset.always === "1"; viaRelay = always;
      start();
    });
  });
  url = stations.length ? stations[0].dataset.url : video.dataset.url;
  if (stations.length) { relay = stations[0].dataset.relay || ""; always = stations[0].dataset.always === "1"; viaRelay = always; }

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
