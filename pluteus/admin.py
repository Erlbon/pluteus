"""What the admin app does, without any window: read a config.toml, change users, edit the file as text.

All of it sits on top of useredit, so the same rules apply as on the command line: every change is re-parsed and
validated before anything is written, comments are kept, the old file is kept as config.toml.bak and the file is
replaced atomically. A running server notices user changes within a few seconds; other settings need a restart.
"""
from __future__ import annotations

import json
import os

from . import useredit
from .auth import MIN_PASSWORD, hash_password
from .config import Config
from .useredit import EditError

MEMORY_FILE = os.path.join(os.path.expanduser("~"), ".pluteus-admin.json")


def remembered_config() -> str:
    """The config file used last time ('' when there is none, or it is gone)."""
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            path = json.load(f).get("config", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return path if isinstance(path, str) and os.path.isfile(path) else ""


def remember_config(path: str) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"config": path}, f)
    except OSError:
        pass                                   # a convenience only


class AdminModel:
    def __init__(self, path: str):
        self.path = path
        self.loaded_text = ""
        self.reload()

    # ---- reading ----------------------------------------------------------------------------------------------
    def reload(self) -> Config:
        """Read the file again (and remember its text, to notice if someone else changes it meanwhile)."""
        self.loaded_text = useredit.read_config_text(self.path)
        return useredit.validate(self.loaded_text)

    def config(self) -> Config:
        return useredit.validate(useredit.read_config_text(self.path))

    def users(self) -> list:
        return list(self.config().users.values())

    def root_names(self) -> list:
        return list(self.config().roots)

    # ---- users --------------------------------------------------------------------------------------------------
    @staticmethod
    def _check_password(password: str, again: str) -> None:
        if password != again:
            raise EditError("the two passwords differ")
        if len(password) < MIN_PASSWORD:
            raise EditError("the password must be at least %d characters" % MIN_PASSWORD)

    def add_user(self, name: str, password: str, again: str, roots=(), adult: bool = False, upload: bool = True,
                 admin: bool = False) -> None:
        name = name.strip()
        if not name:
            raise EditError("the user needs a name")
        self._check_password(password, again)
        useredit.add_user(self.path, name, hash_password(password), tuple(roots), adult, upload, admin)

    def change_password(self, name: str, password: str, again: str) -> None:
        self._check_password(password, again)
        useredit.set_user(self.path, name, password_hash=hash_password(password))

    def set_access(self, name: str, roots=(), adult: bool = False, upload: bool = True, admin: bool = False) -> None:
        """roots: root names, or ("*",) for every root; empty = none."""
        useredit.set_user(self.path, name, roots=tuple(roots), adult=adult, upload=upload, admin=admin)

    def remove_user(self, name: str) -> None:
        if len(self.config().users) <= 1:
            raise EditError("the last user cannot be removed (nobody could log in afterwards)")
        useredit.remove_user(self.path, name)

    # ---- the file as text -----------------------------------------------------------------------------------------
    def read_text(self) -> str:
        self.loaded_text = useredit.read_config_text(self.path)
        return self.loaded_text

    def changed_on_disk(self) -> bool:
        """True when the file is no longer what read_text()/reload() last showed (another admin, or a hand edit)."""
        try:
            return useredit.read_config_text(self.path) != self.loaded_text
        except EditError:
            return True

    def check_text(self, text: str) -> Config:
        """Raises EditError with a readable message when the text would not be a valid config."""
        return useredit.validate(text)

    def save_text(self, text: str) -> None:
        """Validate, keep the old file as .bak, replace the file. Nothing is written when validation fails."""
        text = text.replace("\r\n", "\n") if "\r\n" not in self.loaded_text else text.replace("\r\n", "\n").replace("\n", "\r\n")
        useredit.validate(text)
        useredit._save(self.path, text)
        self.loaded_text = text
