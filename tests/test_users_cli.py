"""User editing in config.toml: text edits, the CLI, and a running server picking the changes up."""
import io
import logging
import sys
import tomllib

import pytest
from conftest import basic
from starlette.testclient import TestClient

from pluteus import useredit
from pluteus.__main__ import main
from pluteus.app import create_app
from pluteus.auth import hash_password, verify_password
from pluteus.config import load_config

SAMPLE = """# my config, keep this comment
[server]
host = "127.0.0.1"   # inline comment
port = 8080

[[roots]]
name = "books"
path = "."

[[roots]]
name = "comics"
path = "."

# Users, hand-written notes
[[users]]
name = "alice"
password_hash = "scrypt$16$8$1$aa$bb"
adult = true   # trailing comment

[[users]]
name = "al"
password_hash = "scrypt$16$8$1$cc$dd"
roots = [
  "books",   # only books
]
"""


@pytest.fixture
def conf(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8", newline="")
    return str(p)


def users(path):
    return load_config(path, check_roots=False).users


def text(path):
    return useredit.read_config_text(path)


# ---- text edits -------------------------------------------------------------------
def test_add_keeps_everything_else_byte_for_byte(conf):
    useredit.add_user(conf, "dave", "scrypt$16$8$1$ee$ff", roots=["comics"], adult=True)
    new = text(conf)
    assert new.startswith(SAMPLE.rstrip("\n"))                     # nothing above was touched
    assert "# my config, keep this comment" in new and "# trailing comment" in new
    d = users(conf)["dave"]
    assert d.roots == ("comics",) and d.adult is True


def test_add_refuses_duplicates_and_unknown_roots_and_changes_nothing(conf):
    with pytest.raises(useredit.EditError, match="already exists"):
        useredit.add_user(conf, "alice", "scrypt$1$2$3$4$5")
    with pytest.raises(useredit.EditError, match="unknown root"):
        useredit.add_user(conf, "dave", "scrypt$1$2$3$4$5", roots=["nope"])
    assert text(conf) == SAMPLE


def test_set_touches_only_the_named_user_even_when_names_overlap(conf):
    useredit.set_user(conf, "al", adult=True)                       # "al" is a prefix of "alice"
    u = users(conf)
    assert u["al"].adult is True and u["al"].roots == ("books",)
    assert u["alice"].adult is True and u["alice"].password_hash == "scrypt$16$8$1$aa$bb"
    assert "# only books" in text(conf)                             # the multi-line array is intact


def test_set_roots_replaces_and_all_roots_removes_a_multiline_array(conf):
    useredit.set_user(conf, "al", roots=["books", "comics"])
    assert users(conf)["al"].roots == ("books", "comics")
    assert "# only books" not in text(conf)
    useredit.set_user(conf, "al", roots=())
    assert users(conf)["al"].roots == ()
    assert '"books",' not in text(conf).split("name = \"al\"")[1]   # no orphaned array lines


def test_set_adult_false_removes_the_line_with_its_comment(conf):
    useredit.set_user(conf, "alice", adult=False)
    assert users(conf)["alice"].adult is False
    assert "trailing comment" not in text(conf)


def test_set_password_hash_replaces_only_that_line(conf):
    useredit.set_user(conf, "alice", password_hash="scrypt$16$8$1$new$hash")
    assert users(conf)["alice"].password_hash == "scrypt$16$8$1$new$hash"
    assert users(conf)["al"].password_hash == "scrypt$16$8$1$cc$dd"


def test_remove_deletes_the_block_and_only_that(conf):
    useredit.remove_user(conf, "alice")
    assert list(users(conf)) == ["al"]
    new = text(conf)
    assert "alice" not in new and "# my config, keep this comment" in new and "# only books" in new


def test_the_last_user_and_the_anonymous_user_cannot_be_removed(conf):
    useredit.remove_user(conf, "al")
    with pytest.raises(useredit.EditError, match="no \\[\\[users\\]\\]"):
        useredit.remove_user(conf, "alice")
    assert list(users(conf)) == ["alice"]
    useredit.add_user(conf, "guest", "scrypt$1$2$3$4$5")
    t = text(conf).replace('port = 8080', 'port = 8080\nanonymous_user = "guest"')
    with open(conf, "w", encoding="utf-8", newline="") as f:
        f.write(t)
    with pytest.raises(useredit.EditError, match="anonymous_user"):
        useredit.remove_user(conf, "guest")
    assert "guest" in users(conf)


def test_unknown_user_is_an_error(conf):
    for op in (lambda: useredit.set_user(conf, "ghost", adult=True), lambda: useredit.remove_user(conf, "ghost")):
        with pytest.raises(useredit.EditError, match="no user named"):
            op()


def test_a_backup_of_the_previous_file_is_kept(conf):
    useredit.set_user(conf, "alice", adult=False)
    assert open(conf + ".bak", encoding="utf-8", newline="").read() == SAMPLE


def test_saving_works_where_timestamps_cannot_be_copied(conf, monkeypatch):
    # Docker Desktop on Windows: a non-root user may create files on a bind mount but not set their timestamps.
    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr("shutil.copystat", refuse)
    monkeypatch.setattr("os.utime", refuse)
    useredit.add_user(conf, "dave", "scrypt$1$2$3$4$5")
    assert "dave" in users(conf) and open(conf + ".bak", encoding="utf-8", newline="").read() == SAMPLE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_edited_file_keeps_its_permissions(conf):
    import os
    import stat
    os.chmod(conf, 0o644)
    useredit.add_user(conf, "dave", "scrypt$1$2$3$4$5")
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o644                    # not the owner-only 0600 of a temp file


def test_windows_line_endings_are_preserved(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE.replace("\n", "\r\n"), encoding="utf-8", newline="")
    useredit.add_user(str(p), "dave", "scrypt$1$2$3$4$5")
    useredit.set_user(str(p), "alice", adult=False)
    raw = p.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")                  # no bare LF crept in
    assert "dave" in tomllib.loads(raw.decode())["users"][-1]["name"]


def test_the_original_file_survives_when_an_edit_is_refused(conf, monkeypatch):
    monkeypatch.setattr(useredit, "validate", lambda t: (_ for _ in ()).throw(useredit.EditError("boom")))
    with pytest.raises(useredit.EditError):
        useredit.add_user(conf, "dave", "scrypt$1$2$3$4$5")
    assert text(conf) == SAMPLE


# ---- command line ------------------------------------------------------------------
def run(monkeypatch, *argv, stdin=None):
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return main(list(argv))


def test_cli_add_list_set_remove(monkeypatch, conf, capsys):
    assert run(monkeypatch, "user", "add", "dave", "--config", conf, "--roots", "comics",
               "--password-stdin", stdin="a-long-password\n") == 0
    assert verify_password("a-long-password", users(conf)["dave"].password_hash)
    capsys.readouterr()
    assert run(monkeypatch, "user", "list", "--config", conf) == 0
    out = capsys.readouterr().out
    assert "dave" in out and "comics" in out and "alice" in out and "(all)" in out
    assert run(monkeypatch, "user", "set", "dave", "--config", conf, "--adult", "--all-roots") == 0
    assert users(conf)["dave"].adult and users(conf)["dave"].roots == ()
    assert run(monkeypatch, "user", "set", "dave", "--config", conf, "--password-stdin",
               stdin="another-password\n") == 0
    assert verify_password("another-password", users(conf)["dave"].password_hash)
    assert run(monkeypatch, "user", "remove", "dave", "--config", conf, "--yes") == 0
    assert "dave" not in users(conf)


def test_cli_refuses_bad_input_with_exit_code_2_and_leaves_the_file_alone(monkeypatch, conf, capsys):
    assert run(monkeypatch, "user", "add", "dave", "--config", conf, "--password-stdin", stdin="short\n") == 2
    assert "at least 8" in capsys.readouterr().err
    assert run(monkeypatch, "user", "add", "alice", "--config", conf, "--password-stdin",
               stdin="long-enough-pw\n") == 2
    assert run(monkeypatch, "user", "set", "alice", "--config", conf) == 2          # nothing to change
    assert run(monkeypatch, "user", "remove", "ghost", "--config", conf, "--yes") == 2
    assert text(conf) == SAMPLE


def test_cli_remove_asks_for_confirmation(monkeypatch, conf):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run(monkeypatch, "user", "remove", "alice", "--config", conf) == 1
    assert "alice" in users(conf)


# ---- a running server picks the changes up -------------------------------------------
def test_running_server_reloads_users_and_keeps_the_old_ones_on_a_broken_file(tmp_path, monkeypatch, caplog):
    (tmp_path / "lib").mkdir()
    conf = tmp_path / "config.toml"
    conf.write_text("""[server]
port = 8080

[[roots]]
name = "books"
path = '%s'

[[users]]
name = "alice"
password_hash = "%s"
""" % (tmp_path / "lib", hash_password("alice-password", n=2 ** 4)), encoding="utf-8", newline="")
    client = TestClient(create_app(load_config(str(conf)), config_path=str(conf), reload_interval=0))
    alice, dave = basic("alice", "alice-password"), basic("dave", "dave-password")
    assert client.get("/opds", headers=alice).status_code == 200
    assert client.get("/opds", headers=dave).status_code == 401              # not a user yet

    run(monkeypatch, "user", "add", "dave", "--config", str(conf), "--password-stdin", stdin="dave-password\n")
    assert client.get("/opds", headers=dave).status_code == 200              # picked up without a restart

    run(monkeypatch, "user", "remove", "dave", "--config", str(conf), "--yes")
    assert client.get("/opds", headers=dave).status_code == 401              # cached login dropped at once

    with caplog.at_level(logging.WARNING, logger="pluteus.config"):
        conf.write_text("this is [not valid toml", encoding="utf-8")
        assert client.get("/opds", headers=alice).status_code == 200         # previous users stay in force
    assert "not valid" in caplog.text
