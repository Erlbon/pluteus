"""Pure-function tests: sorting, display names, passwords, config validation."""
import pytest

from bookshelf.auth import Authenticator, hash_password, verify_password
from bookshelf.config import ConfigError, parse_config
from bookshelf.fs import Library, PathError, check_segment, natural_key


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


def test_config_defaults_and_extensions():
    cfg = parse_config(_base(extensions=["EPUB", ".cbz"], exclude=["Scratch"]))
    assert cfg.extensions == (".epub", ".cbz")
    assert "scratch" in cfg.excludes and "thumbs.db" in cfg.excludes
    assert cfg.page_size == 50 and cfg.base_path == ""
