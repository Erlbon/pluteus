"""Personal upload folders: streaming intake, name safety, limits, isolation between users, rename/delete."""
import dataclasses
import json
import os
import re
import sys

import pytest
from conftest import basic
from starlette.testclient import TestClient
from test_login_web import HTML, make

from pluteus import uploads
from pluteus.config import ConfigError, parse_config

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def up(cfg, tmp_path):
    """(client signed in as alice, app, uploads folder) with uploads switched on."""
    folder = tmp_path / "uploads"
    client, app = make(cfg, tmp_path, uploads_dir=str(folder))
    me = TestClient(app, follow_redirects=False)
    r = me.post("/login", content="username=alice&password=pw-alice",
                headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert r.status_code == 303
    return me, app, folder


def send(client, files, url="/upload/uploads", **kw):
    return client.post(url, files=files, headers={**ORIGIN, "accept": "application/json"}, **kw)


def on_disk(folder, user="alice"):
    base = folder / user
    return sorted(str(p.relative_to(base)).replace(os.sep, "/") for p in base.rglob("*") if p.is_file())


# ---- the basics ---------------------------------------------------------------------------------------------------
def test_upload_lands_in_the_users_own_folder_and_shows_in_the_listing(up):
    client, _, folder = up
    r = send(client, [("files", ("hello.txt", b"hello world", "text/plain")),
                      ("files", ("two.epub", b"PK" + b"x" * 50, "application/epub+zip"))])
    assert r.status_code == 200 and [f["name"] for f in r.json()["saved"]] == ["hello.txt", "two.epub"]
    assert (folder / "alice" / "hello.txt").read_bytes() == b"hello world"
    page = client.get("/browse/uploads", headers=HTML).text
    assert "hello.txt" in page and "two.epub" in page and "class='pl-upload'" in page       # the page offers the form
    assert client.get("/dl/uploads/hello.txt").content == b"hello world"
    assert "uploads" in client.get("/", headers=HTML).text
    assert "/opds/uploads" in client.get("/opds", headers=basic("alice", "pw-alice")).text   # readers can browse it too


def test_the_plain_form_post_redirects_back_with_a_count(up):
    client, _, _ = up
    r = client.post("/upload/uploads", files=[("files", ("a.txt", b"a", "text/plain"))], headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/browse/uploads?saved=1"
    assert "Uploaded 1 file." in client.get("/browse/uploads?saved=1", headers=HTML).text


def test_nothing_from_the_query_string_is_echoed_in_the_flash_message(up):
    client, _, _ = up
    page = client.get("/browse/uploads?saved=<script>&ok=<b>&bad=<i>", headers=HTML).text
    assert "<script>" not in page and "<b>" not in page and "<i>" not in page.replace("<i></i>", "")


def test_a_clashing_name_gets_a_number_and_nothing_is_overwritten(up):
    client, _, folder = up
    for data in (b"one", b"two", b"three"):
        send(client, [("files", ("same.txt", data, "text/plain"))])
    assert on_disk(folder) == ["same (1).txt", "same (2).txt", "same.txt"]
    assert (folder / "alice" / "same.txt").read_bytes() == b"one"


def test_uploads_can_go_into_a_subfolder_made_by_the_user(up):
    client, _, folder = up
    r = client.post("/files/mkdir/uploads", content="name=Trips", headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303 and "ok=folder" in r.headers["location"]
    assert send(client, [("files", ("p.txt", b"x", "text/plain"))], url="/upload/uploads/Trips").status_code == 200
    assert on_disk(folder) == ["Trips/p.txt"]


# ---- names -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,clean", [
    ("normal.txt", "normal.txt"), ("../../etc/passwd", "passwd"), ("..\\..\\boot.ini", "boot.ini"),
    ("C:\\Windows\\evil.exe", "evil.exe"), ("a:b.txt", "a_b.txt"), ("a<b>c.txt", "a_b_c.txt"),
    ("trailing.dot.", "trailing.dot"), ("trailing space ", "trailing space"), (".hidden", "hidden"),
    ("...", None), ("", None), ("CON", "_CON"), ("nul.txt", "_nul.txt"), ("Com1.log", "_Com1.log"),
    ("file.txt::$DATA", "file.txt__$DATA"), ("tab\there.txt", "tab_here.txt"),
])
def test_sanitize_name(raw, clean):
    assert uploads.sanitize_name(raw) == clean


def test_sanitize_keeps_the_extension_when_cutting_a_long_name():
    name = uploads.sanitize_name("x" * 400 + ".epub")
    assert len(name) <= uploads.MAX_NAME and name.endswith(".epub")


def test_hidden_and_marker_names_cannot_be_created(up, cfg):
    client, app, folder = up
    for bad in (".adult", "Thumbs.db", ".git"):
        r = send(client, [("files", (bad, b"x", "text/plain"))])
        assert r.status_code == 200
    assert not (folder / "alice" / ".adult").exists() and not (folder / "alice" / "Thumbs.db").exists()
    assert all(not n.startswith(".") for n in on_disk(folder))


def test_a_file_named_like_a_path_cannot_escape(up, tmp_path):
    client, _, folder = up
    send(client, [("files", ("../../escape.txt", b"x", "text/plain")), ("files", ("..\\bob\\x.txt", b"x", "text/plain"))])
    assert not (tmp_path / "escape.txt").exists() and not (folder / "escape.txt").exists()
    assert on_disk(folder) == ["escape.txt", "x.txt"]
    assert not (folder / "bob").exists()


# ---- limits ----------------------------------------------------------------------------------------------------
def test_a_file_over_the_size_limit_is_refused_without_stopping_the_others(cfg, tmp_path):
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), upload_max_mb=1)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    r = send(me, [("files", ("big.bin", b"x" * (2 * 1024 * 1024), "application/octet-stream")),
                  ("files", ("small.txt", b"ok", "text/plain"))])
    body = r.json()
    assert [f["name"] for f in body["saved"]] == ["small.txt"]
    assert body["failed"] == [{"name": "big.bin", "code": "toobig", "message": "is larger than the size limit"}]
    assert on_disk(tmp_path / "up") == ["small.txt"]                    # no leftover temp file for the refused one


