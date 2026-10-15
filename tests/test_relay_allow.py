"""relay_allow: hosts the owner trusts (a media server on the same machine) may be on a private address or an unusual port; nothing else may."""
import http.server
import threading

import pytest
from test_relay import AUDIO, Handler, fresh_cache, put, signed_in  # noqa: F401

from pluteus import fetchimage, relay
from pluteus.config import ConfigError, parse_config


@pytest.fixture
def local_station():
    """A radio server on 127.0.0.1 (a private address) on a port of its own."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    fetchimage.set_allowed(())


def conf(**server):
    return parse_config({"server": server, "roots": [{"name": "a", "path": "."}], "users": [{"name": "u", "password_hash": "scrypt$16384$8$1$x"}]}, check_roots=False)


def test_the_setting_is_a_list_of_exact_host_and_port_pairs():
    assert conf(relay_allow=["192.168.1.99:9191", "Dispatcharr:9191"]).relay_allow == ("192.168.1.99:9191", "dispatcharr:9191")
    assert conf().relay_allow == ()
    for bad in (["192.168.1.99"], ["http://192.168.1.99:9191"], ["192.168.1.99:9191/path"], ["*.lan:80"], ["host:0"], ["host:70000"], ["a b:80"], "192.168.1.99:9191"):
        with pytest.raises(ConfigError):
            conf(relay_allow=bad)


def test_a_private_host_is_refused_unless_it_is_listed_with_that_exact_port(local_station):
    fetchimage.set_allowed(())
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("127.0.0.1", local_station)
    fetchimage.set_allowed(["127.0.0.1:%d" % local_station])
    assert fetchimage._public_addresses("127.0.0.1", local_station)
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("127.0.0.1", local_station + 1)                          # another port on the same host is not listed
    with pytest.raises(fetchimage.Refused):
        fetchimage._public_addresses("localhost", local_station)                              # the name is not the listed string
    assert fetchimage._split("http://127.0.0.1:%d/x" % local_station)[2] == local_station      # an unusual port is fine for a listed host only
    with pytest.raises(fetchimage.Refused):
        fetchimage._split("http://127.0.0.1:%d/x" % (local_station + 1))


def test_the_relay_plays_a_station_on_a_listed_private_host_and_nothing_else(cfg, tmp_path, local_station):
    me, folder = signed_in(cfg, tmp_path, relay=True, relay_allow=("127.0.0.1:%d" % local_station,))
    put(folder, "a.m3u", "http://127.0.0.1:%d/stream" % local_station, "http://127.0.0.1:%d/stream" % (local_station + 1))
    ok = me.get("/relay/uploads/Radio/a.m3u?i=0")
    assert ok.status_code == 200 and ok.content == AUDIO
    refused = me.get("/relay/uploads/Radio/a.m3u?i=1")                                         # same host, other port: not listed
    assert refused.status_code == 502 and "not a public address" in refused.text


def test_without_the_setting_the_same_station_is_refused(cfg, tmp_path, local_station):
    me, folder = signed_in(cfg, tmp_path, relay=True)
    put(folder, "a.m3u", "http://127.0.0.1:%d/stream" % local_station)
    refused = me.get("/relay/uploads/Radio/a.m3u?i=0")
    assert refused.status_code == 502 and "not a public address" in refused.text


def test_a_setting_written_twice_or_curly_quotes_get_a_plain_words_hint(tmp_path):
    from pluteus.config import load_config
    (tmp_path / "lib").mkdir()
    tail = '\n[[roots]]\nname = "a"\npath = "%s"\n\n[[users]]\nname = "u"\npassword_hash = "scrypt$16384$8$1$x"\n' % (tmp_path / "lib").as_posix()
    twice = tmp_path / "twice.toml"
    twice.write_text('[server]\nrelay = true\nrelay_allow = ["a:80"]\nrelay_allow = ["b:80"]\n' + tail, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(str(twice))
    assert "Cannot overwrite a value" in str(e.value) and "written twice" in str(e.value) and "ONE line" in str(e.value)
    curly = tmp_path / "curly.toml"
    curly.write_text("[server]\nrelay_allow = [“a:80”]\n" + tail, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(str(curly))
    assert "curly quotes" in str(e.value)
    plain = tmp_path / "plain.toml"
    plain.write_text('[server\nrelay = true\n' + tail, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(str(plain))
    assert "not valid TOML" in str(e.value) and "written twice" not in str(e.value) and "curly" not in str(e.value)
