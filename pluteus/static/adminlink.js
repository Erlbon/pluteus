/* The Admin link in the top bar starts hidden. The admin page is on its own port and only reachable from the local network, so ask
   this browser whether it can reach it, and show the link only if it can. From the public address the answer is no (the page is
   not reachable, or the browser refuses to mix a secure page with a plain one) and the link never appears.
   The probe is a request that does not read the answer (no-cors): it only succeeds when something answers on that address.
   A positive answer is remembered for five minutes in this tab; a negative one is asked again on the next page. */
(function () {
  "use strict";
  var link = document.querySelector("a[data-admin]");
  if (!link) return;
  var KEY = "pluteus-admin-probe", TTL = 300000;
  try {
    var seen = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (seen && seen.url === link.href && Date.now() - seen.t < TTL) { link.hidden = false; return; }
  } catch (e) { /* storage blocked: ask */ }
  if (!window.fetch || !window.AbortController) return;
  var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 2500);
  fetch(link.href, { mode: "no-cors", cache: "no-store", credentials: "omit", signal: ctl.signal })
    .then(function () { return true; }, function () { return false; })
    .then(function (ok) {
      clearTimeout(timer);
      if (!ok) return;
      link.hidden = false;
      try { sessionStorage.setItem(KEY, JSON.stringify({ url: link.href, t: Date.now() })); } catch (e) { /* ignore */ }
    });
})();
