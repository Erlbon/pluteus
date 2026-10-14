"""The pluteus admin app: a small window to manage users and edit config.toml. Standard library only (tkinter).

Start it with  python -m pluteus admin [--config PATH]  or run the pluteus-admin executable.
It works on the config FILE (on this machine, or on the server through a network drive), not through the running
server, so nothing extra is exposed to the network. All the rules live in admin.py / useredit.py.
"""
from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .admin import AdminModel, remember_config, remembered_config
from .auth import MIN_PASSWORD
from .config import ConfigError
from .useredit import EditError

RESTART_NOTE = ("User changes reach a running server within a few seconds. Any other change to the file needs a restart "
                "of pluteus (Docker: docker compose restart pluteus).")


class Dialog(tk.Toplevel):
    """A modal window with OK and Cancel; subclasses build the body and set self.result in apply()."""

    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        self.build(body)
        buttons = ttk.Frame(self, padding=(14, 0, 14, 14))
        buttons.grid(sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=(0, 8))
        ok = ttk.Button(buttons, text="OK", command=self._ok, default="active")
        ok.grid(row=0, column=1)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 60, parent.winfo_rooty() + 60))
        self.grab_set()
        self.focus_first()

    def build(self, body: ttk.Frame) -> None:
        raise NotImplementedError

    def focus_first(self) -> None:
        pass

    def collect(self):
        raise NotImplementedError

    def _ok(self) -> None:
        try:
            self.result = self.collect()
        except EditError as e:
            messagebox.showerror("Cannot do that", str(e), parent=self)
            return
        self.destroy()

    def ask(self):
        self.wait_window()
        return self.result


class PasswordFields:
    """Two masked entries, used by the add-user and change-password dialogs."""

    def __init__(self, body: ttk.Frame, row: int):
        self.first, self.second = tk.StringVar(), tk.StringVar()
        ttk.Label(body, text="Password").grid(row=row, column=0, sticky="w", pady=3)
        self.entry = ttk.Entry(body, textvariable=self.first, show="•", width=30)
        self.entry.grid(row=row, column=1, sticky="we", pady=3)
        ttk.Label(body, text="Again").grid(row=row + 1, column=0, sticky="w", pady=3)
        ttk.Entry(body, textvariable=self.second, show="•", width=30).grid(row=row + 1, column=1, sticky="we", pady=3)
        # The rule is shown before anything is typed, and the label follows what has been typed so far.
        self.hint = tk.StringVar()
        ttk.Label(body, textvariable=self.hint, foreground="#555").grid(row=row + 2, column=1, sticky="w")
        self.first.trace_add("write", self._update_hint)
        self._update_hint()

    def _update_hint(self, *_) -> None:
        n = len(self.first.get())
        if n == 0:
            self.hint.set("At least %d characters" % MIN_PASSWORD)
        elif n < MIN_PASSWORD:
            self.hint.set("%d so far: at least %d characters needed" % (n, MIN_PASSWORD))
        else:
            self.hint.set("Long enough")


class PasswordDialog(Dialog):
    def __init__(self, parent, user: str):
        self.user = user
        super().__init__(parent, "New password for %s" % user)

    def build(self, body):
        self.fields = PasswordFields(body, 0)

    def focus_first(self):
        self.fields.entry.focus_set()

    def collect(self):
        return self.fields.first.get(), self.fields.second.get()


