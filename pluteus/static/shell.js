/* pluteus shell: the outer page. It owns the audio, so a queue (a folder, several albums) keeps playing while the frame below goes
   from page to page, readers included. The pages in the frame call window.parent.plShell (see queue.js).
   Everything here is optional: without it the site is still there, in the frame. */
(function () {
  "use strict";
  var frame = document.getElementById("shell-frame"), strip = document.getElementById("sp");
  if (!frame || !strip) return;
  var root = document.documentElement;
  var audio = new Audio(), queue = [], index = -1, failures = 0;
  audio.preload = "auto";
  var el = function (id) { return document.getElementById(id); };
  var playBtn = el("sp-play"), titleEl = el("sp-title"), posEl = el("sp-pos"), barEl = el("sp-bar");

  /* ---- the player ---- */
  function icon(button, name) { var i = button.querySelector(".pl-icon"); if (i) i.className = "pl-icon pl-i-" + name; }
  var listeners = [];                        // pages in the frame that want to know when the player changes (the radio page)
  function playingNow() { return !audio.paused && !audio.ended; }
  function paint() {
    var playing = playingNow();
    icon(playBtn, playing ? "pause" : "play");
    playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    listeners = listeners.filter(function (fn) { try { fn(); return true; } catch (e) { return false; } });     // a page that is gone throws
  }
  function live() { return !!(queue[index] && queue[index].live); }
  function show() {
    var item = queue[index];
    if (!item) return;
    strip.hidden = false;
    document.body.classList.add("pl-shell-playing");
    titleEl.textContent = item.title + (item.folder ? " – " + item.folder : "");
    posEl.textContent = item.live ? "live" : (index + 1) + " / " + queue.length;
    barEl.style.width = "0";
    if ("mediaSession" in navigator && typeof MediaMetadata !== "undefined") {
      try { navigator.mediaSession.metadata = new MediaMetadata({ title: item.title, album: item.folder || "", artist: "Pluteus" }); } catch (e) { /* ignore */ }
    }
  }
  function pauseFrameMedia() {              // a song or movie playing in the page itself gives way to the queue
    try { frame.contentDocument.querySelectorAll("audio, video").forEach(function (m) { m.pause(); }); } catch (e) { /* not ready */ }
  }
  function go(n) {
    if (n < 0 || n >= queue.length) return;
    index = n;
    show();
    pauseFrameMedia();
    audio.src = queue[n].url;
    var p = audio.play();
    if (p && p.catch) p.catch(function () { paint(); });
    paint();
  }
  function stop() {
    audio.pause(); audio.removeAttribute("src"); audio.load();
    queue = []; index = -1;
    strip.hidden = true;
    document.body.classList.remove("pl-shell-playing");
    paint();
  }
  audio.addEventListener("play", paint);
  audio.addEventListener("pause", paint);
  audio.addEventListener("playing", function () { failures = 0; show(); paint(); });
  audio.addEventListener("ended", function () { if (!live() && index + 1 < queue.length) go(index + 1); else paint(); });
  audio.addEventListener("error", function () {         // a file that will not play: on to the next (not forever); a live stream just stops
    var item = queue[index];                             // an error left over from a source that was just replaced is not this file's
    if (!item || !audio.getAttribute("src") || audio.currentSrc !== new URL(item.url, location.href).href) { paint(); return; }
    if (live()) { titleEl.textContent = queue[index].title + " – the stream did not start"; paint(); return; }
    if (++failures > 5) { paint(); return; }
    if (index + 1 < queue.length) go(index + 1);
  });
  audio.addEventListener("timeupdate", function () {
    if (isFinite(audio.duration) && audio.duration > 0) barEl.style.width = (100 * audio.currentTime / audio.duration) + "%";
  });
  function dropStream() { audio.pause(); audio.removeAttribute("src"); audio.load(); paint(); }     // live radio: pause = leave the broadcast
  playBtn.addEventListener("click", function () {
    if (audio.paused) {
      if (live() && !audio.getAttribute("src")) audio.src = queue[index].url;                        // joins the broadcast as it is now
      var p = audio.play(); if (p && p.catch) p.catch(function () { /* blocked */ });
    } else if (live()) dropStream();
    else audio.pause();
  });
  el("sp-prev").addEventListener("click", function () { if (!live() && audio.currentTime > 4) audio.currentTime = 0; else go(index - 1); });
  el("sp-next").addEventListener("click", function () { go(index + 1); });
  el("sp-close").addEventListener("click", stop);
  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.setActionHandler("play", function () { audio.play(); });
      navigator.mediaSession.setActionHandler("pause", function () { audio.pause(); });
      navigator.mediaSession.setActionHandler("previoustrack", function () { go(index - 1); });
      navigator.mediaSession.setActionHandler("nexttrack", function () { go(index + 1); });
    } catch (e) { /* not supported */ }
  }

  /* what the pages in the frame may call */
  window.plShell = {
    playQueue: function (items, start) {
      queue = (items || []).filter(function (i) { return i && typeof i.url === "string"; }).slice(0, 2000);
      failures = 0;
      if (queue.length) go(Math.max(0, Math.min(start || 0, queue.length - 1)));
    },
    /* a song that was playing in the page being left carries on here, from where it was (a short gap while the file is opened again) */
    takeOver: function (items, start, at) {
      window.plShell.playQueue(items, start);
      if (at > 0) audio.addEventListener("loadedmetadata", function () { try { audio.currentTime = at; } catch (e) { /* not seekable */ } }, { once: true });
    },
    pause: function () { if (live()) dropStream(); else audio.pause(); },
    stop: stop,
    active: function () { return queue.length > 0; },
    isPlaying: playingNow,
    currentUrl: function () { return queue[index] ? queue[index].url : ""; },
    onState: function (fn) { listeners.push(fn); }
  };

  /* ---- the frame: keep the address bar, the title and light/dark in step with it ---- */
  function framePath() {
    try { var l = frame.contentWindow.location; return l.pathname + l.search + l.hash; } catch (e) { return ""; }
  }
  function mirror() {
    var path = framePath();
    if (path && !/\/shell(\?|$)/.test(path)) {
      try { history.replaceState(null, "", location.pathname + "?go=" + encodeURIComponent(path)); } catch (e) { /* ignore */ }
    }
    try { document.title = frame.contentDocument.title || "Pluteus"; } catch (e) { /* ignore */ }
    syncScheme(root, frame);
    watchFrameScheme();
  }
  function setScheme(doc, value) {
    try {
      if (value && doc.documentElement.getAttribute("data-scheme") !== value) doc.documentElement.setAttribute("data-scheme", value);
    } catch (e) { /* ignore */ }
  }
  function syncScheme(fromRoot) { setScheme(frame.contentDocument, fromRoot.getAttribute("data-scheme")); }
  var frameWatcher = null;
  function watchFrameScheme() {
    if (frameWatcher) frameWatcher.disconnect();
    try {
      frameWatcher = new MutationObserver(function () { setScheme(document, frame.contentDocument.documentElement.getAttribute("data-scheme")); });
      frameWatcher.observe(frame.contentDocument.documentElement, { attributes: true, attributeFilter: ["data-scheme"] });
    } catch (e) { /* ignore */ }
  }
  new MutationObserver(function () { syncScheme(root); }).observe(root, { attributes: true, attributeFilter: ["data-scheme"] });
  frame.addEventListener("load", mirror);
})();
