"""A small fake library on disk plus an app wired to it. Built in pytest's tmp_path."""
import base64
import os

import pytest
from epub_factory import make_epub
from starlette.testclient import TestClient

from pluteus.app import create_app
from pluteus.auth import hash_password
from pluteus.config import parse_config
from pluteus.fs import longpath

FAST = dict(n=2 ** 4)   # tiny scrypt cost, tests only


def _touch(path, data=b"x"):
    path = longpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def library(tmp_path):
    comics = tmp_path / "comics"
    books = tmp_path / "books"
    # lineage-numbered folders, to check natural order and display names
    for rel in ("0. National Allied (1934-1946)/Superman (1939-1986)/Superman 01.cbz",
                "3 - Marvel (1961-2012)/Spider-Man (1963-)/Amazing 001.cbz",
                "3 - Marvel (1961-2012)/Spider-Man (1963-)/Amazing 002.cbz",
                "3 - Marvel (1961-2012)/Spider-Man (1963-)/Amazing 010.cbz",
                "3 - Marvel (1961-2012)/Spider-Man (1963-)/Amazing 003.cbz",
                "3.10 - Marvel Epic (1982-1996)/Epic Illustrated/x.cbz",
                "3.9 - Marvel Early (1980-1981)/Early/y.cbz",
                "_incoming/hidden.cbz",
                "Adult Pub/Naughty (2000)/n.cbz",
                "Plain/notes.txt"):
        _touch(str(comics / rel.replace("/", os.sep)))
    _touch(str(comics / "Adult Pub" / ".adult"))
    # a folder name ending in a dot, as found on the real server
    _touch(str(books / "Lescroart, John T." / "Il Tredicesimo Giurato.epub"), b"EPUBDATA" * 100)
    make_epub(str(books / "Good Author" / "Real Book.epub"), title="The Real Title",
              authors=("Good Author",), language="no", date="2011-05-01",
              calibre_series="Great Saga", series_index="3")
    _touch(str(books / "Good Author" / "Plain Filename.epub"), b"not a real epub")
    _touch(str(tmp_path / "secret.txt"), b"outside the library")
    return tmp_path


@pytest.fixture
def cfg(library):
    data = {
        "server": {"page_size": 2},
        "exclude": ["_incoming"],
        "roots": [{"name": "comics", "path": str(library / "comics")},
                  {"name": "books", "path": str(library / "books")}],
        "users": [
            {"name": "alice", "password_hash": hash_password("pw-alice", **FAST), "roots": ["*"]},
            {"name": "bob", "password_hash": hash_password("pw-bob", **FAST), "adult": True, "roots": ["*"]},
            {"name": "carol", "password_hash": hash_password("pw-carol", **FAST), "roots": ["comics"]},
        ],
    }
    return parse_config(data)


@pytest.fixture
def client(cfg):
    return TestClient(create_app(cfg))


def basic(user, password):
    token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    return {"Authorization": "Basic " + token}


@pytest.fixture
def alice():
    return basic("alice", "pw-alice")


@pytest.fixture
def bob():
    return basic("bob", "pw-bob")


@pytest.fixture
def carol():
    return basic("carol", "pw-carol")
