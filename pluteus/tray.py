"""The installed Windows Pluteus: an icon in the tray by the clock that keeps the server running while you are logged on.

Started by pluteus-tray.exe (the installer's Start menu entry, and at logon when "Start when I log on" is ticked). It runs as you,
so it reads your libraries on network shares (\\\\server\\share) and mapped drives (Z:) exactly as Explorer does, which a boot-time
service cannot. The server runs in a child process under the supervisor (pluteus/supervise.py): it is started again after a crash,
its output goes to pluteus.log in the settings folder, and it ends with the tray (a job object, so never an orphan holding the port).

The first time, with no configuration yet, the set-up page (pluteus/setupweb.py) opens in the browser instead.

Menu: Open Pluteus, Admin page, Start when I log on, Show the log, Quit Pluteus. A left click opens Pluteus. Only one tray runs per
logged-on user: starting it again opens Pluteus in the browser. `pluteus-tray.exe --quit` asks a running tray to quit (the
installer uses it before it replaces the files).

Plain ctypes on the Win32 API (Shell_NotifyIcon), so there is no extra package to install or lock.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

from . import __version__

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Pluteus"
MUTEX_NAME = "Local\\PluteusTray"
WINDOW_CLASS = "PluteusTrayWindow"

ID_OPEN, ID_ADMIN, ID_LOGON, ID_LOG, ID_QUIT, ID_SETUP = range(1, 7)


# ---- what does not need a window (tested on any system) ----------------------------------------------------------------------
def run_command() -> str:
    """The command written to the Run key: this program with --autostart (no balloon at logon)."""
    if getattr(sys, "frozen", False):
        return '"%s" --autostart' % sys.executable
    exe = sys.executable
    windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return '"%s" -m pluteus tray --autostart' % (windowless if os.path.isfile(windowless) else exe)


def starts_at_logon() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_start_at_logon(on: bool) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, run_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def log_path(config_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "pluteus.log")


def site_urls(cfg) -> tuple:
    """(the site, the admin page or '') as this computer opens them."""
    host = cfg.host if cfg.host not in ("", "0.0.0.0", "::") else "127.0.0.1"
    host = "[%s]" % host if ":" in host else host
    site = "http://%s:%d/" % (host, cfg.port)
    admin = "http://%s:%d/" % (cfg.admin_host if cfg.admin_host not in ("", "0.0.0.0", "::") else "127.0.0.1", cfg.admin_port) \
        if cfg.admin_port else ""
    return site, admin


def tooltip(state: str, port: int | None) -> str:
    text = "Pluteus %s" % __version__
    if port and state == "running":
        return "%s · running on port %d" % (text, port)
    return ("%s · %s" % (text, state) if state else text)[:127]


# ---- the Win32 part ----------------------------------------------------------------------------------------------------------
def _win32():
    import ctypes
    from ctypes import wintypes as w

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    sh32 = ctypes.WinDLL("shell32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                    ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT), ("uCallbackMessage", w.UINT),
                    ("hIcon", w.HICON), ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                    ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                    ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]

    sig = {
        (u32, "RegisterClassExW"): (w.ATOM, [ctypes.POINTER(WNDCLASSEXW)]),
        (u32, "CreateWindowExW"): (w.HWND, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]),
        (u32, "DefWindowProcW"): (LRESULT, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
        (u32, "DestroyWindow"): (w.BOOL, [w.HWND]),
        (u32, "PostQuitMessage"): (None, [ctypes.c_int]),
        (u32, "PostMessageW"): (w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
        (u32, "GetMessageW"): (w.BOOL, [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]),
        (u32, "TranslateMessage"): (w.BOOL, [ctypes.POINTER(w.MSG)]),
        (u32, "DispatchMessageW"): (LRESULT, [ctypes.POINTER(w.MSG)]),
        (u32, "RegisterWindowMessageW"): (w.UINT, [w.LPCWSTR]),
        (u32, "FindWindowW"): (w.HWND, [w.LPCWSTR, w.LPCWSTR]),
        (u32, "CreatePopupMenu"): (w.HMENU, []),
        (u32, "AppendMenuW"): (w.BOOL, [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]),
        (u32, "SetMenuDefaultItem"): (w.BOOL, [w.HMENU, w.UINT, w.UINT]),
        (u32, "TrackPopupMenu"): (ctypes.c_int, [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID]),
        (u32, "DestroyMenu"): (w.BOOL, [w.HMENU]),
        (u32, "GetCursorPos"): (w.BOOL, [ctypes.POINTER(w.POINT)]),
        (u32, "SetForegroundWindow"): (w.BOOL, [w.HWND]),
        (u32, "LoadImageW"): (w.HANDLE, [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]),
        (u32, "LoadIconW"): (w.HICON, [w.HINSTANCE, w.LPVOID]),
        (u32, "GetSystemMetrics"): (ctypes.c_int, [ctypes.c_int]),
        (u32, "MessageBoxW"): (ctypes.c_int, [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]),
        (sh32, "Shell_NotifyIconW"): (w.BOOL, [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]),
        (k32, "GetModuleHandleW"): (w.HMODULE, [w.LPCWSTR]),
        (k32, "CreateMutexW"): (w.HANDLE, [w.LPVOID, w.BOOL, w.LPCWSTR]),
    }
    for (dll, name), (res, args) in sig.items():
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = res, args
    return ctypes, w, u32, sh32, k32, WNDPROC, WNDCLASSEXW, NOTIFYICONDATAW


WM_CLOSE, WM_DESTROY, WM_COMMAND, WM_NULL, WM_ENDSESSION = 0x0010, 0x0002, 0x0111, 0x0000, 0x0016
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
WM_APP = 0x8000
WM_TRAY, WM_REFRESH, WM_OPEN = WM_APP + 1, WM_APP + 2, WM_APP + 3
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR = 0x0, 0x1, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
ERROR_ALREADY_EXISTS = 183


def _icon_file() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "icon", "favicon.ico")


class Tray:
    def __init__(self, config_path: str, autostart: bool = False):
        self.config_path, self.autostart = config_path, autostart
        self.stop = threading.Event()
        self.state, self.port, self.site, self.admin, self.setup_url = "starting", None, "", "", ""
        self.worker = None
        self.taskbar_created = -1                          # CreateWindowExW already sends messages, before it is known
        (self.ctypes, self.w, self.u32, self.sh32, self.k32, WNDPROC, WNDCLASSEXW, self.NID) = _win32()
        self._proc = WNDPROC(self._wndproc)                # kept referenced: Windows calls it for as long as the window lives
        hinst = self.k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=self.ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=WINDOW_CLASS)
        self.u32.RegisterClassExW(self.ctypes.byref(wc))
        # A hidden top-level window, not a message-only one: only top-level windows hear "TaskbarCreated" (Explorer restarted,
        # the icon must be added again) and the WM_CLOSE that `taskkill` (without /F) sends.
        self.hwnd = self.u32.CreateWindowExW(0, WINDOW_CLASS, "Pluteus", 0, 0, 0, 0, 0, None, None, hinst, None)
        self.taskbar_created = self.u32.RegisterWindowMessageW("TaskbarCreated")
        size = self.u32.GetSystemMetrics(49)               # SM_CXSMICON
        self.hicon = self.u32.LoadImageW(None, _icon_file(), 1, size, size, 0x10) or self.u32.LoadIconW(None, self.ctypes.c_void_p(32512))
        self._notify(NIM_ADD)

    # ---- the icon ----
    def _nid(self, flags: int) -> object:
        nid = self.NID(cbSize=self.ctypes.sizeof(self.NID), hWnd=self.hwnd, uID=1, uFlags=flags, uCallbackMessage=WM_TRAY,
                       hIcon=self.hicon)
        nid.szTip = tooltip(self.state, self.port)
        return nid

    def _notify(self, action: int) -> None:
        self.sh32.Shell_NotifyIconW(action, self.ctypes.byref(self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)))

    def balloon(self, title: str, text: str) -> None:
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle, nid.szInfo, nid.dwInfoFlags = title[:63], text[:255], 0x4 | 0x20       # NIIF_USER | NIIF_LARGE_ICON
        nid.hBalloonIcon = self.hicon
        self.sh32.Shell_NotifyIconW(NIM_MODIFY, self.ctypes.byref(nid))

    def set_state(self, text: str) -> None:
        """From any thread: the window thread updates the tooltip."""
        self.state = text
        self.u32.PostMessageW(self.hwnd, WM_REFRESH, 0, 0)

    # ---- the menu ----
    def _menu(self) -> None:
        menu = self.u32.CreatePopupMenu()
        add = lambda ident, text, flags=0: self.u32.AppendMenuW(menu, MF_STRING | flags, ident, text)   # noqa: E731
        if self.setup_url and not self.site:
            add(ID_SETUP, "Open the set-up page")
            self.u32.SetMenuDefaultItem(menu, ID_SETUP, 0)
        else:
            add(ID_OPEN, "Open Pluteus", 0 if self.site else MF_GRAYED)
            add(ID_ADMIN, "Admin page", 0 if self.admin else MF_GRAYED)
            self.u32.SetMenuDefaultItem(menu, ID_OPEN, 0)
        self.u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        add(ID_LOGON, "Start when I log on", MF_CHECKED if starts_at_logon() else 0)
        add(ID_LOG, "Show the log", 0 if os.path.exists(log_path(self.config_path)) else MF_GRAYED)
        self.u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        add(ID_QUIT, "Quit Pluteus")
        pt = self.w.POINT()
        self.u32.GetCursorPos(self.ctypes.byref(pt))
        self.u32.SetForegroundWindow(self.hwnd)            # or the menu does not close when you click elsewhere
        choice = self.u32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        self.u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        self.u32.DestroyMenu(menu)
        if choice:
            self.command(choice)

    def open_main(self) -> None:
        url = self.site or self.setup_url
        if url:
            webbrowser.open(url)

    def command(self, choice: int) -> None:
        if choice == ID_OPEN:
            webbrowser.open(self.site)
        elif choice == ID_SETUP:
            webbrowser.open(self.setup_url)
        elif choice == ID_ADMIN:
            webbrowser.open(self.admin)
        elif choice == ID_LOGON:
            try:
                set_start_at_logon(not starts_at_logon())
            except OSError as err:
                self.u32.MessageBoxW(self.hwnd, "Could not change the start at logon: %s" % err, "Pluteus", 0x30)
        elif choice == ID_LOG:
            try:
                os.startfile(log_path(self.config_path))
            except OSError:
                pass
        elif choice == ID_QUIT:
            self.u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _quit(self) -> None:
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=15)                   # the server is ended (the supervisor waits up to 10 s for it)
        self.sh32.Shell_NotifyIconW(NIM_DELETE, self.ctypes.byref(self._nid(0)))
        self.u32.DestroyWindow(self.hwnd)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONUP:
                    self.open_main()
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu()
                return 0
            if msg == WM_REFRESH:
                self._notify(NIM_MODIFY)
                return 0
            if msg == WM_OPEN:
                self.open_main()
                return 0
            if msg == self.taskbar_created:
                self._notify(NIM_ADD)
                return 0
            if msg == WM_CLOSE:
                self._quit()
                return 0
            if msg == WM_ENDSESSION and wparam:
                self.stop.set()
                return 0
            if msg == WM_DESTROY:
                self.u32.PostQuitMessage(0)
                return 0
        except Exception as err:                           # never let an error escape into Windows' call
            print("tray: %r" % err, file=sys.stderr)
        return self.u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- the server ----
    def _work(self) -> None:
        from . import setupweb, supervise
        from .config import ConfigError, load_config
        if not os.path.exists(self.config_path):
            self.set_state("waiting for the set-up page")
            made = setupweb.run(self.config_path, cancel=self.stop, on_url=lambda url: setattr(self, "setup_url", url))
            if not made:
                if not self.stop.is_set():
                    self.set_state("the set-up page stopped")
                return
        try:
            cfg = load_config(self.config_path, check_roots=False)
            self.port = cfg.port
            self.site, self.admin = site_urls(cfg)
        except ConfigError as err:                         # the server's own start says the same in the log; the menu stays usable
            self.set_state("configuration error, see the log")
            print("tray: %s" % err, file=sys.stderr)
        if not self.autostart and self.site:
            self.balloon("Pluteus is running", "It stays here by the clock. Click the icon to open it; right-click for the menu.")
        supervise.run(self.config_path, log_path(self.config_path), stop=self.stop, on_state=self.set_state)

    def loop(self) -> int:
        self.worker = threading.Thread(target=self._work, name="pluteus-tray-server", daemon=True)
        self.worker.start()
        msg = self.w.MSG()
        while self.u32.GetMessageW(self.ctypes.byref(msg), None, 0, 0) > 0:
            self.u32.TranslateMessage(self.ctypes.byref(msg))
            self.u32.DispatchMessageW(self.ctypes.byref(msg))
        self.stop.set()
        return 0


def _quiet_streams(config_path: str) -> None:
    """A windowed exe has no console: print and the server's logging must still have somewhere to go."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = os.path.dirname(os.path.abspath(config_path))
    try:
        os.makedirs(folder, exist_ok=True)
        out = open(os.path.join(folder, "pluteus-tray.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        out = open(os.devnull, "w")
    sys.stdout = sys.stdout or out
    sys.stderr = sys.stderr or out


def main(argv=None) -> int:
    from .firstrun import default_config_path
    ap = argparse.ArgumentParser(prog="pluteus-tray", description="Pluteus in the tray (Windows).")
    ap.add_argument("--config", default="", help="use this config.toml instead of the one in your settings folder")
    ap.add_argument("--autostart", action="store_true", help="started at logon: no balloon")
    ap.add_argument("--quit", action="store_true", help="ask a running tray to quit, and exit")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        print("The tray is for Windows. Use `pluteus` (or the systemd unit) elsewhere.", file=sys.stderr)
        return 2
    path = a.config or default_config_path()
    _quiet_streams(path)
    ctypes, w, u32, sh32, k32, *_ = _win32()
    if a.quit:
        hwnd = u32.FindWindowW(WINDOW_CLASS, None)
        if hwnd:
            u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return 0
    mutex = k32.CreateMutexW(None, False, MUTEX_NAME)      # noqa: F841 - held until the process ends
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        hwnd = u32.FindWindowW(WINDOW_CLASS, None)
        if hwnd:
            u32.PostMessageW(hwnd, WM_OPEN, 0, 0)          # the one that runs opens Pluteus in the browser
        return 0
    return Tray(path, autostart=a.autostart).loop()
