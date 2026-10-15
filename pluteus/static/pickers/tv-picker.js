/* The TV picker: reads the iptv-org catalogue in the browser, lets you try and test channels, and asks pluteus to write the ticked
   ones as .m3u files into your upload folder (IPTV/). pluteus only writes the files; it never contacts the catalogue or the streams. */
(function () {
  "use strict";
  var BASE = "https://iptv-org.github.io/api/";
  var PAGE = 150;
  var $ = function (id) { return document.getElementById(id); };
  var channels = {}, streams = [], countries = {}, categoryNames = {}, shown = [], limit = PAGE, hls = null, playing = null, page = document.body.dataset;
  var relayOn = page.relay === "1";                          // the server relay is on: a saved channel that a browser cannot play directly can still play through the server
  var secure = location.protocol === "https:";
  if (relayOn) { $("https").checked = false; $("relayNote").hidden = false; }                 // plain-http channels are worth offering then

  function say(text, bad) { var s = $("status"); s.textContent = text; s.className = "status" + (bad ? " warn" : ""); }
  function load(name) { return fetch(BASE + name + ".json").then(function (r) { if (!r.ok) throw new Error(name + ": HTTP " + r.status); return r.json(); }); }
  function clean(text) { return String(text || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 120); }
  function height(q) { var m = /^(\d+)p?/.exec(q || ""); return m ? +m[1] : 0; }

  var logoOf = {}, channelOf = {};                           // channel id -> the address of its logo (the picture closest to 256 pixels wide); stream address -> channel id
  Promise.all([load("channels"), load("streams"), load("countries"), load("categories"), load("logos").catch(function () { return []; })]).then(function (all) {
    all[4].forEach(function (l) {
      if (!l.channel || !/^(PNG|JPEG|WebP)$/i.test(l.format || "") || !/^https?:\/\//i.test(l.url || "")) return;       // the server takes no SVG
      var best = logoOf[l.channel];
      if (!best || Math.abs((l.width || 0) - 256) < Math.abs((best.width || 0) - 256)) logoOf[l.channel] = { url: l.url, width: l.width };
    });
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
    all[1].forEach(function (s) { if (s.channel && s.url) channelOf[s.url] = s.channel; });
    $("fillLogos").disabled = false;
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
               labels: s.labels || [], logo: logoOf[c.id] ? logoOf[c.id].url : "", on: false, test: "" };
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
    s.testEl.textContent = s.test === "ok" ? "plays" : s.test === "no" ? "fails" : s.test === "relay" ? "via the server?" : s.test === "try" ? "play to try" : s.test === "busy" ? "testing…" : "";
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
  $("all").addEventListener("click", function () { shown.forEach(function (s) { s.on = true; }); render(); });
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
    if (!/[.]m3u8(?:$|[?#])/i.test(s.url)) {                 // a transport stream (a panel such as Dispatcharr) never ends: it is tried by playing it
      s.test = "try"; s.why = "An MPEG-TS stream cannot be checked from here. Save it and play it on its channel page."; paintTest(s);
      return Promise.resolve();
    }
    if (relayOn && secure && /^http:/i.test(s.url)) {        // a secure page cannot even ask a plain-http server: only the relay can say
      s.test = "relay"; s.why = "Plain http cannot be tested from this secure page. Saved, it plays through the server (relay)."; paintTest(s);
      return Promise.resolve();
    }
    s.test = "busy"; paintTest(s);
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 9000);
    return fetch(s.url, { signal: ctl.signal }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then(function (t) {
      if (t.indexOf("#EXTM3U") < 0) throw new Error("not a playlist");
      s.test = "ok"; s.why = "";
    }).catch(function (e) {
      s.test = "no"; s.why = e.name === "AbortError" ? "no answer in 9 seconds" : (e.message === "Failed to fetch" ? "the server does not allow a web page to read it, or is down" : e.message);
      if (relayOn && e.message === "Failed to fetch") { s.test = "relay"; s.why += ". The relay fetches it for you, so it may still play in pluteus."; }       // the usual cause is the missing web-page permission (CORS), which the relay does not need
    })
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
      var viaRelay = ticked().filter(function (s) { return s.test === "relay"; }).length;
      var untestable = ticked().filter(function (s) { return s.test === "try"; }).length;
      say("Tested " + total + ": " + (total - bad - viaRelay - untestable) + " answer, " + bad + " do not" + (viaRelay ? ", " + viaRelay + " can only be tried through the server (keep them: the relay is on)" : "") +
          (untestable ? ", " + untestable + " are transport streams that can only be tried by playing them" : "") + ". Untick the ones that fail.");
      update();
    });
  });

  /* ---- import a playlist (an M3U): the server parses it (and fetches it from an address when the relay is on); the channels are listed like search results ---- */
  function m3uSay(text, bad) { var s = $("m3uStatus"); s.textContent = text; s.className = "status" + (bad ? " warn" : ""); }
  function showImported(list) {
    list.forEach(function (s) { if (s.group) categoryNames[s.group] = s.group; });
    shown = list.map(function (s) {
      return { id: s.id || "", name: s.name, country: "", cats: s.group ? [s.group] : [], url: s.url, quality: "", labels: [], logo: s.logo || "", on: false, test: "" };
    });
    limit = PAGE; render();
  }
  function parsePlaylist(body) {
    m3uSay("Reading the playlist…");
    return fetch(page.save + "/parse", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { m3uSay(res.body.error || "Could not read the playlist.", true); return; }
        showImported(res.body.stations);
        m3uSay(res.body.stations.length + " channels" + (res.body.skipped ? " (" + res.body.skipped + " entries skipped: not a plain http or https address, or over the limit)" : "") +
               ". Tick the ones to save below.");
      })
      .catch(function () { m3uSay("The server could not be reached.", true); });
  }
  $("m3uFile").addEventListener("change", function () {
    var f = this.files && this.files[0];
    if (!f) return;
    if (f.size > 4000000) { m3uSay("That file is larger than 4 MB.", true); return; }
    var reader = new FileReader();
    reader.onload = function () { parsePlaylist({ text: String(reader.result) }); };
    reader.onerror = function () { m3uSay("The file could not be read.", true); };
    reader.readAsText(f);
  });
  /* one channel by its address: it joins the list (ticked) so it is saved like any other */
  $("oneAdd").addEventListener("click", function () {
    var url = $("oneUrl").value.trim(), name = clean($("oneName").value) || "Channel";
    if (!/^https?:\/\/[^\s]+$/i.test(url)) { m3uSay("Give a plain http or https address.", true); return; }
    shown.unshift({ id: "", name: name, country: "", cats: [], url: url, quality: "", labels: [], logo: "", on: true, test: "" });
    limit = Math.max(limit, PAGE); render();
    $("oneName").value = ""; $("oneUrl").value = "";
    m3uSay("Added " + name + ". It is ticked in the list below: choose “Save to my IPTV folder”.");
  });
  if (relayOn) $("m3uUrlBar").hidden = false;
  $("m3uFetch").addEventListener("click", function () {
    var url = $("m3uUrl").value.trim();
    if (!url) { m3uSay("Give the playlist's address first.", true); return; }
    parsePlaylist({ url: url });
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
  /* A save request takes at most 300 channels, so a long list goes in batches of 200, one after the other. Names that repeat get a number first, so a
     second "NRK 1" in a later batch is a new file and not "already exists". */
  function uniqueNames(list) {
    var seen = {};
    return list.map(function (s) {
      var key = s.name.toLowerCase(), n = (seen[key] = (seen[key] || 0) + 1);
      return { s: s, name: n > 1 ? s.name + " " + n : s.name };
    });
  }
  function saveInBatches(list) {
    var BATCH = 200, named = uniqueNames(list), at = 0, total = { saved: 0, skipped: 0, logos: 0, folder: "" };
    function next() {
      if (at >= named.length) return Promise.resolve(total);
      var part = named.slice(at, at + BATCH); at += BATCH;
      log("Saving… " + Math.min(at, named.length) + " of " + named.length);
      return fetch(page.save, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "tv", sub: $("sub").value.trim(), by_country: $("bycountry").checked, overwrite: $("overwrite").checked,
                               stations: part.map(function (p) {
                                 var s = p.s;
                                 return { name: p.name, url: s.url, id: s.id, group: categoryNames[s.cats[0]] || "", country: countries[s.country] || s.country, logo: s.logo };
                               }) })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
        .then(function (res) {
          if (!res.ok) { var e = new Error(res.body.error || "Not saved."); e.partial = total; throw e; }
          total.saved += res.body.saved || 0; total.skipped += res.body.skipped || 0; total.logos += res.body.logos || 0; total.folder = res.body.folder;
          return next();
        });
    }
    return next();
  }
  $("saveServer").addEventListener("click", function () {
    var list = ticked();
    $("saveServer").disabled = true;
    log("Saving…");
    saveInBatches(list)
      .then(function (res) {
        var l = $("log");
        l.textContent = "Saved " + res.saved + " in " + res.folder + (res.logos ? " with " + res.logos + " logos" : "") + "." +
          (res.skipped ? " Left alone because they already exist: " + res.skipped + " (tick “overwrite existing” to replace them)." : "") + " ";
        var a = document.createElement("a");
        a.href = page.browse + (res.folder.indexOf("/") > 0 ? "/" + res.folder.split("/").slice(1).map(encodeURIComponent).join("/") : "");
        a.textContent = "Open the folder"; l.appendChild(a);
      })
      .catch(function (e) { log((e && e.message ? e.message : "Not saved: the server could not be reached.") + (e && e.partial && e.partial.saved ? " (" + e.partial.saved + " channels were saved before this)" : ""), true); })
      .then(function () { update(); });
  });

  /* ---- logos for the channel files already saved: ask the server which lack one, find each logo (the catalogue, by stream address)
     and let the server fetch and save them ---- */
  function fillMissing(kind, lookup) {
    var button = $("fillLogos"), note = $("fillStatus");
    button.disabled = true;
    note.textContent = "Looking through your files…";
    fetch(page.save + "/missing?kind=" + kind, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      var items = d.items || [];
      if (!items.length) { note.textContent = "Every channel file already has a logo."; return null; }
      var found = [];
      items.forEach(function (item) {
        var logo = item.logo || lookup(item);
        if (logo) found.push({ path: item.path, logo: logo });
      });
      var totals = { fetched: 0, failed: 0 }, chain = Promise.resolve();
      for (var b = 0; b < found.length; b += 25) (function (batch) {
        chain = chain.then(function () {
          note.textContent = "Fetching logos… (" + totals.fetched + " saved so far)";
          return fetch(page.save.replace(/stations$/, "stations/logos"), {
            method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: kind, items: batch })
          }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "failed"); return j; }); })
            .then(function (j) { totals.fetched += j.fetched; totals.failed += j.failed; });
        });
      })(found.slice(b, b + 25));
      return chain.then(function () {
        note.textContent = "Done: " + totals.fetched + " logos saved; " + (items.length - found.length) + " files have no logo in the catalogue; " +
          totals.failed + " logos could not be fetched.";
      });
    }).catch(function (e) { note.textContent = "Not finished: " + ((e && e.message) || "the server could not be reached") + "."; })
      .then(function () { button.disabled = false; });
  }
  $("fillLogos").addEventListener("click", function () {
    fillMissing("tv", function (item) { var id = channelOf[item.url]; return id && logoOf[id] ? logoOf[id].url : ""; });
  });

  $("go").addEventListener("click", search);
  $("name").addEventListener("keydown", function (e) { if (e.key === "Enter" && !$("go").disabled) search(); });
  update();
})();
