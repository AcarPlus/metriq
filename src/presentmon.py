"""Optional, experimental: FPS for the built-in and LibreHardwareMonitor sources via Intel's PresentMon console app.

Only used when "presentmon_path" in config.json points to PresentMon's console executable (download it from
github.com/GameTechDev/PresentMon). PresentMon reads ETW events: it needs admin rights or membership in the
"Performance Log Users" group. FPS is measured for the application that owns the foreground window.
(HWiNFO has its own PresentMon integration: with the HWiNFO source this module is not used.)
"""
import collections
import ctypes
import csv
import os
import subprocess
import threading
import time
from ctypes import wintypes

from sens_builtin import reading

NOWIN = 0x08000000
SENSOR = "PresentMon"
WINDOW_S = 10.0           # 1 % / 0.1 % lows over the last 10 seconds


def _foreground_pid():
    try:
        u = ctypes.windll.user32
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _job_kill_on_close(proc):
    """Put the child into a job object that kills it when the panel exits (even after a crash)."""
    try:
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = wintypes.HANDLE
        job = k.CreateJobObjectW(None, None)

        class _Basic(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                        ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class _Ext(ctypes.Structure):
            _fields_ = [("basic", _Basic), ("io", ctypes.c_uint64 * 6), ("p1", ctypes.c_size_t),
                        ("p2", ctypes.c_size_t), ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]

        info = _Ext()
        info.basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))
        return job
    except Exception:
        return None


class PresentMon:
    def __init__(self, exe):
        self.exe = exe
        self.err = ""
        self.frames = collections.defaultdict(collections.deque)     # pid -> deque[(arrival time, ms)]
        self.lock = threading.Lock()
        self.p = None
        self.job = None
        self.started = 0.0
        self._start()

    def _start(self):
        self.started = time.time()
        if not os.path.isfile(self.exe):
            self.err = "file not found"
            return
        try:
            self.p = subprocess.Popen([self.exe, "--output_stdout", "--stop_existing_session",
                                       "--session_name", "metriQ"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                      creationflags=NOWIN, text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as ex:
            self.err = str(ex)
            return
        self.job = _job_kill_on_close(self.p)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            rd = csv.reader(self.p.stdout)
            head = None
            col = pcol = None
            for row in rd:
                if head is None:
                    if "ProcessID" not in row:
                        continue
                    head = row
                    pcol = head.index("ProcessID")
                    for c in ("MsBetweenPresents", "FrameTime", "msBetweenPresents"):
                        if c in head:
                            col = head.index(c)
                            break
                    if col is None:
                        self.err = "unknown PresentMon output format"
                        return
                    continue
                try:
                    pid, ms = int(row[pcol]), float(row[col])
                except (ValueError, IndexError):
                    continue
                if ms <= 0:
                    continue
                now = time.time()
                with self.lock:
                    q = self.frames[pid]
                    q.append((now, ms))
                    while q and now - q[0][0] > WINDOW_S:
                        q.popleft()
        except Exception as ex:
            self.err = str(ex)
        finally:
            if self.p and self.p.poll() is not None and not self.err:
                try:
                    msg = (self.p.stderr.read() or "").strip().splitlines()
                    self.err = msg[0] if msg else f"PresentMon exited ({self.p.returncode})"
                except Exception:
                    self.err = "PresentMon exited"

    def running(self):
        return self.p is not None and self.p.poll() is None

    def read(self, add_reading):
        """Adds Framerate (avg / 1% low / 0.1% low) readings while PresentMon runs (0 without a game)."""
        if not self.running():
            if time.time() - self.started > 60:     # retry once a minute (e.g. after the user got the rights)
                self.close()
                self._start()
            return
        now = time.time()
        pid = _foreground_pid()
        with self.lock:
            q = list(self.frames.get(pid, ()))
            for k in [k for k, d in self.frames.items() if not d or now - d[-1][0] > WINDOW_S]:
                del self.frames[k]
        last = [ms for ts, ms in q if now - ts <= 1.0]
        avg = 1000.0 / (sum(last) / len(last)) if last else 0.0
        ms_sorted = sorted((ms for _, ms in q), reverse=True)

        def low(p):
            if not ms_sorted:
                return 0.0
            n = max(1, int(len(ms_sorted) * p))
            return 1000.0 / (sum(ms_sorted[:n]) / n)
        for label, v in (("Framerate (avg)", avg), ("Framerate (1% low)", low(0.01)),
                         ("Framerate (0.1% low)", low(0.001))):
            add_reading(reading(8, SENSOR, label, "FPS", v))

    def close(self):
        if self.p and self.p.poll() is None:
            try:
                self.p.terminate()
            except Exception:
                pass
        self.p = None
        if self.job:
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.job))
            except Exception:
                pass
            self.job = None
