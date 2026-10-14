/* The radio picker: searches radio-browser.info from the browser, lets you listen and tick, and asks pluteus to write the ticked stations
   as .m3u files into your upload folder (Radio/). pluteus only writes the files; it never contacts the directory or the streams. */
(function () {
  "use strict";
  var SERVERS = ["https://all.api.radio-browser.info", "https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info", "https://fi1.api.radio-browser.info"];
  var $ = function (id) { return document.getElementById(id); };
  var audio = $("audio"), stations = [], playing = null, page = document.body.dataset;

  function api(path) {
    var i = 0;
    function next() {
      return fetch(SERVERS[i] + path).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).catch(function (e) {
        if (++i < SERVERS.length) return next();
        throw e;
      });
    }
    return next();
  }
  function say(text, bad) { var s = $("status"); s.textContent = text; s.className = "status" + (bad ? " warn" : ""); }
  function clean(text) { return String(text || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 120); }

  api("/json/countries").then(function (list) {
    list.filter(function (c) { return c.iso_3166_1; }).sort(function (a, b) { return a.name.localeCompare(b.name); }).forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.iso_3166_1; o.textContent = c.name + " (" + c.stationcount + ")";
      $("country").appendChild(o);
    });
  }).catch(function () { say("Could not reach radio-browser.info: check the internet connection and reload.", true); });
  api("/json/tags?order=stationcount&reverse=true&hidebroken=true&limit=300").then(function (list) {
    list.forEach(function (t) { var o = document.createElement("option"); o.value = t.name; $("tags").appendChild(o); });
  }).catch(function () { /* the tag box still works without suggestions */ });

  function search() {
    var q = ["hidebroken=" + ($("working").checked ? "true" : "false"), "order=" + $("order").value, "reverse=" + ($("order").value === "name" ? "false" : "true"),
             "limit=" + $("limit").value];
    if ($("https").checked) q.push("is_https=true");
    if ($("country").value) q.push("countrycode=" + encodeURIComponent($("country").value));
    if ($("tag").value.trim()) q.push("tag=" + encodeURIComponent($("tag").value.trim()));
    if ($("name").value.trim()) q.push("name=" + encodeURIComponent($("name").value.trim()));
    if (+$("bitrate").value) q.push("bitrateMin=" + $("bitrate").value);
    say("Searching…");
    $("go").disabled = true;
    api("/json/stations/search?" + q.join("&")).then(function (list) {
      var seen = {}, kept = [];
      list.forEach(function (s) {
        var url = s.url_resolved || s.url;
        if (!/^https?:\/\//i.test(url || "")) return;
        if ($("nohls").checked && s.hls) return;
        if ($("https").checked && !/^https:/i.test(url)) return;
        var key = (s.name || "").trim().toLowerCase();
        if (!key || seen[key]) return;                    // the same name twice: the more popular one is first
        seen[key] = true;
        kept.push({ uuid: s.stationuuid, name: clean(s.name), url: url, codec: s.codec || "", bitrate: s.bitrate || 0, country: s.countrycode || "",
                    tags: s.tags || "", home: /^https?:\/\//i.test(s.homepage || "") ? s.homepage : "", on: false });
      });
      stations = kept;
      say(kept.length + " stations" + (list.length !== kept.length ? " (" + (list.length - kept.length) + " left out: duplicates, HLS or not https)" : ""));
      render();
    }).catch(function (e) { say("The search failed: " + e.message, true); }).then(function () { $("go").disabled = false; });
  }

  function render() {
    var ul = $("list");
    ul.textContent = "";
    stations.forEach(function (s) {
      var li = document.createElement("li"); li.className = "row";
      var box = document.createElement("input"); box.type = "checkbox"; box.checked = s.on; box.setAttribute("aria-label", "Pick " + s.name);
      box.addEventListener("change", function () { s.on = box.checked; update(); });
      var info = document.createElement("div");
      var nm = document.createElement("div"); nm.className = "name"; nm.textContent = s.name;
      var meta = document.createElement("div"); meta.className = "meta";
      meta.textContent = [s.codec, s.bitrate ? s.bitrate + " kbps" : "", s.country, s.tags.split(",").slice(0, 4).join(", ")].filter(Boolean).join(" · ");
      if (s.home) {
        var a = document.createElement("a"); a.href = s.home; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = " · website";
        meta.appendChild(a);
      }
      info.appendChild(nm); info.appendChild(meta);
      var play = document.createElement("button"); play.className = "play"; play.textContent = "▶"; play.title = "Listen"; play.setAttribute("aria-label", "Listen to " + s.name);
      play.addEventListener("click", function () { toggle(s, play); });
      li.appendChild(box); li.appendChild(info); li.appendChild(play);
      ul.appendChild(li);
    });
    $("results").hidden = !stations.length;
    update();
  }
  function ticked() { return stations.filter(function (s) { return s.on; }); }
  function update() {
    var n = ticked().length;
    $("count").textContent = stations.length + " shown, " + n + " ticked";
    $("picked").textContent = n ? n + " ticked" : "Nothing ticked";
    $("saveServer").disabled = !n;
    $("saveOne").disabled = !n;
  }
  $("all").addEventListener("click", function () { stations.forEach(function (s) { s.on = true; }); render(); });
  $("none").addEventListener("click", function () { stations.forEach(function (s) { s.on = false; }); render(); });

  function reset() {
    document.querySelectorAll(".play.on").forEach(function (b) { b.classList.remove("on"); b.textContent = "▶"; });
  }
  function toggle(s, button) {
    var was = playing;
    audio.pause(); audio.removeAttribute("src"); audio.load();
    reset(); playing = null;
    if (was === s) return;
    playing = s;
    button.classList.add("on"); button.textContent = "■";
    audio.src = s.url;
    audio.play().catch(function () { say("This stream did not start: " + s.name, true); reset(); playing = null; });
    fetch(SERVERS[0] + "/json/url/" + encodeURIComponent(s.uuid)).catch(function () { /* the directory counts listens; optional */ });
  }
  audio.addEventListener("error", function () {
    if (!playing || !audio.getAttribute("src")) return;
    say("This stream did not start: " + playing.name, true);
    reset(); playing = null;
  });

  function log(text, bad) { $("log").textContent = text; $("log").className = "log" + (bad ? " warn" : ""); }
  $("saveOne").addEventListener("click", function () {
    var list = ticked();
    var text = "#EXTM3U\n" + list.map(function (s) { return "#EXTINF:-1," + s.name + "\n" + s.url + "\n"; }).join("");
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "audio/x-mpegurl" }));
    a.download = ($("sub").value.trim() || "Radio") + ".m3u";
    document.body.appendChild(a); a.click(); a.remove();
    log("Downloaded " + list.length + " stations as one list. Put it in a library folder: pluteus shows the stations as a list.");
  });
  $("saveServer").addEventListener("click", function () {
    var list = ticked();
    $("saveServer").disabled = true;
    log("Saving…");
    fetch(page.save, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "radio", sub: $("sub").value.trim(), overwrite: $("overwrite").checked,
                             stations: list.map(function (s) { return { name: s.name, url: s.url }; }) })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { log(res.body.error || "Not saved.", true); return; }
        var msg = "Saved " + res.body.saved + " in " + res.body.folder + "." +
          (res.body.skipped ? " Left alone because they already exist: " + res.body.skipped + " (tick “overwrite existing” to replace them)." : "");
        var l = $("log"); l.textContent = msg + " ";
        var a = document.createElement("a"); a.href = page.browse + (res.body.folder.indexOf("/") > 0 ? "/" + res.body.folder.split("/").slice(1).map(encodeURIComponent).join("/") : "");
        a.textContent = "Open the folder"; l.appendChild(a);
      })
      .catch(function () { log("Not saved: the server could not be reached.", true); })
      .then(function () { update(); });
  });

  $("go").addEventListener("click", search);
  ["tag", "name"].forEach(function (id) { $(id).addEventListener("keydown", function (e) { if (e.key === "Enter") search(); }); });
  update();
})();
