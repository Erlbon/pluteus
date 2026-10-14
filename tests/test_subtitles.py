"""Subtitles next to a video: found by name, converted to WebVTT, served by number, offered in the movie player."""
import pytest
from starlette.testclient import TestClient

from conftest import basic
from pluteus import subtitles
from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

HTML = {"Accept": "text/html"}
SRT = "1\r\n00:00:01,500 --> 00:00:03,000\r\n{\\an8}Hello <i>there</i>\r\n\r\n2\r\n00:01:02,250 --> 00:01:04,000\r\nBye\r\n"


@pytest.fixture
def films(tmp_path):
    folder = tmp_path / "films"
    folder.mkdir()
    (folder / "Movie.mp4").write_bytes(b"\x00" * 64)
    (folder / "Movie.srt").write_text(SRT, encoding="utf-8")
    (folder / "Movie.no.forced.srt").write_bytes("1\n00:00:01,000 --> 00:00:02,000\nBlåbær\n".encode("cp1252"))
    (folder / "Movie.en.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nPlain\n", encoding="utf-8")
    (folder / "Movie 2.srt").write_text(SRT, encoding="utf-8")          # another film's file
    (folder / "Other.mp4").write_bytes(b"\x00" * 64)
    (folder / "Other.srt").write_text(SRT, encoding="utf-8")
    return folder


def make_client(folder):
    cfg = parse_config({"roots": [{"name": "films", "path": str(folder)}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg)), basic("alice", "pw")


def test_only_files_named_like_the_video_are_found_with_a_label(films):
    found = subtitles.find(str(films / "Movie.mp4"))
    assert [label for _, label in found] == ["English", "Norwegian (forced)", "Subtitles"]
    assert [p.rsplit("Movie", 1)[1] for p, _ in found] == [".en.vtt", ".no.forced.srt", ".srt"]
    assert subtitles.find(str(films / "missing" / "x.mp4")) == []


def test_srt_becomes_webvtt(films):
    text = subtitles.to_vtt(str(films / "Movie.srt"))
    assert text.startswith("WEBVTT\n\n")
    assert "00:00:01.500 --> 00:00:03.000" in text and "00:01:02.250 --> 00:01:04.000" in text
    assert "Hello <i>there</i>" in text and "an8" not in text and "\r" not in text


def test_old_western_files_and_vtt_files_are_read(films):
    assert "Blåbær" in subtitles.to_vtt(str(films / "Movie.no.forced.srt"))
    assert subtitles.to_vtt(str(films / "Movie.en.vtt")).startswith("WEBVTT")
    assert subtitles.to_vtt(str(films / "nope.srt")) == ""


def test_the_route_serves_a_numbered_subtitle(films):
    client, auth = make_client(films)
    r = client.get("/subs/films/Movie.mp4?i=2", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/vtt")
    assert r.headers["x-content-type-options"] == "nosniff" and "00:00:01.500 --> 00:00:03.000" in r.text
    assert client.get("/subs/films/Movie.mp4?i=3", headers=auth).status_code == 404
    assert client.get("/subs/films/Movie.mp4?i=-1", headers=auth).status_code == 404
    assert client.get("/subs/films/Movie.mp4?i=x", headers=auth).status_code == 404
    assert client.get("/subs/films/Movie.srt?i=0", headers=auth).status_code == 404          # not a video
    assert client.get("/subs/films/Movie.mp4?i=2").status_code == 401


def test_the_movie_page_lists_the_tracks_and_a_button(films):
    client, auth = make_client(films)
    page = client.get("/view/films/Movie.mp4", headers={**auth, **HTML}).text
    assert "<track kind='subtitles' src='/subs/films/Movie.mp4?i=1' label='Norwegian (forced)'>" in page
    assert "id='cc'" in page
    bare = client.get("/view/films/Other.mp4", headers={**auth, **HTML}).text
    assert "id='cc'" in bare                                                       # Other.srt exists
    (films / "Other.srt").unlink()
    assert "id='cc'" not in client.get("/view/films/Other.mp4", headers={**auth, **HTML}).text
