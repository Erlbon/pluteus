/* TEMPORARY: registers the site's service worker for the /pwa-test pages (they live one level below it). */
if ("serviceWorker" in navigator) navigator.serviceWorker.register(new URL("../sw.js", location.href).href).catch(function () {});
