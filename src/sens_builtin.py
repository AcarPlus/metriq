"""Built-in sensors: no driver, no admin rights, no extra program, no required Python package.

    CPU usage / clocks, network and disk rates   Windows performance counters (PDH, English counter names, so
                                                 any Windows language works); psutil only if PDH is broken
    RAM                                          GlobalMemoryStatusEx
    NVIDIA GPU temperature, clock, load, power,  NVML (nvml.dll comes with the NVIDIA driver)
    fan, VRAM
    other GPUs: load, VRAM                       PDH "GPU Engine" / "GPU Adapter Memory" + DXGI adapter names

Readings use the names HWiNFO uses ("CPU [#0]: ...", "Total CPU Usage", "GPU Temperature" ...), so a layout made
for HWiNFO also finds these rows. Not available without a signed kernel driver: CPU / motherboard temperatures,
fans, voltages, CPU power -> use HWiNFO or LibreHardwareMonitor.
"""
import ctypes
import os
import re
import struct
import time
from ctypes import wintypes

from i18n import t

FORCE_NO_NVML = bool(os.environ.get("METRIQ_NO_NVML"))      # testing: exercise the non-NVIDIA GPU path
FORCE_NO_PDH = bool(os.environ.get("METRIQ_NO_PDH"))        # testing: exercise the psutil fallback
MB = 1048576.0


def reading(rtype, sensor, label, unit, value):
    return dict(type=rtype, sensor=sensor, sensor_user=sensor, id=0, label=label, label_user=label, unit=unit,
                value=float(value), min=float(value), max=float(value), avg=float(value))


# ================================================================== PDH (performance counters)
class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


PDH_MORE_DATA = 0x800007D2
PDH_FMT = 0x00000200 | 0x00008000           # PDH_FMT_DOUBLE | PDH_FMT_NOCAP100


class Pdh:
    def __init__(self):
        p = self.dll = ctypes.WinDLL("pdh")
        for fn, args in (("PdhOpenQueryW", [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]),
                         ("PdhAddEnglishCounterW", [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                                    ctypes.POINTER(wintypes.HANDLE)]),
                         ("PdhCollectQueryData", [wintypes.HANDLE]),
                         ("PdhGetFormattedCounterArrayW", [wintypes.HANDLE, wintypes.DWORD,
                                                           ctypes.POINTER(wintypes.DWORD),
                                                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]),
                         ("PdhCloseQuery", [wintypes.HANDLE])):
            getattr(p, fn).argtypes = args
            getattr(p, fn).restype = ctypes.c_long
        self.q = wintypes.HANDLE()
        st = p.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) & 0xFFFFFFFF
        if st:
            raise OSError("PdhOpenQuery 0x%08X" % st)
        self.c = {}

    def add(self, key, path):
        h = wintypes.HANDLE()
        if self.dll.PdhAddEnglishCounterW(self.q, path, 0, ctypes.byref(h)) == 0:
            self.c[key] = h
            return True
        return False

    def collect(self):
        return self.dll.PdhCollectQueryData(self.q) == 0

    def array(self, key):
        """{instance: value} of the last collection (duplicate instance names are summed)."""
        h = self.c.get(key)
        if not h:
            return {}
        size, n = wintypes.DWORD(0), wintypes.DWORD(0)
        st = self.dll.PdhGetFormattedCounterArrayW(h, PDH_FMT, ctypes.byref(size), ctypes.byref(n), None)
        if (st & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if self.dll.PdhGetFormattedCounterArrayW(h, PDH_FMT, ctypes.byref(size), ctypes.byref(n), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem * n.value)).contents
        out = {}
        for it in items:
            if it.FmtValue.CStatus in (0, 1):            # PDH_CSTATUS_VALID_DATA / NEW_DATA
                out[it.szName] = out.get(it.szName, 0.0) + it.FmtValue.doubleValue
        return out

    def close(self):
        if self.q:
            self.dll.PdhCloseQuery(self.q)
            self.q = wintypes.HANDLE()


