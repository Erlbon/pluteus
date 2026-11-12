"""Writes the two hashed lock files that the Docker image and the release builds install from.

    py -3.12 tools/make_lock.py --work C:/Temp/Claude/pluteus-lock        (any empty scratch folder; it may be deleted afterwards)

    requirements.lock          what pluteus needs to run (the dependencies in pyproject.toml): the Docker image
    requirements-build.lock    the same versions, plus the dev and build extras (pytest, PyInstaller): the release jobs

Both are installed with `pip install --require-hashes -r <file>`, so a build takes exactly these files from PyPI and
nothing else. Run this script again to move to newer versions (it takes the newest that pyproject.toml allows), run the
tests against the result, and commit both files together.

How it works (only pip is used to talk to PyPI):
  1. pip resolves the build set once (`pip install --dry-run --report`), which gives one version per package.
  2. The set is checked against each platform that is built: Windows x86-64 (the exe), Linux x86-64 (the image and the
     Linux program) and Linux arm64 (the image). Each package's own dependency list is walked with that platform's
     markers; a package needed there but missing from the set is added and the set is resolved again. A package that
     only some platforms need gets a marker in the lock (colorama: Windows only).
  3. Every wheel a platform could pick is downloaded (`pip download`, one platform tag at a time for the packages that
     are not pure Python) and its SHA-256 is computed from the file. Nothing is typed in by hand.

The locks are for ONE Python version, PYTHON below, which must be the one in the Dockerfile, in tools/linux-build and in
.github/workflows/release.yml; the script refuses to run under another. Alpine (musl) wheels are not included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ModuleNotFoundError:                 # a bare Python has no `packaging`; pip carries its own copy
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name

PYTHON = (3, 12)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLIBC = range(17, 42)                       # manylinux_2_17 (= manylinux2014) up to the newest glibc tag asked for
TARGETS = {
    # name: (marker values that differ from the machine running this, platform tags to ask pip for)
    "windows": ({"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"},
                ["win_amd64"]),
    "linux-x86_64": ({"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"},
                     ["manylinux2014_x86_64"] + ["manylinux_2_%d_x86_64" % n for n in GLIBC]),
    "linux-arm64": ({"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "aarch64"},
                    ["manylinux2014_aarch64"] + ["manylinux_2_%d_aarch64" % n for n in GLIBC]),
}
ONLY = {frozenset(["windows"]): 'sys_platform == "win32"',
        frozenset(["linux-x86_64", "linux-arm64"]): 'sys_platform == "linux"'}


def pip(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *args], capture_output=True, text=True)
    if check and done.returncode:
        sys.exit("pip %s failed:\n%s\n%s" % (" ".join(args), done.stdout[-2000:], done.stderr[-4000:]))
    return done


def resolve(requirements: list, work: str) -> dict:
    """{canonical name: {"name", "version", "requires": [Requirement]}} for the newest set that satisfies `requirements`."""
    report = os.path.join(work, "report.json")
    pip("install", "--dry-run", "--ignore-installed", "--only-binary=:all:", "--quiet", "--report", report, *requirements)
    with open(report, encoding="utf-8") as f:
        items = json.load(f)["install"]
    out = {}
    for item in items:
        meta = item["metadata"]
        out[canonicalize_name(meta["name"])] = {"name": meta["name"], "version": meta["version"],
                                                "requires": [Requirement(r) for r in meta.get("requires_dist") or []]}
    return out


def needed(tops: list, pins: dict, env: dict) -> tuple:
    """(names of the pinned packages that `tops` pull in under `env`, requirements that are needed but not pinned)."""
    seen, missing = {}, []
    queue = [Requirement(t) for t in tops]
    while queue:
        req = queue.pop()
        name = canonicalize_name(req.name)
        if name not in pins:
            missing.append("%s%s" % (req.name, req.specifier))               # without its marker: it is wanted, whatever this machine is
            continue
        extras = frozenset(req.extras)
        if extras <= seen.get(name, frozenset()) and name in seen:
            continue
        seen[name] = seen.get(name, frozenset()) | extras
        pinned = pins[name]
        if not req.specifier.contains(pinned["version"], prereleases=True):
            sys.exit("%s is pinned at %s, which %s does not allow" % (pinned["name"], pinned["version"], req))
        for dep in pinned["requires"]:
            if dep.marker is None or any(dep.marker.evaluate({**env, "extra": e}) for e in ("", *seen[name])):
                queue.append(dep)
    return set(seen), missing


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(spec: str, folder: str, platform: str = "") -> list:
    """The wheel files pip fetched for `spec` into `folder` ([] when there is none for that platform)."""
    os.makedirs(folder, exist_ok=True)
    args = ["download", "--no-deps", "--only-binary=:all:", "--quiet", "--dest", folder]
    if platform:
        args += ["--python-version", "%d.%d" % PYTHON, "--platform", platform]
    if pip(*args, spec, check=False).returncode:
        return []
    return sorted(os.listdir(folder))


def hashes_for(pin: dict, targets: set, work: str) -> list:
    """The SHA-256 of every wheel of this pinned package that one of `targets` could install."""
    spec = "%s==%s" % (pin["name"], pin["version"])
    base = os.path.join(work, "wheels", "%s-%s" % (canonicalize_name(pin["name"]), pin["version"]))
    files = {}
    first = download(spec, os.path.join(base, "host"))
    if len(first) == 1 and first[0].endswith("-none-any.whl"):                 # pure Python: the same file everywhere
        files[first[0]] = os.path.join(base, "host", first[0])
    else:
        for target in sorted(targets):
            found = 0
            for tag in TARGETS[target][1]:
                folder = os.path.join(base, tag)
                for name in download(spec, folder, tag):
                    files[name] = os.path.join(folder, name)
                    found += 1
            if not found:
                sys.exit("%s has no wheel for %s (Python %d.%d)" % (spec, target, *PYTHON))
    if not files:
        sys.exit("no wheel found for %s" % spec)
    for name in files:
        if not name.endswith(".whl"):
            sys.exit("%s is not a wheel; the locks hold wheels only" % name)
    print("  %-28s %d file%s" % (spec, len(files), "" if len(files) == 1 else "s"))
    return sorted({sha256(path) for path in files.values()})


def write_lock(path: str, title: str, tops: list, pins: dict, envs: dict, hashes: dict) -> int:
    where = {}                                                                  # canonical name -> the targets that need it
    for target, env in envs.items():
        for name in needed(tops, pins, env)[0]:
            where.setdefault(name, set()).add(target)
    lines = ["# %s" % title,
             "# Written by tools/make_lock.py for Python %d.%d: do not edit by hand, run the script again." % PYTHON,
             "# Install with:  python -m pip install --require-hashes -r %s" % os.path.basename(path),
             "# Wheels for: %s. One line per package: the version, then every wheel's SHA-256." % ", ".join(sorted(envs))]
    for name in sorted(where):
        targets = frozenset(where[name])
        if targets != frozenset(envs) and targets not in ONLY:
            sys.exit("%s is needed on %s only: teach ONLY in tools/make_lock.py the marker for that" % (name, sorted(targets)))
        marker = "" if targets == frozenset(envs) else " ; " + ONLY[targets]
        lines.append("%s==%s%s %s" % (pins[name]["name"], pins[name]["version"], marker,
                                      " ".join("--hash=sha256:" + h for h in hashes[name])))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(line + chr(10) for line in lines))
    return len(where)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", required=True, help="an empty scratch folder for the downloads (not inside the repository)")
    args = parser.parse_args()
    if sys.version_info[:2] != PYTHON:
        sys.exit("run this with Python %d.%d (the version that is shipped), not %d.%d" % (*PYTHON, *sys.version_info[:2]))
    work = os.path.abspath(args.work)
    if os.path.commonpath([work, HERE]) == HERE:
        sys.exit("--work must be outside the repository")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(HERE, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    runtime = list(project["dependencies"])
    build = runtime + list(project["optional-dependencies"]["dev"]) + list(project["optional-dependencies"]["build"])
    host = default_environment()
    envs = {name: {**host, **changes} for name, (changes, _) in TARGETS.items()}

    extra: list = []
    for _ in range(6):
        pins = resolve(build + extra, work)
        missing = [m for env in envs.values() for m in needed(build, pins, env)[1]]
        new = sorted(set(missing) - set(extra))
        if not new:
            break
        print("needed on another platform, added:", ", ".join(new))
        extra += new
    else:
        sys.exit("the set of packages did not settle")

    where = {}
    for target, env in envs.items():
        for name in needed(build, pins, env)[0]:
            where.setdefault(name, set()).add(target)
    print("hashing %d packages" % len(where))
    hashes = {name: hashes_for(pins[name], where[name], work) for name in sorted(where)}
    n = write_lock(os.path.join(HERE, "requirements.lock"), "What pluteus needs to run (the Docker image).", runtime, pins, envs, hashes)
    print("requirements.lock: %d packages" % n)
    n = write_lock(os.path.join(HERE, "requirements-build.lock"),
                   "What the release jobs install: the runtime set plus the dev and build extras (pytest, PyInstaller).",
                   build, pins, envs, hashes)
    print("requirements-build.lock: %d packages" % n)


if __name__ == "__main__":
    main()