def test_the_quota_counts_what_is_already_there(cfg, tmp_path):
    client, app = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), upload_quota_mb=1)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert send(me, [("files", ("a.bin", b"x" * 700_000, "application/octet-stream"))]).json()["failed"] == []
    r = send(me, [("files", ("b.bin", b"x" * 700_000, "application/octet-stream"))]).json()
    assert r["saved"] == [] and r["failed"][0]["code"] == "quota"
    assert on_disk(tmp_path / "up") == ["a.bin"]


def test_a_client_that_hangs_up_leaves_no_partial_file(up):
    import asyncio
    client, app, folder = up
    intake = uploads.Intake(str(folder / "alice") if (folder / "alice").exists() else str(folder), max_bytes=10**6, quota_left=None)
    os.makedirs(intake.dir, exist_ok=True)
    intake._part_begin()
    intake._hf, intake._hv = b"content-disposition", b'form-data; name="files"; filename="half.bin"'
    intake._header_end()
    intake._headers_finished()
    intake._part_data(b"abc", 0, 3)
    assert any(n.endswith(".part") for n in os.listdir(intake.dir))
    intake.abort()
    assert not any(n.endswith(".part") for n in os.listdir(intake.dir))


def test_a_body_that_is_not_multipart_is_refused(up):
    client, _, _ = up
    r = client.post("/upload/uploads", content=b"just bytes", headers={**ORIGIN, "content-type": "application/octet-stream"})
    assert r.status_code == 415


