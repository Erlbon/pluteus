"""The optional update and security check: parsing, ranges, the states, and the admin page. GitHub is always faked."""
import threading
import time

import pytest
from test_adminweb import BOSS, admin, make_config  # noqa: F401  (the admin fixture)

from pluteus import adminweb
from pluteus.config import ConfigError, load_config
from pluteus.updatecheck import UpdateChecker, in_range, parse_version


def test_versions_parse_and_pre_releases_are_ignored():
    assert parse_version("v0.3.1") == (0, 3, 1) and parse_version("1.0") == (1, 0)
    for bad in ("0.4.0-rc1", "latest", "", None, "v1.2.3.4.5", "1..2"):
        assert parse_version(bad) is None


@pytest.mark.parametrize("version,spec,expected", [
    ((0, 3, 0), "< 0.3.1", True), ((0, 3, 1), "< 0.3.1", False), ((0, 3, 1), "<= 0.3.1", True),
    ((0, 2, 5), ">= 0.2.0, < 0.3.0", True), ((0, 1, 9), ">= 0.2.0, < 0.3.0", False), ((0, 3, 0), ">= 0.2.0, < 0.3.0", False),
    ((0, 3, 1), "= 0.3.1", True), ((0, 3, 1), "0.3.1", True), ((0, 3), "< 0.3.1", True),
    ((0, 3, 1), "", False), ((0, 3, 1), "banana", False), ((0, 3, 1), "< 0.3.1, banana", False)])
def test_advisory_ranges(version, spec, expected):
    assert in_range(version, spec) is expected


def faker(tags, advisories=None, advisories_fail=False, tags_fail=False):
    calls = []

    def fetch(url):
        calls.append(url)
        if "/tags" in url:
            if tags_fail:
                raise OSError("offline")
            return [{"name": t} for t in tags]
        if advisories_fail:
            raise OSError("403")
        return advisories or []
    fetch.calls = calls
    return fetch


def settle(checker, tries=100):
    for _ in range(tries):
        s = checker.status()
        if s["state"] != "checking":
            return s
        time.sleep(0.02)
    raise AssertionError("the check never finished")


def adv(spec="< 0.3.2", **kw):
    base = {"ghsa_id": "GHSA-xxxx-yyyy-zzzz", "summary": "Path traversal in the viewer", "severity": "high", "state": "published",
            "html_url": "https://github.com/Erlbon/pluteus/security/advisories/GHSA-xxxx-yyyy-zzzz",
            "vulnerabilities": [{"vulnerable_version_range": spec, "patched_versions": "0.3.2"}]}
    base.update(kw)
    return base


def test_states_up_to_date_update_security_and_unknown():
    assert settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.1", "v0.3.0"])))["state"] == "ok"
    s = settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.2", "v0.3.1", "v0.4.0-rc1", "junk"])))
    assert s["state"] == "update" and s["latest"] == "0.3.2"                       # the pre-release is not "newer"
    s = settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.1"], [adv()])))
    assert s["state"] == "security" and s["advisories"][0]["id"] == "GHSA-xxxx-yyyy-zzzz" and s["advisories"][0]["fixed"] == "0.3.2"
    assert settle(UpdateChecker("o/r", "0.3.2", faker(["v0.3.2"], [adv()])))["state"] == "ok"       # already past the fix
    assert settle(UpdateChecker("o/r", "0.3.1", faker([], tags_fail=True)))["state"] == "unknown"
    assert settle(UpdateChecker("o/r", "0.3.1", faker([])))["state"] == "unknown"


def test_unpublished_or_unreadable_advisories_never_raise_an_alarm():
    assert settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.1"], [adv(state="draft")])))["state"] == "ok"
    assert settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.1"], [adv("banana")])))["state"] == "ok"
    assert settle(UpdateChecker("o/r", "0.3.1", faker(["v0.3.1", "v0.3.2"], advisories_fail=True)))["state"] == "update"


