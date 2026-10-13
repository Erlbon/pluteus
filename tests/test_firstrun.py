"""The everyday start of the downloaded programs: `pluteus` with no arguments, first run included."""
import os
import sys

import pytest

from pluteus import __main__ as cli
from pluteus import firstrun
from pluteus.auth import verify_password
from pluteus.config import load_config


def test_the_settings_folder_depends_on_the_system_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUTEUS_HOME", str(tmp_path / "mine"))
    assert firstrun.default_config_path() == os.path.join(os.path.abspath(str(tmp_path / "mine")), "config.toml")
    monkeypatch.delenv("PLUTEUS_HOME")
    path = firstrun.default_config_path().lower()
    assert path.endswith("config.toml") and ("pluteus" in path)


def feed(answers):
    items = iter(answers)
    return lambda prompt="": next(items)


def test_the_questions_insist_on_a_real_password_the_person_types(monkeypatch):
    said = []
    name, password, folder = firstrun.ask_setup(feed(["", "D:/Books"]), feed(["short", "long-enough-1", "different-1", "long-enough-1", "long-enough-1"]),
                                                said.append)
    assert (name, password, folder) == ("admin", "long-enough-1", "D:/Books")
    assert any("Too short" in s for s in said) and any("differ" in s for s in said)


def test_the_first_configuration_is_valid_and_has_one_administrator(tmp_path):
    path = tmp_path / "Pluteus Home" / "config.toml"                       # a folder name with a space
    folder = firstrun.create_config(str(path), "Erlend", "long-enough-1", "", port=8123, admin_port=8124)
    assert os.path.isdir(folder) and folder.endswith("library")          # the placeholder library next to the settings
    cfg = load_config(str(path), check_roots=False)
    assert cfg.port == 8123 and cfg.admin_port == 8124 and cfg.admin_host == "127.0.0.1" and list(cfg.roots) == ["library"]
    user = cfg.users["Erlend"]
    assert user.admin and user.roots == ("*",) and verify_password("long-enough-1", user.password_hash)
    assert "long-enough-1" not in path.read_text()                       # only the hash is stored


def test_a_given_library_folder_is_used_and_a_missing_one_is_refused(tmp_path):
    books = tmp_path / "My Books"
    books.mkdir()
    path = tmp_path / "home" / "config.toml"
    firstrun.create_config(str(path), "admin", "long-enough-1", str(books))
    assert load_config(str(path)).roots["library"].path == str(books)
    with pytest.raises(ValueError):
        firstrun.create_config(str(tmp_path / "other" / "config.toml"), "admin", "long-enough-1", str(tmp_path / "missing"))


def test_start_without_settings_and_without_a_terminal_explains_instead_of_hanging(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert firstrun.start(str(tmp_path / "none" / "config.toml"), lambda cfg, path: 0) == 2
    assert "terminal" in capsys.readouterr().err


def test_first_run_asks_creates_the_settings_and_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    started = []
    path = str(tmp_path / "home" / "config.toml")
    code = firstrun.start(path, lambda cfg, p: started.append((cfg, p)) or 0, read=feed(["boss", ""]),
                          read_password=feed(["long-enough-1", "long-enough-1"]), say=lambda *a: None, open_browser=False)
    assert code == 0 and os.path.exists(path) and started[0][1] == path and "boss" in started[0][0].users
    # the second start asks nothing and runs with the same settings
    again = []
    assert firstrun.start(path, lambda cfg, p: again.append(p) or 0, read=lambda *a: pytest.fail("asked again"),
                          read_password=lambda *a: pytest.fail("asked again"), say=lambda *a: None, open_browser=False) == 0
    assert again == [path]


def test_no_arguments_and_start_both_mean_start(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cli, "_run_server", lambda cfg, path: seen.append(path) or 0)
    path = tmp_path / "home" / "config.toml"
    firstrun.create_config(str(path), "admin", "long-enough-1", "")
    monkeypatch.setenv("PLUTEUS_HOME", str(path.parent))
    assert cli.main([]) == 0 and cli.main(["start"]) == 0 and cli.main(["start", "--config", str(path)]) == 0
    assert seen == [str(path)] * 3