# ---- who may do what ---------------------------------------------------------------------------------------------
def test_every_user_has_a_separate_folder_and_cannot_see_anothers(cfg, tmp_path):
    folder = tmp_path / "up"
    client, app = make(cfg, tmp_path, uploads_dir=str(folder))
    sessions = {}
    for name in ("alice", "bob"):
        c = TestClient(app, follow_redirects=False)
        c.post("/login", content="username=%s&password=pw-%s" % (name, name),
               headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
        sessions[name] = c
    send(sessions["alice"], [("files", ("alices.txt", b"secret", "text/plain"))])
    assert sessions["bob"].get("/dl/uploads/alices.txt").status_code == 404
    assert "alices.txt" not in sessions["bob"].get("/browse/uploads", headers=HTML).text
    send(sessions["bob"], [("files", ("bobs.txt", b"x", "text/plain"))])
    assert on_disk(folder, "alice") == ["alices.txt"] and on_disk(folder, "bob") == ["bobs.txt"]


def test_uploads_need_a_login_and_a_same_site_request(up, cfg, tmp_path):
    client, app, _ = up
    anon = TestClient(app, follow_redirects=False)
    assert anon.post("/upload/uploads", files=[("files", ("a.txt", b"x", "text/plain"))], headers=ORIGIN).status_code in (303, 401)
    r = client.post("/upload/uploads", files=[("files", ("a.txt", b"x", "text/plain"))], headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.post("/upload/uploads", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 403   # no Origin at all


def test_uploads_only_go_to_the_uploads_root(up):
    client, _, _ = up
    assert send(client, [("files", ("a.txt", b"x", "text/plain"))], url="/upload/books").status_code == 404
    assert send(client, [("files", ("a.txt", b"x", "text/plain"))], url="/upload/comics/Plain").status_code == 404
    assert send(client, [("files", ("a.txt", b"x", "text/plain"))], url="/upload/uploads/..%2f..").status_code in (400, 404)
    assert send(client, [("files", ("a.txt", b"x", "text/plain"))], url="/upload/uploads/NoSuchFolder").status_code == 404


def test_the_anonymous_user_and_users_without_the_flag_get_no_upload_folder(cfg, tmp_path):
    users = dict(cfg.users)
    users["carol"] = dataclasses.replace(users["carol"], upload=False)
    client, app = make(dataclasses.replace(cfg, users=users), tmp_path, uploads_dir=str(tmp_path / "up"), anonymous_user="alice")
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/opds").status_code == 200 and "/opds/uploads" not in anon.get("/opds").text
    assert anon.post("/upload/uploads", files=[("files", ("a.txt", b"x", "text/plain"))], headers=ORIGIN).status_code == 404
    carol = basic("carol", "pw-carol")
    assert "/opds/uploads" not in client.get("/opds", headers=carol).text
    assert client.get("/opds/uploads", headers=carol).status_code == 404


def test_uploads_are_off_unless_configured(cfg, tmp_path):
    client, app = make(cfg, tmp_path)
    me = TestClient(app, follow_redirects=False)
    me.post("/login", content="username=alice&password=pw-alice",
            headers={"content-type": "application/x-www-form-urlencoded", **ORIGIN})
    assert me.get("/browse/uploads", headers=HTML).status_code == 404
    assert me.post("/upload/uploads", files=[("files", ("a.txt", b"x", "text/plain"))], headers=ORIGIN).status_code == 404
    assert "share_target" not in me.get("/manifest.webmanifest").json()


# ---- rename and delete -------------------------------------------------------------------------------------------
def form(client, url, **fields):
    from urllib.parse import urlencode
    return client.post(url, content=urlencode(fields), headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"})


def test_rename_and_delete(up):
    client, _, folder = up
    send(client, [("files", ("old.txt", b"x", "text/plain")), ("files", ("keep.txt", b"y", "text/plain"))])
    r = form(client, "/files/rename/uploads/old.txt", name="new name.txt")
    assert r.status_code == 303 and "ok=renamed" in r.headers["location"]
    assert on_disk(folder) == ["keep.txt", "new name.txt"]
    assert "bad=exists" in form(client, "/files/rename/uploads/keep.txt", name="new name.txt").headers["location"]
    assert "bad=name" in form(client, "/files/rename/uploads/keep.txt", name="...").headers["location"]
    r = form(client, "/files/delete/uploads/new%20name.txt")
    assert "ok=deleted" in r.headers["location"] and on_disk(folder) == ["keep.txt"]
    assert form(client, "/files/delete/uploads/ghost.txt").status_code == 404


def test_rename_may_only_change_letter_case(up):
    client, _, folder = up
    send(client, [("files", ("Book.epub", b"x", "text/plain"))])
    assert "ok=renamed" in form(client, "/files/rename/uploads/Book.epub", name="book.EPUB").headers["location"]
    assert on_disk(folder) == ["book.EPUB"]


def test_a_folder_is_deleted_only_when_empty(up):
    client, _, folder = up
    form(client, "/files/mkdir/uploads", name="Box")
    send(client, [("files", ("in.txt", b"x", "text/plain"))], url="/upload/uploads/Box")
    assert "bad=notempty" in form(client, "/files/delete/uploads/Box").headers["location"]
    form(client, "/files/delete/uploads/Box/in.txt")
    assert "ok=deleted" in form(client, "/files/delete/uploads/Box").headers["location"]
    assert not (folder / "alice" / "Box").exists()


def test_file_operations_cannot_leave_the_users_folder_or_touch_the_libraries(up, tmp_path):
    client, _, folder = up
    (tmp_path / "books" / "Good Author" / "Plain Filename.epub").exists()
    assert form(client, "/files/delete/books/Good%20Author/Plain%20Filename.epub").status_code == 404
    assert form(client, "/files/rename/comics/Plain/notes.txt", name="x").status_code == 404
    assert (tmp_path / "comics" / "Plain" / "notes.txt").exists()
    assert form(client, "/files/delete/uploads/..%2f..%2fsecret.txt").status_code in (400, 404)
    assert (tmp_path / "secret.txt").exists()
    assert form(client, "/files/delete/uploads").status_code == 404                  # the folder itself is not deletable


def test_file_operations_need_a_same_site_post(up):
    client, _, _ = up
    send(client, [("files", ("a.txt", b"x", "text/plain"))])
    assert client.post("/files/delete/uploads/a.txt", content="", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.get("/files/delete/uploads/a.txt").status_code == 405


# ---- Android "Share to pluteus" ------------------------------------------------------------------------------------
def test_manifest_offers_a_share_target_only_when_uploads_are_on(up):
    client, _, _ = up
    data = client.get("/manifest.webmanifest").json()
    target = data["share_target"]
    assert target["action"] == "/share" and target["method"] == "POST" and target["enctype"] == "multipart/form-data"
    assert target["params"]["files"][0]["name"] == "files"
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")


def test_share_accepts_a_browser_navigation_without_an_origin_header(up):
    client, _, folder = up
    files = [("files", ("shared.pdf", b"%PDF", "application/pdf"))]
    assert client.post("/share", files=files).status_code == 403                               # no proof of origin
    assert client.post("/share", files=files, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    r = client.post("/share", files=files, headers={"sec-fetch-site": "none"})
    assert r.status_code == 303 and r.headers["location"] == "/browse/uploads?saved=1"
    assert on_disk(folder) == ["shared.pdf"]


def test_base_path_shows_up_in_the_share_target(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), base_path="/lib")
    assert client.get("/manifest.webmanifest").json()["share_target"]["action"] == "/lib/share"


# ---- configuration -------------------------------------------------------------------------------------------------
def conf(library, **extra):
    data = {"server": {"uploads_dir": str(library / "uploads"), **extra},
            "roots": [{"name": "books", "path": str(library / "books")}],
            "users": [{"name": "alice", "password_hash": "scrypt$1$2$3$4$5"}]}
    return data


def test_uploads_dir_must_be_separate_from_the_library_and_from_pluteus_own_files(library):
    parse_config(conf(library))
    with pytest.raises(ConfigError, match="overlap"):
        parse_config(conf(library, uploads_dir=str(library / "books" / "up")))
    with pytest.raises(ConfigError, match="overlap"):
        parse_config(conf(library, uploads_dir=str(library)))                               # contains the root
    with pytest.raises(ConfigError, match="data_dir"):
        parse_config(conf(library, data_dir=str(library / "uploads" / "data")))


def test_the_name_uploads_is_reserved_for_the_virtual_root(library):
    data = conf(library)
    data["roots"].append({"name": "uploads", "path": str(library / "comics")})
    with pytest.raises(ConfigError, match="reserved"):
        parse_config(data)


@pytest.mark.parametrize("name", ["bad/name", "trailing.", " lead", "CON", "a:b"])
def test_user_names_must_work_as_folder_names_when_uploads_are_on(library, name):
    data = conf(library)
    data["users"] = [{"name": name, "password_hash": "scrypt$1$2$3$4$5"}]
    with pytest.raises(ConfigError, match="folder name|no colon"):
        parse_config(data)
    data["users"][0]["upload"] = False
    if ":" not in name:
        parse_config(data)                                                                  # fine for users who do not upload


def test_users_that_differ_only_in_case_would_share_a_folder(library):
    data = conf(library)
    data["users"] = [{"name": "Alice", "password_hash": "scrypt$1$2$3$4$5"},
                     {"name": "alice", "password_hash": "scrypt$1$2$3$4$5"}]
    with pytest.raises(ConfigError, match="share one upload folder"):
        parse_config(data)


def test_limits_are_range_checked(library):
    for key, value in (("upload_max_mb", 0), ("upload_quota_mb", -1)):
        with pytest.raises(ConfigError, match=key):
            parse_config(conf(library, **{key: value}))


def test_a_refused_cross_site_request_is_logged_with_the_deciding_headers_only(up, caplog):
    import logging
    client, _, _ = up
    with caplog.at_level(logging.WARNING, logger="pluteus"):
        client.post("/share", files=[("files", ("a.txt", b"x", "text/plain"))], headers={"sec-fetch-site": "cross-site"})
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "refused POST /share" in line and "sec-fetch-site='cross-site'" in line
    assert "pluteus_session" not in line and "pw-alice" not in line


def test_manifest_has_an_id_an_explicit_share_list_and_the_share_entry_can_be_switched_off(cfg, tmp_path):
    client, _ = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), base_path="/lib")
    data = client.get("/manifest.webmanifest").json()
    assert data["id"] == "/lib/" and data["start_url"] == "/lib/" and data["scope"] == "/lib/"
    accept = data["share_target"]["params"]["files"][0]["accept"]
    assert "*/*" not in accept and "image/*" in accept and ".cbz" in accept                  # explicit, never 'everything'
    client, _ = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), base_path="/lib", share_target=False)
    off = client.get("/manifest.webmanifest").json()
    assert "share_target" not in off and off["id"] == "/lib/"                               # the app itself stays installable


def test_the_temporary_pwa_test_pages_exist_only_when_switched_on(cfg, tmp_path):
    off, _ = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), base_path="/lib")
    assert off.get("/pwa-test/v1").status_code == 404 and off.get("/pwa-test").status_code == 404
    on, _ = make(cfg, tmp_path, uploads_dir=str(tmp_path / "up"), base_path="/lib", pwa_test=True)
    assert "pwa test v1" in on.get("/pwa-test/v1").text and "manifest.webmanifest" in on.get("/pwa-test/v1").text
    assert on.get("/pwa-test/v9").status_code == 404
    v1, v2, v3, v4 = (on.get("/pwa-test/%s/manifest.webmanifest" % v).json() for v in ("v1", "v2", "v3", "v4"))
    assert "id" not in v1 and v2["id"] == "/lib/pwa-test/v2" and v2["scope"] == "/lib/"
    assert not any(i.get("purpose") == "maskable" for i in v2["icons"]) and any(i.get("purpose") == "maskable" for i in v3["icons"])
    assert "share_target" not in v3 and v4["share_target"]["action"] == "/lib/share"
    assert "/lib/pwa-test/v4" in on.get("/pwa-test").text
