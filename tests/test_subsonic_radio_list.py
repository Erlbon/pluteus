"""Subsonic internet radio: the station files of a top-level Radio folder, in it and one folder down."""
from pluteus import subsonic
from test_subsonic import body, call, lib, make  # noqa: F401


def stations(c):
    return body(call(c, "getInternetRadioStations"))["internetRadioStations"].get("internetRadioStation", [])


def test_station_files_become_the_radio_list(lib):
    radio = lib / "Radio"
    (radio / "Norway").mkdir(parents=True)
    (radio / "NRK P1.m3u").write_text("https://example.org/p1\n", encoding="utf-8")
    (radio / "Norway" / "NRK P3.m3u").write_text("#EXTM3U\n#EXTINF:-1,NRK P3 live\nhttps://example.org/p3\n", encoding="utf-8")
    (radio / "Norway" / "Mix.pls").write_text("[playlist]\nFile1=https://example.org/a\nTitle1=Alpha\nFile2=https://example.org/b\n", encoding="utf-8")
    (radio / "Norway" / "local.m3u").write_text("song.mp3\n", encoding="utf-8")                 # no stream: not a station
    (radio / "Norway" / "notes.txt").write_text("https://example.org/not-a-station-file\n", encoding="utf-8")
    c = make(lib)
    found = {s["name"]: s["streamUrl"] for s in stations(c)}
    assert found == {"NRK P1": "https://example.org/p1", "NRK P3 live": "https://example.org/p3",
                     "Alpha": "https://example.org/a", "Mix 2": "https://example.org/b"}
    ids = [s["id"] for s in stations(c)]
    assert len(set(ids)) == len(ids)


def test_no_radio_folder_is_an_empty_list_and_a_deeper_radio_folder_does_not_count(lib):
    c = make(lib)
    assert stations(c) == []
    (lib / "Kaizers" / "Radio").mkdir()
    (lib / "Kaizers" / "Radio" / "x.m3u").write_text("https://example.org/x\n", encoding="utf-8")
    assert stations(make(lib)) == []
    assert subsonic.RADIO_FOLDER == "radio"
