"""HTML for the admin pages (status, security, users). Same look as the rest of pluteus (base.css and the theme); every
value that comes from outside (names, paths, addresses) is escaped, and nothing from a query string is ever echoed:
results are fixed sentences chosen by a code."""
from __future__ import annotations

import contextvars
import time
from html import escape

from . import __version__
from .web import Ctx, _size, layout, scheme_button

NAV = (("status", "/", "Status"), ("libraries", "/libraries", "Libraries"), ("security", "/security", "Security"), ("users", "/users", "Users"),
       ("debug", "/debug", "Debug"))
FLASH = {
    "unlocked": "The lock is cleared.", "nothing": "There was nothing to clear.", "forgot": "That device is forgotten.",
    "signedout": "Signed out everywhere (sessions and known devices removed).",
    "libadded": "Library added. It is available now.", "libremoved": "Library removed. Nothing on disk was touched.",
    "added": "User added.", "password": "Password changed.", "access": "Access saved.", "removed": "User removed.", "keyrevoked": "App key revoked.", "debugon": "Debug logging is on for 15 minutes.",
    "debugoff": "Debug logging is off.", "debugcleared": "The debug lines are cleared.", "stopped": "That stream is stopped.",
}


def ago(ts: float, now: float | None = None) -> str:
    seconds = max(0, int((time.time() if now is None else now) - ts))
    if seconds < 90:
        return "just now"
    for limit, unit, size in ((5400, "min", 60), (172800, "h", 3600), (10 ** 9, "days", 86400)):
        if seconds < limit:
            return "%d %s ago" % (round(seconds / size), unit)
    return ""


def duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%d d %d h" % (days, hours)
    return "%d h %d min" % (hours, minutes) if hours else "%d min" % minutes


current_site_link: contextvars.ContextVar = contextvars.ContextVar("current_site_link", default="")    # set per request


def _header(active: str) -> str:
    links = "".join("<a class='pl-admin-tab%s' href='%s'>%s</a>" % (" pl-admin-on" if key == active else "", path, label)
                    for key, path, label in NAV)
    site = current_site_link.get()
    # The right-hand side is laid out like the main site's bar: contrast button, the link to the other site ("Admin" there,
    # "Pluteus" here), then Sign out.
    other = "<a href='%s'>Pluteus</a>" % escape(site) if site else ""
    return ("<header class='pl-bar'><span class='pl-brand'>Pluteus Admin</span> <span class='pl-muted pl-ver'>%s</span>"
            "<nav class='pl-admin-nav'>%s</nav><span class='pl-spacer'></span>%s%s"
            "<form class='pl-inline' method='post' action='/logout'>"
            "<button class='pl-btn' type='submit'>Sign out</button></form></header>"
            % (escape(__version__), links, scheme_button(), other))


def signed_out_page(ctx: Ctx) -> str:
    return layout("Pluteus Admin: signed out", "<main class='pl-card pl-admin-box'><h1>Signed out</h1>"
                  "<p>Your browser has forgotten the login for this page.</p><p><a class='pl-btn' href='/'>Sign in again</a></p></main>",
                  ctx, bar=False)


def page(ctx: Ctx, title: str, active: str, body: str, flash: str = "", error: str = "", scripts: tuple = ()) -> str:
    note = ""
    if error:
        note = "<p class='pl-error' role='alert'>%s</p>" % escape(error[:1].upper() + error[1:])
    elif flash in FLASH:
        note = "<p class='pl-flash' role='status'>%s</p>" % escape(FLASH[flash])
    main = ("<main class='pl-main pl-admin'><h1>%s</h1>%s%s"
            "<p class='pl-muted pl-foot'>This page is only for the local network: do not route or forward its port to the "
            "internet.</p></main>" % (escape(title), note, body))
    return layout("Pluteus Admin: " + title, _header(active) + main, ctx, bar=False,
                  scripts=(ctx.urls.static("uploads.js"),) + tuple(scripts))


