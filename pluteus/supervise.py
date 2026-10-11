"""Keeps `pluteus serve` running: starts it, writes its output to a log file and starts it again after any exit.

Meant for the Windows scheduled task made by tools/install-task.ps1 (python -m pluteus supervise ...), where nothing
else restarts a dead server. On Windows the supervisor puts itself and everything it starts into a job object that is
killed when the supervisor goes away, so stopping the task (or ending the supervisor) can never leave an orphaned
server holding the port. Standard library only.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime

CREATE_NO_WINDOW = 0x08000000


def _kill_on_close_job():
    """Windows: a job object that kills every process in it when its last handle closes (that is, when this process
    dies, however it dies). Returns the handle (keep it alive for the whole run) or None elsewhere / on failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                        "OtherBytes")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000                                  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            return None
        return job
    except Exception:                                  # no job object: the supervisor still works, just without the guarantee
        return None


def _stamp(message: str) -> bytes:
    return ("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)).encode("utf-8")


def run(config: str, log: str, delay: float = 10.0, max_runs: int | None = None, command: list | None = None) -> int:
    """Run the server in a loop. `command` and `max_runs` exist for tests; normally this never returns."""
    job = _kill_on_close_job()                     # noqa: F841 - must stay referenced until we exit
    command = command or [sys.executable, "-m", "pluteus", "serve", "--config", config]
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    runs = 0
    while max_runs is None or runs < max_runs:
        runs += 1
        with open(log, "ab") as out:
            out.write(_stamp("starting pluteus"))
            out.flush()
            try:
                child = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=flags)
            except OSError as err:
                out.write(_stamp("could not start: %s" % err))
                code = -1
            else:
                try:
                    code = child.wait()
                except KeyboardInterrupt:
                    child.terminate()
                    child.wait()
                    out.write(_stamp("stopped"))
                    return 0
            if max_runs is not None and runs >= max_runs:
                out.write(_stamp("pluteus exited with code %s" % code))
                break
            out.write(_stamp("pluteus exited with code %s, starting again in %d seconds" % (code, delay)))
        time.sleep(delay)
    return 0