# ================================================================== CPU topology / name, RAM
def core_map():
    """{(group, logical index): physical core number} from GetLogicalProcessorInformationEx."""
    k = ctypes.windll.kernel32
    n = wintypes.DWORD(0)
    k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(n))          # RelationProcessorCore
    if not n.value:
        return {}
    buf = ctypes.create_string_buffer(n.value)
    if not k.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(n)):
        return {}
    out, off, core = {}, 0, 0
    raw = buf.raw
    while off < n.value:
        rel, size = struct.unpack_from("<II", raw, off)
        if size <= 0:
            break
        if rel == 0:
            gcount = struct.unpack_from("<H", raw, off + 30)[0]
            for g in range(gcount):
                mask, grp = struct.unpack_from("<QH", raw, off + 32 + 16 * g)
                for b in range(64):
                    if mask >> b & 1:
                        out[(grp, b)] = core
            core += 1
        off += size
    return out


def cpu_name():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())
    except OSError:
        return "CPU"


class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + \
               [(n, ctypes.c_ulonglong) for n in ("tp", "ap", "tf", "af", "tv", "av", "ae")]


def memory():
    m = _MEMSTAT()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return m


class _FT(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def _system_times():
    i, k, u = _FT(), _FT(), _FT()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    v = [x.hi << 32 | x.lo for x in (i, k, u)]
    return v[0], v[1] + v[2]                  # idle, total (kernel time includes idle)


# ================================================================== DXGI adapter names (no package needed)
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", wintypes.UINT)]


def _com(obj, index, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def dxgi_adapters():
    """[dict(name, vendor, vram_mb, luid "0xHHHHHHHH_0xLLLLLLLL")] of the hardware adapters; [] on any error."""
    out = []
    try:
        iid = (ctypes.c_byte * 16).from_buffer_copy(
            struct.pack("<IHH8B", 0x770AAE78, 0xF26F, 0x4DBA, 0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
        fac = ctypes.c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(fac)) != 0:
            return out
        try:
            i = 0
            while i < 16:
                ad = ctypes.c_void_p()
                if _com(fac, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                        fac, i, ctypes.byref(ad)) != 0:            # IDXGIFactory1::EnumAdapters1
                    break
                d = _ADAPTER_DESC1()
                ok = _com(ad, 10, ctypes.c_long, ctypes.POINTER(_ADAPTER_DESC1))(ad, ctypes.byref(d)) == 0
                _com(ad, 2, wintypes.ULONG)(ad)                    # Release
                i += 1
                if ok and not d.Flags & 2:                         # skip DXGI_ADAPTER_FLAG_SOFTWARE
                    luid = "0x%08X_0x%08X" % (d.AdapterLuid.HighPart & 0xFFFFFFFF, d.AdapterLuid.LowPart)
                    out.append(dict(name=d.Description.strip(), vendor=d.VendorId,
                                    vram_mb=d.DedicatedVideoMemory / MB, luid=luid))
        finally:
            _com(fac, 2, wintypes.ULONG)(fac)
    except Exception:
        pass
    return out


# ================================================================== NVML (NVIDIA)
class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Mem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _FanRpm(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("fan", ctypes.c_uint), ("speed", ctypes.c_uint)]


class Nvml:
    def __init__(self):
        cands = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")]
        for env in ("ProgramW6432", "ProgramFiles"):
            if os.environ.get(env):
                cands.append(os.path.join(os.environ[env], "NVIDIA Corporation", "NVSMI", "nvml.dll"))
        path = next((c for c in cands if os.path.isfile(c)), None)
        if not path:
            raise OSError("nvml.dll not found")
        self.dll = ctypes.WinDLL(path)
        init = self.fn("nvmlInit_v2") or self.fn("nvmlInit")
        rc = init() if init else -1
        if rc != 0:
            raise OSError(f"nvmlInit error {rc}")
        cnt = ctypes.c_uint(0)
        (self.fn("nvmlDeviceGetCount_v2") or self.fn("nvmlDeviceGetCount"))(ctypes.byref(cnt))
        self.devs = []
        get = self.fn("nvmlDeviceGetHandleByIndex_v2") or self.fn("nvmlDeviceGetHandleByIndex")
        for i in range(cnt.value):
            h = ctypes.c_void_p()
            if get(i, ctypes.byref(h)) != 0:
                continue
            buf = ctypes.create_string_buffer(96)
            self.fn("nvmlDeviceGetName")(h, buf, 96)
            name = buf.value.decode("utf-8", "replace").strip() or "NVIDIA GPU"
            fans = ctypes.c_uint(0)
            f = self.fn("nvmlDeviceGetNumFans")
            nf = fans.value if f and f(h, ctypes.byref(fans)) == 0 else 1
            rpm = self.fn("nvmlDeviceGetFanSpeedRPM")
            if rpm:
                x = _FanRpm(ctypes.sizeof(_FanRpm) | (1 << 24), 0, 0)
                if rpm(h, ctypes.byref(x)) != 0:
                    rpm = None
            self.devs.append((h, name, nf, rpm))
        if not self.devs:
            raise OSError("no NVIDIA GPU")

    def fn(self, name):
        return getattr(self.dll, name, None)

    def read(self, add, first_index=0):
        u = ctypes.c_uint()
        for i, (h, name, nf, rpm) in enumerate(self.devs):
            s = f"GPU [#{first_index + i}]: {name}"
            if self.dll.nvmlDeviceGetTemperature(h, 0, ctypes.byref(u)) == 0:
                add(1, s, "GPU Temperature", "°C", u.value)
            if self.dll.nvmlDeviceGetClockInfo(h, 0, ctypes.byref(u)) == 0:
                add(6, s, "GPU Clock", "MHz", u.value)
            if self.dll.nvmlDeviceGetClockInfo(h, 2, ctypes.byref(u)) == 0:
                add(6, s, "GPU Memory Clock", "MHz", u.value)
            ut = _Util()
            if self.dll.nvmlDeviceGetUtilizationRates(h, ctypes.byref(ut)) == 0:
                add(7, s, "GPU Core Load", "%", ut.gpu)
                add(7, s, "GPU Memory Controller Load", "%", ut.memory)
            if self.dll.nvmlDeviceGetPowerUsage(h, ctypes.byref(u)) == 0:
                add(5, s, "GPU Power", "W", u.value / 1000.0)
            for k in range(nf):
                if rpm:
                    x = _FanRpm(ctypes.sizeof(_FanRpm) | (1 << 24), k, 0)
                    if rpm(h, ctypes.byref(x)) == 0:
                        add(3, s, f"GPU Fan{k + 1}", "RPM", x.speed)
                f2 = self.fn("nvmlDeviceGetFanSpeed_v2")
                ok = f2(h, k, ctypes.byref(u)) == 0 if f2 else (k == 0 and self.dll.nvmlDeviceGetFanSpeed(
                    h, ctypes.byref(u)) == 0)
                if ok:
                    add(7, s, f"GPU Fan{k + 1} Speed", "%", u.value)
            m = _Mem()
            if self.dll.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) == 0:
                add(8, s, "GPU Memory Allocated", "MB", m.used / MB)
                add(8, s, "GPU Memory Available", "MB", m.free / MB)
                if m.total:
                    add(7, s, "GPU Memory Usage", "%", 100.0 * m.used / m.total)

    def close(self):
        f = self.fn("nvmlShutdown")
        if f:
            f()


# ================================================================== the source
_LUID_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", re.I)
_ENG_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)", re.I)


class BuiltinSource:
    name = "builtin"

    def __init__(self):
        self.notes = []                       # (i18n key, kwargs): data that is not available and why
        self.cpu = f"CPU [#0]: {cpu_name()}"
        self.cores = core_map()
        self.pdh = self.ps = self.nvml = None
        self.pdh_err = ""
        self.totals = {}                      # network instance -> [down bytes, up bytes]
        self.last = None
        self.adapters = dxgi_adapters()
        self.gpu_order = []
        if not FORCE_NO_NVML:
            try:
                self.nvml = Nvml()
            except Exception as ex:
                if any(a["vendor"] == 0x10DE for a in self.adapters):
                    self.notes.append(("na_nvml", {"err": str(ex)}))
        if not self.nvml:
            self.notes.append(("na_gpu_other", {}))
        try:
            if FORCE_NO_PDH:
                raise OSError("disabled for testing")
            self.pdh = Pdh()
            ok = self.pdh.add("cpu", r"\Processor Information(*)\% Processor Time")
            self.pdh.add("perf", r"\Processor Information(*)\% Processor Performance")
            self.pdh.add("freq", r"\Processor Information(*)\Processor Frequency")
            ok &= self.pdh.add("rx", r"\Network Interface(*)\Bytes Received/sec")
            self.pdh.add("tx", r"\Network Interface(*)\Bytes Sent/sec")
            ok &= self.pdh.add("dr", r"\PhysicalDisk(*)\Disk Read Bytes/sec")
            self.pdh.add("dw", r"\PhysicalDisk(*)\Disk Write Bytes/sec")
            self.pdh.add("didle", r"\PhysicalDisk(*)\% Idle Time")
            if not self.nvml:
                self.pdh.add("geng", r"\GPU Engine(*)\Utilization Percentage")
                self.pdh.add("gded", r"\GPU Adapter Memory(*)\Dedicated Usage")
            if not ok:
                raise OSError("performance counters missing")
            self.pdh.collect()
        except Exception as ex:
            self.pdh_err = str(ex)
            if self.pdh:
                self.pdh.close()
            self.pdh = None
            try:
                import psutil
                self.ps = psutil
                self.ps_prev = None
                psutil.cpu_percent(percpu=True)
                self.notes.append(("na_pdh_psutil", {"err": self.pdh_err}))
            except Exception:
                self.notes.append(("na_pdh", {"err": self.pdh_err}))
        self.st_prev = _system_times()
        self.notes.append(("na_driver", {}))
        self.t0 = time.time()

    # -------------------------------------------------------------- helpers
    def note_texts(self):
        return [t(k, **kw) for k, kw in self.notes]

    def _cpu_threads(self, util):
        """[(group, idx, value)] sorted, from 'g,i' instances (no _Total)."""
        out = []
        for inst, v in util.items():
            m = re.match(r"^(\d+),(\d+)$", inst)
            if m:
                out.append((int(m.group(1)), int(m.group(2)), v))
        return sorted(out)

    def read_all(self):
        rd = []

        def add(rtype, sensor, label, unit, value):
            rd.append(reading(rtype, sensor, label, unit, value))

        now = time.time()
        dt = max(1e-3, now - self.last) if self.last else 0.0
        self.last = now
        if self.pdh:
            self.pdh.collect()
        self._cpu(add)
        self._memory(add)
        self._gpu(add)
        self._net_disk(add, dt)
        return rd

    # -------------------------------------------------------------- CPU
    def _cpu(self, add):
        s = self.cpu
        idle, total = _system_times()
        pi, pt = self.st_prev
        self.st_prev = (idle, total)
        tot = 100.0 * (1 - (idle - pi) / (total - pt)) if total > pt else 0.0
        threads = []
        if self.pdh:
            util = self.pdh.array("cpu")
            threads = [((g, i), v) for g, i, v in self._cpu_threads(util)]
            tot = util.get("_Total", tot)
        elif self.ps:
            threads = [((0, i), v) for i, v in enumerate(self.ps.cpu_percent(percpu=True))]
        add(7, s, "Total CPU Usage", "%", max(0.0, min(100.0, tot)))
        if threads:
            add(7, s, "Max CPU/Thread Usage", "%", min(100.0, max(v for _, v in threads)))
        if self.pdh:
            perf, freq = self.pdh.array("perf"), self.pdh.array("freq")
            clocks = {}
            for g, i, p in self._cpu_threads(perf):
                f = freq.get(f"{g},{i}")
                if f:
                    c = self.cores.get((g, i), i)
                    clocks[c] = max(clocks.get(c, 0.0), f * p / 100.0)
            for c in sorted(clocks):
                add(6, s, f"Core {c} Clock", "MHz", clocks[c])
            if clocks:
                add(6, s, "Core Clocks (avg)", "MHz", sum(clocks.values()) / len(clocks))
        seen = {}
        for (g, i), v in threads:
            c = self.cores.get((g, i), i)
            n = seen[c] = seen.get(c, -1) + 1
            add(7, s, f"Core {c} T{n} Usage", "%", min(100.0, v))

    def _memory(self, add):
        m = memory()
        if not m:
            return
        s = "System: Memory"
        add(8, s, "Physical Memory Used", "MB", (m.tp - m.ap) / MB)
        add(8, s, "Physical Memory Available", "MB", m.ap / MB)
        add(7, s, "Physical Memory Load", "%", m.dwMemoryLoad)
        add(8, s, "Virtual Memory Committed", "MB", (m.tf - m.af) / MB)

    # -------------------------------------------------------------- GPU
    def _gpu(self, add):
        if self.nvml:
            self.nvml.read(add)
            return
        if not self.pdh or ("geng" not in self.pdh.c and "gded" not in self.pdh.c):
            return
        load = {}
        for inst, v in self.pdh.array("geng").items():
            m = _ENG_RE.search(inst)
            if m:
                key = (m.group(1).upper(), m.group(2), m.group(3))
                load[key] = load.get(key, 0.0) + v
        per = {}
        for (luid, _, _), v in load.items():
            per[luid] = max(per.get(luid, 0.0), v)
        ded = {}
        for inst, v in self.pdh.array("gded").items():
            m = _LUID_RE.search(inst)
            if m:
                ded[m.group(1).upper()] = ded.get(m.group(1).upper(), 0.0) + v
        names = {a["luid"].upper(): a for a in self.adapters}
        for luid in sorted(set(per) | set(ded), key=lambda x: -(names.get(x, {}).get("vram_mb", 0))):
            if luid in names and luid not in self.gpu_order:
                self.gpu_order.append(luid)
        for n, luid in enumerate(self.gpu_order):
            a = names[luid]
            s = f"GPU [#{n}]: {a['name']}"
            add(7, s, "GPU Core Load", "%", min(100.0, per.get(luid, 0.0)))
            if luid in ded:
                used = ded[luid] / MB
                add(8, s, "GPU D3D Memory Dedicated", "MB", used)
                if a["vram_mb"] > 0:
                    add(8, s, "GPU Memory Allocated", "MB", used)
                    add(8, s, "GPU Memory Available", "MB", max(0.0, a["vram_mb"] - used))

    # -------------------------------------------------------------- network / disk
    def _net_disk(self, add, dt):
        if self.pdh:
            rx, tx = self.pdh.array("rx"), self.pdh.array("tx")
            nets = [(k, rx.get(k, 0.0), tx.get(k, 0.0)) for k in rx]
            dr, dw, idle = self.pdh.array("dr"), self.pdh.array("dw"), self.pdh.array("didle")
            disks = [(k, dr.get(k, 0.0), dw.get(k, 0.0), idle.get(k)) for k in dr if k != "_Total"]
        elif self.ps:
            nets, disks = self._psutil_io(dt)
        else:
            return
        for name, down, up in nets:
            s = f"Network: {name}"
            tot = self.totals.setdefault(name, [0.0, 0.0])
            tot[0] += down * dt
            tot[1] += up * dt
            add(8, s, "Current DL rate", "KB/s", down / 1024.0)
            add(8, s, "Current UP rate", "KB/s", up / 1024.0)
            add(8, s, "Total DL", "MB", tot[0] / MB)
            add(8, s, "Total UP", "MB", tot[1] / MB)
        for name, r, w, idle in sorted(disks):
            m = re.match(r"^(\d+)\s*(.*)$", name)
            num, letters = (m.group(1), m.group(2).split()) if m else (name, [])
            s = f"Drive: Disk {num}" + (f" [{', '.join(letters)}]" if letters else "")
            add(8, s, "Read Rate", "MB/s", r / MB)
            add(8, s, "Write Rate", "MB/s", w / MB)
            if idle is not None:
                add(7, s, "Total Activity", "%", max(0.0, min(100.0, 100.0 - idle)))

    def _psutil_io(self, dt):
        n = self.ps.net_io_counters(pernic=True)
        d = self.ps.disk_io_counters(perdisk=True) or {}
        prev, self.ps_prev = self.ps_prev, (n, d)
        nets, disks = [], []
        if not prev or dt <= 0:
            return [(k, 0.0, 0.0) for k in n], [(k.replace("PhysicalDrive", ""), 0.0, 0.0, None) for k in d]
        for k, v in n.items():
            p = prev[0].get(k)
            if p:
                nets.append((k, max(0, v.bytes_recv - p.bytes_recv) / dt, max(0, v.bytes_sent - p.bytes_sent) / dt))
        for k, v in d.items():
            p = prev[1].get(k)
            if p:
                disks.append((k.replace("PhysicalDrive", ""), max(0, v.read_bytes - p.read_bytes) / dt,
                              max(0, v.write_bytes - p.write_bytes) / dt, None))
        return nets, disks

    def close(self):
        if self.pdh:
            self.pdh.close()
        if self.nvml:
            try:
                self.nvml.close()
            except Exception:
                pass
