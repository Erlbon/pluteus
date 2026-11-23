"""The first run in the browser (Windows): the page that makes the first administrator and writes config.toml.

The installed Pluteus lives in the tray and has no console to ask in, so on the first start (no configuration yet) it serves this
one page on 127.0.0.1 only, on a free port, and opens it in the browser. The address carries a random token, so neither another
computer nor a web page that guesses the port can use it. The page asks three things, as the console first-run does: the
administrator's name, a password typed twice, and optionally a first library folder. It writes the configuration with
firstrun.create_config, which checks it exactly as the next start will, and then the page closes and Pluteus starts.
"""
from __future__ import annotations

import html
import secrets
import threading
import webbrowser

from .auth import MIN_PASSWORD

SETUP_PORT = 8190


class Setup:
    """One first run: what the page writes, and the event that says it is done."""

    def __init__(self, config_path: str, port: int = 8080, admin_port: int = 8101):
        self.config_path, self.port, self.admin_port = config_path, port, admin_port
        self.token = secrets.token_urlsafe(24)
        self.done = threading.Event()

    def admin_url(self) -> str:
        return "http://127.0.0.1:%d/libraries" % self.admin_port

    def submit(self, name: str, password: str, again: str, folder: str) -> str:
        """Write the configuration; '' when it was written, else the reason in words (nothing was written)."""
        from .firstrun import SetupError, create_config, name_ok
        name, folder = name.strip(), folder.strip().strip('"')
        if not name_ok(name):
            return ("The name may use letters (a to z), digits, space, - _ . only, and may not start or end with a space or a dot "
                    "(it also becomes the name of your upload folder).")
        if len(password) < MIN_PASSWORD:
            return "The password is too short: at least %d characters." % MIN_PASSWORD
        if password != again:
            return "The two passwords differ."
        try:
            create_config(self.config_path, name, password, folder, port=self.port, admin_port=self.admin_port)
        except (SetupError, ValueError) as err:
            return str(err).replace("\\\\", "\\")           # paths in the checker's words are written as in TOML (doubled)
        self.done.set()
        return ""


STYLE = """
:root { --bg:#f7f6f2; --card:#fff; --text:#1f1e1c; --muted:#5f5e5a; --border:#d3d1c7; --accent:#8a6410; --bad:#a32d2d; }
@media (prefers-color-scheme: dark) { :root { --bg:#1b1b1a; --card:#262624; --text:#ecebe6; --muted:#b4b2a9; --border:#444441;
  --accent:#d4a94f; --bad:#f09595; } }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width:520px; margin:40px auto; padding:0 16px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px 20px; }
h1 { font-size:20px; margin:0 0 4px; color:var(--accent); letter-spacing:.04em; }
p { margin:8px 0; } .muted { color:var(--muted); font-size:14px; } .bad { color:var(--bad); }
label { display:block; margin:12px 0 4px; font-weight:600; }
input { width:100%; box-sizing:border-box; padding:7px 9px; border:1px solid var(--border); border-radius:8px; background:var(--card);
  color:var(--text); font:inherit; }
button { margin-top:16px; padding:8px 18px; border:0; border-radius:8px; background:var(--accent); color:#fff; font:inherit; cursor:pointer; }
"""


def _page(body: str, refresh: str = "") -> str:
    meta = "<meta http-equiv='refresh' content='6;url=%s'>" % html.escape(refresh, quote=True) if refresh else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Pluteus set-up</title>%s<style>%s</style></head><body><main><div class='card'>%s</div></main></body></html>"
            % (meta, STYLE, body))


def form_page(error: str = "", name: str = "admin", folder: str = "") -> str:
    e = html.escape
    problem = "<p class='bad'>%s</p>" % e(error) if error else ""
    return _page(
        "<h1>PLUTEUS</h1><p>Welcome. This is the first start, so make the administrator account. Only this computer can open this "
        "page.</p>%s<form method='post'>"
        "<label for='name'>Administrator name</label><input id='name' name='name' value='%s' autocomplete='username' required>"
        "<label for='pw'>Password (at least %d characters)</label><input id='pw' name='password' type='password' minlength='%d' "
        "autocomplete='new-password' required>"
        "<label for='pw2'>Password again</label><input id='pw2' name='again' type='password' minlength='%d' autocomplete='new-password' required>"
        "<label for='folder'>Folder with your books or comics (optional)</label><input id='folder' name='folder' value='%s' "
        "placeholder='for example D:\\Books or \\\\server\\share\\Books'>"
        "<p class='muted'>Leave it empty to add libraries later on the admin page, which has a folder picker.</p>"
        "<button type='submit'>Create and start</button></form>"
        % (problem, e(name, quote=True), MIN_PASSWORD, MIN_PASSWORD, MIN_PASSWORD, e(folder, quote=True)))


def done_page(admin_url: str) -> str:
    u = html.escape(admin_url, quote=True)
    return _page("<h1>PLUTEUS</h1><p>Done. Pluteus is starting and lives in the tray by the clock from now on.</p>"
                 "<p>The admin page opens in a few seconds: sign in with the name and password you just chose, then add your "
                 "libraries on the Libraries tab. If it does not open, <a href='%s'>open it here</a>.</p>" % u, refresh=admin_url)


def make_app(setup: Setup):
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, PlainTextResponse
    from starlette.routing import Route

    headers = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}   # no-referrer would make the form's Origin "null"

    async def handle(request):
        if not secrets.compare_digest(request.path_params["token"], setup.token):
            return PlainTextResponse("Not found", status_code=404)
        if setup.done.is_set():
            return HTMLResponse(done_page(setup.admin_url()), headers=headers)
        if request.method == "GET":
            return HTMLResponse(form_page(), headers=headers)
        origin = request.headers.get("origin")
        if origin and origin != "http://%s" % request.headers.get("host", ""):
            return PlainTextResponse("Forbidden", status_code=403)
        form = await request.form()
        get = lambda key: str(form.get(key) or "")          # noqa: E731
        error = setup.submit(get("name"), get("password"), get("again"), get("folder"))
        if error:
            return HTMLResponse(form_page(error, get("name"), get("folder")), status_code=400, headers=headers)
        return HTMLResponse(done_page(setup.admin_url()), headers=headers)

    return Starlette(routes=[Route("/{token}", handle, methods=["GET", "POST"])])


def run(config_path: str, cancel: threading.Event | None = None, open_browser=webbrowser.open, on_url=None) -> bool:
    """Serve the set-up page until the configuration is written (True) or `cancel` is set (False). Blocks."""
    import uvicorn

    from .share import free_port
    setup = Setup(config_path, port=free_port(8080), admin_port=free_port(8101, "127.0.0.1"))
    port = free_port(SETUP_PORT, "127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(make_app(setup), host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, name="pluteus-setup-page", daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/%s" % (port, setup.token)
    for _ in range(100):                                     # until it listens, so the browser does not open on a dead address
        if server.started:
            break
        threading.Event().wait(0.05)
    if on_url:
        on_url(url)
    threading.Thread(target=open_browser, args=(url,), name="pluteus-open-browser", daemon=True).start()
    while not setup.done.is_set() and not (cancel and cancel.is_set()) and thread.is_alive():
        setup.done.wait(0.25)
    if setup.done.is_set():
        threading.Event().wait(1.0)                          # let the "Done" page reach the browser first
    server.should_exit = True
    thread.join(timeout=5)
    return setup.done.is_set()
