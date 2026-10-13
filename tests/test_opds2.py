"""OPDS 2.0 (JSON) feeds, chosen by the Accept header next to the Atom feeds."""
import json

from conftest import basic

OPDS2 = {"Accept": "application/opds+json"}


def test_the_root_feed_lists_the_libraries(client, alice):
    r = client.get("/opds", headers={**alice, **OPDS2})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/opds+json") and r.headers["vary"] == "Accept"
    feed = r.json()
    assert [n["title"] for n in feed["navigation"]] == ["comics", "books"]
    assert feed["navigation"][0]["href"] == "/opds/comics" and feed["navigation"][0]["type"] == "application/opds+json"
    assert {l["rel"] for l in feed["links"]} >= {"self", "start"}


def test_the_atom_feed_stays_the_default(client, alice):
    r = client.get("/opds", headers=alice)
    assert r.headers["content-type"].startswith("application/atom+xml") and r.content.startswith(b"<?xml")


def test_a_folder_has_publications_with_tags_series_and_a_download(client, alice):
    feed = client.get("/opds/books/Good%20Author", headers={**alice, **OPDS2}).json()
    books = {p["metadata"]["title"]: p for p in feed["publications"]}
    real = books["The Real Title"]
    assert real["metadata"]["author"] == [{"name": "Good Author"}] and real["metadata"]["language"] == "no"
    assert real["metadata"]["belongsTo"]["series"] == [{"name": "Great Saga", "position": 3.0}]
    link = real["links"][0]
    assert link["rel"] == "http://opds-spec.org/acquisition" and link["href"].startswith("/dl/books/Good%20Author/")
    assert any(l["rel"] == "up" for l in feed["links"]) and feed["metadata"]["numberOfItems"] == 2


def test_subfolders_are_navigation_and_paging_links_exist(client, alice):
    feed = client.get("/opds/comics/3%20-%20Marvel%20(1961-2012)", headers={**alice, **OPDS2}).json()
    assert [n["title"] for n in feed["navigation"]] and "publications" not in feed
    big = client.get("/opds/comics/3%20-%20Marvel%20(1961-2012)/Spider-Man%20(1963-)", headers={**alice, **OPDS2}).json()
    assert len(big["publications"]) == 2 and any(l["rel"] == "next" for l in big["links"])      # page_size is 2 in the test config


def test_the_login_still_applies(client):
    r = client.get("/opds", headers=OPDS2)
    assert r.status_code == 401


def test_a_person_can_ask_in_the_address_bar(client, alice):
    r = client.get("/opds?format=opds2", headers=alice)
    assert json.loads(r.content)["navigation"]
