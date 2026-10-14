"""The vault: stored logins and headers for protected streams, encrypted at rest, sent only to the hosts they name."""
import base64
import os
import sys

import pytest

from pluteus import vault
from pluteus.state import StateDB

KEY = bytes(range(32))
ENTRY = {"auth": {"type": "basic", "user": "alice", "password": "s3cret-pass"}, "headers": {"Referer": "https://example.org/"}, "hosts": ["stream.example.org"]}


def test_an_entry_round_trips_and_the_blob_shows_nothing():
    v = vault.Vault(KEY)
    blob = v.seal(ENTRY)
    assert v.open(blob) == ENTRY
    for secret in (b"s3cret-pass", b"alice", b"Referer", b"stream.example.org", b"basic"):
        assert secret not in blob
    assert v.seal(ENTRY) != blob and v.open(v.seal(ENTRY)) == ENTRY                       # a fresh random nonce every time
    assert v.open(v.seal({"auth": {"type": "bearer", "token": "tøken-☃"}}))["auth"]["token"] == "tøken-☃"


def test_a_wrong_key_or_a_changed_blob_is_refused_never_decrypted_to_garbage():
    v = vault.Vault(KEY)
    blob = v.seal(ENTRY)
    with pytest.raises(vault.VaultError):
        vault.Vault(bytes(reversed(KEY))).open(blob)
    for i in (0, vault.NONCE, len(blob) // 2, len(blob) - 1):                              # nonce, ciphertext, tag: any changed byte
        bad = bytearray(blob)
        bad[i] ^= 1
        with pytest.raises(vault.VaultError):
            v.open(bytes(bad))
    for short in (b"", b"x" * 10, blob[: vault.NONCE + vault.TAG - 1]):
        with pytest.raises(vault.VaultError):
            v.open(short)
    with pytest.raises(vault.VaultError):
        vault.Vault(b"short")


def test_the_fingerprint_tells_keys_apart_without_revealing_them():
    a, b = vault.Vault(KEY), vault.Vault(bytes(reversed(KEY)))
    assert a.key_id == vault.Vault(KEY).key_id and a.key_id != b.key_id and len(a.key_id) == 12 and KEY.hex() not in a.key_id


def test_the_config_key_wins_then_the_file_then_memory(tmp_path):
    cfg_key, source = vault.load_master("my passphrase", str(tmp_path))
    assert source == "config" and len(cfg_key) == 32 and not (tmp_path / "relay.key").exists()          # nothing is written when the config has one
    assert vault.load_master("my passphrase", "")[0] == cfg_key and vault.load_master("another", "")[0] != cfg_key
    made, source = vault.load_master("", str(tmp_path))
    assert source == "file" and (tmp_path / "relay.key").read_text().strip() == made.hex()
    assert vault.load_master("", str(tmp_path)) == (made, "file")                                           # the same key at the next start
    if sys.platform != "win32":
        assert (os.stat(tmp_path / "relay.key").st_mode & 0o077) == 0                                       # readable by the server only
    first, source = vault.load_master("", "")
    assert source == "memory" and vault.load_master("", "")[0] != first


def test_a_key_file_that_cannot_be_used_is_never_replaced(tmp_path):
    for bad in ("", "not hex at all", "ab" * 10, "zz" * 32):
        (tmp_path / "relay.key").write_text(bad)
        with pytest.raises(vault.VaultError):
            vault.load_master("", str(tmp_path))
        assert (tmp_path / "relay.key").read_text() == bad                                                    # still there: replacing it would lose every entry


def test_the_form_is_checked_and_nothing_is_trusted():
    ok = vault.clean_entry("basic", "alice", "pw", "", "Referer: https://example.org/\nUser-Agent: Mozilla/5.0", "Stream.Example.org, cdn.example.org.")
    assert ok == {"auth": {"type": "basic", "user": "alice", "password": "pw"}, "headers": {"Referer": "https://example.org/", "User-Agent": "Mozilla/5.0"},
                  "hosts": ["cdn.example.org", "stream.example.org"]}
    assert vault.clean_entry("bearer", "", "", "tok.en-123", "", "a.example.org")["auth"] == {"type": "bearer", "token": "tok.en-123"}
    assert vault.clean_entry("none", "", "", "", "Referer: x", "a.example.org")["auth"] == {"type": "none"}
    bad = [("basic", "a:b", "pw", "", "", "h.org"), ("basic", "", "pw", "", "", "h.org"), ("basic", "a", "p\nw", "", "", "h.org"), ("bearer", "", "", "a b", "", "h.org"),
           ("bearer", "", "", "", "", "h.org"), ("magic", "", "", "", "Referer: x", "h.org"), ("none", "", "", "", "", "h.org"),
           ("none", "", "", "", "Referer", "h.org"), ("none", "", "", "", "Bad Name: x", "h.org"),
           ("none", "", "", "", "Host: evil.example", "h.org"), ("none", "", "", "", "Authorization: Basic x", "h.org"), ("none", "", "", "", "Content-Length: 5", "h.org"),
           ("none", "", "", "", "Proxy-Foo: 1", "h.org"), ("none", "", "", "", "Referer: x", ""), ("none", "", "", "", "Referer: x", "http://h.org"),
           ("none", "", "", "", "Referer: x", "h.org/path"), ("none", "", "", "", "Referer: x", "-bad.org"), ("none", "", "", "", "Referer: x", ",".join("h%d.org" % i for i in range(11))),
           ("none", "", "", "", "\n".join("H%d: v" % i for i in range(11)), "h.org"), ("none", "", "", "", "Referer: " + "x" * 501, "h.org")]
    for case in bad:
        with pytest.raises(ValueError):
            vault.clean_entry(*case)
    assert vault.clean_name("  My subscription ") == "My subscription"
    for name in ("", "x" * 61, 'a"b', "a\nb"):
        with pytest.raises(ValueError):
            vault.clean_name(name)


def test_credentials_go_only_to_the_hosts_the_entry_names():
    entry = {"auth": {"type": "basic", "user": "alice", "password": "pw"}, "headers": {"Referer": "https://example.org/"}, "hosts": ["stream.example.org"]}
    sent = vault.request_headers(entry, "Stream.Example.ORG")
    assert sent == {"Referer": "https://example.org/", "Authorization": "Basic " + base64.b64encode(b"alice:pw").decode()}
    assert vault.request_headers(entry, "cdn.example.org") == {} and vault.request_headers(entry, "evil.example") == {}          # e.g. after a redirect
    assert vault.request_headers(entry, "stream.example.org.evil.example") == {} and vault.request_headers(entry, "") == {}
    assert vault.request_headers({"auth": {"type": "bearer", "token": "T"}, "hosts": ["a.org"]}, "a.org") == {"Authorization": "Bearer T"}


def test_what_may_be_shown_never_includes_a_value():
    shown = vault.describe(ENTRY)
    assert shown == {"login": "basic", "headers": ["Referer"], "hosts": ["stream.example.org"]}
    assert "s3cret-pass" not in repr(shown) and "alice" not in repr(shown) and "example.org/" not in repr(shown["headers"])


def test_the_state_keeps_entries_per_user(tmp_path):
    state = StateDB(str(tmp_path))
    state.stream_put("alice", "Sub", b"blob-a", "key1")
    state.stream_put("bob", "Sub", b"blob-b", "key1")
    assert state.stream_get("alice", "Sub") == (b"blob-a", "key1") and state.stream_get("bob", "Sub") == (b"blob-b", "key1")
    assert state.stream_get("alice", "Other") is None and state.stream_get("carol", "Sub") is None
    state.stream_put("alice", "Sub", b"new", "key2")                                                        # replaced, not duplicated
    assert state.stream_list("alice") == [("Sub", b"new", "key2")] and state.stream_get("bob", "Sub")[0] == b"blob-b"
    state.stream_put("alice", "abc", b"x", "key2")
    assert [n for n, _, _ in state.stream_list("alice")] == ["abc", "Sub"]
    assert state.stream_delete("alice", "Sub") == 1 and state.stream_delete("alice", "Sub") == 0
    assert state.streams_delete_user("alice") == 1 and state.stream_list("alice") == [] and state.stream_list("bob")
