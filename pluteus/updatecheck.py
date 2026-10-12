"""Optional update and security check for the admin page (config: update_check = true; off by default).

Once a day, in a background thread, it asks GitHub two questions about the project's repository (update_repo):
  * its tags: is there a newer release than the running version?
  * its published security advisories: does one of them name the running version as vulnerable?
Nothing about the library, the users or this server is sent (it is a plain HTTPS GET, so GitHub sees the server's address).
A failure of any kind only means "unknown"; it never slows or breaks a page. pluteus cannot update itself: the page only says
what the newest version is and, for an advisory, links to it.

Pre-release tags (0.4.0-rc1) are ignored, and so is anything that does not look like a version number.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request

from . import __version__

log = logging.getLogger("pluteus.update")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")      # no "." or ".." segments
MAX_BYTES = 1_000_000
INTERVAL = 24 * 3600
RETRY = 3600                      # after a failed check, try again in an hour
_VERSION = re.compile(r"v?(\d+(?:\.\d+){0,3})")


def parse_version(text) -> tuple | None:
    """'v0.3.1' -> (0, 3, 1). None for anything else, pre-releases included."""
    m = _VERSION.fullmatch(str(text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _pad(v: tuple, n: int = 4) -> tuple:
    return v + (0,) * (n - len(v))


def in_range(version: tuple, spec: str) -> bool:
    """Does `version` fall in a GitHub advisory range such as '< 0.3.2' or '>= 0.2.0, < 0.3.2'? An empty or unreadable
    range counts as NOT matching (never raise a false alarm from text we cannot read)."""
    parts = [p.strip() for p in str(spec or "").split(",") if p.strip()]
    if not parts:
        return False
    v = _pad(version)
    for part in parts:
        m = re.fullmatch(r"(<=|>=|<|>|=)?\s*v?(\d+(?:\.\d+){0,3})", part)
        if not m:
            return False
        bound = _pad(tuple(int(x) for x in m.group(2).split(".")))
        op = m.group(1) or "="
        ok = {"<": v < bound, "<=": v <= bound, ">": v > bound, ">=": v >= bound, "=": v == bound}[op]
        if not ok:
            return False
    return True


def fetch_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pluteus-update-check",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(MAX_BYTES + 1)[:MAX_BYTES])


class UpdateChecker:
    def __init__(self, repo: str, current: str = __version__, fetch=fetch_json, interval: float = INTERVAL, retry: float = RETRY,
                 clock=time.time):
        if not REPO_RE.fullmatch(repo or ""):
            raise ValueError("update_repo must look like owner/name")
        self.repo, self.current, self._fetch = repo, parse_version(current), fetch
        self.interval, self.retry, self._clock = interval, retry, clock
        self._lock = threading.Lock()
        self._running = False
        self._next = 0.0
        self._result = {"state": "checking", "latest": "", "advisories": [], "checked": 0.0}

    # ---- the check itself (runs in a thread) ---------------------------------------------------------------------------
    def _check(self) -> dict:
        base = "https://api.github.com/repos/%s" % self.repo
        tags = self._fetch(base + "/tags?per_page=100")
        versions = [v for v in (parse_version(t.get("name")) for t in tags if isinstance(t, dict)) if v]
        latest = max(versions, key=_pad) if versions else None
        advisories = []
        try:
            items = self._fetch(base + "/security-advisories?state=published&per_page=100")
        except Exception as err:            # no advisories yet, or not allowed: not a failure of the version check
            log.info("security advisories not read: %s", err)
            items = []
        for adv in items if isinstance(items, list) else []:
            if not isinstance(adv, dict) or adv.get("state", "published") != "published":
                continue
            for vuln in adv.get("vulnerabilities") or []:
                if self.current and in_range(self.current, (vuln or {}).get("vulnerable_version_range")):
                    advisories.append({"id": str(adv.get("ghsa_id") or ""), "summary": str(adv.get("summary") or "")[:200],
                                       "severity": str(adv.get("severity") or ""), "url": str(adv.get("html_url") or ""),
                                       "fixed": str((vuln or {}).get("patched_versions") or "")[:80]})
                    break
        latest_text = ".".join(str(p) for p in latest) if latest else ""
        if advisories:
            state = "security"
        elif latest and self.current and _pad(latest) > _pad(self.current):
            state = "update"
        elif latest:
            state = "ok"
        else:
            state = "unknown"
        return {"state": state, "latest": latest_text, "advisories": advisories, "checked": self._clock()}

    def _run(self) -> None:
        try:
            result = self._check()
            wait = self.interval
        except Exception as err:            # network down, rate limit, repo private, bad JSON: all just 'unknown'
            log.info("update check failed: %s", err)
            result = {"state": "unknown", "latest": "", "advisories": [], "checked": self._clock()}
            wait = self.retry
        with self._lock:
            self._result, self._next, self._running = result, self._clock() + wait, False

    # ---- what the page asks ----------------------------------------------------------------------------------------------
    def status(self) -> dict:
        """The last known answer; starts a background check when one is due. Never waits for the network."""
        with self._lock:
            due = not self._running and self._clock() >= self._next
            if due:
                self._running = True
            result = dict(self._result)
        if due:
            threading.Thread(target=self._run, name="pluteus-update-check", daemon=True).start()
        return result
