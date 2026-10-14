/* The TV picker: reads the iptv-org catalogue in the browser, lets you try and test channels, and asks pluteus to write the ticked
   ones as .m3u files into your upload folder (IPTV/). pluteus only writes the files; it never contacts the catalogue or the streams. */
(function () {
  "use strict";
  var BASE = "https://iptv-org.github.io/api/";
  var PAGE = 150;
  var $ = function (id) { return document.getElementById(id); };
  var channels = {}, streams = [], countries = {}, categoryNames = {}, shown = [], limit = PAGE, hls = null, playing = null, page = document.body.dataset;

  function say(text, bad) { var s = $("status"); s.textContent = text; s.className = "status" + (bad ? " warn" : ""); }
  function load(name) { return fetch(BASE + name + ".json").then(function (r) { if (!r.ok) throw new Error(name + ": HTTP " + r.status); return r.json(); }); }
  function clean(text) { return String(text || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 120); }
  function height(q) { var m = /^(\d+)p?/.exec(q || ""); return m ? +m[1] : 0; }

  Promise.all([load("channels"), load("streams"), load("countries"), load("categories")]).then(function (all) {
    all[0].forEach(function (c) { if (!c.is_nsfw && !c.closed) channels[c.id] = c; });
    all[2].forEach(function (c) { countries[c.code] = c.name; });
    all[2].sort(function (a, b) { return a.name.localeCompare(b.name); }).forEach(function (c) {
      var o = document.createElement("option"); o.value = c.code; o.textContent = c.flag + " " + c.name; $("country").appendChild(o);
    });
    all[3].sort(function (a, b) { return a.name.localeCompare(b.name); }).forEach(function (c) {
      if (c.id === "xxx") return;
      categoryNames[c.id] = c.name;
      var o = document.createElement("option"); o.value = c.id; o.textContent = c.name; $("category").appendChild(o);
    });
    streams = all[1].filter(function (s) {
      return s.channel && channels[s.channel] && /^https?:\/\//i.test(s.url) && !s.user_agent && !s.referrer;       // a web page cannot send these headers
    });
    $("go").disabled = false; $("go").textContent = "Search";
    say(Object.keys(channels).length + " channels, " + streams.length + " streams a browser could try.");
  }).catch(function (e) { $("go").textContent = "Could not load the catalogue"; say("Failed: " + e.message + ". Check the internet connection and reload.", true); });

  function search() {
    var country = $("country").value, category = $("category").value, name = $("name").value.trim().toLowerCase(), q = +$("quality").value;
    var out = streams.filter(function (s) {
      var c = channels[s.channel];
      if (country && c.country !== country) return false;
      if (category && (c.categories || []).indexOf(category) < 0) return false;
      if (name && c.name.toLowerCase().indexOf(name) < 0 && (c.alt_names || []).join("|").toLowerCase().indexOf(name) < 0) return false;
      if (q && height(s.quality) < q) return false;
      if ($("https").checked && !/^https:/i.test(s.url)) return false;
      if ($("hls").checked && !/[.]m3u8(?:$|[?#])/i.test(s.url)) return false;
      if ($("nogeo").checked && (s.labels || []).indexOf("Geo-blocked") >= 0) return false;
      return true;
    }).map(function (s) {
      var c = channels[s.channel];
      return { id: c.id, name: clean(c.name), country: c.country || "", cats: c.categories || [], url: s.url, quality: s.quality || "",
               labels: s.labels || [], on: false, test: "" };
    });
    out.sort(function (a, b) { return a.name.localeCompare(b.name) || height(b.quality) - height(a.quality); });
    if ($("best").checked) {
      var seen = {};
      out = out.filter(function (s) { if (seen[s.id]) return false; seen[s.id] = true; return true; });
    }
    shown = out; limit = PAGE;
    say(out.length + " channels" + (out.length ? "" : ": loosen a filter"));
    render();
  }

  function render() {
    var ul = $("list"); ul.textContent = "";
    shown.slice(0, limit).forEach(function (s) {
      var li = document.createElement("li"); li.className = "row";
      var box = document.createElement("input"); box.type = "checkbox"; box.checked = s.on; box.setAttribute("aria-label", "Pick " + s.name);
      box.addEventListener("change", function () { s.on = box.checked; update(); });
      var info = document.createElement("div");
      var nm = document.createElement("div"); nm.className = "name"; nm.textContent = s.name;
      var meta = document.createElement("div"); meta.className = "meta";
      meta.textContent = [s.quality, countries[s.country] || s.country, s.cats.join(", "), s.labels.join(", ")].filter(Boolean).join(" · ");
      info.appendChild(nm); info.appendChild(meta);
      var test = document.createElement("span"); test.className = "test"; s.testEl = test; paintTest(s);
      var play = document.createElement("button"); play.className = "play"; play.textContent = "▶"; play.title = "Try it"; play.setAttribute("aria-label", "Try " + s.name);
      play.addEventListener("click", function () { preview(s, play); });
      li.appendChild(box); li.appendChild(info); li.appendChild(test); li.appendChild(play);
      ul.appendChild(li);
    });
    $("results").hidden = !shown.length;
    $("more").hidden = shown.length <= limit;
    update();
  }
  function paintTest(s) {
    if (!s.testEl) return;
    s.testEl.textContent = s.test === "ok" ? "plays" : s.test === "no" ? "fails" : s.test === "busy" ? "testing…" : "";
    s.testEl.className = "test" + (s.test === "ok" ? " ok" : s.test === "no" ? " no" : "");
    if (s.why) s.testEl.title = s.why;
  }
  $("more").addEventListener("click", function () { limit += PAGE; render(); });
  function ticked() { return shown.filter(function (s) { return s.on; }); }
  function update() {
    var n = ticked().length;
    $("count").textContent = Math.min(limit, shown.length) + " of " + shown.length + " shown, " + n + " ticked";
    $("picked").textContent = n ? n + " ticked" : "Nothing ticked";
    $("saveServer").disabled = !n; $("saveOne").disabled = !n; $("check").disabled = !n;
  }
  $("all").addEventListener("click", function () { shown.slice(0, limit).forEach(function (s) { s.on = true; }); render(); });
  $("none").addEventListener("click", function () { shown.forEach(function (s) { s.on = false; }); render(); });

  /* ---- try one channel in the page ---- */
  function release() {
    if (hls) { try { hls.destroy(); } catch (e) { /* ignore */ } hls = null; }
    var v = $("video"); v.pause(); v.removeAttribute("src"); v.load();
    document.querySelectorAll(".play.on").forEach(function (b) { b.classList.remove("on"); b.textContent = "▶"; });
  }
  function preview(s, button) {
    var again = playing === s;
    release(); playing = null;
    if (again) { $("preview").classList.remove("open"); return; }
    playing = s; button.classList.add("on"); button.textContent = "■";
    $("preview").classList.add("open"); $("capname").textContent = s.name; $("capmsg").textContent = "Connecting…";
    var v = $("video"), fail = function () { $("capmsg").textContent = "Does not play here (server refuses a web page, off the air, or a format this browser cannot play)."; };
    var go = function () { var p = v.play(); if (p && p.catch) p.catch(function () { /* the controls start it */ }); };
    if (window.Hls && Hls.isSupported() && /[.]m3u8(?:$|[?#])/i.test(s.url)) {
      hls = new Hls({ enableWorker: false });
      hls.on(Hls.Events.MANIFEST_PARSED, go);
      hls.on(Hls.Events.ERROR, function (e, d) { if (d && d.fatal) fail(); });
      hls.loadSource(s.url); hls.attachMedia(v);
    } else { v.src = s.url; go(); }
    v.onplaying = function () { $("capmsg").textContent = ""; };
    v.onerror = function () { if (v.getAttribute("src")) fail(); };
  }

  /* ---- test the ticked channels: can a web page read the manifest at all? (the usual reason a channel will not play in pluteus) ---- */
  function testOne(s) {
    s.test = "busy"; paintTest(s);
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 9000);
    return fetch(s.url, { signal: ctl.signal }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then(function (t) {
      if (t.indexOf("#EXTM3U") < 0) throw new Error("not a playlist");
      s.test = "ok"; s.why = "";
    }).catch(function (e) { s.test = "no"; s.why = e.name === "AbortError" ? "no answer in 9 seconds" : (e.message === "Failed to fetch" ? "the server does not allow a web page to read it, or is down" : e.message); })
      .then(function () { clearTimeout(timer); paintTest(s); });
  }
  $("check").addEventListener("click", function () {
    var queue = ticked().slice(), workers = [], done = 0, total = queue.length;
    $("check").disabled = true;
    function next() {
      var s = queue.shift();
      if (!s) return Promise.resolve();
      return testOne(s).then(function () { done++; say("Tested " + done + " of " + total); return next(); });
    }
    for (var i = 0; i < 4; i++) workers.push(next());
    Promise.all(workers).then(function () {
      var bad = ticked().filter(function (s) { return s.test === "no"; }).length;
      say("Tested " + total + ": " + (total - bad) + " answer, " + bad + " do not. Untick the ones that fail.");
      update();
    });
  });

  /* ---- saving ---- */
  function log(text, bad) { $("log").textContent = text; $("log").className = "log" + (bad ? " warn" : ""); }
  function attr(text) { return clean(text).replace(/"/g, "'"); }
  $("saveOne").addEventListener("click", function () {
    var list = ticked();
    var text = "#EXTM3U\n" + list.map(function (s) {
      var cat = s.cats.length ? " group-title=\"" + attr(categoryNames[s.cats[0]] || s.cats[0]) + "\"" : "";
      return "#EXTINF:-1 tvg-id=\"" + attr(s.id) + "\"" + cat + "," + s.name + "\n" + s.url + "\n";
    }).join("");
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "audio/x-mpegurl" }));
    a.download = ($("sub").value.trim() || "Channels") + ".m3u";
    document.body.appendChild(a); a.click(); a.remove();
    log("Downloaded " + list.length + " channels as one list: Kodi's PVR IPTV Simple Client can read it. Without a programme guide they appear as plain channels.");
  });
  $("saveServer").addEventListener("click", function () {
    var list = ticked();
    $("saveServer").disabled = true;
    log("Saving…");
    fetch(page.save, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "tv", sub: $("sub").value.trim(), by_country: $("bycountry").checked, overwrite: $("overwrite").checked,
                             stations: list.map(function (s) {
                               return { name: s.name, url: s.url, id: s.id, group: categoryNames[s.cats[0]] || "", country: countries[s.country] || s.country };
                             }) })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { log(res.body.error || "Not saved.", true); return; }
        var l = $("log");
        l.textContent = "Saved " + res.body.saved + " in " + res.body.folder + "." +
          (res.body.skipped ? " Left alone because they already exist: " + res.body.skipped + " (tick “overwrite existing” to replace them)." : "") + " ";
        var a = document.createElement("a");
        a.href = page.browse + (res.body.folder.indexOf("/") > 0 ? "/" + res.body.folder.split("/").slice(1).map(encodeURIComponent).join("/") : "");
        a.textContent = "Open the folder"; l.appendChild(a);
      })
      .catch(function () { log("Not saved: the server could not be reached.", true); })
      .then(function () { update(); });
  });

  $("go").addEventListener("click", search);
  $("name").addEventListener("keydown", function (e) { if (e.key === "Enter" && !$("go").disabled) search(); });
  update();
})();
