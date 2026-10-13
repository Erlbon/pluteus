/* pluteus audio/video viewer.
   - resumes where this user stopped (position saved per user on the server)
   - big buttons for listening on a phone: 30 second skips, playback speed (remembered), sleep timer
   - plays the next file of the folder when one ends (best effort: the browser may ask for a tap)
   - lock-screen / notification controls through the Media Session API (title, skip, next/previous)
   Everything here is optional: without it the page is still a working audio or video player. */
(function () {
  "use strict";
  var root = document.getElementById("reader"), media = document.getElementById("media");
  if (!root || !media) return;
  var data = root.dataset;
  var progressUrl = data.progress, prevUrl = data.prev || "", nextUrl = data.next || "";
  var statusEl = document.getElementById("status");
  var statusTimer = null;
  var timer = null, lastSent = -1;
  var SKIP = 30;

  function say(text, ms) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = false;
    clearTimeout(statusTimer);
    if (ms) statusTimer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }

  /* ---- progress ---- */
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
      if (at > 3 && !/[?&]autoplay=1/.test(location.search)) {      // a file started by auto-advance begins at 0
        var seek = function () { try { media.currentTime = at; } catch (e) { /* not seekable yet */ } };
        if (media.readyState >= 1) seek(); else media.addEventListener("loadedmetadata", seek, { once: true });
      }
    });

  media.addEventListener("play", function () { clearInterval(timer); timer = setInterval(function () { send(false); }, 5000); });
  media.addEventListener("pause", function () { clearInterval(timer); if (!media.ended) send(false); });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden" && !media.paused) send(false, true); });
  window.addEventListener("pagehide", function () { if (!media.paused) send(false, true); });

  /* ---- own controls (audio): play button, scrubber, times, lyrics. Without this script the native controls stay. ---- */
  var playButton = document.getElementById("play"), scrub = document.getElementById("scrub");
  var curEl = document.getElementById("cur"), durEl = document.getElementById("dur");
  function clock(s) {
    if (!isFinite(s) || s < 0) return "–:––";
    s = Math.floor(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (r < 10 ? "0" : "") + r;
  }
  if (playButton && scrub) {
    document.documentElement.classList.add("pl-js");
    media.controls = false;
    var dragging = false;
    var paint = function () {
      var playing = !media.paused && !media.ended;
      var icon = playButton.querySelector(".pl-icon");
      if (icon) icon.className = "pl-icon " + (playing ? "pl-i-pause" : "pl-i-play");
      playButton.setAttribute("aria-label", playing ? "Pause" : "Play");
    };
    var tick = function () {
      if (curEl) curEl.textContent = clock(media.currentTime);
      if (durEl) durEl.textContent = clock(media.duration);
      if (!dragging && isFinite(media.duration) && media.duration > 0) scrub.value = Math.round(1000 * media.currentTime / media.duration);
    };
    playButton.addEventListener("click", function () {
      if (media.paused) { var p = media.play(); if (p && p.catch) p.catch(function () { say("Tap play to start", 4000); }); }
      else media.pause();
    });
    scrub.addEventListener("input", function () {
      dragging = true;
      if (isFinite(media.duration)) { var t = media.duration * scrub.value / 1000; if (curEl) curEl.textContent = clock(t); }
    });
    scrub.addEventListener("change", function () {
      dragging = false;
      if (isFinite(media.duration)) { try { media.currentTime = media.duration * scrub.value / 1000; } catch (e) { /* not seekable yet */ } }
    });
    ["play", "pause", "ended"].forEach(function (n) { media.addEventListener(n, paint); });
    ["timeupdate", "loadedmetadata", "durationchange"].forEach(function (n) { media.addEventListener(n, tick); });
    paint(); tick();
    document.addEventListener("keydown", function (e) {
      if (e.target && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(e.target.tagName) && e.key === " ") return;
      if (e.key === " ") { e.preventDefault(); playButton.click(); }
      else if (e.key === "ArrowLeft") jump(-10);
      else if (e.key === "ArrowRight") jump(10);
    });
  }

  /* ---- lyrics: a button opens the panel; timed lines light up and scroll along ---- */
  var lyricsBox = document.getElementById("lyrics"), lyricsButton = document.getElementById("lyrics-btn");
  if (lyricsBox && lyricsButton) lyricsButton.addEventListener("click", function () {
    lyricsBox.hidden = !lyricsBox.hidden;
    lyricsButton.setAttribute("aria-expanded", lyricsBox.hidden ? "false" : "true");
  });
  var lines = lyricsBox ? Array.prototype.slice.call(lyricsBox.querySelectorAll("p[data-t]")) : [];
  if (lines.length) {
    var times = lines.map(function (p) { return parseFloat(p.dataset.t); }), current = -1;
    media.addEventListener("timeupdate", function () {
      var t = media.currentTime + 0.25, lo = 0, hi = times.length - 1, found = -1;
      while (lo <= hi) { var mid = (lo + hi) >> 1; if (times[mid] <= t) { found = mid; lo = mid + 1; } else hi = mid - 1; }
      if (found === current) return;
      if (current >= 0) lines[current].classList.remove("pl-now");
      current = found;
      if (current >= 0) {
        lines[current].classList.add("pl-now");
        if (!lyricsBox.hidden) lyricsBox.scrollTop = lines[current].offsetTop - lyricsBox.clientHeight / 2 + lines[current].clientHeight / 2;
      }
    });
  }

  /* ---- skip ---- */
  function jump(seconds) {
    var t = (media.currentTime || 0) + seconds;
    if (isFinite(media.duration)) t = Math.min(t, media.duration);
    try { media.currentTime = Math.max(0, t); } catch (e) { /* not seekable yet */ }
  }
  var back = document.getElementById("back30"), forward = document.getElementById("fwd30");
  if (back) back.addEventListener("click", function () { jump(-SKIP); });
  if (forward) forward.addEventListener("click", function () { jump(SKIP); });

  /* ---- speed (remembered in this browser) ---- */
  var RATES = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5];
  var speedButton = document.getElementById("speed");
  var rate = 1;
  try { rate = parseFloat(localStorage.getItem("pluteus-media-rate")) || 1; } catch (e) { /* storage blocked */ }
  if (RATES.indexOf(rate) < 0) rate = 1;
  /* The buttons hold an icon and a text: only the text changes */
  function setLabel(button, text) { var t = button.querySelector(".pl-btn-text"); (t || button).textContent = text; }
  function getLabel(button) { var t = button.querySelector(".pl-btn-text"); return (t || button).textContent; }
  function applyRate() {
    media.defaultPlaybackRate = rate;
    media.playbackRate = rate;
    if (speedButton) setLabel(speedButton, (rate % 1 === 0 ? rate.toFixed(0) : String(rate)) + "×");
  }
  applyRate();
  media.addEventListener("loadedmetadata", applyRate);              // some browsers reset it when a file loads
  if (speedButton) speedButton.addEventListener("click", function () {
    rate = RATES[(RATES.indexOf(rate) + 1) % RATES.length];
    try { localStorage.setItem("pluteus-media-rate", String(rate)); } catch (e) { /* storage blocked */ }
    applyRate();
    say("Speed " + getLabel(speedButton), 1200);
  });

  /* ---- sleep timer: off -> 15 -> 30 -> 60 minutes -> end of this file -> off ---- */
  var STEPS = [0, 15, 30, 60, -1];
  var sleepButton = document.getElementById("sleep");
  var step = 0, deadline = 0, sleepTick = null;
  function sleepLabel() {
    if (!sleepButton) return;
    if (STEPS[step] === 0) setLabel(sleepButton, "Sleep");
    else if (STEPS[step] === -1) setLabel(sleepButton, "Sleep: file end");
    else setLabel(sleepButton, "Sleep " + Math.max(1, Math.ceil((deadline - Date.now()) / 60000)) + " min");
  }
  function stopSleepTick() { clearInterval(sleepTick); sleepTick = null; }
  if (sleepButton) sleepButton.addEventListener("click", function () {
    step = (step + 1) % STEPS.length;
    stopSleepTick();
    if (STEPS[step] > 0) {
      deadline = Date.now() + STEPS[step] * 60000;
      sleepTick = setInterval(function () {
        if (Date.now() >= deadline) { stopSleepTick(); media.pause(); step = 0; sleepLabel(); say("Sleep timer: paused", 4000); }
        else sleepLabel();
      }, 10000);
    }
    sleepLabel();
    say(STEPS[step] === 0 ? "Sleep timer off" : STEPS[step] === -1 ? "Stops at the end of this file" : "Pauses in " + STEPS[step] + " minutes", 2000);
  });

  /* ---- moving between files REPLACES the history entry, so Back goes to the folder, not through every file played ---- */
  function withAutoplay(url) { return url + (url.indexOf("?") < 0 ? "?" : "&") + "autoplay=1"; }
  ["prev", "next"].forEach(function (id) {                           // the footer arrows
    var link = document.getElementById(id);
    if (link && link.tagName === "A") link.addEventListener("click", function (e) { e.preventDefault(); location.replace(link.href); });
  });

  /* ---- the end of a file: play the next one, unless the sleep timer says stop ---- */
  media.addEventListener("ended", function () {
    clearInterval(timer);
    send(true, true);
    if (STEPS[step] === -1) { step = 0; sleepLabel(); say("Sleep timer: stopped at the end of the file", 4000); return; }
    if (nextUrl) location.replace(withAutoplay(nextUrl));
  });
  if (/[?&]autoplay=1/.test(location.search)) {
    var started = media.play();
    if (started && started.catch) started.catch(function () { say("Tap play to continue", 6000); });
  }

  /* ---- lock screen and notification controls ---- */
  if ("mediaSession" in navigator && window.MediaMetadata) {
    var session = navigator.mediaSession;
    var handle = function (action, fn) { try { session.setActionHandler(action, fn); } catch (e) { /* not supported here */ } };
    try {
      session.metadata = new MediaMetadata({
        title: data.title || document.title, album: data.album || "", artist: data.artist || "",
        artwork: data.artwork ? [{ src: data.artwork, sizes: "512x512" }] : []
      });
    } catch (e) { /* ignore */ }
    handle("play", function () { media.play(); });
    handle("pause", function () { media.pause(); });
    handle("seekbackward", function (d) { jump(-((d && d.seekOffset) || SKIP)); });
    handle("seekforward", function (d) { jump((d && d.seekOffset) || SKIP); });
    handle("seekto", function (d) { if (d && typeof d.seekTime === "number") media.currentTime = d.seekTime; });
    if (prevUrl) handle("previoustrack", function () { location.replace(withAutoplay(prevUrl)); });
    if (nextUrl) handle("nexttrack", function () { location.replace(withAutoplay(nextUrl)); });
    var lastPosition = 0;
    media.addEventListener("timeupdate", function () {
      var now = Date.now();
      if (now - lastPosition < 1000 || !isFinite(media.duration)) return;
      lastPosition = now;
      try { session.setPositionState({ duration: media.duration, playbackRate: media.playbackRate, position: Math.min(media.currentTime, media.duration) }); } catch (e) { /* ignore */ }
    });
  }
})();
