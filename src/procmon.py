"""Per-process sampling without admin rights (CPU, RAM, GPU, VRAM, disk), grouped by program.

    CPU %      Task-Manager style: process CPU time delta / wall time / number of logical processors
    RAM        private working set (MB), straight from NtQuerySystemInformation
    GPU %      PDH "\\GPU Engine(*)\\Utilization Percentage": summed per (pid, engine type), the busiest engine
               type counts (like Task Manager)
    VRAM       PDH "\\GPU Process Memory(*)\\Dedicated Usage" (MB)
    Disk       read + write transfer counters of the process (MB/s)

No third-party package: the process table comes from ntdll, the GPU numbers from PDH (see sens_builtin.Pdh).
Sampling happens in one background thread that only exists while somebody has called ``acquire()`` (the process
window, the CSV logger); ``snapshot()`` returns the newest result and is safe to call from any thread.
"""
import ctypes
import os
import re
import struct
import threading
import time
from ctypes import wintypes

METRICS = ("cpu", "ram", "gpu", "vram", "disk")
CATS = ("apps", "background", "windows")
MB = 1024.0 * 1024.0
MIN_PERIOD = 1.0                                   # never sample faster than once a second

_ntdll = ctypes.WinDLL("ntdll")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32")
try:
    _dwm = ctypes.WinDLL("dwmapi")
except OSError:
    _dwm = None

_ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
_ntdll.NtQuerySystemInformation.restype = ctypes.c_long
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
_u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.restype = ctypes.c_int
_u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.GetWindow.restype = wintypes.HWND
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# SYSTEM_PROCESS_INFORMATION (x64) field offsets
_S_HEAD = struct.Struct("<IIQ")            # 0: NextEntryOffset, NumberOfThreads, WorkingSetPrivateSize
_S_TIME = struct.Struct("<qqq")            # 32: CreateTime, UserTime, KernelTime (100 ns units)
_S_NAME = struct.Struct("<HHIQ")           # 56: ImageName (UNICODE_STRING)
_S_ID = struct.Struct("<QQII")             # 80: UniqueProcessId, ParentId, HandleCount, SessionId
_S_IO = struct.Struct("<qq")               # 232: ReadTransferCount, WriteTransferCount
_ENTRY_MIN = 256
_NCPU = max(1, os.cpu_count() or 1)
_SYSROOT = (os.environ.get("SystemRoot") or r"C:\Windows").lower().rstrip("\\") + "\\"
_SYSTEM_NAMES = {"system", "registry", "memory compression", "secure system", "system idle process"}
_GPU_ENG = re.compile(r"pid_(\d+)_.*?engtype_(.+)$", re.I)
_GPU_MEM = re.compile(r"pid_(\d+)_", re.I)
_SKIP_WIN_TITLES = ("Program Manager",)


class Proc:
    __slots__ = ("pid", "name", "exe", "cat", "cpu", "ram", "gpu", "vram", "disk")

    def __init__(self, pid, name, exe, cat):
        self.pid, self.name, self.exe, self.cat = pid, name, exe, cat
        self.cpu = self.ram = self.gpu = self.vram = self.disk = 0.0


class Group:
    """All processes of one executable (e.g. every chrome.exe)."""
    __slots__ = ("key", "name", "cat", "procs", "cpu", "ram", "gpu", "vram", "disk")

    def __init__(self, key, name):
        self.key, self.name, self.cat, self.procs = key, name, "background", []
        self.cpu = self.ram = self.gpu = self.vram = self.disk = 0.0

    def value(self, metric):
        return getattr(self, metric)


class Snapshot:
    def __init__(self, t, groups, gpu_ok):
        self.t, self.groups, self.gpu_ok = t, groups, gpu_ok
        self.by_key = {g.key: g for g in groups}

    def top(self, metric, n):
        """The n busiest groups for a metric (only those above zero)."""
        return sorted((g for g in self.groups if g.value(metric) > 0), key=lambda g: -g.value(metric))[:n]


