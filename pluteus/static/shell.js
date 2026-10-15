/* pluteus shell: the outer page. It owns the audio, so a queue (a folder, several albums) keeps playing while the frame below goes
   from page to page, readers included. The pages in the frame call window.parent.plShell (see queue.js).
   Everything here is optional: without it the site is still there, in the frame. */
(function () {
  "use strict";
  var frame = document.getElementById("shell-frame"), strip = document.getElementById("sp");
  if (!frame || !strip) return;
  var root = document.documentElement;
  /* one media element for everything; a (never shown) video element, so it can also play the sound of a TV channel */
  var audio = document.createElement("video"), queue = [], index = -1, failures = 0, hls = null, hlsToken = 0, hlsLoading = null, ts = null, mpegtsLoading = null;
  audio.preload = "auto";
  audio.playsInline = true;
  var el = function (id) { return document.getElementById(id); };
  /* on a wide screen the player sits inside the pluteus bar (one row); on a phone it stays a strip under it */
  var header = document.querySelector("header.pl-bar"), spacer = header && header.querySelector(".pl-spacer");
  var wide = window.matchMedia ? window.matchMedia("(min-width: 1000px)") : null;
  function place() {
    if (!header || !spacer || !wide) return;
    if (wide.matches) {
      if (strip.parentNode !== header) { strip.classList.add("pl-merged"); header.insertBefore(strip, spacer); }
    } else if (strip.parentNode === header) {
      strip.classList.remove("pl-merged"); header.parentNode.insertBefore(strip, header.nextSibling);
    }
  }
  if (wide) { if (wide.addEventListener) wide.addEventListener("change", place); else if (wide.addListener) wide.addListener(place); }
  place();
  var playBtn = el("sp-play"), titleEl = el("sp-title"), posEl = el("sp-pos"), barEl = el("sp-bar"), linkEl = el("sp-link"), logoEl = el("sp-logo");

  /* ---- the player ---- */
  function icon(button, name) { var i = button.querySelector(".pl-icon"); if (i) i.className = "pl-icon pl-i-" + name; }
  var listeners = [];                        // pages in the frame that want to know when the player changes (the radio page)
  function playingNow() { return !audio.paused && !audio.ended && !audio.error; }
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
    // a radio station that tells what it plays now (ICY title): "Artist – Title · Station"; otherwise "Station – Live radio"
    titleEl.textContent = (item.nowText ? item.nowText + " · " + item.title : item.title + (item.folder ? " – " + item.folder : ""))
      + (item.viaRelay ? " · via the server" : "");
    posEl.textContent = item.live ? "live" : (index + 1) + " / " + queue.length;
    logoEl.hidden = !item.logo;                           // a station or channel with a logo: it shows at the left of the title
    if (item.logo) logoEl.src = item.logo;
    linkEl.hidden = !item.page;                           // a TV channel or a radio station: a link back to its page
    if (item.page) { linkEl.href = item.page; linkEl.textContent = item.linkText || "Picture"; }
    barEl.style.width = "0";
    if ("mediaSession" in navigator && typeof MediaMetadata !== "undefined") {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({ title: item.nowTitle || item.nowText || item.title,
                                                              album: item.nowText ? item.title : (item.folder || ""), artist: item.nowArtist || "Pluteus" });
      } catch (e) { /* ignore */ }
    }
  }
  function pauseFrameMedia() {              // a song or movie playing in the page itself gives way to the queue
    try { frame.contentDocument.querySelectorAll("audio, video").forEach(function (m) { m.pause(); }); } catch (e) { /* not ready */ }
  }
  /* the sound of a TV channel: hls.js (loaded when first needed) plays the stream, at its lowest quality, into the media element */
  function releaseHls() {
    hlsToken++;
    if (hls) { try { hls.destroy(); } catch (e) { /* ignore */ } hls = null; }
    if (ts) { try { ts.pause(); ts.unload(); ts.detachMediaElement(); ts.destroy(); } catch (e) { /* ignore */ } ts = null; }
  }
  /* an MPEG-TS channel (a panel such as Dispatcharr serves raw transport streams): mpegts.js, loaded when first needed */
  function needMpegts() {
    if (window.mpegts) return Promise.resolve();
    if (!mpegtsLoading) mpegtsLoading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = strip.dataset.mpegts; s.onload = resolve; s.onerror = function () { mpegtsLoading = null; reject(); };
      document.head.appendChild(s);
    });
    return mpegtsLoading;
  }
  function needHls() {
    if (window.Hls) return Promise.resolve();
    if (!hlsLoading) hlsLoading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = strip.dataset.hls; s.onload = resolve; s.onerror = function () { hlsLoading = null; reject(); };
      document.head.appendChild(s);
    });
    return hlsLoading;
  }
  function failedLive(item) { titleEl.textContent = item.title + " – the stream did not start"; paint(); }
  function load(item) {
    releaseHls();
    if (item.always && !item.relay) { titleEl.textContent = item.title + " – needs the server relay (a stored login or header)"; paint(); return; }
    var go = function () { var p = audio.play(); if (p && p.catch) p.catch(function () { paint(); }); };
    if (item.tv && /[.]m3u8(?:$|[?#])/i.test(item.url)) {
      var token = hlsToken;
      needHls().then(function () {
        if (token !== hlsToken) return;                  // another item was started meanwhile
        if (window.Hls && window.Hls.isSupported()) {
          hls = new window.Hls({ enableWorker: false, lowLatencyMode: false });
          hls.on(window.Hls.Events.MANIFEST_PARSED, function () { hls.autoLevelCapping = 0; go(); });
          hls.on(window.Hls.Events.ERROR, function (e, d) {
            if (!d || !d.fatal) return;
            releaseHls();
            if (item.relay && !item.viaRelay) { item.viaRelay = true; show(); load(item); return; }      // once through the server
            failedLive(item);
          });
          hls.loadSource(item.viaRelay && item.relay ? item.relay : item.url);
          hls.attachMedia(audio);
        } else { audio.src = item.viaRelay && item.relay ? item.relay : item.url; go(); }           // a browser that plays HLS itself
      }, function () { failedLive(item); });
    } else if (item.tv && strip.dataset.mpegts && !/[.](?:mp3|aac|ogg|mp4|webm)(?:$|[?#])/i.test(item.url)) {
      // a channel that is not HLS is an MPEG transport stream: mpegts.js plays it (the sound only is kept in the bar)
      if (item.relay && !item.viaRelay && location.protocol === "https:" && /^http:/i.test(item.url)) item.viaRelay = true;
      var tsToken = hlsToken;
      needMpegts().then(function () {
        if (tsToken !== hlsToken) return;
        if (!window.mpegts || !window.mpegts.isSupported()) { failedLive(item); return; }
        ts = window.mpegts.createPlayer({ type: "mpegts", isLive: true, hasVideo: false, url: item.viaRelay && item.relay ? item.relay : item.url },
                                        { enableWorker: false, liveBufferLatencyChasing: true });
        ts.on(window.mpegts.Events.ERROR, function () {
          releaseHls();
          if (item.relay && !item.viaRelay) { item.viaRelay = true; show(); load(item); return; }          // once through the server
          failedLive(item);
        });
        ts.attachMediaElement(audio); ts.load(); go();
      }, function () { failedLive(item); });
    } else {
      // a plain http stream on this secure page cannot play directly: through the server when it offers that (the relay)
      if (item.relay && !item.viaRelay && location.protocol === "https:" && /^http:/i.test(item.url)) item.viaRelay = true;
      audio.src = item.viaRelay && item.relay ? item.relay : item.url;
      go();
    }
  }
  /* what a radio station plays now: asked of the server (which reads the station's ICY title) every 20 seconds while a station plays */
  var nowTimer = null, nowSeq = 0;
  function stopNow() { clearInterval(nowTimer); nowTimer = null; nowSeq++; }
  function pollNow() {
    var item = queue[index];
    if (!item || !item.now) return;
    var seq = ++nowSeq;
    fetch(item.now, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : {}; }).then(function (d) {
      if (seq !== nowSeq || queue[index] !== item) return;                // another station or a stop came meanwhile
      item.nowText = (d && d.text) || ""; item.nowArtist = (d && d.artist) || ""; item.nowTitle = (d && d.title) || "";
      show(); paint();
    }).catch(function () { /* no title: the bar stays as it is */ });
  }
  function startNow() {
    stopNow();
    var item = queue[index];
    if (item && item.live && item.now) { item.nowText = ""; pollNow(); nowTimer = setInterval(pollNow, 20000); }
  }
  function go(n) {
    if (n < 0 || n >= queue.length) return;
    index = n;
    show();
    pauseFrameMedia();
    load(queue[n]);
    startNow();
    paint();
  }
  function stop() {
    stopNow();
    releaseHls();
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
    var item = queue[index];                             // an error left over from a source that was just replaced is gone (audio.error is reset)
    if (!item || !audio.error || !audio.getAttribute("src")) { paint(); return; }
    if (live() && item.relay && !item.viaRelay) {        // the direct stream failed (no CORS, refused): try it once through the server
      item.viaRelay = true; show(); load(item); paint(); return;
    }
    if (live()) { titleEl.textContent = queue[index].title + " – the stream did not start"; paint(); return; }
    if (++failures > 5) { paint(); return; }
    if (index + 1 < queue.length) go(index + 1);
  });
  audio.addEventListener("timeupdate", function () {
    if (isFinite(audio.duration) && audio.duration > 0) barEl.style.width = (100 * audio.currentTime / audio.duration) + "%";
  });
  function dropStream() { stopNow(); releaseHls(); audio.pause(); audio.removeAttribute("src"); audio.load(); paint(); }     // live: pause = leave the broadcast
  playBtn.addEventListener("click", function () {
    if (live() && !audio.getAttribute("src") && !hls && !ts) { load(queue[index]); startNow(); paint(); return; }          // joins the broadcast as it is now
    if (audio.paused) { var p = audio.play(); if (p && p.catch) p.catch(function () { /* blocked */ }); }
    else if (live()) dropStream();
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

  /* full screen for the pages in the frame: the FRAME goes full screen, not the page's own player, so moving to the next file (the page is
     replaced) keeps the screen full. A page asks with enter("max") (player with its controls) or enter("cinema") (the picture alone), is told of
     every change with onChange, and finds the mode in mode() when it opens. enter() is false where the browser cannot do it (then the page
     goes full screen by itself, and a new page starts normal). */
  var fsMode = "", fsListeners = [];
  function frameFull() { return !!frame && (document.fullscreenElement || document.webkitFullscreenElement) === frame; }
  function fsChanged() {
    if (!frameFull()) fsMode = "";
    fsListeners = fsListeners.filter(function (fn) { try { fn(); return true; } catch (e) { return false; } });       // a page that is gone throws: dropped
  }
  document.addEventListener("fullscreenchange", fsChanged);
  document.addEventListener("webkitfullscreenchange", fsChanged);
  var fullscreen = {
    mode: function () { return frameFull() ? fsMode : ""; },
    enter: function (mode) {
      var ask = frame && (frame.requestFullscreen || frame.webkitRequestFullscreen);
      if (!ask) return false;
      fsMode = mode === "cinema" ? "cinema" : "max";
      var p = ask.call(frame);
      if (p && p.catch) p.catch(function () { fsMode = ""; fsChanged(); });
      return true;
    },
    setMode: function (mode) {                                   // already full screen: change the mode without leaving it
      if (!frameFull()) return;
      fsMode = mode === "cinema" ? "cinema" : "max";
      fsChanged();
    },
    exit: function () { (document.exitFullscreen || document.webkitExitFullscreen || function () {}).call(document); },
    onChange: function (fn) { fsListeners.push(fn); }
  };

  /* what the pages in the frame may call */
  window.plShell = {
    fullscreen: fullscreen,
    playQueue: function (items, start) {
      queue = (items || []).filter(function (i) { return i && typeof i.url === "string"; }).slice(0, 2000);
      failures = 0;
      if (queue.length) go(Math.max(0, Math.min(start || 0, queue.length - 1)));
    },
    /* a TV channel being left: its sound carries on here (item: title, url, folder, page = the address of its picture) */
    keepTv: function (item) {
      if (!item || typeof item.url !== "string") return;
      window.plShell.playQueue([{ title: item.title, url: item.url, folder: item.folder || "Live TV", live: true, tv: true, page: item.page || "",
                                  logo: item.logo || "", linkText: "Picture", relay: item.relay || "", viaRelay: !!item.viaRelay, always: !!item.always }], 0);
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
    position: function () { return audio.currentTime; },
    currentUrl: function () { return queue[index] ? queue[index].url : ""; },
    nowText: function () { return queue[index] && queue[index].live ? (queue[index].nowText || "") : ""; },
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
