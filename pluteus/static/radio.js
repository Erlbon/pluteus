/* pluteus radio player: plays the stream of a station file (.m3u / .pls / .url) with the browser's own audio element.
   - a live stream has no position: play starts it, pause drops it (so it does not keep downloading, and play joins the live broadcast)
   - a file with several stations shows them as a list; a tap switches station
   - an http:// stream on an https:// page is blocked by the browser: say so instead of failing quietly
   Without this script the page is still a working player: the browser's own controls on the first station. */
(function () {
  "use strict";
  var root = document.getElementById("reader"), audio = document.getElementById("radio");
  if (!root || !audio) return;
  var play = document.getElementById("play"), now = document.getElementById("now"), statusEl = document.getElementById("status");
  var stations = Array.prototype.slice.call(document.querySelectorAll(".pl-station"));
  var url = audio.getAttribute("src") || "", name = now ? now.textContent : "", wanted = false, statusTimer = null;
  document.documentElement.classList.add("pl-js");
  audio.controls = false;
  audio.removeAttribute("src");                                // nothing is fetched until play is pressed
  /* in the shell the stream plays there (the resident player), so it keeps going when this page is left */
  var shell = null, current = 0;
  try { shell = window.parent !== window ? window.parent.plShell : null; } catch (e) { shell = null; }
  function items() {
    var logo = root.dataset.logo || "";                       // the picture next to the station file (a file with several stations: all share it)
    var back = location.pathname + location.search;           // the player bar links back to this page
    var relays = (root.dataset.relay || "").split("|");       // the address of each station through the server (empty when the relay is off)
    var always = (root.dataset.always || "").split("|");          // "1": the station needs a stored stream, so only the server may fetch it
    function now(i) { return relays[i] ? relays[i] + "&now=1" : ""; }                  // the address that tells what the station plays now
    var list = stations.length ? stations.map(function (b, i) { return { title: b.dataset.name, url: b.dataset.url, folder: "Live radio", live: true, logo: logo, page: back, linkText: "Station", relay: relays[i] || "", now: now(i), always: always[i] === "1", viaRelay: always[i] === "1" }; })
                               : [{ title: name, url: url, folder: "Live radio", live: true, logo: logo, page: back, linkText: "Station", relay: relays[0] || "", now: now(0), always: always[0] === "1", viaRelay: always[0] === "1" }];
    return list;
  }
  function shellPlaying() { return !!shell && shell.isPlaying() && shell.currentUrl() === url; }

  function say(text, ms) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = !text;
    clearTimeout(statusTimer);
    if (ms) statusTimer = setTimeout(function () { statusEl.hidden = true; }, ms);
  }
  function paint() {
    var icon = play.querySelector(".pl-icon");
    var line = document.getElementById("now-playing");                  // what the station plays now, when it tells (needs the relay)
    if (line) { var text = shell && shellPlaying() ? shell.nowText() : ""; line.textContent = text; line.hidden = !text; }
    if (shell) {
      if (icon) icon.className = "pl-icon " + (shellPlaying() ? "pl-i-pause" : "pl-i-play");
      play.setAttribute("aria-label", shellPlaying() ? "Stop" : "Play");
      return;
    }
    var playing = wanted && !audio.paused;
    if (icon) icon.className = "pl-icon " + (playing || (wanted && !audio.error) ? "pl-i-pause" : "pl-i-play");
    play.setAttribute("aria-label", wanted ? "Stop" : "Play");
  }
  function relayFor() { return (root.dataset.relay || "").split("|")[current] || ""; }
  function alwaysFor() { return (root.dataset.always || "").split("|")[current] === "1"; }
  function start() {
    if (alwaysFor() && !relayFor()) {
      wanted = false; paint();
      say("This station needs a stored login or header, which only the server relay can use. Switch the relay on (relay = true) and add the stream under Settings, My streams.", 14000);
      return;
    }
    if (location.protocol === "https:" && /^http:/i.test(url) && !relayFor()) {
      wanted = false; paint();
      say("This station is on plain http and the browser blocks it on this secure page. Open the site over http on the local network, or use a station with an https address.", 12000);
      return;
    }
    say("");
    wanted = true;
    if (shell) { shell.playQueue(items(), current); paint(); return; }
    audio.src = (alwaysFor() || (location.protocol === "https:" && /^http:/i.test(url))) && relayFor() ? relayFor() : url;       // outside the shell too: http on https goes through the server
    var p = audio.play();
    if (p && p.catch) p.catch(function (e) {
      if (e && e.name === "NotAllowedError") { wanted = false; paint(); say("Tap play to start", 4000); }   // anything else is the error event's job
    });
    paint();
  }
  function stop() {
    wanted = false;
    if (shell) { if (shell.currentUrl() === url) shell.stop(); paint(); return; }
    audio.pause();
    audio.removeAttribute("src");
    audio.load();                                              // drops the connection
    paint();
  }
  play.addEventListener("click", function () { if (shell ? shellPlaying() : wanted) stop(); else start(); });
  audio.addEventListener("playing", function () { say(""); paint(); });
  audio.addEventListener("waiting", function () { if (wanted) say("Connecting…"); });
  audio.addEventListener("error", function () {
    if (!wanted) return;
    wanted = false; paint();
    say("The stream did not start. It may be off the air, or in a format this browser cannot play (HLS is not supported).", 10000);
  });

  stations.forEach(function (button) {
    button.addEventListener("click", function () {
      stations.forEach(function (b) { b.classList.toggle("pl-now", b === button); });
      url = button.dataset.url; name = button.dataset.name; current = stations.indexOf(button);
      if (now) now.textContent = name;
      mediaSession();
      start();
    });
  });
  if (stations.length) { url = stations[0].dataset.url; name = stations[0].dataset.name; }

  /* lock-screen / notification controls */
  function mediaSession() {
    if (shell || !("mediaSession" in navigator) || typeof MediaMetadata === "undefined") return;       // the shell has the lock-screen controls
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: name, artist: "Live radio", artwork: [{ src: root.dataset.artwork || "", sizes: "512x512" }] });
      navigator.mediaSession.setActionHandler("play", start);
      navigator.mediaSession.setActionHandler("pause", stop);
    } catch (e) { /* not supported */ }
  }
  mediaSession();

  /* volume (wide screens only, like the music player): player-common.js */
  if (!shell && window.plVolume) window.plVolume(audio);
  if (shell) shell.onState(paint);
  paint();
})();
