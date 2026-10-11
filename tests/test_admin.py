"""The admin app: its logic (no window needed) and a smoke test of the window itself."""
import os

import pytest

from pluteus import admin, auth, useredit
from pluteus.admin import AdminModel
from pluteus.config import load_config
from pluteus.useredit import EditError

BASE = """# my comment, must survive
[server]
host = "0.0.0.0"
exclude = ["_incoming"]

[[roots]]
name = "books"
path = "/library/books"

[[roots]]
name = "comics"
path = "/library/comics"

[[users]]
name = "alice"
password_hash = "%s"
adult = true
""" % auth.hash_password("alice-password-1", n=2 ** 4)


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(BASE, encoding="utf-8", newline="")
    return AdminModel(str(path))


def names(m):
    return [u.name for u in m.users()]


def test_lists_users_and_roots(model):
    assert names(model) == ["alice"] and model.root_names() == ["books", "comics"]
    assert model.users()[0].adult and model.users()[0].upload


def test_add_user_with_access_and_password(model):
    model.add_user("bob", "bob-password-1", "bob-password-1", roots=("books",), adult=False, upload=False)
    bob = {u.name: u for u in model.users()}["bob"]
    assert bob.roots == ("books",) and not bob.adult and not bob.upload
    assert auth.verify_password("bob-password-1", bob.password_hash)
    assert "# my comment, must survive" in open(model.path, encoding="utf-8").read()
    assert os.path.exists(model.path + ".bak")


@pytest.mark.parametrize("name,pw,again,message", [
    ("", "long-enough-1", "long-enough-1", "name"), ("x", "short", "short", "at least"),
    ("x", "long-enough-1", "different-1234", "differ"), ("alice", "long-enough-1", "long-enough-1", "already exists"),
    ("a:b", "long-enough-1", "long-enough-1", "colon")])
def test_add_user_refuses_bad_input_and_writes_nothing(model, name, pw, again, message):
    before = open(model.path, encoding="utf-8").read()
    with pytest.raises(EditError, match=message):
        model.add_user(name, pw, again)
    assert open(model.path, encoding="utf-8").read() == before


def test_change_password_and_access(model):
    model.change_password("alice", "brand-new-pass-1", "brand-new-pass-1")
    assert auth.verify_password("brand-new-pass-1", model.users()[0].password_hash)
    model.set_access("alice", roots=("comics",), adult=False, upload=False)
    alice = model.users()[0]
    assert alice.roots == ("comics",) and not alice.adult and not alice.upload
    model.set_access("alice", roots=(), adult=True, upload=True)            # empty roots = every folder again
    alice = model.users()[0]
    assert alice.roots == () and alice.adult and alice.upload
    assert "upload" not in open(model.path, encoding="utf-8").read().split("[[users]]")[1]    # default needs no key
    with pytest.raises(EditError, match="differ"):
        model.change_password("alice", "brand-new-pass-1", "other-pass-12345")


def test_unknown_roots_are_refused(model):
    with pytest.raises(EditError, match="unknown root"):
        model.set_access("alice", roots=("nope",))


def test_the_last_user_cannot_be_removed_but_others_can(model):
    with pytest.raises(EditError, match="last user"):
        model.remove_user("alice")
    model.add_user("bob", "bob-password-1", "bob-password-1")
    model.remove_user("bob")
    assert names(model) == ["alice"]


def test_editing_the_file_as_text_validates_and_keeps_a_backup(model):
    text = model.read_text()
    with pytest.raises(EditError):
        model.save_text(text.replace('name = "books"', 'name = "bad name!"'))
    assert open(model.path, encoding="utf-8").read() == text                       # nothing was written
    with pytest.raises(EditError):
        model.save_text(text + "\nnot valid toml [[")
    model.save_text(text.replace('host = "0.0.0.0"', 'host = "127.0.0.1"'))
    assert 'host = "127.0.0.1"' in open(model.path, encoding="utf-8").read()
    assert open(model.path + ".bak", encoding="utf-8").read() == text
    assert load_config(model.path, check_roots=False).host == "127.0.0.1"


