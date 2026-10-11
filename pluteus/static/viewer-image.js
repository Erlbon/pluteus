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
  img.addEventListener("click", function () { setFit(fit === "orig" ? "page" : "orig"); });   // click: zoom to actual size and back
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
  var tapPrev = document.getElementById("tap-prev"), tapNext = document.getElementById("tap-next");
  if (tapPrev) tapPrev.addEventListener("click", function () { go(cfg.prev); });
  if (tapNext) tapNext.addEventListener("click", function () { go(cfg.next); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(cfg.next); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(cfg.prev); }
  });
  var tx = null, ty = null;
  stage.addEventListener("touchstart", function (e) { var t = e.changedTouches[0]; tx = t.clientX; ty = t.clientY; }, { passive: true });
  stage.addEventListener("touchend", function (e) {
    if (tx == null || fit === "orig") { tx = ty = null; return; }
    var t = e.changedTouches[0], dx = t.clientX - tx, dy = t.clientY - ty;
    tx = ty = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? cfg.next : cfg.prev);
  }, { passive: true });

  if (cfg.nextRaw) new Image().src = cfg.nextRaw;          // the next image is already loading while you look at this one
})();
