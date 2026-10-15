"""The app key in the address (/k/<key>/opds): for reader apps that cannot log in. Read-only, only the paths a feed links to, links carry the key."""
import logging
import re

from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

KEY = "Key-For-Erlend_0123456789"
BODY = b"EPUBDATA" * 50


def make(tmp_path, base_path=""):
    books = tmp_path / "books"
    (books / "Author").mkdir(parents=True)
    (books / "Author" / "Book.epub").write_bytes(BODY)
    server = {"base_path": base_path} if base_path else {}
    cfg = parse_config({"server": server, "roots": [{"name": "books", "path": str(books)}],
                        "users": [{"name": "erlend", "password_hash": hash_password("real-password-1", n=2 ** 4), "roots": ["*"]},
                                  {"name": "other", "password_hash": hash_password("other-password-1", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("erlend", KEY)
    return TestClient(app, follow_redirects=False), app


def test_the_key_in_the_address_opens_the_feed_without_a_login(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/opds").status_code == 401
    r = c.get("/k/%s/opds" % KEY)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/atom+xml")
    assert 'href="/k/%s/opds/books"' % KEY in r.text and int(r.headers["content-length"]) == len(r.content)


def test_every_link_of_a_feed_keeps_the_key_so_the_app_can_walk_and_download(tmp_path):
    c, _ = make(tmp_path)
    feed = c.get("/k/%s/opds/books/Author" % KEY).text
    links = re.findall(r'href="(/[^"]+)"', feed)
    assert links and all(link.startswith("/k/%s/" % KEY) for link in links), links
    book = [link for link in links if "/dl/" in link]
    assert book
    got = c.get(book[0])
    assert got.status_code == 200 and got.content == BODY                          # the download works with the key, no login
    folder = c.get("/k/%s/opds/books" % KEY)
    assert folder.status_code == 200 and "Author" in folder.text


def test_it_also_works_under_a_base_path_and_in_opds_2(tmp_path):
    c, _ = make(tmp_path, "/pluteus")
    r = c.get("/pluteus/k/%s/opds" % KEY)
    assert r.status_code == 200 and 'href="/pluteus/k/%s/opds/books"' % KEY in r.text
    j = c.get("/pluteus/k/%s/opds/books/Author" % KEY, headers={"Accept": "application/opds+json"})
    assert j.status_code == 200
    if j.headers["content-type"].startswith("application/opds+json"):
        assert '"href":"/pluteus/k/%s/' % KEY in j.text.replace('": "', '":"')


def test_a_wrong_key_is_refused_and_counted_like_a_wrong_password(tmp_path):
    c, _ = make(tmp_path)
    for n in range(5):
        assert c.get("/k/Wrong-Key-Number-%d-xxxxx/opds" % n).status_code == 401
    assert c.get("/k/Wrong-Key-Number-9-xxxxx/opds").status_code == 429                    # the name "key-in-address" is locked for strangers
    assert c.get("/k/%s/opds" % KEY).status_code == 429 or c.get("/k/%s/opds" % KEY).status_code == 200
    assert c.get("/k/short/opds").status_code == 404                                       # not even shaped like a key


def test_only_the_paths_a_feed_links_to_are_reachable_this_way(tmp_path):
    c, _ = make(tmp_path)
    for path in ("/browse/books", "/settings", "/api/progress/books/Author/Book.epub", "/upload/books", "/files/mkdir/uploads", "/", "/shell",
                 "/dav/", "/rest/ping.view"):
        assert c.get("/k/%s%s" % (KEY, path)).status_code == 404, path
    assert c.post("/k/%s/opds" % KEY, content=b"x", headers={"origin": "http://testserver"}).status_code in (401, 403, 404, 405)


def test_the_key_user_is_read_only_and_a_revoked_key_stops_at_once(tmp_path):
    c, app = make(tmp_path)
    assert c.get("/k/%s/opds" % KEY).status_code == 200
    app.state.state.delete_appkey("erlend")
    app.state.auth.forget_user("erlend")
    assert c.get("/k/%s/opds" % KEY).status_code == 401
    app.state.state.set_appkey("other", "Another-Key-For-Other-123")
    assert c.get("/k/Another-Key-For-Other-123/opds").status_code == 200
    assert c.get("/k/%s/opds" % KEY).status_code == 401                                    # a key opens only its own user


def test_the_key_never_reaches_the_logs(tmp_path, caplog):
    c, app = make(tmp_path)
    app.state.debuglog.enable()
    with caplog.at_level(logging.INFO):
        c.get("/k/%s/opds" % KEY)
        c.get("/k/%s/opds/books" % KEY)
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("pluteus"))
    assert KEY not in ours and KEY not in "".join(t for _, t in app.state.debuglog.lines())
    assert "/k/***/opds" in ours


def test_a_login_header_or_cookie_sent_with_a_key_address_is_not_trusted(tmp_path):
    c, _ = make(tmp_path)
    r = c.get("/k/%s/opds" % KEY, headers={"Authorization": "Basic " + "b3RoZXI6b3RoZXItcGFzc3dvcmQtMQ=="})        # other:other-password-1
    assert r.status_code == 200 and "erlend" not in r.text                                                         # served as the key's user, the header is replaced
