"""The supervisor used by the scheduled task: restarts the server, logs every exit, leaves no orphan behind."""
import subprocess
import sys
import time

import pytest

from pluteus import supervise


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_it_starts_the_server_again_after_every_exit_and_logs_the_codes(tmp_path):
    log = tmp_path / "service.log"
    supervise.run("unused.toml", str(log), delay=0, max_runs=3, command=[sys.executable, "-c", "print('hi'); raise SystemExit(3)"])
    text = log.read_text(encoding="utf-8")
    assert text.count("starting pluteus") == 3 and text.count("hi") == 3
    assert text.count("exited with code 3") == 3 and "starting again in" in text


def test_a_program_that_cannot_be_started_is_logged_not_fatal(tmp_path):
    log = tmp_path / "service.log"
    supervise.run("unused.toml", str(log), delay=0, max_runs=2, command=[str(tmp_path / "no-such-program.exe")])
    assert log.read_text(encoding="utf-8").count("could not start") == 2


def test_the_default_command_is_pluteus_serve_with_the_config(tmp_path, monkeypatch):
    seen = []

    class Fake:
        def __init__(self, command, **kwargs):
            seen.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(supervise.subprocess, "Popen", Fake)
    supervise.run("C:/x/config.toml", str(tmp_path / "l.log"), delay=0, max_runs=1)
    assert seen[0][1:] == ["-m", "pluteus", "serve", "--config", "C:/x/config.toml"]


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are a Windows feature")
def test_killing_the_supervisor_kills_the_server_it_started(tmp_path):
    """What stopping the scheduled task does: the supervisor is terminated and must not leave a server behind."""
    pidfile = tmp_path / "child.pid"
    child = "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(120)" % str(pidfile)
    boot = ("import sys; from pluteus import supervise; "
            "supervise.run('x', %r, delay=1, command=[sys.executable, '-c', %r])" % (str(tmp_path / "s.log"), child))
    sup = subprocess.Popen([sys.executable, "-c", boot])
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.1)
        pid = int(pidfile.read_text())
        assert _alive(pid)
        sup.kill()                                         # like Stop-ScheduledTask: no chance to clean up
        sup.wait(timeout=10)
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        assert not _alive(pid), "the server survived its supervisor"
    finally:
        sup.kill()
