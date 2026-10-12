"""Pure-function tests: sorting, display names, passwords, config validation."""
import os

import pytest

from pluteus.auth import Authenticator, hash_password, verify_password
from pluteus.config import ConfigError, parse_config
from pluteus.fs import Library, PathError, check_segment, natural_key


def test_natural_key_orders_numbers_numerically():
    names = ["3.10 - b", "3.9 - a", "3 - x", "0. y", "Amazing 010", "Amazing 002"]
    assert sorted(names, key=natural_key) == ["0. y", "3 - x", "3.9 - a", "3.10 - b",
                                              "Amazing 002", "Amazing 010"]


def test_natural_key_is_case_insensitive():
    assert sorted(["b", "A", "c"], key=natural_key) == ["A", "b", "c"]


def test_display_name_strips_lineage_numbers(cfg):
    lib = Library(cfg)
    assert lib.display_name("3.23 - DC, Vertigo (1993-2020)") == "DC, Vertigo (1993-2020)"
    assert lib.display_name("0. National Allied (1934-1946)") == "National Allied (1934-1946)"
    assert lib.display_name("3-D Action") == "3-D Action"
    assert lib.display_name("2000 A.D. (1977-)") == "2000 A.D. (1977-)"
    assert lib.display_name("1984") == "1984"          # never strip down to nothing


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\x00b"])
def test_check_segment_rejects(bad):
    with pytest.raises(PathError):
        check_segment(bad)


def test_password_roundtrip_and_rejection():
    h = hash_password("hunter2", n=2 ** 4)
    assert verify_password("hunter2", h)
    assert not verify_password("hunter3", h)
    assert not verify_password("hunter2", "garbage")
    assert not verify_password("hunter2", "scrypt$x$y$z$q$w")


def test_authenticator_caches_and_rejects(cfg):
    auth = Authenticator(cfg.users, cache_seconds=60)
    from conftest import basic
    header = basic("alice", "pw-alice")["Authorization"]
    assert auth.authenticate(header).name == "alice"
    assert auth.authenticate(header).name == "alice"          # second call served from the cache
    assert auth.authenticate(basic("alice", "wrong")["Authorization"]) is None
    assert auth.authenticate(basic("nobody", "x")["Authorization"]) is None
    assert auth.authenticate("Bearer abc") is None
    assert auth.authenticate("Basic !!!notbase64") is None
    assert auth.authenticate(None) is None


def _base(**extra):
    d = {"roots": [{"name": "r", "path": "."}],
         "users": [{"name": "u", "password_hash": hash_password("p", n=2 ** 4)}]}
    d.update(extra)
    return d


def test_config_rejects_bad_input():
    with pytest.raises(ConfigError):
        parse_config({"users": _base()["users"]})                                  # no roots
    with pytest.raises(ConfigError):
        parse_config({"roots": _base()["roots"]})                                  # no users
    with pytest.raises(ConfigError):
        parse_config(_base(roots=[{"name": "bad name!", "path": "."}]))
    with pytest.raises(ConfigError):
        parse_config(_base(users=[{"name": "u", "password_hash": "plain"}]))
    with pytest.raises(ConfigError):
        parse_config(_base(users=[{"name": "u", "password_hash": "scrypt$1$2$3$4$5", "roots": ["nope"]}]))
    with pytest.raises(ConfigError):
        parse_config(_base(display_strip="(unclosed"))
    with pytest.raises(ConfigError):
        parse_config(_base(roots=[{"name": "r", "path": "/no/such/folder/anywhere"}]))


def test_misplaced_settings_are_errors_not_silently_ignored():
    # a setting typed under [[roots]] / [[users]] belongs to that table in TOML and would do nothing
    with pytest.raises(ConfigError, match="exclude.*\\[server\\]"):
        parse_config(_base(roots=[{"name": "r", "path": ".", "exclude": ["x"]}]))
    with pytest.raises(ConfigError, match="unknown key 'adulte'"):
        parse_config(_base(users=[{"name": "u", "password_hash": "scrypt$1$2$3$4$5", "adulte": True}]))
    with pytest.raises(ConfigError, match="unknown key 'bogus'"):
        parse_config(_base(server={"bogus": 1}))
    with pytest.raises(ConfigError, match="unknown key 'bogus'"):
        parse_config(_base(bogus=1))


def test_settings_work_in_server_or_at_the_top_level_and_server_wins():
    top = parse_config(_base(exclude=["A"], cache_dir="top", metadata=False, server={"show_all_files": False}))
    assert "a" in top.excludes and top.cache_dir == "top" and top.metadata is False
    inside = parse_config(_base(server={"exclude": ["B"], "cache_dir": "srv", "adult_marker": ".x",
                                        "show_all_files": False}))
    assert "b" in inside.excludes and inside.cache_dir == "srv" and inside.adult_marker == ".x"
    both = parse_config(_base(cache_dir="top", server={"cache_dir": "srv", "show_all_files": False}))
    assert both.cache_dir == "srv"


def test_the_shipped_example_config_parses_and_applies_its_settings():
    import tomllib
    from pathlib import Path
    data = tomllib.loads(Path(__file__).resolve().parent.parent.joinpath("config.example.toml").read_text("utf-8"))
    cfg = parse_config(data, check_roots=False)
    assert "_incoming" in cfg.excludes and cfg.adult_marker == ".adult"
    assert cfg.extensions == (".epub", ".cbz", ".pdf")


