"""Kobo sync (kobo.py): a Kobo's own reader gets the Read next list, downloads, covers and keeps its reading position here."""
import base64
import io
import json

import pytest
from PIL import Image
from starlette.testclient import TestClient

from epub_factory import make_epub
from pluteus import keypath
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config
from pluteus.kobo import book_id, read_token

KEY = "kobo-key-for-alice-123"
BASE = "/kobo/" + KEY


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 90), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def lib(tmp_path):
    books = tmp_path / "b"
    make_epub(books / "Dune.epub", title="Dune", authors=("Frank Herbert",), calibre_series="Dune", series_index="1", cover=png())
    make_epub(books / "Emma.kepub.epub", title="Emma", authors=("Jane Austen",))
    make_epub(books / "Series" / "01 One.epub", title="One")
    make_epub(books / "Series" / "02 Two.epub", title="Two")
    (books / "Series" / "notes.txt").write_text("not a book")
    (books / "comic.cbz").write_bytes(b"PK")
    return tmp_path


def make_client(tmp_path, **server):
    cfg = parse_config({"server": {"kobo": True, **server}, "roots": [{"name": "b", "path": str(tmp_path / "b")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]},
                                  {"name": "bob", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    app = create_app(cfg)
    app.state.state.set_appkey("alice", KEY)
    return TestClient(app)


@pytest.fixture
def client(lib):
    return make_client(lib)


def shelve(client, path, is_dir=False, user="alice", now=None):
    client.app.state.state.shelf_add(user, "next", "b", path, is_dir, now=now)


def sync(client, token=None):
    headers = {"x-kobo-synctoken": token} if token else {}
    r = client.get(BASE + "/v1/library/sync", headers=headers)
    assert r.status_code == 200, r.text
    return r.json(), r.headers


def test_it_is_off_by_default(lib):
    cfg = parse_config({"roots": [{"name": "b", "path": str(lib / "b")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    assert TestClient(create_app(cfg)).get(BASE + "/v1/initialization").status_code == 404


def test_a_wrong_key_is_refused(client):
    assert client.get("/kobo/not-the-right-key/v1/initialization").status_code == 401
    assert client.get("/kobo/x/v1/initialization").status_code == 401
    assert client.get(BASE + "/v1/initialization").status_code == 200


def test_initialization_points_the_library_here(client):
    r = client.get(BASE + "/v1/initialization")
    res = r.json()["Resources"]
    assert r.headers["x-kobo-apitoken"] == "e30="
    here = "http://testserver" + BASE
    assert res["library_sync"] == here + "/v1/library/sync"
    assert res["reading_state"] == here + "/v1/library/{Ids}/state"
    assert res["image_url_template"].startswith(here + "/{ImageId}/")
    assert res["user_profile"].startswith("https://storeapi.kobo.com/")             # the store's own pages stay with Kobo


def test_device_auth_gives_tokens_and_keeps_the_user_key(client):
    got = client.post(BASE + "/v1/auth/device", json={"UserKey": "abc"}).json()
    assert got["UserKey"] == "abc" and got["AccessToken"] and got["TokenType"] == "Bearer"


def test_sync_sends_the_read_next_list(client):
    shelve(client, "Dune.epub")
    shelve(client, "Emma.kepub.epub")
    shelve(client, "comic.cbz")                                                    # not an EPUB: left out
    shelve(client, "Series", is_dir=True)                                          # a folder: the EPUBs inside it
    items, headers = sync(client)
    new = [i["NewEntitlement"] for i in items]
    titles = [n["BookMetadata"]["Title"] for n in new]
    assert titles == ["Dune", "Emma", "One", "Two"]
    dune = new[0]["BookMetadata"]
    assert dune["EntitlementId"] == book_id("b", "Dune.epub") == new[0]["BookEntitlement"]["Id"]
    assert dune["Contributors"] == ["Frank Herbert"] and dune["Series"]["Name"] == "Dune" and dune["Series"]["NumberFloat"] == 1.0
    assert [u["Format"] for u in dune["DownloadUrls"]] == ["EPUB3", "EPUB"]
    assert [u["Format"] for u in new[1]["BookMetadata"]["DownloadUrls"]] == ["KEPUB"]
    assert dune["DownloadUrls"][0]["Url"].startswith("http://testserver" + BASE + "/download/")
    assert new[0]["ReadingState"]["StatusInfo"]["Status"] == "ReadyToRead"
    assert read_token(headers["x-kobo-synctoken"]) and "x-kobo-sync" not in headers
    again, _ = sync(client, headers["x-kobo-synctoken"])
    assert again == []                                                             # nothing new


def test_other_users_lists_are_not_sent(client):
    shelve(client, "Dune.epub", user="bob")
    assert sync(client)[0] == []


def test_a_book_taken_off_the_list_is_removed_from_the_kobo(client):
    shelve(client, "Dune.epub")
    _, headers = sync(client)
    client.app.state.state.shelf_remove("alice", "next", "b", "Dune.epub")
    items, headers = sync(client, headers["x-kobo-synctoken"])
    assert items == [{"ChangedEntitlement": {"BookEntitlement": items[0]["ChangedEntitlement"]["BookEntitlement"]}}]
    assert items[0]["ChangedEntitlement"]["BookEntitlement"]["IsRemoved"] is True
    assert sync(client, headers["x-kobo-synctoken"])[0] == []


def test_a_book_removed_on_the_kobo_is_not_sent_again_until_it_is_put_on_the_list_again(client):
    shelve(client, "Dune.epub", now=1000)
    _, headers = sync(client)
    book = book_id("b", "Dune.epub")
    assert client.delete(BASE + "/v1/library/" + book).status_code == 204
    token = headers["x-kobo-synctoken"]
    assert sync(client, token)[0] == []
    client.app.state.state.shelf_remove("alice", "next", "b", "Dune.epub")
    shelve(client, "Dune.epub")                                                    # put on the list again, now
    items, _ = sync(client, token)
    assert [i["NewEntitlement"]["BookMetadata"]["Title"] for i in items] == ["Dune"]


def test_no_token_means_a_fresh_device_and_everything_is_sent_again(client):
    shelve(client, "Dune.epub")
    sync(client)
    assert len(sync(client)[0]) == 1
    assert len(sync(client, "bm90IG91cnM=")[0]) == 1                              # a token that is not ours: the same


def test_a_long_list_comes_in_pages(client, lib):
    for n in range(105):
        make_epub(lib / "b" / "many" / ("%03d.epub" % n), title="Book %d" % n)
        shelve(client, "many/%03d.epub" % n)
    first, headers = sync(client)
    assert len(first) == 100 and headers["x-kobo-sync"] == "continue"
    rest, headers = sync(client, headers["x-kobo-synctoken"])
    assert len(rest) == 5 and "x-kobo-sync" not in headers


def test_metadata_download_and_cover(client):
    shelve(client, "Dune.epub")
    sync(client)
    book = book_id("b", "Dune.epub")
    meta = client.get(BASE + "/v1/library/%s/metadata" % book).json()
    assert meta[0]["Title"] == "Dune"
    dl = client.get(meta[0]["DownloadUrls"][0]["Url"].replace("http://testserver", ""))
    assert dl.status_code == 200 and dl.content[:2] == b"PK" and dl.headers["content-type"] == "application/epub+zip"
    cover = client.get(BASE + "/%s/355/530/false/image.jpg" % book)
    assert cover.status_code == 200 and cover.headers["content-type"].startswith("image/")
    assert client.get(BASE + "/%s/355/530/80/false/image.jpg" % book).status_code == 200      # the quality form
    unknown = book_id("b", "nothing.epub")
    assert client.get(BASE + "/v1/library/%s/metadata" % unknown).status_code == 404
    assert client.get(BASE + "/download/%s/x.epub" % unknown).status_code == 404


def test_another_users_key_cannot_open_alices_books(client):
    shelve(client, "Dune.epub")
    sync(client)
    client.app.state.state.set_appkey("bob", "bobs-kobo-key-0000")
    book = book_id("b", "Dune.epub")
    assert client.get("/kobo/bobs-kobo-key-0000/download/%s/Dune.epub" % book).status_code == 404


def test_a_book_this_user_may_not_open_is_neither_sent_nor_served(lib):
    (lib / "b" / "Series" / ".private").write_text("bob\n")                     # only bob (and administrators) may open it
    client = make_client(lib)
    shelve(client, "Series/01 One.epub")
    shelve(client, "Series", is_dir=True)
    assert sync(client)[0] == []
    book = book_id("b", "Series/01 One.epub")
    client.app.state.state.kobo_remember("alice", [(book, "b", "Series/01 One.epub")])     # an id it was given before the marker came
    assert client.get(BASE + "/download/%s/x.epub" % book).status_code == 404
    assert client.get(BASE + "/v1/library/%s/metadata" % book).status_code == 404


def test_reading_state_is_kept_and_sent_with_the_next_sync(client):
    shelve(client, "Dune.epub")
    _, headers = sync(client)
    book = book_id("b", "Dune.epub")
    body = {"ReadingStates": [{"EntitlementId": book,
                               "CurrentBookmark": {"ProgressPercent": 42, "ContentSourceProgressPercent": 12.5,
                                                   "Location": {"Value": "kobo.12.3", "Type": "KoboSpan", "Source": "OEBPS/ch3.xhtml"}},
                               "Statistics": {"SpentReadingMinutes": 30, "RemainingTimeMinutes": 90},
                               "StatusInfo": {"Status": "Reading"}}]}
    r = client.put(BASE + "/v1/library/%s/state" % book, json=body)
    assert r.json()["RequestResult"] == "Success"
    assert r.json()["UpdateResults"][0]["CurrentBookmarkResult"] == {"Result": "Success"}
    got = client.get(BASE + "/v1/library/%s/state" % book).json()[0]
    assert got["CurrentBookmark"]["ProgressPercent"] == 42
    assert got["CurrentBookmark"]["Location"]["Value"] == "kobo.12.3"
    assert got["StatusInfo"]["Status"] == "Reading" and got["StatusInfo"]["TimesStartedReading"] == 1
    assert got["Statistics"]["SpentReadingMinutes"] == 30
    items, _ = sync(client, headers["x-kobo-synctoken"])
    assert items[0]["ChangedReadingState"]["ReadingState"]["CurrentBookmark"]["ProgressPercent"] == 42
    # a fresh device gets the position with the book
    items, _ = sync(client)
    assert items[0]["NewEntitlement"]["ReadingState"]["CurrentBookmark"]["ProgressPercent"] == 42


@pytest.mark.parametrize("bad", [{}, {"ReadingStates": []}, {"ReadingStates": [{"StatusInfo": {"Status": "Eaten"}}]},
                                 {"ReadingStates": [{"CurrentBookmark": {"ProgressPercent": "x"}}]},
                                 {"ReadingStates": [{"CurrentBookmark": {"Location": {"Value": "v" * 5000}}}]}])
def test_bad_reading_states_are_refused(client, bad):
    shelve(client, "Dune.epub")
    sync(client)
    assert client.put(BASE + "/v1/library/%s/state" % book_id("b", "Dune.epub"), json=bad).status_code == 400


def test_reading_state_of_an_unknown_book_is_404(client):
    assert client.get(BASE + "/v1/library/%s/state" % book_id("b", "Dune.epub")).status_code == 404
    assert client.get(BASE + "/v1/library/not-a-uuid/state").status_code == 404


def test_store_pages_are_sent_to_the_store(client):
    r = client.get(BASE + "/v1/products/featured/abc", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "https://storeapi.kobo.com/v1/products/featured/abc"
    assert client.post(BASE + "/v1/analytics/event", json={}).json() == {}
    assert client.get(BASE + "/v1/user/loyalty/benefits").json() == {"Benefits": {}}


def test_addresses_honour_the_base_path_and_the_proxy(lib):
    c = make_client(lib, base_path="/pluteus", trust_proxy=True)
    res = c.get("/pluteus" + BASE + "/v1/initialization",
                headers={"x-forwarded-proto": "https", "x-forwarded-host": "books.example"}).json()["Resources"]
    assert res["library_sync"] == "https://books.example/pluteus" + BASE + "/v1/library/sync"


def test_the_key_is_masked_in_logs():
    assert keypath.masked(BASE + "/v1/library/sync") == "/kobo/***/v1/library/sync"
    assert keypath.masked("/k/abcdefgh/opds") == "/k/***/opds"


def test_a_removed_user_takes_their_kobo_rows_along(client):
    shelve(client, "Dune.epub")
    sync(client)
    st = client.app.state.state
    assert st.kobo_sent("alice")
    st.purge_user("alice")
    assert st.kobo_sent("alice") == {} and st.kobo_where("alice", book_id("b", "Dune.epub")) is None


def test_token_round_trip():
    assert read_token(base64.b64encode(json.dumps({"pluteus": 1, "states": 5}).encode()).decode()) == {"pluteus": 1, "states": 5}
    assert read_token("") is None and read_token("!!!") is None
