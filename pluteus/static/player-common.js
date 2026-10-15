/* pluteus player code shared by the channel, movie, song and radio pages, in one place so they behave alike.
   plSubtitles: the CC button.
   plScreenModes: the Full screen and Maximize buttons of every picture player (live channels and movies).
   - Full screen ("cinema", button #fs): the picture fills the screen, the title and controls lie over it and fade away after a few seconds
     without movement (class pl-idle); they come back with the mouse or a touch, and a tap that only brought them back does not also play or pause.
   - Maximize ("max", button #mx): the whole player, controls under the picture, fills the screen.
   - In the shell the FRAME goes full screen (shell.js plShell.fullscreen), so the next file opened in it stays in the same mode; the page only
     paints it (pl-full / pl-maxed on the player box). Elsewhere the player box itself goes full screen and a new page starts normal.
   Pressing the other button while full switches the mode and stays full screen; the same button again leaves.
   Usage: window.plVolume(media); var modes = window.plScreenModes(box, mediaElement, { onEnter: fn, active: fn }); modes.toggle(true|false); modes.mode(); modes.tapWokeControls().
   options.onEnter / options.onExit: called when a mode was entered / left (a phone turns sideways and back); options.active: false while the idle fade must not start (a paused movie). */
(function () {
  "use strict";
  /* the volume slider and the mute icon (#volume, #mute): the level is remembered in this browser; the icon is a mute switch; at zero it brings the sound back */
  window.plVolume = function (media) {
    var volume = document.getElementById("volume"), muteButton = document.getElementById("mute");
    if (!volume || !muteButton) return;
    var level = 1;
    try { var saved = parseFloat(localStorage.getItem("pluteus-media-volume")); if (saved >= 0 && saved <= 1) level = saved; } catch (e) { /* storage blocked */ }
    var show = function () {
      volume.value = String(Math.round((media.muted ? 0 : media.volume) * 100));
      volume.style.setProperty("--p", (100 * (volume.value - volume.min) / (volume.max - volume.min || 1)) + "%");
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
      if (media.volume === 0) { media.muted = false; media.volume = 0.5; }
      else media.muted = !media.muted;
    });
    media.addEventListener("volumechange", show);
    show();
  };

  /* the subtitle button (#cc, present or not as the page decides): hidden until there are tracks. The tracks are an HLS stream's (hls.js lists them),
     else the video's own text tracks (.srt / .vtt files offered as <track>s, a natively played stream). Each press steps to the next track and
     finally off; the choice is remembered by name in this browser and picked again whenever a track of that name appears. The button follows what is
     really showing (a stream may mark a track as its default). A raw MPEG-TS channel has none the browser can show.
     options.say(text, ms): the page's message line; options.hls(): the page's current hls.js object, or null. Returns { refresh }: call it when tracks change. */
  window.plSubtitles = function (media, options) {
    options = options || {};
    var button = document.getElementById("cc"), at = -1;
    if (!button) return { refresh: function () { } };
    function viaHls() { var h = options.hls ? options.hls() : null; return h && h.subtitleTracks && h.subtitleTracks.length ? h : null; }
    function tracks() {
      var h = viaHls(), list = [], i;
      if (h) { for (i = 0; i < h.subtitleTracks.length; i++) list.push({ name: h.subtitleTracks[i].name || h.subtitleTracks[i].lang || ("Track " + (i + 1)) }); return list; }
      for (i = 0; i < media.textTracks.length; i++) {
        var t = media.textTracks[i];
        if (t.kind === "subtitles" || t.kind === "captions") list.push({ name: t.label || t.language || ("Track " + (list.length + 1)), track: t });
      }
      return list;
    }
    function paint() { button.classList.toggle("pl-on", at >= 0); button.setAttribute("aria-pressed", String(at >= 0)); }
    function apply(index) {
      at = index;
      var h = viaHls();
      if (h) {                                                  // off only hides the track: switching it off would drop what is downloaded
        h.subtitleDisplay = index >= 0;
        if (index >= 0 && h.subtitleTrack !== index) h.subtitleTrack = index;
      } else {
        tracks().forEach(function (item, n) { item.track.mode = n === index ? "showing" : "disabled"; });
      }
      paint();
    }
    function shown(list) {                                      // which track is showing now, -1 for none
      var h = viaHls();
      if (h) return h.subtitleDisplay === false ? -1 : h.subtitleTrack;
      for (var n = 0; n < list.length; n++) if (list[n].track.mode === "showing") return n;
      return -1;
    }
    function refresh() {
      var list = tracks(), wanted = "";
      button.hidden = list.length === 0;
      try { wanted = localStorage.getItem("pluteus-subs") || ""; } catch (e) { /* storage blocked */ }
      at = shown(list);
      for (var n = 0; n < list.length; n++) if (wanted && list[n].name === wanted && at !== n) { apply(n); break; }
      paint();
    }
    button.addEventListener("click", function () {
      var list = tracks();
      if (!list.length) return;
      var next = at + 1 >= list.length ? -1 : at + 1;
      apply(next);
      try { localStorage.setItem("pluteus-subs", next >= 0 ? list[next].name : ""); } catch (e) { /* storage blocked */ }
      if (options.say) options.say(next < 0 ? "Subtitles off" : "Subtitles: " + list[next].name, 2500);
    });
    if (media.textTracks && media.textTracks.addEventListener) media.textTracks.addEventListener("addtrack", refresh);
    refresh();
    return { refresh: refresh };
  };

  window.plScreenModes = function (box, media, options) {
    options = options || {};
    var shellFs = null, cinema = false, idleTimer = null, wasIdle = false, wasOn = false;
    try { shellFs = window.parent !== window && window.parent.plShell ? window.parent.plShell.fullscreen : null; } catch (e) { shellFs = null; }

    function fullElement() { return document.fullscreenElement || document.webkitFullscreenElement; }
    function mode() {                                           // "cinema", "max" or "" for now
      if (shellFs) return shellFs.mode();
      return fullElement() === box ? (cinema ? "cinema" : "max") : "";
    }
    function wake() {
      box.classList.remove("pl-idle");
      clearTimeout(idleTimer);
      if (mode() === "cinema" && (!options.active || options.active())) idleTimer = setTimeout(function () { box.classList.add("pl-idle"); }, 3000);
    }
    function paint() {
      var now = mode();
      if (!now) cinema = false;
      if (wasOn && !now && options.onExit) options.onExit();
      wasOn = !!now;
      box.classList.toggle("pl-full", now === "cinema");
      box.classList.toggle("pl-maxed", now === "max" && !!shellFs);       // outside the shell the browser's own :fullscreen styles the box
      wake();
    }
    function toggle(asCinema) {
      var wanted = asCinema === false ? "max" : "cinema", current = mode();
      if (current && current !== wanted) {                         // the other button while full: switch to that mode, stay full screen
        if (shellFs) shellFs.setMode(wanted); else { cinema = wanted === "cinema"; paint(); }
        return;
      }
      if (current) { if (shellFs) shellFs.exit(); else (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
      if (shellFs && shellFs.enter(wanted)) { if (options.onEnter) options.onEnter(); return; }
      cinema = wanted === "cinema";
      if (box.requestFullscreen) box.requestFullscreen().then(function () { if (options.onEnter) options.onEnter(); }).catch(function () { /* refused */ });
      else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
      else if (media.webkitEnterFullscreen) media.webkitEnterFullscreen();             // an iPhone: only the picture, with its own controls
    }

    document.addEventListener("fullscreenchange", paint);
    document.addEventListener("webkitfullscreenchange", paint);
    if (shellFs) { shellFs.onChange(paint); paint(); }                    // a page opened while the frame is full screen starts in that mode
    box.addEventListener("pointerdown", function () { wasIdle = box.classList.contains("pl-idle"); }, true);
    ["mousemove", "pointerdown", "touchstart", "keydown"].forEach(function (name) { box.addEventListener(name, wake, { passive: true }); });
    media.addEventListener("play", wake);
    media.addEventListener("pause", wake);
    var fsButton = document.getElementById("fs"), maxButton = document.getElementById("mx");
    if (fsButton) fsButton.addEventListener("click", function () { toggle(true); });
    if (maxButton) maxButton.addEventListener("click", function () { toggle(false); });

    return {
      mode: mode,
      toggle: toggle,
      /* true once for a tap that only brought the controls back: the page must not also play or pause */
      tapWokeControls: function () { var woke = wasIdle; wasIdle = false; return woke; }
    };
  };
})();
