"""A folder full of station files is one flat grid of logo tiles (search, country chips, Random), and the home page has a Live card."""
from test_login_web import HTML
from test_stations import post
from test_uploads import up  # noqa: F401

from pluteus import streams


def _save(me):
    post(me, {"kind": "radio", "sub": "Norway", "stations": [{"name": "NRK P1", "url": "https://example.org/p1"},
                                                              {"name": "NRK P3", "url": "https://example.org/p3"}]})
    post(me, {"kind": "radio", "sub": "Sweden", "stations": [{"name": "Sveriges P2", "url": "https://example.org/sr2"}]})


def test_a_radio_folder_with_country_folders_is_shown_flat_with_filters(up):
    me, app, folder = up
    _save(me)
    html = me.get("/browse/uploads/Radio", headers=HTML).text
    assert "id='streams'" in html and html.count("class='pl-tile pl-stream'") == 3            # every country in one grid
    assert "data-country='Norway'" in html and "data-country='Sweden'" in html
    assert "data-country=''>All<" in html and "id='stream-search'" in html and "id='stream-random'" in html
    assert "View:" not in html                                                                  # no List / Covers switch here
    inside = me.get("/browse/uploads/Radio/Norway", headers=HTML).text
    assert inside.count("class='pl-tile pl-stream'") == 2 and "pl-stream-chips" not in inside   # no sub-folders: no chips
    plain = me.get("/browse/uploads/Radio?plain=1", headers=HTML).text
    assert "id='streams'" not in plain and "Norway" in plain                                    # the ordinary listing is one click away


def test_home_has_a_live_card_for_radio_and_iptv(up):
    me, app, folder = up
    assert "pl-live-box" not in me.get("/", headers=HTML).text                                  # nothing saved yet
    _save(me)
    home = me.get("/", headers=HTML).text
    assert "pl-live-box" in home and "pl-i-radio" in home and "Radio" in home


def test_only_folders_made_mostly_of_station_files_count(tmp_path):
    radio_dir = tmp_path / "Radio"
    (radio_dir / "Norway").mkdir(parents=True)
    (radio_dir / "Norway" / "NRK.m3u").write_text("#EXTM3U\nhttps://example.org/a\n", encoding="utf-8")
    assert streams.is_stream_folder(str(radio_dir)) and streams.is_stream_folder(str(radio_dir / "Norway"))
    books = tmp_path / "Books"
    books.mkdir()
    for n in range(5):
        (books / ("b%d.epub" % n)).write_bytes(b"x")
    (books / "one.m3u").write_text("#EXTM3U\nhttps://example.org/a\n", encoding="utf-8")
    assert not streams.is_stream_folder(str(books))                                              # one station among books: ordinary folder
    assert not streams.is_stream_folder(str(tmp_path / "missing"))
    stations, countries, cut = streams.collect(str(radio_dir))
    assert [s["name"] for s in stations] == ["NRK"] and countries == ["Norway"] and not cut and stations[0]["kind"] == "radio"
