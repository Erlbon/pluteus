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
  /* ?autoplay=1 only means "this page was reached by auto-advance". Read it, then take it off the address: a reload, a restored
     tab or a bookmark of this page must resume where the listener stopped, not start from 0 and overwrite the saved position. */
  var autoplay = /[?&]autoplay=1/.test(location.search);
  if (autoplay && window.history && history.replaceState) {
    var kept = location.search.replace(/^\?/, "").split("&").filter(function (p) { return p && p !== "autoplay=1"; });
    try { history.replaceState(null, "", location.pathname + (kept.length ? "?" + kept.join("&") : "") + location.hash); } catch (e) { /* ignore */ }
  }
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
      if (at > 3 && !autoplay) {      // a file started by auto-advance begins at 0
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
    /* the gold part of a slider's track: --p is the share of the way to the handle */
    function fill(input) { input.style.setProperty("--p", (100 * (input.value - input.min) / (input.max - input.min || 1)) + "%"); }
    var tick = function () {
      if (curEl) curEl.textContent = clock(media.currentTime);
      if (durEl) durEl.textContent = clock(media.duration);
      if (!dragging && isFinite(media.duration) && media.duration > 0) scrub.value = Math.round(1000 * media.currentTime / media.duration);
      fill(scrub);
    };
    playButton.addEventListener("click", function () {
      if (media.paused) { var p = media.play(); if (p && p.catch) p.catch(function () { say("Tap play to start", 4000); }); }
      else media.pause();
    });
    scrub.addEventListener("input", function () {
      dragging = true;
      fill(scrub);
      if (isFinite(media.duration)) { var t = media.duration * scrub.value / 1000; if (curEl) curEl.textContent = clock(t); }
    });
    scrub.addEventListener("change", function () {
      dragging = false;
      if (isFinite(media.duration)) { try { media.currentTime = media.duration * scrub.value / 1000; } catch (e) { /* not seekable yet */ } }
    });
    ["play", "pause", "ended"].forEach(function (n) { media.addEventListener(n, paint); });
    ["timeupdate", "loadedmetadata", "durationchange"].forEach(function (n) { media.addEventListener(n, tick); });
    paint(); tick();

    /* a movie: a click on the picture plays or pauses, a double click goes full screen, and the full-screen button takes the whole
       player (picture and controls) full screen; an iPhone can only do the picture, with the browser's own controls */
    var fsButton = document.getElementById("fs"), box = document.querySelector(".pl-videobox");
    /* a wide picture on a phone: turn the screen sideways while full screen (it is released again on leaving; browsers that cannot do this skip it) */
    function turnSideways() {
      try {
        if (media.videoWidth > media.videoHeight && screen.orientation && screen.orientation.lock) {
          screen.orientation.lock("landscape").catch(function () { /* not allowed here */ });
        }
      } catch (e) { /* no orientation API */ }
    }
    document.addEventListener("fullscreenchange", function () {
      if (document.fullscreenElement) return;
      try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (e) { /* ignore */ }
    });
    function toggleFullscreen() {
      if (!box) return;
      var inside = document.fullscreenElement || document.webkitFullscreenElement;
      if (inside) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
      if (box.requestFullscreen) box.requestFullscreen().then(turnSideways).catch(function () { /* refused */ });
      else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
      else if (media.webkitEnterFullscreen) media.webkitEnterFullscreen();
    }
    if (fsButton) fsButton.addEventListener("click", toggleFullscreen);
    if (box) {
      media.addEventListener("click", function () { playButton.click(); });
      media.addEventListener("dblclick", function (e) { e.preventDefault(); toggleFullscreen(); });
    }

    /* subtitles (.srt / .vtt next to the movie, offered by the page as <track>s): the CC button steps through them and off again;
       the last choice is remembered by name. Without this script the browser's own controls list the tracks. */
    var ccButton = document.getElementById("cc");
    if (ccButton && media.textTracks && media.textTracks.length) {
      var tracks = media.textTracks, shown = -1, wanted = "";
      try { wanted = localStorage.getItem("pluteus-subs") || ""; } catch (e) { /* storage blocked */ }
      var showTrack = function (n, announce) {
        for (var i = 0; i < tracks.length; i++) tracks[i].mode = i === n ? "showing" : "disabled";
        shown = n;
        var label = n >= 0 ? tracks[n].label : "";
        ccButton.setAttribute("aria-pressed", n >= 0 ? "true" : "false");
        ccButton.classList.toggle("pl-on", n >= 0);
        try { localStorage.setItem("pluteus-subs", label); } catch (e) { /* storage blocked */ }
        if (announce) say(n >= 0 ? "Subtitles: " + label : "Subtitles off", 2500);
      };
      var first = -1;
      for (var t = 0; t < tracks.length; t++) if (wanted && tracks[t].label === wanted) first = t;
      showTrack(first, false);
      ccButton.addEventListener("click", function () { showTrack(shown + 1 < tracks.length ? shown + 1 : -1, true); });
    }

    /* volume (shown on wide screens only, see base.css): remembered in this browser; the icon is a mute switch */
    var volume = document.getElementById("volume"), muteButton = document.getElementById("mute");
    if (volume && muteButton) {
      var level = 1;
      try { var saved = parseFloat(localStorage.getItem("pluteus-media-volume")); if (saved >= 0 && saved <= 1) level = saved; } catch (e) { /* storage blocked */ }
      var showVolume = function () {
        volume.value = String(Math.round((media.muted ? 0 : media.volume) * 100));
        fill(volume);
        var icon = muteButton.querySelector(".pl-icon");
        if (icon) icon.className = "pl-icon " + (media.muted || media.volume === 0 ? "pl-i-mute" : "pl-i-volume");
        muteButton.setAttribute("aria-label", media.muted ? "Unmute" : "Mute");
      };
      media.volume = level;
      volume.addEventListener("input", function () {
        media.muted = false;
        media.volume = volume.value / 100;
        try { localStorage.setItem("pluteus-media-volume", String(media.volume)); } catch (e) { /* storage blocked */ }
      });
      muteButton.addEventListener("click", function () {
        if (media.volume === 0) { media.muted = false; media.volume = 0.5; }                    // at zero the icon brings the sound back
        else media.muted = !media.muted;
      });
      media.addEventListener("volumechange", showVolume);
      showVolume();
    }
    document.addEventListener("keydown", function (e) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;                    // Alt+Arrow is the browser's Back / Forward
      if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;      // the scrubber and fields keep their own keys
      if (e.target && e.target.tagName === "BUTTON" && e.key === " ") return;
      if (e.key === " ") { e.preventDefault(); playButton.click(); }
      else if (e.key === "ArrowLeft") jump(-10);
      else if (e.key === "ArrowRight") jump(10);
    });
  }

  /* ---- lyrics: a button opens the panel; timed lines light up and scroll along ---- */
  var lyricsBox = document.getElementById("lyrics");
  /* the buttons Lyrics / Chapters / Bookmarks: each opens its own panel, one at a time */
  var panelButtons = Array.prototype.slice.call(document.querySelectorAll(".pl-panel-btn"));
  function showPanel(name) {
    panelButtons.forEach(function (b) {
      var box = document.getElementById(b.dataset.panel);
      var open = b.dataset.panel === name && box && box.hidden;
      if (box) box.hidden = !open;
      b.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  panelButtons.forEach(function (b) { b.addEventListener("click", function () { showPanel(b.dataset.panel); if (b.dataset.panel === "bookmarks") loadBookmarks(); }); });

  /* ---- chapters (audiobooks): a click seeks; the current one is marked ---- */
  var chapterRows = Array.prototype.slice.call(document.querySelectorAll("#chapters .pl-chapter"));
  if (chapterRows.length) {
    var chapterTimes = chapterRows.map(function (r) { return parseFloat(r.dataset.t); }), chapterNow = -1;
    chapterRows.forEach(function (r) { r.addEventListener("click", function () { try { media.currentTime = parseFloat(r.dataset.t); } catch (e) { /* not seekable yet */ } if (media.paused) media.play(); }); });
    media.addEventListener("timeupdate", function () {
      var t = media.currentTime + 0.1, found = -1;
      for (var i = 0; i < chapterTimes.length; i++) { if (chapterTimes[i] <= t) found = i; else break; }
      if (found === chapterNow) return;
      if (chapterNow >= 0) chapterRows[chapterNow].classList.remove("pl-now");
      chapterNow = found;
      if (found >= 0) {
        chapterRows[found].classList.add("pl-now");
        var box = document.getElementById("chapters");
        if (box && !box.hidden) box.scrollTop = chapterRows[found].offsetTop - box.clientHeight / 2;
      }
    });
  }

  /* ---- bookmarks: saved on the server for this user and file ---- */
  var bookmarkUrl = data.bookmarks || "", bookmarkList = document.getElementById("bookmark-list"), bookmarkAdd = document.getElementById("bookmark-add");
  function bookmarkRow(b) {
    var row = document.createElement("div"), go = document.createElement("button"), del = document.createElement("button");
    row.className = "pl-bookmark";
    go.type = "button"; go.className = "pl-chapter";
    var when = document.createElement("span"); when.className = "pl-chapter-time"; when.textContent = clock(b.position);
    go.appendChild(when); go.appendChild(document.createTextNode(" " + (b.note || "Bookmark")));
    go.addEventListener("click", function () { try { media.currentTime = b.position; } catch (e) { /* not seekable yet */ } });
    del.type = "button"; del.className = "pl-bookmark-del"; del.textContent = "\u00d7"; del.setAttribute("aria-label", "Delete this bookmark");
    del.addEventListener("click", function () {
      fetch(bookmarkUrl + "?id=" + b.id, { method: "DELETE", credentials: "same-origin" }).then(function () { loadBookmarks(); });
    });
    row.appendChild(go); row.appendChild(del);
    return row;
  }
  function loadBookmarks() {
    if (!bookmarkUrl || !bookmarkList) return;
    fetch(bookmarkUrl, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : { bookmarks: [] }; }).then(function (d) {
      while (bookmarkList.firstChild) bookmarkList.removeChild(bookmarkList.firstChild);
      (d.bookmarks || []).forEach(function (b) { bookmarkList.appendChild(bookmarkRow(b)); });
    }).catch(function () { /* offline */ });
  }
  if (bookmarkAdd) bookmarkAdd.addEventListener("click", function () {
    fetch(bookmarkUrl, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position: media.currentTime || 0, note: "" }) })
      .then(function (r) { if (r.ok) { say("Bookmark saved at " + clock(media.currentTime), 1500); loadBookmarks(); } else say("The bookmark could not be saved", 3000); })
      .catch(function () { say("The bookmark could not be saved", 3000); });
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
  var RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  var speedButton = document.getElementById("speed");               // the readout between the - and + buttons
  var slower = document.getElementById("speed-down"), faster = document.getElementById("speed-up");
  var rate = 1;
  /* With the speed controls switched off in Settings the file always plays at normal speed */
  if (speedButton) { try { rate = parseFloat(localStorage.getItem("pluteus-media-rate")) || 1; } catch (e) { /* storage blocked */ } }
  if (RATES.indexOf(rate) < 0) rate = 1;
  /* The buttons hold an icon and a text: only the text changes */
  function setLabel(button, text) { var t = button.querySelector(".pl-btn-text"); (t || button).textContent = text; }
  function getLabel(button) { var t = button.querySelector(".pl-btn-text"); return (t || button).textContent; }
  function rateText() { return (rate % 1 === 0 ? rate.toFixed(0) : String(rate)) + "×"; }
  function applyRate() {
    media.defaultPlaybackRate = rate;
    media.playbackRate = rate;
    /* Written along the play button only when it is not normal speed; the chip on a video always shows it */
    if (speedButton) setLabel(speedButton, rate === 1 && speedButton.classList.contains("pl-speed-arc") ? "" : rateText());
  }
  applyRate();
  media.addEventListener("loadedmetadata", applyRate);              // some browsers reset it when a file loads
  function stepRate(direction) {
    var i = Math.max(0, Math.min(RATES.length - 1, RATES.indexOf(rate) + direction));
    rate = RATES[i];
    try { localStorage.setItem("pluteus-media-rate", String(rate)); } catch (e) { /* storage blocked */ }
    applyRate();
    say("Speed " + rateText(), 1200);
  }
  /* the readout in the middle is a switch: normal -> 1.25 -> 1.5 -> 2 -> normal (the - and + beside it fine-tune) */
  var QUICK = [1, 1.25, 1.5, 2];
  if (speedButton) speedButton.addEventListener("click", function () {
    var i = QUICK.indexOf(rate);
    rate = QUICK[(i + 1) % QUICK.length];                            // from any other speed the first click goes to the next quick one
    try { localStorage.setItem("pluteus-media-rate", String(rate)); } catch (e) { /* storage blocked */ }
    applyRate();
    say("Speed " + rateText(), 1200);
  });
  if (slower) slower.addEventListener("click", function () { stepRate(-1); });
  if (faster) faster.addEventListener("click", function () { stepRate(1); });

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
  if (autoplay) {
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