def test_it_checks_once_a_day_retries_after_a_failure_and_never_blocks():
    now = [1000.0]
    fetch = faker(["v0.3.1"])
    checker = UpdateChecker("o/r", "0.3.1", fetch, interval=86400, retry=3600, clock=lambda: now[0])
    gate = threading.Event()
    slow = checker._fetch
    checker._fetch = lambda url: (gate.wait(2), slow(url))[1]
    t0 = time.monotonic()
    assert checker.status()["state"] == "checking" and time.monotonic() - t0 < 0.5          # returns at once
    gate.set()
    assert settle(checker)["state"] == "ok"
    first = len(fetch.calls)
    now[0] += 3600
    checker.status()
    time.sleep(0.1)
    assert len(fetch.calls) == first                                                       # not due yet
    now[0] += 86400
    settle(checker)
    checker.status()
    time.sleep(0.2)
    assert len(fetch.calls) > first                                                        # a day later: asked again
    failing = UpdateChecker("o/r", "0.3.1", faker([], tags_fail=True), clock=lambda: now[0])
    settle(failing)
    n = len(failing._fetch.calls)
    now[0] += 4000
    failing.status()
    time.sleep(0.2)
    assert len(failing._fetch.calls) > n                                                   # a failure is retried after an hour


def test_the_repository_name_is_checked():
    for bad in ("", "nowhere", "a/b/c", "a b/c", "../x", "a/b?x=1"):
        with pytest.raises(ValueError):
            UpdateChecker(bad)


def test_config_option_is_off_by_default_and_validated(tmp_path):
    path = make_config(tmp_path)
    cfg = load_config(str(path))
    assert cfg.update_check is False and cfg.update_repo == "Erlbon/pluteus"
    path.write_text(path.read_text().replace("admin_port = 8101", "admin_port = 8101\nupdate_check = true\nupdate_repo = \"me/fork\""))
    cfg = load_config(str(path))
    assert cfg.update_check and cfg.update_repo == "me/fork"
    path.write_text(path.read_text().replace('"me/fork"', '"not a repo"'))
    with pytest.raises(ConfigError):
        load_config(str(path))


def render(update):
    from pluteus.web import Ctx
    from pluteus.urls import Urls
    d = {"version": "0.3.1", "python": "3", "uptime": 5, "counts": {"users": 1, "sessions": 0, "devices": 0, "locked": 0},
         "names": {}, "roots": [], "uploads": None, "update": update,
         "state": {"persistent": True, "path": "p", "size": 1, "backups": 1, "newest_backup": time.time()}}
    return adminweb.status_page(Ctx(Urls(""), "", "default"), d)


def test_the_status_page_shows_each_state_and_a_red_alert_for_a_security_issue():
    assert "update check is off" in render(None)
    assert "up to date" in render({"state": "ok", "latest": "0.3.1", "advisories": []})
    page = render({"state": "update", "latest": "0.3.2", "advisories": []})
    assert "pl-dot-warn" in page and "0.3.2 is available" in page
    assert "pl-dot-off" in render({"state": "unknown", "latest": "", "advisories": []})
    sec = render({"state": "security", "latest": "0.3.1", "advisories": [
        {"id": "GHSA-1", "summary": "<script>alert(1)</script>", "severity": "high", "fixed": "0.3.2",
         "url": "https://github.com/Erlbon/pluteus/security/advisories/GHSA-1"}]})
    assert "role='alert'" in sec and "Security issue in this version" in sec and "pl-dot-bad" in sec
    assert "<script>alert(1)" not in sec and "&lt;script&gt;" in sec                  # text from outside is escaped
    assert "href='https://github.com/Erlbon/pluteus/security/advisories/GHSA-1'" in sec
    evil = render({"state": "security", "latest": "", "advisories": [{"id": "G", "summary": "x", "url": "javascript:alert(1)"}]})
    assert "javascript:" not in evil                                                  # only github.com links become links


def test_the_admin_page_asks_nothing_unless_switched_on(admin):
    client, app, _ = admin
    html = client.get("/", headers=BOSS).text
    assert "update check is off" in html
