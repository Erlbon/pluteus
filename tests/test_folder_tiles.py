"""In the cover view a sub-folder's tile shows that folder's own picture (cover.jpg / folder.jpg ...), one level up."""
import pytest
from cbz_factory import png
from conftest import basic
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config

AUTH = basic("alice", "pw")
HTML = {"Accept": "text/html"}


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    (root / "Parent" / "WithCover").mkdir(parents=True)
    (root / "Parent" / "WithFolderJpg").mkdir()
    (root / "Parent" / "Plain").mkdir()
    (root / "Parent" / "WithCover" / "cover.png").write_bytes(png((200, 30, 30), size=(300, 400)))
    (root / "Parent" / "WithFolderJpg" / "folder.png").write_bytes(png((30, 200, 30), size=(300, 400)))
    (root / "Parent" / "Plain" / "note.txt").write_text("nothing here")
    return tmp_path


def make_client(tmp_path, thumbnails=True):
    cfg = parse_config({"server": {"data_dir": str(tmp_path / "data"), "thumbnails": thumbnails},
                        "roots": [{"name": "lib", "path": str(tmp_path / "lib")}],
                        "users": [{"name": "alice", "password_hash": hash_password("pw", n=2 ** 4), "roots": ["*"]}]})
    return TestClient(create_app(cfg))


def tile(page: str, name: str) -> str:
    """The <li> of the folder tile with this name."""
    start = page.index("class='pl-tile pl-tile-folder'><a href='/browse/lib/Parent/%s'" % name)
    return page[start:page.index("</li>", start)]


def test_a_folder_tile_shows_the_cover_found_inside_that_folder(lib):
    page = make_client(lib).get("/browse/lib/Parent?view=grid", headers={**AUTH, **HTML}).text
    assert "src='/cover/lib/Parent/WithCover'" in tile(page, "WithCover")
    assert "src='/cover/lib/Parent/WithFolderJpg'" in tile(page, "WithFolderJpg")        # folder.* counts the same as cover.*
    assert "pl-tile-fallback" in tile(page, "WithCover")                                  # the icon stays underneath if the picture fails


def test_a_folder_without_a_picture_keeps_its_icon_and_costs_no_request(lib):
    page = make_client(lib).get("/browse/lib/Parent?view=grid", headers={**AUTH, **HTML}).text
    assert "<img" not in tile(page, "Plain")


def test_the_tile_picture_is_really_served(lib):
    c = make_client(lib)
    r = c.get("/cover/lib/Parent/WithCover", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    assert c.get("/cover/lib/Parent/WithCover").status_code == 401                        # the login still applies


def test_many_folders_are_probed_in_parallel_and_still_matched_one_to_one(tmp_path):
    for i in range(12):
        folder = tmp_path / "lib" / "Many" / ("f%02d" % i)
        folder.mkdir(parents=True)
        if i % 3 == 0:
            (folder / "front.png").write_bytes(png((10, 10, 200), size=(300, 400)))
    page = make_client(tmp_path).get("/browse/lib/Many?view=grid", headers={**AUTH, **HTML}).text
    for i in range(12):
        li = page[page.index("href='/browse/lib/Many/f%02d'" % i):]
        li = li[:li.index("</li>")]
        assert ("/cover/lib/Many/f%02d" % i in li) == (i % 3 == 0)


def test_with_thumbnails_off_nothing_changes(lib):
    page = make_client(lib, thumbnails=False).get("/browse/lib/Parent?view=grid", headers={**AUTH, **HTML}).text
    assert "/cover/lib/Parent/WithCover" not in page


def test_the_list_view_is_unchanged(lib):
    page = make_client(lib).get("/browse/lib/Parent?view=list", headers={**AUTH, **HTML}).text
    assert "pl-row pl-folder" in page and "/cover/lib/Parent/WithCover" not in page          # rows stay plain: no picture requests in the list


# ---- marked sub-folders: .private / .adult are honoured one level up, for the tile and for its picture --------------------------------------

BOB = basic("bob", "pw")


def marked_client(tmp_path):
    root = tmp_path / "lib"
    for name in ("Open", "Secret", "Adult"):
        (root / "Parent" / name).mkdir(parents=True)
        (root / "Parent" / name / "cover.png").write_bytes(png((50, 50, 200), size=(300, 400)))
    (root / "Parent" / "Secret" / ".private").write_text("alice\n")
    (root / "Parent" / "Adult" / ".adult").write_text("")
    pw = hash_password("pw", n=2 ** 4)
    cfg = parse_config({"server": {"data_dir": str(tmp_path / "data"), "thumbnails": True}, "roots": [{"name": "lib", "path": str(root)}],
                        "users": [{"name": "alice", "password_hash": pw, "roots": ["*"], "adult": True},
                                  {"name": "bob", "password_hash": pw, "roots": ["*"], "adult": False}]})
    return TestClient(create_app(cfg))


@pytest.mark.parametrize("who, sees_secret, sees_adult", [(AUTH, True, True), (BOB, False, False)])
def test_marked_subfolders_are_not_tiled_for_people_without_access(tmp_path, who, sees_secret, sees_adult):
    page = marked_client(tmp_path).get("/browse/lib/Parent?view=grid", headers={**who, **HTML}).text
    assert "/browse/lib/Parent/Open'" in page
    assert ("/browse/lib/Parent/Secret'" in page) is sees_secret
    assert ("/browse/lib/Parent/Adult'" in page) is sees_adult
    assert ("/cover/lib/Parent/Secret" in page) is sees_secret and ("/cover/lib/Parent/Adult" in page) is sees_adult


def test_the_picture_of_a_marked_folder_is_not_served_to_the_wrong_person(tmp_path):
    c = marked_client(tmp_path)
    assert c.get("/cover/lib/Parent/Secret", headers=AUTH).status_code == 200
    assert c.get("/cover/lib/Parent/Secret", headers=BOB).status_code == 404
    assert c.get("/cover/lib/Parent/Adult", headers=BOB).status_code == 404
    assert c.get("/cover/lib/Parent/Open", headers=BOB).status_code == 200
