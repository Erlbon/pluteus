"""The radio and TV pickers (pages served from Settings) and the endpoint that saves their stations as .m3u files in the upload folder."""
import json

import pytest
from starlette.testclient import TestClient
from test_uploads import ORIGIN, up  # noqa: F401  (the signed-in client with uploads on)

from test_login_web import HTML

JSON = {**ORIGIN, "content-type": "application/json"}


def post(client, body, **kw):
    return client.post("/api/stations", content=json.dumps(body), headers=JSON, **kw)


def test_settings_links_to_the_pickers_and_the_pages_are_served_with_their_own_policy(up):
    me, app, folder = up
    settings = me.get("/settings", headers=HTML).text
    assert "href='/stations/radio'" in settings and "href='/stations/tv'" in settings
    radio = me.get("/stations/radio", headers=HTML)
    assert radio.status_code == 200 and 'data-save="/api/stations"' in radio.text and "__" not in radio.text.replace("__STATIC", "")
    assert "https://*.api.radio-browser.info" in radio.headers["content-security-policy"]
    tv = me.get("/stations/tv", headers=HTML)
    assert tv.status_code == 200 and "/static/vendor/hls.min.js" in tv.text
    assert "connect-src 'self' http: https:" in tv.headers["content-security-policy"]
    assert me.get("/stations/nothing", headers=HTML).status_code == 404
    assert me.get("/static/pickers/radio-picker.js").status_code == 200 and me.get("/static/pickers/picker.css").status_code == 200


def test_stations_are_saved_as_m3u_files_in_the_users_radio_folder(up):
    me, app, folder = up
    r = post(me, {"kind": "radio", "sub": "Norway", "stations": [
        {"name": "NRK P3", "url": "https://example.org/p3"},
        {"name": "Bad address", "url": "javascript:alert(1)"},
        {"name": "Spaces", "url": "https://example.org/a b"},
        {"name": "../../Escape", "url": "https://example.org/e"},
        {"name": "Line\nbreak", "url": "http://example.org:8000/x"}]})
    assert r.status_code == 200 and r.json() == {"saved": 3, "skipped": 0, "folder": "Radio/Norway"}
    mine = folder / "alice" / "Radio" / "Norway"
    assert sorted(p.name for p in mine.iterdir()) == ["Escape.m3u", "Line break.m3u", "NRK P3.m3u"]
    assert (mine / "NRK P3.m3u").read_text(encoding="utf-8") == "#EXTM3U\n#EXTINF:-1,NRK P3\nhttps://example.org/p3\n"
    assert not (folder / "Escape.m3u").exists() and not (folder / "alice" / "Escape.m3u").exists()


def test_existing_files_are_left_alone_unless_asked(up):
    me, app, folder = up
    body = {"kind": "radio", "stations": [{"name": "One", "url": "https://example.org/one"}]}
    assert post(me, body).json()["saved"] == 1
    again = post(me, {**body, "stations": [{"name": "One", "url": "https://example.org/changed"}]})
    assert again.json()["saved"] == 0 and again.json()["skipped"] == 1
    assert "one" in (folder / "alice" / "Radio" / "One.m3u").read_text(encoding="utf-8")
    forced = post(me, {**body, "overwrite": True, "stations": [{"name": "One", "url": "https://example.org/changed"}]})
    assert forced.json()["saved"] == 1 and "changed" in (folder / "alice" / "Radio" / "One.m3u").read_text(encoding="utf-8")


def test_tv_channels_carry_their_attributes_and_can_go_in_a_folder_per_country(up):
    me, app, folder = up
    r = post(me, {"kind": "tv", "by_country": True, "stations": [
        {"name": "NRK1", "url": "https://example.org/nrk1.m3u8", "id": "NRK1.no", "group": 'News "x"', "country": "Norway"},
        {"name": "Other", "url": "https://example.org/o.m3u8", "id": "<b>", "group": "", "country": ""}]})
    assert r.json()["saved"] == 2
    nrk = (folder / "alice" / "IPTV" / "Norway" / "NRK1.m3u").read_text(encoding="utf-8")
    assert nrk == '#EXTM3U\n#EXTINF:-1 tvg-id="NRK1.no" group-title="News x",NRK1\nhttps://example.org/nrk1.m3u8\n'
    other = (folder / "alice" / "IPTV" / "Other.m3u").read_text(encoding="utf-8")
    assert "<" not in other.split("\n")[1]


def test_bad_requests_and_other_users_are_refused(up, cfg):
    me, app, folder = up
    assert post(me, {"kind": "nope", "stations": [{"name": "x", "url": "https://example.org/x"}]}).status_code == 400
    assert post(me, {"kind": "radio", "stations": []}).status_code == 400
    assert me.post("/api/stations", content="not json", headers=JSON).status_code == 400
    assert me.post("/api/stations", content="x" * 500_000, headers=JSON).status_code == 413
    assert me.post("/api/stations", content="{}", headers={"content-type": "application/json"}).status_code in (401, 403)   # no same-site origin
    anon = TestClient(app, follow_redirects=False)
    assert anon.post("/api/stations", content="{}", headers=JSON).status_code in (401, 303)
    assert anon.get("/stations/radio").status_code in (401, 303)


def test_no_upload_folder_means_no_pages_and_no_links(cfg, tmp_path):
    from test_login_web import make
    client, app = make(cfg, tmp_path)                     # uploads are off
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert "/stations/radio" not in me.get("/settings", headers=HTML).text
    assert me.get("/stations/radio", headers=HTML).status_code == 404
    assert me.post("/api/stations", content="{}", headers=JSON).status_code == 404


def test_the_users_space_limit_applies(cfg, tmp_path):
    from test_login_web import make
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), upload_quota_mb=1)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice", headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    big = (tmp_path / "up" / "alice")
    big.mkdir(parents=True, exist_ok=True)
    (big / "fill.bin").write_bytes(b"x" * (1024 * 1024))
    r = post(me, {"kind": "radio", "stations": [{"name": "Full", "url": "https://example.org/f"}]})
    assert r.status_code == 400 and "space" in r.json()["error"]