class UserDialog(Dialog):
    """Add a user (name + password + access) or change a user's access."""

    def __init__(self, parent, root_names: list, user=None):
        self.root_names = root_names
        self.user = user
        super().__init__(parent, "Add user" if user is None else "Access for %s" % user.name)

    def build(self, body):
        row = 0
        self.name = tk.StringVar()
        if self.user is None:
            ttk.Label(body, text="User name").grid(row=0, column=0, sticky="w", pady=3)
            self.name_entry = ttk.Entry(body, textvariable=self.name, width=30)
            self.name_entry.grid(row=0, column=1, sticky="we", pady=3)
            self.fields = PasswordFields(body, 1)
            row = 4
        selected = set(self.user.roots) if self.user else set()
        self.everything = tk.BooleanVar(value="*" in selected)       # a new user starts with no folders
        ttk.Separator(body).grid(row=row, column=0, columnspan=2, sticky="we", pady=8)
        ttk.Label(body, text="Folders this user may open").grid(row=row + 1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(body, text="All of them", variable=self.everything, value=True,
                        command=self._sync).grid(row=row + 2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(body, text="Only these:", variable=self.everything, value=False,
                        command=self._sync).grid(row=row + 3, column=0, columnspan=2, sticky="w")
        self.root_vars, self.root_boxes = {}, []
        for i, name in enumerate(self.root_names):
            var = tk.BooleanVar(value=name in selected)
            box = ttk.Checkbutton(body, text=name, variable=var)
            box.grid(row=row + 4 + i, column=1, sticky="w")
            self.root_vars[name] = var
            self.root_boxes.append(box)
        row += 4 + len(self.root_names)
        ttk.Separator(body).grid(row=row, column=0, columnspan=2, sticky="we", pady=8)
        self.adult = tk.BooleanVar(value=bool(self.user and self.user.adult))
        self.upload = tk.BooleanVar(value=True if self.user is None else self.user.upload)
        ttk.Checkbutton(body, text="May see folders marked as adult (.adult)", variable=self.adult).grid(
            row=row + 1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(body, text="Has a personal upload folder", variable=self.upload).grid(
            row=row + 2, column=0, columnspan=2, sticky="w")
        self.admin = tk.BooleanVar(value=bool(self.user and self.user.admin))
        ttk.Checkbutton(body, text="May use the web admin page (admin_port)", variable=self.admin).grid(
            row=row + 3, column=0, columnspan=2, sticky="w")
        self.notes = tk.BooleanVar(value=bool(self.user and self.user.notes))
        ttk.Checkbutton(body, text="May write shared notes into libraries set to notes = \"edit\"", variable=self.notes).grid(
            row=row + 4, column=0, columnspan=2, sticky="w")
        self._sync()

    def _sync(self) -> None:
        state = "disabled" if self.everything.get() else "normal"
        for box in self.root_boxes:
            box.configure(state=state)

    def focus_first(self):
        if self.user is None:
            self.name_entry.focus_set()

    def collect(self):
        roots = ("*",) if self.everything.get() else tuple(n for n, v in self.root_vars.items() if v.get())
        result = {"roots": roots, "adult": self.adult.get(), "upload": self.upload.get(), "admin": self.admin.get(),
                  "notes": self.notes.get()}
        if self.user is None:
            result.update(name=self.name.get(), password=self.fields.first.get(), again=self.fields.second.get())
        return result


class App(tk.Tk):
    def __init__(self, path: str):
        super().__init__()
        self.title("pluteus admin")
        self.geometry("860x540")
        self.minsize(640, 400)
        self.model: AdminModel | None = None
        self.status = tk.StringVar(value="")
        self._menus()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._users_tab()
        self._config_tab()
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(10, 4)).pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        if path:
            self.open_config(path)

    # ---- window furniture ---------------------------------------------------------------------------------------
    def _menus(self) -> None:
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=False)
        file_menu.add_command(label="Open config file...", command=self.choose_config)
        file_menu.add_command(label="Reload", command=self.reload_all)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.quit_app)
        bar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(bar, tearoff=False)
        help_menu.add_command(label="About", command=self.about)
        bar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=bar)

    def about(self) -> None:
        messagebox.showinfo("pluteus admin", "pluteus %s\n\nConfig file:\n%s\n\n%s" % (
            __version__, self.model.path if self.model else "(none)", RESTART_NOTE), parent=self)

    def say(self, text: str) -> None:
        self.status.set(text)

    def guard(self, action, success: str = ""):
        """Run action(); show a readable error instead of a traceback. Returns (ok, value)."""
        try:
            value = action()
        except (EditError, ConfigError) as e:
            messagebox.showerror("Cannot do that", str(e), parent=self)
            return False, None
        except OSError as e:
            messagebox.showerror("The file cannot be reached", "%s\n\n(Is the server's network drive connected?)" % e,
                                 parent=self)
            return False, None
        if success:
            self.say(success)
        return True, value

    # ---- the users tab ---------------------------------------------------------------------------------------------
    def _users_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(frame, text="Users")
        columns = ("name", "folders", "adult", "upload", "notes", "admin")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (("name", "User", 150), ("folders", "Folders", 200), ("adult", "Adult", 70),
                                  ("upload", "Uploads", 90), ("notes", "Notes", 70), ("admin", "Admin page", 90)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, stretch=key == "folders",
                             anchor="w" if key in ("name", "folders") else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        side = ttk.Frame(frame, padding=(12, 0, 0, 0))
        side.grid(row=0, column=2, sticky="n")
        for text, command in (("Add user...", self.add_user), ("Change password...", self.change_password),
                              ("Edit access...", self.edit_access), ("Remove user...", self.remove_user),
                              ("Refresh", self.refresh_users)):
            ttk.Button(side, text=text, command=command, width=20).pack(pady=3)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        ttk.Label(frame, text=RESTART_NOTE, wraplength=780, foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.tree.bind("<Double-1>", lambda e: self.edit_access())

    def selected_user(self):
        chosen = self.tree.selection()
        if not chosen:
            messagebox.showinfo("Choose a user", "Select a user in the list first.", parent=self)
            return None
        name = self.tree.item(chosen[0], "values")[0]
        for user in self.model.users():
            if user.name == name:
                return user
        return None

    def refresh_users(self) -> None:
        if not self.model:
            return
        ok, users = self.guard(self.model.users)
        if not ok:
            return
        self.tree.delete(*self.tree.get_children())
        for u in users:
            self.tree.insert("", "end", values=(u.name, "all" if "*" in u.roots else (", ".join(u.roots) or "none"),"yes" if u.adult else "",
                                                "yes" if u.upload else "no", "yes" if u.notes else "", "yes" if u.admin else ""))

    def add_user(self) -> None:
        if not self.model:
            return
        ok, roots = self.guard(self.model.root_names)
        if not ok:
            return
        data = UserDialog(self, roots).ask()
        if data:
            ok, _ = self.guard(lambda: self.model.add_user(data["name"], data["password"], data["again"], data["roots"],
                                                           data["adult"], data["upload"], data["admin"], data["notes"]),
                               "Added %s." % data["name"].strip())
            self.after_change(ok)

    def change_password(self) -> None:
        user = self.selected_user()
        if user is None:
            return
        data = PasswordDialog(self, user.name).ask()
        if data:
            ok, _ = self.guard(lambda: self.model.change_password(user.name, *data), "Password changed for %s." % user.name)
            self.after_change(ok)

    def edit_access(self) -> None:
        user = self.selected_user()
        if user is None:
            return
        ok, roots = self.guard(self.model.root_names)
        if not ok:
            return
        data = UserDialog(self, roots, user).ask()
        if data:
            ok, _ = self.guard(lambda: self.model.set_access(user.name, data["roots"], data["adult"], data["upload"],
                                                             data["admin"], data["notes"]), "Saved the access of %s." % user.name)
            self.after_change(ok)

    def remove_user(self) -> None:
        user = self.selected_user()
        if user is None:
            return
        if messagebox.askyesno("Remove user", "Remove %s?\n\nTheir upload folder and reading progress stay on the server; "
                               "they just cannot log in any more." % user.name, parent=self, icon="warning"):
            ok, _ = self.guard(lambda: self.model.remove_user(user.name), "Removed %s." % user.name)
            self.after_change(ok)

    def after_change(self, ok: bool) -> None:
        self.refresh_users()
        if ok and not self.text.edit_modified():      # keep the file tab in step unless the admin is typing in it
            self._load_text()

    # ---- the config file tab ----------------------------------------------------------------------------------------
    def _config_tab(self) -> None:
        frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(frame, text="Config file")
        ttk.Label(frame, text=RESTART_NOTE, wraplength=780, foreground="#555").grid(row=0, column=0, columnspan=2, sticky="w")
        self.text = tk.Text(frame, wrap="none", undo=True, font=("Consolas", 10), height=10)
        self.text.grid(row=1, column=0, sticky="nsew", pady=8)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=1, column=1, sticky="ns", pady=8)
        xscroll.grid(row=2, column=0, sticky="we")
        bar = ttk.Frame(frame)
        bar.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(bar, text="Check", command=self.check_text).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Save", command=self.save_text).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Reload from disk", command=self.reload_text).pack(side="left")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

    def _load_text(self) -> None:
        ok, text = self.guard(self.model.read_text)
        if ok:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", text)
            self.text.edit_modified(False)
            self.text.edit_reset()

    def _typed(self) -> str:
        return self.text.get("1.0", "end-1c")

    def check_text(self) -> None:
        ok, cfg = self.guard(lambda: self.model.check_text(self._typed()))
        if ok:
            messagebox.showinfo("Config check", "The file is valid: %d folder(s), %d user(s)." % (len(cfg.roots), len(cfg.users)),
                                parent=self)

    def save_text(self) -> None:
        if not self.model:
            return
        ok, _ = self.guard(lambda: self.model.check_text(self._typed()))
        if not ok:
            return
        if self.model.changed_on_disk() and not messagebox.askyesno(
                "The file changed", "The file on disk is different from what you opened (someone, or a user change in this "
                "app, edited it).\n\nSave anyway and overwrite those changes?", parent=self, icon="warning"):
            return
        ok, _ = self.guard(lambda: self.model.save_text(self._typed()),
                           "Saved. The previous file is kept as config.toml.bak. " + RESTART_NOTE)
        if ok:
            self.text.edit_modified(False)
            self.refresh_users()

    def reload_text(self) -> None:
        if self.text.edit_modified() and not messagebox.askyesno(
                "Discard changes", "Throw away your unsaved edits and read the file again?", parent=self, icon="warning"):
            return
        self._load_text()
        self.refresh_users()

    # ---- opening files ---------------------------------------------------------------------------------------------
    def choose_config(self) -> None:
        start = os.path.dirname(self.model.path) if self.model else ""
        path = filedialog.askopenfilename(parent=self, title="Open the pluteus config file", initialdir=start or None,
                                          filetypes=[("pluteus config", "*.toml"), ("All files", "*.*")])
        if path:
            self.open_config(os.path.normpath(path))

    def open_config(self, path: str) -> None:
        ok, model = self.guard(lambda: AdminModel(path))
        if not ok:
            return
        self.model = model
        remember_config(path)
        self.title("pluteus admin - %s" % path)
        self.refresh_users()
        self._load_text()
        self.say("Opened %s" % path)

    def reload_all(self) -> None:
        if self.model:
            self.reload_text()

    def quit_app(self) -> None:
        if self.text.edit_modified() and not messagebox.askyesno(
                "Unsaved edits", "The Config file tab has unsaved edits. Quit and lose them?", parent=self, icon="warning"):
            return
        self.destroy()


def make_dpi_aware() -> None:
    """Windows: draw sharp text on scaled screens instead of letting the system blow up a low-resolution picture."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main(argv=None) -> int:
    make_dpi_aware()
    ap = argparse.ArgumentParser(prog="pluteus admin", description="Manage pluteus users and the config file.")
    ap.add_argument("--config", help="path to config.toml (default: the one used last time)")
    a = ap.parse_args(argv)
    path = a.config or remembered_config()
    app = App(path)
    if not path:
        app.withdraw()
        chosen = filedialog.askopenfilename(title="Open the pluteus config file",
                                            filetypes=[("pluteus config", "*.toml"), ("All files", "*.*")])
        if not chosen:
            app.destroy()
            return 1
        app.deiconify()
        app.open_config(os.path.normpath(chosen))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
