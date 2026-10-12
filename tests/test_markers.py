"""The .private and .hidden marker files (next to the older .adult one)."""
import os

import pytest

from conftest import basic
from pluteus.fs import Library, NotFound


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def names(lib, user, *parts, root="comics"):
    return [e.name for e in lib.list_dir(user, root, parts) if e.is_dir]


@pytest.fixture
def lib(cfg):
    return Library(cfg)


def test_private_folder_is_only_for_the_named_users(cfg, library, lib):
    alice, bob, carol = cfg.users["alice"], cfg.users["bob"], cfg.users["carol"]
    _write(library / "comics" / "Family" / "Photos" / "a.cbz", "x")
    _write(library / "comics" / "Family" / ".private", "# who may open this\nAlice\n\n")
    assert "Family" in names(lib, alice)                                   # named (any letter case): sees it
    assert "Family" not in names(lib, bob) and "Family" not in names(lib, carol)    # adult is not a pass
    assert lib.list_dir(alice, "comics", ("Family", "Photos"), True)       # and everything below it
    for user in (bob, carol):
        with pytest.raises(NotFound):
            lib.list_dir(user, "comics", ("Family",))
        with pytest.raises(NotFound):
            lib.file_info(user, "comics", ("Family", "Photos", "a.cbz"))   # a direct URL answers like a missing folder


def test_an_empty_or_unreadable_private_file_names_nobody(cfg, library, lib):
    _write(library / "comics" / "Closed" / ".private", "")
    for user in cfg.users.values():
        assert "Closed" not in names(lib, user)
    (library / "comics" / "Closed" / ".private").unlink()
    (library / "comics" / "Closed" / ".private").mkdir()                   # exists but cannot be read as a file
    lib2 = Library(cfg)
    assert "Closed" not in names(lib2, cfg.users["alice"])


def test_names_may_be_comma_separated_and_commented(cfg, library, lib):
    _write(library / "comics" / "Shared" / ".private", "bob, carol  # the two of them\n")
    assert "Shared" in names(lib, cfg.users["carol"]) and "Shared" in names(lib, cfg.users["bob"])
    assert "Shared" not in names(lib, cfg.users["alice"])


def test_nested_private_folders_must_all_allow_the_user(cfg, library, lib):
    _write(library / "comics" / "Outer" / ".private", "alice\nbob\n")
    _write(library / "comics" / "Outer" / "Inner" / ".private", "bob\n")
    _write(library / "comics" / "Outer" / "Inner" / "x.cbz", "x")
    assert "Inner" in names(lib, cfg.users["bob"], "Outer")
    assert "Inner" not in names(lib, cfg.users["alice"], "Outer")
    with pytest.raises(NotFound):
        lib.file_info(cfg.users["alice"], "comics", ("Outer", "Inner", "x.cbz"))


def test_hidden_folder_is_left_out_of_listings_but_a_direct_link_works(cfg, library, lib):
    _write(library / "comics" / "Draft" / "d.cbz", "x")
    _write(library / "comics" / "Draft" / ".hidden", "")
    for user in cfg.users.values():
        assert "Draft" not in names(lib, user)
        assert [e.name for e in lib.list_dir(user, "comics", ("Draft",))] == ["d.cbz"]
        assert lib.file_info(user, "comics", ("Draft", "d.cbz")).size == 1


def test_hidden_with_a_list_still_shows_for_those_named(cfg, library, lib):
    _write(library / "comics" / "Draft" / ".hidden", "alice\n")
    assert "Draft" in names(lib, cfg.users["alice"]) and "Draft" not in names(lib, cfg.users["bob"])


def test_marker_files_never_show_up_or_download(cfg, library, client):
    _write(library / "comics" / "Family" / ".private", "alice\n")
    _write(library / "comics" / "Family" / "a.cbz", "x")
    alice = basic("alice", "pw-alice")
    page = client.get("/browse/comics/Family", headers=alice)
    assert page.status_code == 200 and "a.cbz" in page.text and ".private" not in page.text
    assert client.get("/dl/comics/Family/.private", headers=alice).status_code == 404
    assert client.get("/browse/comics/Family", headers=basic("carol", "pw-carol")).status_code == 404
    lib = Library(cfg)
    assert lib.is_hidden_name(".private") and lib.is_hidden_name(".HIDDEN") and lib.is_hidden_name(".adult")
