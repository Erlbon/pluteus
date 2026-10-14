/* Dark/light toggle. Runs in <head> (not deferred) so the saved choice is applied before the first paint.
   The choice lives in this browser only; with none saved the system setting decides. It is kept in a cookie as well as in
   localStorage: a browser keeps localStorage apart for every port, but cookies are shared between ports of one address, so the
   site (say :8100) and the admin page (:8101) show the same choice. */
(function () {
  var KEY = "pluteus-scheme", root = document.documentElement;
  function readCookie() {
    var m = document.cookie.match(new RegExp("(?:^|; )" + KEY + "=(dark|light)"));
    return m ? m[1] : null;
  }
  function remember(value) {
    try { localStorage.setItem(KEY, value); } catch (e) { /* ignore */ }
    try {
      document.cookie = KEY + "=" + value + "; path=/; max-age=31536000; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    } catch (e) { /* cookies blocked */ }
  }
  var saved = readCookie();
  try { if (!saved) { var stored = localStorage.getItem(KEY); if (stored === "dark" || stored === "light") { saved = stored; remember(stored); } } }
  catch (e) { /* storage blocked: the system setting still works */ }
  if (saved === "dark" || saved === "light") root.setAttribute("data-scheme", saved);

  /* TV mode: for a browser on a TV (large buttons, the remote's arrow keys). ?tv=1 on any address turns it on for this browser, ?tv=0 off. */
  var TV = "pluteus-tv", asked = location.search.match(/[?&]tv=([01])/);
  try {
    if (asked) { if (asked[1] === "1") localStorage.setItem(TV, "1"); else localStorage.removeItem(TV); }
    if (localStorage.getItem(TV) === "1") {
      root.classList.add("pl-tv");
      var me = document.currentScript, nav = document.createElement("script");
      if (me && me.src) { nav.src = me.src.replace(/scheme[.]js.*$/, "tvnav.js"); nav.defer = true; document.head.appendChild(nav); }
    }
  } catch (e) { /* storage blocked: no TV mode */ }

  /* The shell: a signed-in page opened on its own moves into the shell page (the player lives there and keeps playing while the
     site is shown in its frame); a page that is already in the frame hides its own bar, the shell has one. ?noshell=1 opts out. */
  var shell = root.getAttribute("data-shell");
  if (window.top !== window) {
    root.classList.add("pl-framed");
  } else if (shell && !/[?&]noshell=1/.test(location.search)) {
    location.replace(shell + "?go=" + encodeURIComponent(location.pathname + location.search + location.hash));
  }

  /* A do-nothing service worker, only so that Chrome offers "Install app" (it lives at <site prefix>/sw.js) */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      var link = document.querySelector("link[rel=manifest]");
      if (link) navigator.serviceWorker.register(link.href.replace(/manifest\.webmanifest.*$/, "sw.js")).catch(function () { /* ignore */ });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector(".pl-scheme");
    if (!button) return;
    button.addEventListener("click", function () {
      var now = root.getAttribute("data-scheme") ||
        (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var next = now === "dark" ? "light" : "dark";
      root.setAttribute("data-scheme", next);
      remember(next);
    });
  });
})();
