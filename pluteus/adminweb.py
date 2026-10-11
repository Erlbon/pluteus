"""HTML for the admin pages (status, security, users). Same look as the rest of pluteus (base.css and the theme); every
value that comes from outside (names, paths, addresses) is escaped, and nothing from a query string is ever echoed:
results are fixed sentences chosen by a code."""
from __future__ import annotations

import time
from html import escape

from .web import Ctx, _size, layout, scheme_button

NAV = (("status", "/", "Status"), ("security", "/security", "Security"), ("users", "/users", "Users"))
FLASH = {
    "unlocked": "The lock is cleared.", "nothing": "There was nothing to clear.", "forgot": "That device is forgotten.",
    "signedout": "Signed out everywhere (sessions and known devices removed).",
    "added": "User added.", "password": "Password changed.", "access": "Access saved.", "removed": "User removed.",
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


def _header(active: str) -> str:
    links = "".join("<a class='pl-admin-tab%s' href='%s'>%s</a>" % (" pl-admin-on" if key == active else "", path, label)
                    for key, path, label in NAV)
    return ("<header class='pl-bar'><span class='pl-brand'>pluteus admin</span><nav class='pl-admin-nav'>%s</nav>"
            "<span class='pl-spacer'></span>%s</header>" % (links, scheme_button()))


def page(ctx: Ctx, title: str, active: str, body: str, flash: str = "", error: str = "") -> str:
    note = ""
    if error:
        note = "<p class='pl-error' role='alert'>%s</p>" % escape(error[:1].upper() + error[1:])
    elif flash in FLASH:
        note = "<p class='pl-flash' role='status'>%s</p>" % escape(FLASH[flash])
    main = ("<main class='pl-main pl-admin'><h1>%s</h1>%s%s"
            "<p class='pl-muted pl-foot'>This page is only for the local network: do not route or forward its port to the "
            "internet.</p></main>" % (escape(title), note, body))
    return layout("pluteus admin: " + title, _header(active) + main, ctx, bar=False,
                  scripts=(ctx.urls.static("uploads.js"),))


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


def _box(title: str, inner: str) -> str:
    return "<section class='pl-card pl-admin-box'><h2>%s</h2>%s</section>" % (escape(title), inner)


# ---- status ----------------------------------------------------------------------------------------------------------------
def status_page(ctx: Ctx, d: dict) -> str:
    facts = _table(["", ""], [["Version", escape(d["version"])], ["Python", escape(d["python"])],
                              ["Running for", escape(duration(d["uptime"]))],
                              ["Users", str(d["counts"]["users"])], ["Active sessions", str(d["counts"]["sessions"])],
                              ["Known devices", str(d["counts"]["devices"])], ["Locked names", str(d["counts"]["locked"])]], "")
    roots = []
    for r in d["roots"]:
        state = "<span class='pl-ok'>reachable</span>" if r["ok"] else "<span class='pl-error'>NOT reachable</span>"
        space = "%s free of %s" % (_size(r["free"]), _size(r["total"])) if r["free"] is not None else ""
        roots.append([escape(r["name"]), "<code>%s</code>" % escape(r["path"]), state, escape(space)])
    boxes = [_box("Server", facts), _box("Libraries", _table(["Name", "Folder", "State", "Disk"], roots, "No libraries."))]
    up = d["uploads"]
    if up:
        rows = [[escape(u["name"]), _size(u["size"])] for u in up["users"]]
        space = "%s free of %s" % (_size(up["free"]), _size(up["total"])) if up["free"] is not None else ""
        quota = "no per-user limit" if not up["quota_mb"] else "limit %d MB per user" % up["quota_mb"]
        boxes.append(_box("Uploads", "<p>Folder <code>%s</code> &middot; %s &middot; %s</p>%s"
                          % (escape(up["dir"]), escape(space), escape(quota), _table(["User", "Size"], rows, "Nothing uploaded yet."))))
    st = d["state"]
    where = "<code>%s</code>" % escape(st["path"]) if st["persistent"] else "<span class='pl-error'>memory only: lost on restart (set data_dir)</span>"
    backup = "newest %s" % ago(st["newest_backup"]) if st["newest_backup"] else "none yet"
    boxes.append(_box("State (sessions, progress)", "<p>%s &middot; %s &middot; %d backup(s), %s</p>"
                      % (where, _size(st["size"]), st["backups"], escape(backup))))
    return page(ctx, "Status", "status", "".join(boxes))


# ---- security ---------------------------------------------------------------------------------------------------------------
def security_page(ctx: Ctx, locks: list, devices: list, sessions: dict, flash: str = "") -> str:
    lock_rows = []
    for r in locks:
        state = ("<strong>locked for %s</strong>" % escape(duration(r["locked_for"]))) if r["locked_for"] else "not locked"
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
    boxes = "".join("<label class='pl-check'><input type='checkbox' name='root' value='%s'%s> %s</label>"
                    % (escape(n), " checked" if n in chosen else "", escape(n)) for n in root_names)
    return "<fieldset class='pl-fieldset'><legend>Folders (none ticked = all)</legend>%s</fieldset>" % boxes


def _flags(adult: bool, upload: bool, admin: bool) -> str:
    return ("<label class='pl-check'><input type='checkbox' name='adult'%s> May see adult folders</label>"
            "<label class='pl-check'><input type='checkbox' name='upload'%s> Personal upload folder</label>"
            "<label class='pl-check'><input type='checkbox' name='admin'%s> May use this admin page</label>"
            % (" checked" if adult else "", " checked" if upload else "", " checked" if admin else ""))


def users_page(ctx: Ctx, users: list, root_names: list, me: str, flash: str = "", error: str = "",
               can_edit: bool = True) -> str:
    if not can_edit:
        return page(ctx, "Users", "users", "<p>This server was started without a configuration file, so users cannot be "
                    "changed here.</p>", flash, error)
    rows = []
    for u in users:
        flags = ", ".join(f for f, on in (("adult", u.adult), ("uploads", u.upload), ("admin", u.admin)) if on) or "-"
        edit = ("<details><summary>Edit</summary>"
                "<form class='pl-admin-form' method='post' action='/users/password'><input type='hidden' name='name' value='%s'>"
                "<label class='pl-field'><span>New password (at least 8 characters)</span><input type='password' name='password' "
                "autocomplete='new-password' required></label><label class='pl-field'><span>Again</span>"
                "<input type='password' name='again' autocomplete='new-password' required></label>"
                "<button class='pl-btn' type='submit'>Change password</button></form>"
                "<form class='pl-admin-form' method='post' action='/users/access'><input type='hidden' name='name' value='%s'>%s%s"
                "<button class='pl-btn' type='submit'>Save access</button></form>%s</details>"
                % (escape(u.name), escape(u.name), _roots_boxes(root_names, u.roots), _flags(u.adult, u.upload, u.admin),
                   "" if u.name == me else _post("/users/remove", {"name": u.name}, "Remove user",
                                                 "Remove %s? Their upload folder and reading progress stay on the server." % u.name, True)))
        rows.append([escape(u.name) + (" <span class='pl-muted'>(you)</span>" if u.name == me else ""),
                     escape(", ".join(u.roots) or "all"), escape(flags), edit])
    table = _table(["User", "Folders", "Rights", ""], rows, "No users.")
    add = ("<form class='pl-admin-form' method='post' action='/users/add'>"
           "<label class='pl-field'><span>User name</span><input type='text' name='name' autocapitalize='off' required></label>"
           "<label class='pl-field'><span>Password (at least 8 characters)</span><input type='password' name='password' "
           "autocomplete='new-password' required></label><label class='pl-field'><span>Again</span>"
           "<input type='password' name='again' autocomplete='new-password' required></label>%s%s"
           "<button class='pl-btn pl-btn-primary' type='submit'>Add user</button></form>"
           % (_roots_boxes(root_names, ()), _flags(False, True, False)))
    note = ("<p class='pl-muted'>Changes reach the running server within a few seconds. Settings other than users live in "
            "<code>config.toml</code> and need a restart.</p>")
    return page(ctx, "Users", "users", _box("Users", table + note) + _box("Add a user", add), flash, error)