# ------------------------------------------------------------------ low level readers
class _ProcTable:
    """NtQuerySystemInformation(SystemProcessInformation) into a reused buffer."""

    def __init__(self):
        self.buf = ctypes.create_string_buffer(1 << 20)

    def read(self):
        while True:
            ret = ctypes.c_ulong(0)
            st = _ntdll.NtQuerySystemInformation(5, self.buf, len(self.buf), ctypes.byref(ret))
            if st == 0:
                break
            if (st & 0xFFFFFFFF) == 0xC0000004:                 # STATUS_INFO_LENGTH_MISMATCH: grow and retry
                self.buf = ctypes.create_string_buffer(max(ret.value + (1 << 16), len(self.buf) * 2))
                continue
            raise OSError("NtQuerySystemInformation 0x%08X" % (st & 0xFFFFFFFF))
        raw = self.buf.raw
        base = ctypes.addressof(self.buf)
        out, off = [], 0
        while True:
            nxt, _thr, wsp = _S_HEAD.unpack_from(raw, off)
            create, user, kern = _S_TIME.unpack_from(raw, off + 32)
            nlen, _ml, _pad, nptr = _S_NAME.unpack_from(raw, off + 56)
            pid, _ppid, _hc, sess = _S_ID.unpack_from(raw, off + 80)
            rd, wr = _S_IO.unpack_from(raw, off + 232)
            name = ""
            if nlen and nptr:
                o = nptr - base
                if 0 <= o and o + nlen <= len(raw):
                    name = raw[o:o + nlen].decode("utf-16-le", "replace")
            out.append((pid, name, create, user + kern, wsp, rd + wr, sess))
            if not nxt:
                break
            off += nxt
        return out


def _image_path(pid):
    """Full exe path, or None when the process cannot be opened (protected / system processes)."""
    h = _k32.OpenProcess(0x1000, False, pid)                   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        n = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return None
    finally:
        _k32.CloseHandle(h)


def windowed_pids():
    """PIDs owning at least one visible, un-cloaked, titled top-level window (what Task Manager calls an app)."""
    pids = set()
    cls = ctypes.create_unicode_buffer(64)

    def cb(hwnd, lparam):
        try:
            if not _u32.IsWindowVisible(hwnd) or _u32.GetWindow(hwnd, 4):          # GW_OWNER: owned popups
                return True
            ex = _u32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF                       # GWL_EXSTYLE
            if ex & 0x80 and not ex & 0x40000:                                     # tool window w/o APPWINDOW
                return True
            if _u32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            if _dwm is not None:
                cloaked = wintypes.DWORD(0)
                if _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
                    return True                                                    # DWMWA_CLOAKED (hidden UWP)
            _u32.GetClassNameW(hwnd, cls, 64)
            if cls.value in ("Progman", "WorkerW"):
                return True
            pid = wintypes.DWORD(0)
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                pids.add(pid.value)
        except Exception:
            pass
        return True
    cb_ref = _WNDENUMPROC(cb)
    _u32.EnumWindows(ctypes.cast(cb_ref, ctypes.c_void_p), 0)
    return pids


