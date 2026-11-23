"""The Windows tray (pluteus/tray.py) and the installer that installs it (installer/pluteus.iss). The icon itself is tried by hand;
what is tested here is everything around it: the Run value, the addresses, the tooltip, the supervisor's stop switch."""
import os
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from pluteus import supervise, tray

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "pluteus.iss").read_text(encoding="utf-8")


def test_the_run_value_of_the_installed_program_is_the_one_the_installer_writes(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\x\AppData\Local\Programs\Pluteus\pluteus-tray.exe")
    assert tray.run_command() == r'"C:\Users\x\AppData\Local\Programs\Pluteus\pluteus-tray.exe" --autostart'
    line = next(x for x in ISS.splitlines() if x.startswith("Root: HKCU") and "CurrentVersion\\Run" in x)
    assert 'ValueName: "%s"' % tray.RUN_VALUE in line
    assert 'ValueData: """{app}\\pluteus-tray.exe"" --autostart"' in line
    assert "uninsdeletevalue" in line
    assert "'%s'" % tray.RUN_VALUE in ISS.split("CurUninstallStepChanged")[1]      # a value the menu wrote goes too


def test_from_the_checkout_the_run_value_starts_the_tray_module():
    assert tray.run_command().endswith(" -m pluteus tray --autostart")


def test_the_installer_closes_the_tray_by_its_window_class_and_keeps_the_settings():
    assert ISS.count("'%s'" % tray.WINDOW_CLASS) >= 3 and "--quit" in ISS
    assert "PrivilegesRequired=lowest" in ISS and "{autopf}\\Pluteus" in ISS
    assert "APPDATA" not in ISS.split("[UninstallDelete]")[-1] if "[UninstallDelete]" in ISS else True
    assert 'Source: "installed-by-setup.txt"' in ISS and (ROOT / "installer" / "installed-by-setup.txt").exists()
    assert "OutputBaseFilename=Pluteus-Setup-{#AppVersion}" in ISS
    assert "netsh" not in ISS.lower() and "firewall" not in ISS.split("[Setup]")[1].lower()


def test_the_release_publishes_the_setup_it_built():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "PLUTEUS_BUILD: folder" in wf and "installer/pluteus.iss" in wf
    assert re.search(r"gh release create \"\$TAG\" \"\$setup\" release/pluteus\.exe", wf)


def cfg(**kw):
    base = dict(host="0.0.0.0", port=8080, admin_host="127.0.0.1", admin_port=8101)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_the_menu_opens_this_computer_s_addresses():
    assert tray.site_urls(cfg()) == ("http://127.0.0.1:8080/", "http://127.0.0.1:8101/")
    assert tray.site_urls(cfg(host="192.168.1.20", admin_port=0)) == ("http://192.168.1.20:8080/", "")
    assert tray.site_urls(cfg(host="::1"))[0] == "http://[::1]:8080/"


def test_the_tooltip_says_version_and_port(monkeypatch):
    monkeypatch.setattr(tray, "__version__", "9.9.9")
    assert tray.tooltip("running", 8080) == "Pluteus 9.9.9 \u00b7 running on port 8080"
    assert tray.tooltip("stopped, starting again in 30 seconds", 8080).endswith("starting again in 30 seconds")
    assert len(tray.tooltip("x" * 500, None)) <= 127


def test_the_log_sits_in_the_settings_folder(tmp_path):
    assert tray.log_path(str(tmp_path / "config.toml")) == str(tmp_path / "pluteus.log")


@pytest.mark.skipif(sys.platform != "win32", reason="the Run key is Windows'")
def test_start_at_logon_switches_on_and_off(monkeypatch):
    monkeypatch.setattr(tray, "RUN_VALUE", "PluteusTest-%d" % os.getpid())     # never the real value
    try:
        assert not tray.starts_at_logon()
        tray.set_start_at_logon(True)
        assert tray.starts_at_logon()
        tray.set_start_at_logon(False)
        assert not tray.starts_at_logon()
        tray.set_start_at_logon(False)                                       # already off: no error
    finally:
        tray.set_start_at_logon(False)


def test_the_supervisor_stops_the_server_when_told(tmp_path):
    stop = threading.Event()
    states = []
    log = tmp_path / "pluteus.log"
    t = threading.Thread(target=supervise.run, args=("x", str(log)),
                         kwargs={"command": [sys.executable, "-c", "import time; time.sleep(60)"], "stop": stop,
                                 "on_state": states.append})
    t.start()
    for _ in range(100):
        if states:
            break
        time.sleep(0.05)
    began = time.monotonic()
    stop.set()
    t.join(timeout=15)
    assert not t.is_alive() and time.monotonic() - began < 10
    assert states[0] == "running" and "stopped" in log.read_text(encoding="utf-8")


def test_the_supervisor_s_wait_between_starts_ends_at_once_on_stop(tmp_path):
    stop = threading.Event()
    states = []

    def on_state(text):
        states.append(text)
        if text.startswith("stopped"):
            stop.set()

    began = time.monotonic()
    supervise.run("x", str(tmp_path / "l.log"), delay=60, command=[sys.executable, "-c", "pass"], stop=stop, on_state=on_state)
    assert time.monotonic() - began < 30 and states[-1] == "stopped, starting again in 60 seconds"


def test_the_installed_tray_starts_its_console_sibling(tmp_path, monkeypatch):
    (tmp_path / "pluteus.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pluteus-tray.exe"))
    assert supervise.own_command() == [str(tmp_path / "pluteus.exe")]
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pluteus.exe"))
    assert supervise.own_command() == [str(tmp_path / "pluteus.exe")]


def test_the_tray_entry_hands_commands_to_the_command_line():
    text = (ROOT / "tools" / "pluteus_tray_entry.py").read_text(encoding="utf-8")
    assert "pluteus.__main__ import main" in text and "pluteus.tray import main" in text
