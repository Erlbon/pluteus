/* pluteus player code shared by the channel, movie, song and radio pages, in one place so they behave alike.
   plScreenModes: the Full screen and Maximize buttons of every picture player (live channels and movies).
   - Full screen ("cinema", button #fs): the picture fills the screen, the title and controls lie over it and fade away after a few seconds
     without movement (class pl-idle); they come back with the mouse or a touch, and a tap that only brought them back does not also play or pause.
   - Maximize ("max", button #mx): the whole player, controls under the picture, fills the screen.
   - In the shell the FRAME goes full screen (shell.js plShell.fullscreen), so the next file opened in it stays in the same mode; the page only
     paints it (pl-full / pl-maxed on the player box). Elsewhere the player box itself goes full screen and a new page starts normal.
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
      if (mode()) { if (shellFs) shellFs.exit(); else (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
      var wanted = asCinema === false ? "max" : "cinema";
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