def test_a_file_edited_elsewhere_is_noticed(model):
    model.read_text()
    assert not model.changed_on_disk()
    with open(model.path, "a", encoding="utf-8") as f:
        f.write("\n# edited by someone else\n")
    assert model.changed_on_disk()


def test_windows_line_endings_are_kept(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(BASE, encoding="utf-8", newline="\r\n")
    m = AdminModel(str(path))
    m.save_text(m.read_text().replace("\r\n", "\n") + "# added\n")
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_an_unreadable_file_gives_a_readable_error(tmp_path):
    with pytest.raises(EditError, match="cannot read"):
        AdminModel(str(tmp_path / "missing.toml"))


def test_the_remembered_config_is_only_used_while_the_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "MEMORY_FILE", str(tmp_path / "memory.json"))
    assert admin.remembered_config() == ""
    target = tmp_path / "config.toml"
    target.write_text(BASE, encoding="utf-8")
    admin.remember_config(str(target))
    assert admin.remembered_config() == str(target)
    target.unlink()
    assert admin.remembered_config() == ""


def test_the_command_line_understands_the_upload_flag(tmp_path, capsys):
    from pluteus.__main__ import main
    path = tmp_path / "config.toml"
    path.write_text(BASE, encoding="utf-8")
    assert main(["user", "set", "alice", "--no-upload", "--config", str(path)]) == 0
    assert main(["user", "list", "--config", str(path)]) == 0
    listing = capsys.readouterr().out
    assert "upload" in listing and "alice" in listing.splitlines()[-1] and "no" in listing.splitlines()[-1]
    assert main(["user", "set", "alice", "--upload", "--config", str(path)]) == 0
    assert load_config(str(path), check_roots=False).users["alice"].upload


# ---- the window ------------------------------------------------------------------------------------------------------
@pytest.fixture
def window(tmp_path):
    tk = pytest.importorskip("tkinter")
    from pluteus.admin_gui import App
    path = tmp_path / "config.toml"
    path.write_text(BASE, encoding="utf-8", newline="")
    try:
        app = App(str(path))
    except tk.TclError as e:                                        # no display (a build server)
        pytest.skip("no display: %s" % e)
    app.withdraw()
    yield app, path
    app.text.edit_modified(False)
    app.destroy()


def test_window_shows_users_and_the_file(window):
    app, path = window
    rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
    assert rows == [("alice", "all", "yes", "yes")]
    assert "# my comment, must survive" in app.text.get("1.0", "end")
    assert "pluteus admin - " in app.title()


def test_window_refreshes_after_a_change_and_keeps_typed_edits(window):
    app, path = window
    app.model.add_user("bob", "bob-password-1", "bob-password-1")
    app.after_change(True)
    assert [app.tree.item(i, "values")[0] for i in app.tree.get_children()] == ["alice", "bob"]
    assert 'name = "bob"' in app.text.get("1.0", "end")                      # the file tab followed along
    app.text.insert("end", "# typed but not saved\n")
    app.text.edit_modified(True)
    app.model.remove_user("bob")
    app.after_change(True)
    assert "# typed but not saved" in app.text.get("1.0", "end")             # the admin's edit is not thrown away


def test_window_saves_the_typed_text(window, monkeypatch):
    app, path = window
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: pytest.fail("unexpected error box"))
    app.text.insert("end", "# added in the window\n")
    app.save_text()
    assert "# added in the window" in path.read_text(encoding="utf-8")
    assert not app.text.edit_modified()


def test_window_refuses_to_save_an_invalid_file(window, monkeypatch):
    app, path = window
    shown = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: shown.append(a))
    before = path.read_text(encoding="utf-8")
    app.text.insert("end", "this is not toml [[\n")
    app.save_text()
    assert shown and path.read_text(encoding="utf-8") == before
