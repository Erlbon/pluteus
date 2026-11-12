/* pluteus image viewer: one image at a time, with the other images of the folder one key press away. */
(function () {
  "use strict";
  var root = document.getElementById("reader");
  if (!root) return;
  var cfg = root.dataset, img = document.getElementById("page"), stage = document.querySelector(".pl-reader-stage");
  var status = document.getElementById("status");

  var KEY = "pluteus-image-fit", fit = "page";
  try { var saved = localStorage.getItem(KEY); if (saved === "width" || saved === "orig") fit = saved; } catch (e) { /* default */ }
  function applyFit() {
    stage.classList.toggle("pl-fit-page", fit === "page");
    stage.classList.toggle("pl-fit-width", fit === "width");
    stage.classList.toggle("pl-fit-orig", fit === "orig");
    root.classList.toggle("pl-actual", fit === "orig");
    stage.scrollTop = 0; stage.scrollLeft = 0;
  }
  function setFit(mode) { fit = mode; try { localStorage.setItem(KEY, fit); } catch (e) { /* ignore */ } applyFit(); }
  applyFit();

  var order = ["page", "width", "orig"];
  document.getElementById("fit-btn").addEventListener("click", function () { setFit(order[(order.indexOf(fit) + 1) % order.length]); });
  document.getElementById("fs-btn").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen(); else if (root.requestFullscreen) root.requestFullscreen();
  });

  img.addEventListener("error", function () { status.textContent = "This image could not be shown."; status.hidden = false; });

  // Moving to another image REPLACES the history entry: after looking through twenty pictures, Back returns to the folder.
  function go(url) { if (url) location.replace(url); }
  ["prev", "next"].forEach(function (id) {                           // the footer arrows
    var link = document.getElementById(id);
    if (link && link.tagName === "A") link.addEventListener("click", function (e) { e.preventDefault(); go(link.href); });
  });
  // a tap on the picture (tapzones.js): upper part back, lower part forward, as in the readers (zoom is the button in the bar);
  // at actual size a tap does not turn, the finger moves the picture
  window.plTapZones({ stage: stage, back: function () { go(cfg.prev); }, forward: function () { go(cfg.next); },
                      enabled: function () { return fit !== "orig"; } });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;                      // the browser's own: Alt+Left is Back
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(cfg.next); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(cfg.prev); }
  });
  // a sideways swipe: the next or previous picture (not at actual size, where the finger moves it; a pinch is no swipe: tapzones.js)
  var swipe = window.plSwipe({ forward: function () { go(cfg.next); }, back: function () { go(cfg.prev); },
                               enabled: function () { return fit !== "orig"; } });
  stage.addEventListener("touchstart", swipe.start, { passive: true });
  stage.addEventListener("touchend", swipe.end, { passive: true });

  if (cfg.nextRaw) new Image().src = cfg.nextRaw;          // the next image is already loading while you look at this one
})();