def test_config_defaults_and_extensions():
    cfg = parse_config(_base(extensions=["EPUB", ".cbz"], exclude=["Scratch"]))
    assert cfg.extensions == (".epub", ".cbz")
    assert "scratch" in cfg.excludes and "thumbs.db" in cfg.excludes
    assert cfg.page_size == 50 and cfg.base_path == ""


# ---- reading sizes and dates only for the entries that are shown ---------------------------------------------------------------
class CountingEntry:
    """Wraps os.DirEntry and counts stat() calls (os.DirEntry itself cannot be patched)."""
    calls = 0

    def __init__(self, entry):
        self._e, self.name, self.path = entry, entry.name, entry.path

    def is_dir(self):
        return self._e.is_dir()

    def is_file(self):
        return self._e.is_file()

    def stat(self):
        CountingEntry.calls += 1
        return self._e.stat()


def counting_scandir(monkeypatch):
    import contextlib
    real = os.scandir

    @contextlib.contextmanager
    def fake(path):
        with real(path) as it:
            yield (CountingEntry(e) for e in it)
    monkeypatch.setattr("pluteus.fs.os.scandir", fake)
    CountingEntry.calls = 0


def test_list_dir_without_stat_reads_names_only_and_with_stats_fills_the_same_values(cfg, library, monkeypatch):
    lib = Library(cfg)
    user = cfg.users["bob"]
    full = lib.list_dir(user, "comics", ("3 - Marvel (1961-2012)", "Spider-Man (1963-)"))
    counting_scandir(monkeypatch)
    lazy = lib.list_dir(user, "comics", ("3 - Marvel (1961-2012)", "Spider-Man (1963-)"), False, False)
    assert CountingEntry.calls == 0 and [e.name for e in lazy] == [e.name for e in full]
    assert all(not e.has_stat and e.size == 0 and e.mtime == 0.0 for e in lazy)
    filled = lib.with_stats(lazy[:2])
    assert CountingEntry.calls == 0                                    # with_stats uses os.stat, not the directory scan
    assert [(e.name, e.size, e.mtime) for e in filled] == [(e.name, e.size, e.mtime) for e in full[:2]]
    assert all(e.has_stat for e in filled) and lib.with_stats(filled) == filled      # already filled: unchanged


def test_with_stats_leaves_out_an_entry_that_vanished_and_handles_many_entries(cfg, library):
    lib = Library(cfg)
    folder = library / "comics" / "Plain"
    for i in range(20):
        (folder / ("f%02d.txt" % i)).write_bytes(b"x" * i)
    lazy = lib.list_dir(cfg.users["bob"], "comics", ("Plain",), True, False)
    gone = [e for e in lazy if e.name == "f05.txt"][0]
    os.remove(folder / "f05.txt")
    filled = lib.with_stats(lazy)                                      # more than 8 entries: read in parallel
    assert "f05.txt" not in [e.name for e in filled] and len(filled) == len(lazy) - 1
    assert {e.name: e.size for e in filled}["f07.txt"] == 7
    assert gone.has_stat is False


def test_a_default_listing_reads_only_the_shown_page_and_a_size_sort_reads_everything(cfg, tmp_path, library, monkeypatch):
    from conftest import basic
    from test_login_web import HTML, make
    folder = library / "comics" / "Plain"
    for i in range(30):
        (folder / ("g%02d.txt" % i)).write_bytes(b"y" * i)
    client, _ = make(cfg, tmp_path, page_size=5)
    h = dict(basic("alice", "pw-alice"), **HTML)
    counting_scandir(monkeypatch)
    assert client.get("/browse/comics/Plain", headers=h).status_code == 200
    by_name = CountingEntry.calls
    assert by_name == 0                                                # the page's 5 are read with os.stat, none via the scan
    assert "g00.txt" in client.get("/browse/comics/Plain", headers=h).text
    CountingEntry.calls = 0
    r = client.get("/browse/comics/Plain?sort=size&dir=desc", headers=h)
    assert r.status_code == 200 and CountingEntry.calls >= 30         # sorting by size needs every file's size
    assert client.get("/opds/comics/Plain", headers=basic("alice", "pw-alice")).status_code == 200


def test_a_user_without_a_roots_list_may_open_nothing_and_star_means_every_root():
    from pluteus.config import User, users_without_folders
    assert not User("a", "h").may_use("books")
    assert User("b", "h", ("*",)).may_use("books") and User("b", "h", ("*",)).may_use("later-root")
    assert User("c", "h", ("books",)).may_use("books") and not User("c", "h", ("books",)).may_use("comics")
    cfg = parse_config(_base(users=[{"name": "a", "password_hash": "scrypt$1$2$3$4$5"},
                                    {"name": "b", "password_hash": "scrypt$1$2$3$4$5", "roots": ["*"]}]), check_roots=False)
    assert users_without_folders(cfg) == ["a"]
    with pytest.raises(ConfigError):
        parse_config(_base(users=[{"name": "a", "password_hash": "scrypt$1$2$3$4$5", "roots": ["*", "books"]}]), check_roots=False)