# ------------------------------------------------------------------ the sampler
class ProcMon:
    def __init__(self):
        self._lock = threading.Lock()
        self._owners = {}                    # owner -> wanted period (s)
        self._snap = None
        self._thread = None
        self._wake = threading.Event()
        self.error = ""
        # state of the sampler thread (only touched there)
        self._table = None
        self._pdh = None
        self._prev = {}                      # pid -> (create, cpu 100ns, io bytes)
        self._prev_t = None
        self._static = {}                    # (pid, create) -> (exe, is_system)

    # ---- use
    def acquire(self, owner, period=1.5):
        period = max(MIN_PERIOD, float(period))
        with self._lock:
            changed = self._owners.get(owner) != period
            self._owners[owner] = period
            if self._thread is None or not self._thread.is_alive():
                self._wake.clear()
                self._thread = threading.Thread(target=self._run, name="procmon", daemon=True)
                self._thread.start()
        if changed:
            self._wake.set()

    def release(self, owner):
        with self._lock:
            self._owners.pop(owner, None)
        self._wake.set()

    def active(self):
        with self._lock:
            return bool(self._owners)

    def snapshot(self):
        return self._snap

    # ---- thread
    def _period(self):
        with self._lock:
            return min(self._owners.values()) if self._owners else None

    def _run(self):
        try:
            while True:
                per = self._period()
                if per is None:
                    time.sleep(0.2)              # grace: a quick release / acquire does not restart everything
                    if self._period() is None:
                        break
                    continue
                t0 = time.perf_counter()
                try:
                    self.sample()
                    self.error = ""
                except Exception as ex:          # keep going: a single failed round must not end sampling
                    self.error = str(ex)
                self._wake.wait(max(0.05, per - (time.perf_counter() - t0)))
                self._wake.clear()
        finally:
            self._reset()

    def _reset(self):
        if self._pdh is not None:
            try:
                self._pdh.close()
            except Exception:
                pass
        self._pdh = None
        self._prev, self._prev_t, self._static = {}, None, {}
        self._snap = None if not self.active() else self._snap

    def _gpu(self):
        """({pid: GPU %}, {pid: VRAM MB}, ok)"""
        if self._pdh is None:
            try:
                from sens_builtin import Pdh
                p = Pdh()
                ok = p.add("eng", r"\GPU Engine(*)\Utilization Percentage")
                ok2 = p.add("mem", r"\GPU Process Memory(*)\Dedicated Usage")
                self._pdh = p if (ok or ok2) else False
                if self._pdh is False:
                    p.close()
            except Exception:
                self._pdh = False
            if self._pdh:
                self._pdh.collect()
        if not self._pdh:
            return {}, {}, False
        self._pdh.collect()
        per_type = {}
        for inst, v in self._pdh.array("eng").items():
            m = _GPU_ENG.match(inst)
            if m:
                k = (int(m.group(1)), m.group(2).lower())
                per_type[k] = per_type.get(k, 0.0) + v
        gpu = {}
        for (pid, _typ), v in per_type.items():
            if v > gpu.get(pid, 0.0):
                gpu[pid] = v
        vram = {}
        for inst, v in self._pdh.array("mem").items():
            m = _GPU_MEM.match(inst)
            if m:
                pid = int(m.group(1))
                vram[pid] = vram.get(pid, 0.0) + v / MB
        return gpu, vram, True

    def sample(self):
        """One sampling round (also usable synchronously: call twice, one second apart, for rates)."""
        if self._table is None:
            self._table = _ProcTable()
        now = time.perf_counter()
        rows = self._table.read()
        gpu, vram, gpu_ok = self._gpu()
        wins = windowed_pids()
        dt = (now - self._prev_t) if self._prev_t else 0.0
        prev, cur = self._prev, {}
        groups = {}
        for pid, name, create, cpu100, wsp, io, sess in rows:
            if pid == 0:
                continue                                               # System Idle Process
            cur[pid] = (create, cpu100, io)
            st = self._static.get((pid, create))
            if st is None:
                exe = _image_path(pid) if pid != 4 else None
                low = (exe or "").lower()
                is_sys = (exe is None or sess == 0 or low.startswith(_SYSROOT) or pid == 4
                          or name.lower() in _SYSTEM_NAMES)
                st = self._static[(pid, create)] = (exe, is_sys)
            exe, is_sys = st
            nm = name or (os.path.basename(exe) if exe else "pid %d" % pid)
            cat = "apps" if pid in wins else ("windows" if is_sys else "background")
            p = Proc(pid, nm, exe, cat)
            p.ram = wsp / MB
            p.gpu = min(100.0, gpu.get(pid, 0.0))
            p.vram = vram.get(pid, 0.0)
            pv = prev.get(pid)
            if pv and pv[0] == create and dt > 0:
                p.cpu = max(0.0, min(100.0, (cpu100 - pv[1]) * 1e-7 / dt / _NCPU * 100.0))
                p.disk = max(0.0, (io - pv[2]) / dt / MB)
            key = nm.lower()
            g = groups.get(key)
            if g is None:
                g = groups[key] = Group(key, nm)
            g.procs.append(p)
        # drop the static cache of processes that are gone
        if len(self._static) > 2 * len(cur) + 64:
            live = {(pid, c[0]) for pid, c in cur.items()}
            self._static = {k: v for k, v in self._static.items() if k in live}
        for g in groups.values():
            for p in g.procs:
                g.cpu += p.cpu
                g.ram += p.ram
                g.gpu += p.gpu
                g.vram += p.vram
                g.disk += p.disk
            g.gpu = min(100.0, g.gpu)
            cats = {p.cat for p in g.procs}
            g.cat = "apps" if "apps" in cats else ("windows" if cats == {"windows"} else "background")
            g.procs.sort(key=lambda q: -q.cpu)
        self._prev, self._prev_t = cur, now
        self._snap = Snapshot(time.time(), list(groups.values()), gpu_ok)
        return self._snap


MON = ProcMon()                     # shared by the process window and the CSV logger


if __name__ == "__main__":
    m = ProcMon()
    m.sample()
    time.sleep(1.5)
    s = m.sample()
    print("groups:", len(s.groups), "procs:", sum(len(g.procs) for g in s.groups), "gpu counters:", s.gpu_ok)
    for met in ("cpu", "ram", "gpu", "vram", "disk"):
        print(met, [(g.name, round(g.value(met), 1), g.cat) for g in s.top(met, 5)])