def _table(headers: list, rows: list, empty: str) -> str:
    if not rows:
        return "<p class='pl-muted'>%s</p>" % escape(empty)
    head = "".join("<th>%s</th>" % escape(h) for h in headers)
    return "<div class='pl-admin-scroll'><table class='pl-admin-table'><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>" % (
        head, "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell for cell in row) for row in rows))


def _post(action: str, fields: dict, label: str, confirm: str = "", danger: bool = False) -> str:
    hidden = "".join("<input type='hidden' name='%s' value='%s'>" % (escape(k), escape(str(v))) for k, v in fields.items())
    data = " data-confirm='%s'" % escape(confirm) if confirm else ""
    return ("<form class='pl-inline' method='post' action='%s'%s>%s<button class='pl-btn pl-btn-small%s' type='submit'>%s</button></form>"
            % (escape(action), data, hidden, " pl-btn-danger" if danger else "", escape(label)))


USER_ICON = ("<svg class='pl-user-icon' viewBox='0 0 24 24' width='16' height='16' aria-hidden='true' fill='none' "
             "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
             "<circle cx='12' cy='8' r='4'/><path d='M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7'/></svg>")


def _box(title: str, inner: str) -> str:
    return "<section class='pl-card pl-admin-box'><h2>%s</h2>%s</section>" % (escape(title), inner)


# ---- status ----------------------------------------------------------------------------------------------------------------
SLOW_SECONDS = 2.0          # a library that takes longer than this to answer a listing is shown yellow
LOW_DISK = 0.05             # less than this share of a disk free is shown yellow
_LIGHT_CLASS = {"ok": "pl-dot-ok", "warn": "pl-dot-warn", "bad": "pl-dot-bad", "off": "pl-dot-off"}


def _light(level: str, text: str) -> str:
    """A traffic-light dot ('ok' green, 'warn' yellow, 'bad' red) followed by a short text."""
    return "<span class='pl-dot %s'></span>%s" % (_LIGHT_CLASS[level], escape(text))


def _disk_level(free, total):
    """('warn', 'low disk space') when under LOW_DISK is free, else None."""
    if free is not None and total and free / total < LOW_DISK:
        return "warn", "low disk space"
    return None


def _root_light(r: dict) -> str:
    if not r["ok"]:
        return _light("bad", "NOT available")
    if r.get("seconds", 0) > SLOW_SECONDS:
        return _light("warn", "slow (%.1f s)" % r["seconds"])
    low = _disk_level(r["free"], r["total"])
    return _light(*low) if low else _light("ok", "ok")


def _update_cell(u) -> str:
    """The note beside the version: up to date, a newer one exists, a security advisory, or why we do not know."""
    if not u:
        return "<span class='pl-muted'>update check is off (update_check in config.toml)</span>"
    state = u.get("state")
    if state == "security":
        return _light("bad", "security issue in this version")
    if state == "update":
        return _light("warn", "%s is available (git pull, then docker compose build --pull and docker compose up -d)" % u.get("latest", "a newer version"))
    if state == "ok":
        return _light("ok", "up to date")
    return _light("off", "checking..." if state == "checking" else "could not check for updates")


def _security_alert(u) -> str:
    """A red notice above everything for each published advisory that names this version."""
    out = ""
    for adv in (u or {}).get("advisories", []):
        link = adv.get("url", "")
        details = " <a href='%s'>Details</a>" % escape(link) if link.startswith("https://github.com/") else ""
        fixed = " Fixed in %s." % escape(adv["fixed"]) if adv.get("fixed") else ""
        out += ("<p class='pl-error' role='alert'><strong>Security issue in this version:</strong> %s (%s%s).%s%s</p>"
                % (escape(adv.get("summary") or "see the advisory"), escape(adv.get("id", "")),
                   ", " + escape(adv["severity"]) if adv.get("severity") else "", fixed, details))
    return out


def _services_box(services) -> str:
    """Which optional servers this installation answers, the config line that switches each one, and the address apps use."""
    if not services:
        return ""
    rows = []
    for s in services:
        if s["on"]:
            state = _light("ok", "on")
        else:
            state = _light("off", "off") + " <span class='pl-muted'>&middot; <code>%s</code></span>" % escape(s["setting"])
        address = ("<code>%s</code>" % escape(s["address"])) if (s["on"] and s["address"]) else "<span class='pl-muted'>&ndash;</span>"
        keys = escape(s["keys"]) if (s["on"] and s["keys"]) else "<span class='pl-muted'>&ndash;</span>"
        rows.append([escape(s["name"]), state, address, keys])
    note = ("<p class='pl-muted'>Switch a service on with its line under <code>[server]</code> in <code>config.toml</code> and restart. "
            "The address is added to your site address (the part before it).</p>")
    return _box("Services", _table(["Service", "State", "Address", "Login"], rows, "") + note)


def _relay_box(r) -> str:
    """The stream relay: what is open now (with a Stop), what each user has carried since the start, and who has stored streams (counts only)."""
    if not r:
        return ""
    if r["key_id"]:
        key = _light("ok", "key from %s" % escape(r["key_source"])) + " <span class='pl-muted'>&middot; fingerprint <code>%s</code></span>" % escape(r["key_id"])
    else:
        key = _light("bad", "stored streams off") + " <span class='pl-muted'>&middot; %s</span>" % escape(r["problem"] or "no key")
    now = time.time()
    def quality(s: dict) -> list:
        """Rate and the two kinds of stall: the SOURCE being silent for over a second, and the BROWSER'S connection taking over a second to take data."""
        seconds = max(1.0, now - s["since"])
        rate = "%.0f kB/s" % (s["bytes"] / 1024 / seconds)
        source = ("<span class='pl-error'>%d (longest %.1f s)</span>" % (s["stalls"], s["longest"])) if s["stalls"] else "0"
        browser = ("<span class='pl-error'>%d</span>" % s["slow_client"]) if s["slow_client"] else "0"
        return [rate, source, browser]
    rows = [[escape(s["user"]), escape(s["kind"]), escape(s["label"]), escape(ago(s["since"], now)), _size(s["bytes"]), *quality(s),
             _post("/relay/stop", {"id": s["id"]}, "Stop", "Stop this stream? The listener's player will end.", True)] for s in r["open"]]
    open_html = _table(["User", "Kind", "Station (file#number)", "Since", "Carried", "Rate", "Source stalls", "Slow browser link", ""], rows, "No audio stream is open.")
    open_help = ("<p class='pl-muted'>A stream that is spotty shows where: <b>source stalls</b> mean the stream itself went quiet for over a second before it "
                 "reached pluteus (the provider or Dispatcharr); <b>slow browser link</b> means pluteus had the data but the connection to the listener "
                 "(upload speed, the proxy) could not take it fast enough.</p>") if rows else ""
    channel_rows = [[escape(user), escape(label), _post("/relay/stop-channel", {"user": user, "key": label}, "Stop",
                                                          "Stop this channel? It stays refused for two minutes.", True)] for user, label in r["channels"]]
    channels = _table(["User", "TV channel (file#number)", ""], channel_rows, "") if channel_rows else ""
    totals = ", ".join("%s %s" % (escape(n), _size(b)) for n, b in sorted(r["totals"].items())) or "nothing yet"
    stored = ", ".join("%s (%d)" % (escape(n), c) for n, c in sorted(r["stored"].items())) or "none"
    return _box("Stream relay", "<p>%s &middot; limits %d per user, %d in all</p>%s%s%s<p>Carried since the start: %s.</p>"
                "<p>Stored streams (logins and headers, encrypted; counts only): %s.</p>" % (key, r["per_user"], r["total"], open_html, open_help, channels, totals, stored))


def status_page(ctx: Ctx, d: dict) -> str:
    n = d.get("names", {})

    def counted(counts: dict) -> str:
        return escape(", ".join("%s (%d)" % (name, c) if c > 1 else name for name, c in sorted(counts.items())))

    facts = _table(["", "", ""], [["Version", escape(d["version"]), _update_cell(d.get("update"))], ["Python", escape(d["python"]), ""],
                                  ["Running for", escape(duration(d["uptime"])), ""],
                                  ["RAR and 7z comics", _light("ok", "bsdtar found") if d.get("bsdtar") else _light("warn", "bsdtar not found: CBR and CB7 files cannot be read in the reader (CBZ and CBT still can)"), ""],
                                  ["Users", str(d["counts"]["users"]), escape(", ".join(n.get("users", [])))],
                                  ["Active sessions", str(d["counts"]["sessions"]), counted(n.get("sessions", {}))],
                                  ["Known devices", str(d["counts"]["devices"]), counted(n.get("devices", {}))],
                                  ["Locked names", str(d["counts"]["locked"]), escape(", ".join(n.get("locked", [])))]], "")
    roots = []
    for r in d["roots"]:
        state = _root_light(r)
        space = "%s free of %s" % (_size(r["free"]), _size(r["total"])) if r["free"] is not None else ""
        roots.append([escape(r["name"]), "<code>%s</code>" % escape(r["path"]), state, escape(space)])
    services = _services_box(d.get("services"))
    boxes = [_box("Server", facts)] + ([services] if services else []) + ([_relay_box(d.get("relay"))] if d.get("relay") else []) + [_box("Libraries", _table(["Name", "Folder", "State", "Disk"], roots, "No libraries."))]
    up = d["uploads"]
    if up:
        rows = [[escape(u["name"]), _size(u["size"])] for u in up["users"]]
        space = "%s free of %s" % (_size(up["free"]), _size(up["total"])) if up["free"] is not None else ""
        quota = "no per-user limit" if not up["quota_mb"] else "limit %d MB per user" % up["quota_mb"]
        low = _disk_level(up["free"], up["total"])
        light = _light(*low) if low else _light("ok", "ok")
        boxes.append(_box("Uploads", "<p>%s &middot; Folder <code>%s</code> &middot; %s &middot; %s</p>%s"
                          % (light, escape(up["dir"]), escape(space), escape(quota), _table(["User", "Size"], rows, "Nothing uploaded yet."))))
    st = d["state"]
    backup = "newest %s" % ago(st["newest_backup"]) if st["newest_backup"] else "none yet"
    if not st["persistent"]:
        light, where = _light("bad", "memory only"), "lost on restart (set data_dir)"
    elif not st["backups"]:
        light, where = _light("warn", "no backup yet"), "<code>%s</code>" % escape(st["path"])
    else:
        light, where = _light("ok", "ok"), "<code>%s</code>" % escape(st["path"])
    boxes.append(_box("State (sessions, progress)", "<p>%s &middot; %s &middot; %s &middot; %d backup(s), %s</p>"
                      % (light, where, _size(st["size"]), st["backups"], escape(backup))))
    return page(ctx, "Status", "status", _security_alert(d.get("update")) + "".join(boxes))


# ---- libraries --------------------------------------------------------------------------------------------------------------
def libraries_page(ctx: Ctx, roots: list, can_browse: bool, can_edit: bool, flash: str = "", error: str = "") -> str:
    if not can_edit:
        return page(ctx, "Libraries", "libraries", "<p>This server was started without a configuration file, so libraries cannot be "
                    "changed here.</p>", flash, error)
    rows = []
    for r in roots:
        space = "%s free of %s" % (_size(r["free"]), _size(r["total"])) if r["free"] is not None else ""
        rows.append([escape(r["name"]), "<code>%s</code>" % escape(r["path"]),
                     _light("ok", "ok") if r["ok"] else _light("bad", "NOT available"), escape(space),
                     _post("/libraries/remove", {"name": r["name"]}, "Remove",
                           "Remove the library %s? Nothing on disk is touched and reading progress stays on the server, but "
                           "people limited to this library lose access." % r["name"], True)])
    table = _table(["Name", "Folder", "State", "Disk", ""], rows, "No libraries.")
    browse = ("<button class='pl-btn' type='button' id='lib-browse'>Browse folders&hellip;</button>"
              "<div class='pl-picker' id='lib-picker' data-browse='/libraries/browse' hidden></div>") if can_browse else (
        "<p class='pl-muted'>The folder picker is off here: type the path. (In Docker it is the path inside the container, for "
        "example <code>/library/music</code>, and the folder must be mounted in <code>docker-compose.yml</code>. Set "
        "<code>library_bases</code> in <code>config.toml</code> to enable the picker below your mounted folders.)</p>")
    add = ("<form class='pl-admin-form' method='post' action='/libraries/add'>"
           "<label class='pl-field'><span>Folder</span><input type='text' name='path' id='lib-path' autocapitalize='off' required "
           "placeholder='D:/Music or /home/me/music'></label>%s"
           "<label class='pl-field'><span>Name (letters, digits, - and _)</span>"
           "<input type='text' name='name' id='lib-name' autocapitalize='off' required placeholder='music'></label>"
           "<button class='pl-btn pl-btn-primary' type='submit'>Add library</button></form>" % browse)
    note = ("<p class='pl-muted'>A new library is available as soon as you add it. Users whose folders are set to <strong>All "
            "folders</strong> see it at once; for the others tick it on the Users tab. Pluteus only reads these folders.</p>")
    scripts = (ctx.urls.static("admin-libraries.js"),) if can_browse else ()
    return page(ctx, "Libraries", "libraries", _box("Libraries", table + note) + _box("Add a library", add), flash, error, scripts)


# ---- security ---------------------------------------------------------------------------------------------------------------
def debug_page(ctx: Ctx, always: bool, remaining: int, lines: list, flash: str = "") -> str:
    """Debug logging: switch it on for a while, read what the site was asked, download it with the diagnostics."""
    if always:
        state = _light("warn", "on all the time (debug = true in the config)") + " <span class='pl-muted'>&middot; remove that line and restart to stop it</span>"
        controls = ""
    elif remaining:
        state = _light("warn", "on, switches itself off in %s" % duration(remaining))
        controls = _post("/debug/on", {}, "Another 15 minutes") + " " + _post("/debug/off", {}, "Switch off")
    else:
        state = _light("off", "off")
        controls = _post("/debug/on", {}, "Switch on for 15 minutes")
    controls += " " + _post("/debug/clear", {}, "Clear the lines") if lines else ""
    note = ("<p class='pl-muted'>While it is on, every request to the site leaves one line here and in the log: the time, what was asked "
            "(the names of query parameters, never their values), the answer, who asked (address, what the proxy said, the app's name), "
            "and for a login what could be said about it: how long the name and password are, whether the name matches a user, why it was "
            "refused. <strong>Never recorded:</strong> passwords, keys, cookie values, query values, file contents. Nothing is written to disk, "
            "and a restart forgets the lines.</p>")
    rows = [[escape(t), escape(text)] for t, text in lines[:200]]
    table = _table(["Time", "Request"], rows, "Nothing recorded yet." if (always or remaining) else "Switch it on, try the thing that fails, then come back.")
    download = ("<p><a class='pl-btn' href='/debug/download'>Download diagnostics</a> <span class='pl-muted'>"
                "(version, settings summary and these lines, as one text file to send with a bug report)</span></p>")
    return page(ctx, "Debug", "debug", _box("Debug logging", "<p>%s</p><p>%s</p>%s" % (state, controls, note)) + download + _box("What the site was asked", table), flash)


def security_page(ctx: Ctx, locks: list, devices: list, sessions: dict, flash: str = "") -> str:
    lock_rows = []
    for r in locks:
        state = _light("warn", "locked for %s" % duration(r["locked_for"])) if r["locked_for"] else _light("ok", "not locked")
        lock_rows.append([escape(r["name"]), state, str(r["locks"]), str(r["recent_failures"]),
                          _post("/security/unlock", {"name": r["name"]}, "Clear")])
    locks_html = _box("Wrong passwords and locks", _table(
        ["Name", "State", "Locks so far", "Recent wrong passwords", ""], lock_rows,
        "No wrong passwords at the moment.") + "<p class='pl-muted'>A name is locked after 5 wrong passwords from a device "
        "that has not signed in as that user before; each lock is longer than the last (5 min, 30 min, 3 h, 6 h). "
        "Known devices are not stopped by a lock. A restart also clears every lock.</p>")
    dev_rows = [[escape(d["username"]), escape(ago(d["first_seen"])), escape(ago(d["last_seen"])), escape(d["last_ip"] or "?"),
                 _post("/security/forget", {"id": d["id"]}, "Forget", "Forget this device? It will be treated like a stranger.")]
                for d in devices]
    dev_html = _box("Known devices (browsers that have signed in)", _table(
        ["User", "First seen", "Last seen", "Last address", ""], dev_rows, "No known devices yet."))
    names = sorted(set(sessions) | {d["username"] for d in devices})
    out_rows = [[escape(n), str(sessions.get(n, 0)),
                 _post("/security/signout", {"name": n}, "Sign out everywhere", "Sign %s out of every browser and forget their devices?" % n, True)]
                for n in names]
    out_html = _box("Sign a user out everywhere", _table(["User", "Active sessions", ""], out_rows, "Nobody is signed in.") +
                    "<p class='pl-muted'>Removes every login session and known device of that user. Do this if a password may have "
                    "leaked, together with changing the password.</p>")
    return page(ctx, "Security", "security", locks_html + dev_html + out_html, flash)


# ---- users ------------------------------------------------------------------------------------------------------------------
def _roots_boxes(root_names: list, chosen: tuple) -> str:
    everything = "*" in chosen
    boxes = ("<label class='pl-check'><input type='checkbox' name='all'%s> All folders (also ones added later)</label>"
             % (" checked" if everything else ""))
    boxes += "".join("<label class='pl-check'><input type='checkbox' name='root' value='%s'%s> %s</label>"
                     % (escape(n), " checked" if n in chosen else "", escape(n)) for n in root_names)
    return "<fieldset class='pl-fieldset'><legend>Folders this user may open (none ticked = none)</legend>%s</fieldset>" % boxes


def roots_cell(roots: tuple) -> str:
    """The Folders column: 'all', the names, or a red 'none'."""
    if "*" in roots:
        return "all"
    return escape(", ".join(roots)) if roots else "<span class='pl-error'>none</span>"


def _flags(adult: bool, upload: bool, admin: bool, notes: bool = False) -> str:
    return ("<label class='pl-check'><input type='checkbox' name='adult'%s> May see adult folders</label>"
            "<label class='pl-check'><input type='checkbox' name='upload'%s> Personal upload folder</label>"
            "<label class='pl-check'><input type='checkbox' name='notes'%s> May write shared notes (into libraries set to notes = \"edit\")</label>"
            "<label class='pl-check'><input type='checkbox' name='admin'%s> May use this admin page</label>"
            % (" checked" if adult else "", " checked" if upload else "", " checked" if notes else "", " checked" if admin else ""))


RIGHTS = (("Adult", "adult"), ("Uploads", "upload"), ("Notes", "notes"), ("Admin", "admin"))    # one table column each; add a pair for a new right


def _key_form(name: str, has_key: bool) -> str:
    """The app key controls of one user: one key for reader apps, music apps, KOReader sync and WebDAV (read-only)."""
    state = ("A key is active." if has_key else "No key yet: apps cannot log in with a key as this user.")
    make = ("<form class='pl-admin-form' method='post' action='/users/appkey'><input type='hidden' name='name' value='%s'>"
            "<p class='pl-muted'>%s</p><button class='pl-btn' type='submit'>%s</button></form>"
            % (escape(name), escape(state), "Make a new key (the old one stops working)" if has_key else "Make a key"))
    return make + (_post("/users/appkey/revoke", {"name": name}, "Revoke the key", "Revoke the app key of %s? Apps using it stop working until they get a new one." % name, True) if has_key else "")


def _new_key_box(new_key: tuple, address: str) -> str:
    name, secret = new_key
    return _box("App key for %s" % name,
                "<p>Copy it now: it is shown only this once.</p><p><code class='pl-secret'>%s</code></p>"
                "<p class='pl-muted'>Use it as the <b>password</b>, with the user name <code>%s</code>, in reader apps (OPDS: PocketBook, "
                "Moon+ ...), music apps (Subsonic: Symfonium, Tempo ...; the server address is <code>%s</code>), KOReader's progress sync "
                "(the address ends in /kosync) and WebDAV. A key can only read: no uploads, no changes, no admin page. Subsonic apps that "
                "support API keys can use it as the key without a user name.</p>"
                "<p class='pl-muted'><b>A reader app that cannot log in</b> (the PocketBook Android app does not send the login to this server): put the "
                "key in the catalogue address and leave the login boxes empty: <code>%s/k/%s/opds</code>. For use away from home, replace "
                "<code>%s</code> with your public address (the one with https). Anyone who has that address can read what this user may read, so "
                "keep it private; revoking the key ends it.</p>"
                % (escape(secret), escape(name), escape(address), escape(address), escape(secret), escape(address)))


def users_page(ctx: Ctx, users: list, root_names: list, me: str, flash: str = "", error: str = "",
               can_edit: bool = True, keys: dict | None = None, new_key: tuple | None = None,
               address: str = "") -> str:
    if not can_edit:
        return page(ctx, "Users", "users", "<p>This server was started without a configuration file, so users cannot be "
                    "changed here.</p>", flash, error)
    rows = []
    for u in users:
        rights = [_light("ok", "yes") if getattr(u, attr) else "<span class='pl-muted'>-</span>" for _, attr in RIGHTS]
        edit = ("<details><summary>Edit</summary>"
                "<form class='pl-admin-form' method='post' action='/users/password'><input type='hidden' name='name' value='%s'>"
                "<label class='pl-field'><span>New password (at least 8 characters)</span><input type='password' name='password' "
                "autocomplete='new-password' required></label><label class='pl-field'><span>Again</span>"
                "<input type='password' name='again' autocomplete='new-password' required></label>"
                "<button class='pl-btn' type='submit'>Change password</button></form>"
                "<form class='pl-admin-form' method='post' action='/users/access'><input type='hidden' name='name' value='%s'>%s%s"
                "<button class='pl-btn' type='submit'>Save access</button></form>%s</details>"
                % (escape(u.name), escape(u.name), _roots_boxes(root_names, u.roots), _flags(u.adult, u.upload, u.admin, u.notes),
                   _key_form(u.name, u.name in (keys or {})) +
                   ("" if u.name == me else _post("/users/remove", {"name": u.name}, "Remove user",
                                                  "Remove %s? Their upload folder and reading progress stay on the server." % u.name, True))))
        rows.append([USER_ICON + escape(u.name) + (" <span class='pl-muted'>(you)</span>" if u.name == me else ""),
                     roots_cell(u.roots)] + rights + [edit])
    table = _table(["User", "Folders"] + [label for label, _ in RIGHTS] + [""], rows, "No users.")
    add = ("<form class='pl-admin-form' method='post' action='/users/add'>"
           "<label class='pl-field'><span>User name</span><input type='text' name='name' autocapitalize='off' required></label>"
           "<label class='pl-field'><span>Password (at least 8 characters)</span><input type='password' name='password' "
           "autocomplete='new-password' required></label><label class='pl-field'><span>Again</span>"
           "<input type='password' name='again' autocomplete='new-password' required></label>%s%s"
           "<button class='pl-btn pl-btn-primary' type='submit'>Add user</button></form>"
           % (_roots_boxes(root_names, ()), _flags(False, True, False)))
    note = ("<p class='pl-muted'>Changes reach the running server within a few seconds. Settings other than users live in "
            "<code>config.toml</code> and need a restart.</p>")
    shown = _new_key_box(new_key, address) if new_key else ""
    return page(ctx, "Users", "users", shown + _box("Users", table + note) + _box("Add a user", add), flash, error)
