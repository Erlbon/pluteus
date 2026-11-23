"""The first run in the browser (Windows): the set-up page that makes the first administrator and writes config.toml."""
from starlette.testclient import TestClient

from pluteus import setupweb
from pluteus.config import load_config


def client(tmp_path):
    setup = setupweb.Setup(str(tmp_path / "home" / "config.toml"), port=8080, admin_port=8101)
    return TestClient(setupweb.make_app(setup)), setup


def form(**extra):
    data = {"name": "boss", "password": "long-enough-1", "again": "long-enough-1", "folder": ""}
    data.update(extra)
    return data


def test_only_the_address_with_the_token_answers(tmp_path):
    c, setup = client(tmp_path)
    assert c.get("/" + setup.token).status_code == 200
    assert c.get("/wrong-token").status_code == 404
    assert c.post("/wrong-token", data=form()).status_code == 404
    assert not (tmp_path / "home" / "config.toml").exists()


def test_the_page_asks_name_password_twice_and_a_folder(tmp_path):
    c, setup = client(tmp_path)
    page = c.get("/" + setup.token)
    for field in ("name='name'", "name='password'", "name='again'", "name='folder'"):
        assert field in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["x-frame-options"] == "DENY"


def test_mistakes_are_explained_and_nothing_is_written(tmp_path):
    c, setup = client(tmp_path)
    url = "/" + setup.token
    assert "too short" in c.post(url, data=form(password="short", again="short")).text
    assert "differ" in c.post(url, data=form(again="something-else")).text
    bad_name = c.post(url, data=form(name="bo:ss"))
    assert bad_name.status_code == 400 and "letters" in bad_name.text
    missing = c.post(url, data=form(folder=str(tmp_path / "missing")))
    assert "is not a folder" in missing.text and str(tmp_path / "missing") in missing.text
    assert "value='boss'" in missing.text               # what was typed stays, the password does not
    assert "long-enough-1" not in missing.text
    assert not (tmp_path / "home" / "config.toml").exists() and not setup.done.is_set()


def test_a_post_from_another_site_is_refused(tmp_path):
    c, setup = client(tmp_path)
    assert c.post("/" + setup.token, data=form(), headers={"origin": "http://evil.example"}).status_code == 403
    assert not setup.done.is_set()


def test_a_good_answer_writes_a_configuration_that_loads_and_says_done(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    c, setup = client(tmp_path)
    done = c.post("/" + setup.token, data=form(folder='"%s"' % books), headers={"origin": "http://testserver"})
    assert done.status_code == 200 and "Done" in done.text and "http://127.0.0.1:8101/libraries" in done.text
    assert setup.done.is_set()
    cfg = load_config(setup.config_path, check_roots=False)
    assert cfg.port == 8080 and cfg.admin_port == 8101 and cfg.users["boss"].admin
    assert cfg.roots["library"].path == str(books)
    assert "Done" in c.get("/" + setup.token).text      # once done, it stays done: no second account
    assert "Done" in c.post("/" + setup.token, data=form(name="other")).text
    assert "other" not in load_config(setup.config_path, check_roots=False).users


def test_run_serves_the_page_until_it_is_filled_in_or_cancelled(tmp_path):
    import threading
    import urllib.request
    seen = []

    def browser(url):
        seen.append(url)
        assert b"Administrator name" in urllib.request.urlopen(url, timeout=5).read()

    cancel = threading.Event()
    threading.Timer(1.5, cancel.set).start()
    assert setupweb.run(str(tmp_path / "config.toml"), cancel=cancel, open_browser=browser) is False
    assert seen and seen[0].startswith("http://127.0.0.1:")
    assert not (tmp_path / "config.toml").exists()
