"""metriQ benchmark engine (no GUI): CPU, RAM, SSD and GPU tests, sensor statistics, scores, history, export.

    python perf_core.py [cpu|ram|ssd|gpu|all]      headless run, prints the results (METRIQ_BENCH_QUICK=1: short)

Every test is  run_<name>(ctl) -> result dict  and can be cancelled at any time (ctl.cancelled() -> raises
Cancelled). ctl.progress(fraction, text) reports progress, ctl.sensors collects temperatures / power / clocks from
the sensor backend while the test runs. Scores are normalised against the dev PC (see REF) and are only comparable
inside metriQ - they are NOT a replacement for 3DMark, Cinebench & co.
"""
import contextlib
import csv
import ctypes
import io
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import config

TESTS = ("cpu", "ram", "ssd", "gpu")
MIN_FREE_GB = 4                      # SSD test: skip below this much free space on the temp drive
HISTORY_MAX = 60
GPU_EXE = "GpuBench.exe"

# ---------------------------------------------------------------------------------------------- reference
# 1000 points = what the dev PC measured (AMD Ryzen 7 9700X, 32 GB DDR5, NVMe SSD, GeForce RTX 5070 Ti, Python 3.13,
# 2026-10-27). Units: CPU kernel iterations / s, GB/s, ns, MB/s, GFLOPS. Scores are only comparable inside metriQ.
REF = {
    "cpu": {"single_int": 1.0, "single_float": 1.0, "multi_int": 1.0, "multi_float": 1.0},
    "ram": {"read": 1.0, "write": 1.0, "copy": 1.0, "latency": 1.0},
    "ssd": {"seq_read": 1.0, "seq_write": 1.0, "rnd_read": 1.0, "rnd_write": 1.0},
    "gpu": {"gflops": 1.0, "bandwidth": 1.0},
}
LOWER_IS_BETTER = {("ram", "latency")}


class Cancelled(Exception):
    pass


def quick():
    return os.environ.get("METRIQ_BENCH_QUICK", "") not in ("", "0")


def _scale():
    return 0.1 if quick() else 1.0


def geomean(vals):
    vals = [v for v in vals if v and v > 0]
    return math.exp(sum(math.log(v) for v in vals) / len(vals)) if vals else 0.0


def metric_score(test, name, value):
    ref = REF[test][name]
    if not value or value <= 0:
        return 0.0
    r = ref / value if (test, name) in LOWER_IS_BETTER else value / ref
    return 1000.0 * r


def score_of(test, metrics):
    """Test score: geometric mean of the per-metric scores (CPU: single and multi thread weigh the same)."""
    sc = {k: metric_score(test, k, v) for k, v in metrics.items() if k in REF[test]}
    if test == "cpu":
        s = geomean([sc.get("single_int"), sc.get("single_float")])
        m = geomean([sc.get("multi_int"), sc.get("multi_float")])
        return geomean([s, m]), {"single": s, "multi": m}
    return geomean(list(sc.values())), sc


def overall(results):
    """Overall system score: geometric mean of the test scores that exist (dict test -> result)."""
    sc = [r["score"] for r in results.values() if r and r.get("score")]
    return geomean(sc)


# ---------------------------------------------------------------------------------------------- sensors
def _num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def extract(rd):
    """Pull the readings a benchmark cares about from a backend reading list -> dict (None = not available)."""
    out = dict(cpu_temp=None, cpu_power=None, cpu_clock=None, gpu_temp=None, gpu_power=None, gpu_clock=None,
               throttle=False, throttle_name="")
    cand = {k: [] for k in ("cpu_temp", "cpu_power", "cpu_clock", "gpu_temp", "gpu_power", "gpu_clock", "core_clocks")}
    for r in rd or []:
        v = _num(r.get("value"))
        if v is None:
            continue
        ty, lab, sen = r.get("type"), (r.get("label") or "").lower(), (r.get("sensor") or "").lower()
        is_gpu = "gpu" in sen or lab.startswith("gpu")
        drive = any(x in sen for x in ("drive", "nvme", "s.m.a.r.t", "memory", "dimm", "ssd"))
        if ty == 1 and not drive and v > 0:
            if is_gpu:
                if lab in ("gpu temperature", "gpu core temperature", "gpu hot spot temperature", "gpu hotspot"):
                    cand["gpu_temp"].append((0 if lab == "gpu temperature" else 1, v))
            else:
                for i, key in enumerate(("cpu package", "tctl/tdie", "cpu die", "core max", "tdie", "tctl",
                                         "cpu temperature", "package", "core average")):
                    if key in lab:
                        cand["cpu_temp"].append((i, v))
                        break
        elif ty == 5 and v >= 0:
            if is_gpu:
                if lab == "gpu power":
                    cand["gpu_power"].append((0, v))
                elif lab in ("gpu board power", "total board power", "gpu chip power", "gpu total board power"):
                    cand["gpu_power"].append((1, v))
            else:
                for i, key in enumerate(("cpu package power", "package power", "ppt", "cpu power")):
                    if key in lab:
                        cand["cpu_power"].append((i, v))
                        break
        elif ty == 6 and v > 0:
            if is_gpu:
                if lab in ("gpu clock", "gpu core clock"):
                    cand["gpu_clock"].append((0, v))
            elif "memory" not in lab and "bus" not in lab and "uncore" not in lab and "fabric" not in lab:
                if "core clocks (avg)" in lab or "average effective clock" in lab or "average core clock" in lab:
                    cand["cpu_clock"].append((0, v))
                elif re.match(r"^core\s*\d+.*clock", lab) and "effective" not in lab:
                    cand["core_clocks"].append((0, v))
        elif ty == 8 and v >= 1 and "thermal" in lab and ("throttl" in lab or "limit" in lab):
            out["throttle"], out["throttle_name"] = True, r.get("label") or ""
    for key in ("cpu_temp", "cpu_power", "cpu_clock", "gpu_temp", "gpu_power", "gpu_clock"):
        c = cand[key]
        if c:
            best = min(p for p, _ in c)
            vals = [v for p, v in c if p == best]
            out[key] = max(vals) if key not in ("cpu_clock",) else sum(vals) / len(vals)
    if out["cpu_clock"] is None and cand["core_clocks"]:
        vals = [v for _, v in cand["core_clocks"]]
        out["cpu_clock"] = sum(vals) / len(vals)
    return out


class SensorLog:
    """Collects extract() samples; add(readings) is called about once per second by whoever owns the backend."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
        self.on = False
        self.last = {}

    def start(self):
        with self.lock:
            self.rows, self.on = [], True

    def add(self, rd):
        s = extract(rd)
        s["t"] = time.time()
        with self.lock:
            self.last = s
            if self.on:
                self.rows.append(s)

    def stop(self):
        with self.lock:
            self.on = False
            rows = list(self.rows)
        return summarize(rows)


def _stat(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals), max(vals)) if vals else (None, None)


def summarize(rows):
    s = {"samples": len(rows)}
    for key in ("cpu_temp", "cpu_power", "cpu_clock", "gpu_temp", "gpu_power", "gpu_clock"):
        series = [r[key] for r in rows]
        avg, mx = _stat(series)
        s[key + "_avg"], s[key + "_max"] = avg, mx
    notes = []
    flagged = [r for r in rows if r.get("throttle")]
    if flagged:
        notes.append(flagged[0].get("throttle_name") or "thermal limit")
    # clock sag: last third of the run clearly slower than the first third while it was hot
    for clk, tmp, label in (("cpu_clock", "cpu_temp", "CPU"), ("gpu_clock", "gpu_temp", "GPU")):
        series = [r[clk] for r in rows if r.get(clk)]
        if len(series) >= 9:
            n = len(series) // 3
            a, b = sum(series[:n]) / n, sum(series[-n:]) / n
            hot = s.get(tmp + "_max") or 0
            if a > 0 and b < 0.88 * a and hot >= (80 if label == "CPU" else 75):
                notes.append("%s clock dropped %.0f %%" % (label, 100 * (1 - b / a)))
    s["throttled"] = bool(notes) if rows and any(r.get("cpu_temp") or r.get("gpu_temp") for r in rows) else None
    s["throttle_notes"] = notes
    return s


def power_for(test, sens):
    """Average watts the efficiency figure refers to: GPU test -> GPU board power, the others -> CPU package."""
    p = sens.get("gpu_power_avg") if test == "gpu" else sens.get("cpu_power_avg")
    return p if p and p > 1 else None


# ---------------------------------------------------------------------------------------------- control
class Ctl:
    """What a test gets: cancel flag, progress callback, sensor log."""

    def __init__(self, progress=None, sensors=None):
        self.stop = threading.Event()
        self._progress = progress
        self.sensors = sensors or SensorLog()
        self.stop_mp = None            # multiprocessing.Event shared with the CPU workers

    def cancel(self):
        self.stop.set()
        if self.stop_mp is not None:
            self.stop_mp.set()

    def cancelled(self):
        return self.stop.is_set()

    def check(self):
        if self.stop.is_set():
            raise Cancelled()

    def progress(self, frac, text=""):
        if self._progress:
            try:
                self._progress(max(0.0, min(1.0, frac)), text)
            except Exception:
                pass

    def sleep(self, secs):
        end = time.time() + secs
        while time.time() < end:
            self.check()
            time.sleep(min(0.05, max(0.0, end - time.time())))


def _finish(test, ctl, metrics, t0, extra=None):
    sens = ctl.sensors.stop()
    score, parts = score_of(test, metrics)
    res = {"test": test, "ts": time.time(), "duration": time.time() - t0, "metrics": metrics, "score": score,
           "parts": parts, "sensors": sens, "quick": quick()}
    w = power_for(test, sens)
    res["power_avg"] = w
    res["efficiency"] = score / w if w else None
    if extra:
        res.update(extra)
    return res


# ---------------------------------------------------------------------------------------------- CPU
def _k_int(x, n):
    for _ in range(n):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
    return x


def _k_float(a, b, n):
    sqrt = math.sqrt
    for _ in range(n):
        a = a * 0.9999991 + b * 0.0000009
        b = b * 1.0000003 - a * 0.0000011 + 1e-9
        a += sqrt(b * b + 1.0) * 1e-9
    return a, b


CHUNK = 12000          # kernel iterations between two clock reads (~3 ms)


def _worker(conn, stop):
    """Child process: runs ("int"|"float", seconds) jobs, answers with (iterations, seconds)."""
    x, a, b = 12345, 1.0, 0.5
    while True:
        try:
            job = conn.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        kind, secs = job
        t0 = time.perf_counter()
        end, n = t0 + secs, 0
        while not stop.is_set():
            if kind == "int":
                x = _k_int(x, CHUNK)
            else:
                a, b = _k_float(a, b, CHUNK)
            n += CHUNK
            if time.perf_counter() >= end:
                break
        try:
            conn.send((n, time.perf_counter() - t0))
        except (OSError, ValueError):
            return


def run_cpu(ctl):
    t0 = time.time()
    k = _scale()
    phases = [("single", "int", 8 * k), ("single", "float", 8 * k), ("multi", "int", 22 * k), ("multi", "float", 22 * k)]
    total = sum(p[2] for p in phases)
    nproc = max(1, os.cpu_count() or 1)
    mpctx = mp.get_context("spawn")
    stop = mpctx.Event()
    ctl.stop_mp = stop
    procs, conns = [], []
    metrics = {}
    ctl.progress(0.0, "start")
    ctl.sensors.start()
    try:
        for _ in range(nproc):
            a, b = mpctx.Pipe()
            p = mpctx.Process(target=_worker, args=(b, stop), daemon=True)
            p.start()
            procs.append(p)
            conns.append(a)
        done = 0.0
        for mode, kind, secs in phases:
            ctl.check()
            use = conns[:1] if mode == "single" else conns
            for c in use:
                c.send((kind, secs))
            end = time.time() + secs
            label = "%s %s" % (mode, kind)
            while True:
                ctl.check()
                ready = sum(1 for c in use if c.poll(0))
                ctl.progress((done + min(secs, max(0.0, secs - (end - time.time())))) / total, label)
                if ready == len(use):
                    break
                if time.time() > end + 20:
                    raise RuntimeError("CPU worker did not answer")
                time.sleep(0.05)
            rates = []
            for c in use:
                n, dt = c.recv()
                rates.append(n / dt if dt > 0 else 0.0)
            metrics["%s_%s" % (mode, kind)] = sum(rates)
            done += secs
        ctl.progress(1.0, "done")
        res = _finish("cpu", ctl, metrics, t0, {"threads": nproc})
        mt, st = metrics["multi_int"] + metrics["multi_float"], metrics["single_int"] + metrics["single_float"]
        res["scaling"] = mt / st if st else None
        return res
    finally:
        ctl.sensors.stop()
        stop.set()
        for c in conns:
            with contextlib.suppress(Exception):
                c.send(None)
        for p in procs:
            p.join(1.5)
            if p.is_alive():
                p.terminate()
        ctl.stop_mp = None


# ---------------------------------------------------------------------------------------------- RAM
def _crt():
    lib = ctypes.cdll.msvcrt
    lib.memchr.restype = ctypes.c_void_p
    lib.memchr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    lib.memset.restype = ctypes.c_void_p
    lib.memset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    lib.memmove.restype = ctypes.c_void_p
    lib.memmove.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    return lib


def _free_ram():
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                    ("ap", ctypes.c_ulonglong), ("tf", ctypes.c_ulonglong), ("af", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ex", ctypes.c_ulonglong)]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ap


def _threaded(ctl, secs, fn, nthreads):
    """Run fn(i, deadline) in nthreads threads (ctypes calls drop the GIL); returns (bytes, seconds)."""
    res = [0] * nthreads
    t0 = time.perf_counter()
    deadline = t0 + secs

    def run(i):
        res[i] = fn(i, deadline)
    ths = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(nthreads)]
    for th in ths:
        th.start()
    for th in ths:
        while th.is_alive():
            th.join(0.05)
            if ctl.cancelled():
                break
    dt = time.perf_counter() - t0
    for th in ths:
        th.join(2.0)
    ctl.check()
    return sum(res), dt


def run_ram(ctl):
    t0 = time.time()
    k = _scale()
    crt = _crt()
    nth = max(1, min(4, os.cpu_count() or 1))
    per = 128 << 20
    while per > (16 << 20) and per * nth * 2 > _free_ram() * 0.5:
        per //= 2
    ctl.progress(0.0, "alloc")
    ctl.sensors.start()
    metrics = {}
    try:
        bufs = []
        for i in range(nth):
            ctl.check()
            src, dst = (ctypes.c_char * per)(), (ctypes.c_char * per)()
            crt.memset(ctypes.addressof(src), 1, per)         # touch every page, no 0xFF inside (memchr scans all)
            crt.memset(ctypes.addressof(dst), 2, per)
            bufs.append((ctypes.addressof(src), ctypes.addressof(dst), src, dst))

        def reader(i, end):
            n = 0
            a = bufs[i][0]
            while time.perf_counter() < end:
                crt.memchr(a, 0xFF, per)
                n += per
            return n

        def writer(i, end):
            n = 0
            a = bufs[i][1]
            while time.perf_counter() < end:
                crt.memset(a, (n >> 20) & 0x7F, per)
                n += per
            return n

        def copier(i, end):
            n = 0
            s, d = bufs[i][0], bufs[i][1]
            while time.perf_counter() < end:
                crt.memmove(d, s, per)
                n += per
            return n

        plan = (("read", reader, 3.0), ("write", writer, 3.0), ("copy", copier, 3.0))
        total = sum(p[2] for p in plan) * k + 4.0 * k
        done = 0.0
        for name, fn, secs in plan:
            secs *= k
            ev = threading.Event()

            def tick(name=name, secs=secs, base=done):
                s0 = time.time()
                while not ev.is_set() and not ctl.cancelled():
                    ctl.progress((base + min(secs, time.time() - s0)) / total, "RAM " + name)
                    time.sleep(0.1)
            threading.Thread(target=tick, daemon=True).start()
            try:
                nbytes, dt = _threaded(ctl, secs, fn, nth)
            finally:
                ev.set()
            metrics[name] = nbytes / dt / 1e9               # copy: bytes copied / s
            done += secs
        del bufs
        metrics["latency"] = _latency(ctl, 4.0 * k, lambda f: ctl.progress((done + 4.0 * k * f) / total, "RAM latency"))
        ctl.progress(1.0, "done")
        return _finish("ram", ctl, metrics, t0, {"threads": nth, "buffer_mb": per * nth * 2 >> 20})
    finally:
        ctl.sensors.stop()


def _chase(mv, mask, steps):
    i = 0
    for _ in range(steps):
        i = ((i + mv[i]) * 1664525 + 1013904223) & mask
    return i


def _latency(ctl, secs, prog):
    """Random-access latency estimate in ns: dependent loads through a big buffer minus the same loop on a buffer
    that fits the L1 cache (removes the interpreter overhead). Mostly DRAM + TLB latency, an estimate only."""
    size = 1 << 29
    while size > (64 << 20) and size > _free_ram() // 4:
        size >>= 1
    big = memoryview(bytearray(size))
    small = memoryview(bytearray(1 << 14))
    for i in range(0, size, 4096):
        big[i] = 0                                            # touch pages
    mb, ms = size - 1, (1 << 14) - 1
    steps = 200000
    ds, db = [], []
    end = time.perf_counter() + secs
    n = 0
    while time.perf_counter() < end or n < 3:
        ctl.check()
        a = time.perf_counter()
        _chase(small, ms, steps)
        b = time.perf_counter()
        _chase(big, mb, steps)
        c = time.perf_counter()
        ds.append((b - a) / steps)
        db.append((c - b) / steps)
        n += 1
        prog(min(1.0, (time.perf_counter() - (end - secs)) / secs))
    ds.sort()
    db.sort()
    m = len(ds) // 2
    return max(1.0, (db[m] - ds[m]) * 1e9)


# ---------------------------------------------------------------------------------------------- SSD
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
CREATE_ALWAYS = 2
F_NO_BUFFERING, F_WRITE_THROUGH, F_DELETE_ON_CLOSE = 0x20000000, 0x80000000, 0x04000000


class _OVL(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", ctypes.c_uint32),
                ("OffsetHigh", ctypes.c_uint32), ("hEvent", ctypes.c_void_p)]


def free_space_gb(path=None):
    try:
        return shutil.disk_usage(path or tempfile.gettempdir()).free / 1e9
    except OSError:
        return 0.0


def run_ssd(ctl):
    t0 = time.time()
    k = max(0.2, _scale() * 2) if quick() else 1.0
    tmpdir = tempfile.gettempdir()
    free = free_space_gb(tmpdir)
    if free < MIN_FREE_GB:
        return {"test": "ssd", "ts": time.time(), "skipped": "only %.1f GB free on %s (need %d GB)" %
                (free, os.path.splitdrive(tmpdir)[0] or tmpdir, MIN_FREE_GB)}
    import random
    kern = ctypes.WinDLL("kernel32", use_last_error=True)
    kern.CreateFileW.restype = ctypes.c_void_p
    kern.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                 ctypes.c_uint32, ctypes.c_void_p]
    kern.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                              ctypes.c_void_p]
    kern.WriteFile.argtypes = kern.ReadFile.argtypes
    kern.VirtualAlloc.restype = ctypes.c_void_p
    kern.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    kern.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    kern.CloseHandle.argtypes = [ctypes.c_void_p]
    size = (512 << 20) if quick() else (2 << 30)
    blk = 2 << 20
    path = os.path.join(tmpdir, "metriq_bench_%d.tmp" % os.getpid())
    INVALID = ctypes.c_void_p(-1).value
    cached = False
    h = kern.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, CREATE_ALWAYS,
                         F_NO_BUFFERING | F_WRITE_THROUGH | F_DELETE_ON_CLOSE, None)
    if h in (None, INVALID):
        cached = True                    # e.g. a RAM disk: fall back to write-through only (numbers then too high)
        h = kern.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, CREATE_ALWAYS,
                             F_WRITE_THROUGH | F_DELETE_ON_CLOSE, None)
    if h in (None, INVALID):
        raise OSError("cannot create %s (error %d)" % (path, ctypes.get_last_error()))
    buf = kern.VirtualAlloc(None, blk, 0x3000, 4)
    if not buf:
        kern.CloseHandle(h)
        raise MemoryError("VirtualAlloc")
    ctypes.memmove(buf, os.urandom(blk), blk)
    nbytes = ctypes.c_uint32(0)
    ov = _OVL()
    metrics = {}
    total = 4 * 7.0 * k
    ctl.sensors.start()
    ctl.progress(0.0, "SSD")

    def io(fn, off, n):
        ov.Offset, ov.OffsetHigh = off & 0xFFFFFFFF, off >> 32
        if not fn(h, buf, n, ctypes.byref(nbytes), ctypes.byref(ov)):
            raise OSError("I/O error %d at %d" % (ctypes.get_last_error(), off))

    def phase(name, label, secs, step, rnd, write, base):
        end_t = time.perf_counter() + secs
        t_start = time.perf_counter()
        nops, off = 0, 0
        blocks = size // step
        fn = kern.WriteFile if write else kern.ReadFile
        must_fill = (name == "seq_write")
        while True:
            now = time.perf_counter()
            if now >= end_t and not (must_fill and nops * step < size and now < end_t + 20 * k):
                break
            if (nops & 15) == 0:
                ctl.check()
                ctl.progress((base + min(secs, now - t_start)) / total, label)
            if rnd:
                off = random.randrange(blocks) * step
            else:
                off = (nops % blocks) * step
            io(fn, off, step)
            nops += 1
        dt = time.perf_counter() - t_start
        mbs = nops * step / dt / 1e6
        return mbs, nops / dt

    try:
        secs = 7.0 * k
        base = 0.0
        mbs, _ = phase("seq_write", "SSD seq write", secs, blk, False, True, base)
        metrics["seq_write"] = mbs
        base += secs
        mbs, _ = phase("seq_read", "SSD seq read", secs, blk, False, False, base)
        metrics["seq_read"] = mbs
        base += secs
        mbs, iops = phase("rnd_read", "SSD 4K read", secs, 4096, True, False, base)
        metrics["rnd_read"], metrics["rnd_read_iops"] = mbs, iops
        base += secs
        mbs, iops = phase("rnd_write", "SSD 4K write", secs, 4096, True, True, base)
        metrics["rnd_write"], metrics["rnd_write_iops"] = mbs, iops
        ctl.progress(1.0, "done")
        return _finish("ssd", ctl, metrics, t0, {"drive": os.path.splitdrive(tmpdir)[0], "file_mb": size >> 20,
                                                 "cached": cached})
    finally:
        ctl.sensors.stop()
        kern.VirtualFree(buf, 0, 0x8000)
        kern.CloseHandle(h)                  # DELETE_ON_CLOSE removes the file
        with contextlib.suppress(OSError):
            os.remove(path)


# ---------------------------------------------------------------------------------------------- GPU
def gpu_exe():
    for d in (config.APP_DIR, os.path.join(config.APP_DIR, "..", "gpu", "build"), os.path.join(config.APP_DIR, "gpu")):
        p = os.path.normpath(os.path.join(d, GPU_EXE))
        if os.path.isfile(p):
            return p
    return None


def run_gpu(ctl):
    t0 = time.time()
    exe = gpu_exe()
    if not exe:
        return {"test": "gpu", "ts": time.time(), "skipped": "helper %s not found" % GPU_EXE}
    secs = 6 if quick() else 60
    flags = 0x08000000                                           # CREATE_NO_WINDOW
    ctl.sensors.start()
    ctl.progress(0.0, "GPU")
    proc = subprocess.Popen([exe, "--seconds", str(secs)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags)
    state = {"result": None, "err": None}

    def reader():
        for raw in proc.stdout:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "progress" in d:
                ctl.progress(float(d["progress"]), "GPU " + str(d.get("phase", "")))
            elif "result" in d:
                state["result"] = d["result"]
            elif "error" in d:
                state["err"] = d["error"]
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        while proc.poll() is None:
            ctl.check()
            if time.time() - t0 > secs + 60:
                raise RuntimeError("GPU helper timed out")
            time.sleep(0.1)
        th.join(2)
        r = state["result"]
        if not r:
            err = state["err"] or (proc.stderr.read().decode("utf-8", "replace")[:200] if proc.stderr else "")
            return {"test": "gpu", "ts": time.time(), "skipped": "GPU helper failed: %s" % (err or proc.returncode)}
        metrics = {"gflops": float(r["gflops"]), "bandwidth": float(r["bandwidth_gbs"])}
        ctl.progress(1.0, "done")
        return _finish("gpu", ctl, metrics, t0, {"adapter": r.get("adapter", ""), "vram_mb": r.get("vram_mb")})
    finally:
        ctl.sensors.stop()
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(3)


RUNNERS = {"cpu": run_cpu, "ram": run_ram, "ssd": run_ssd, "gpu": run_gpu}


# ---------------------------------------------------------------------------------------------- history / export
def history_path():
    return os.path.join(config.data_dir(), "benchmarks.json")


def load_history():
    try:
        with open(history_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_run(run):
    hist = load_history()
    hist.append(run)
    hist = hist[-HISTORY_MAX:]
    os.makedirs(config.data_dir(), exist_ok=True)
    tmp = history_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    os.replace(tmp, history_path())
    return hist


def new_run(results, system=None):
    good = {k: v for k, v in results.items() if v and v.get("score")}
    return {"ts": time.time(), "version": 1, "system": system or system_info(), "results": good,
            "overall": overall(good) if good else 0.0, "quick": quick()}


def system_info():
    info = {}
    try:
        import sens_builtin
        info["cpu"] = sens_builtin.cpu_name()
    except Exception:
        pass
    info["cores"] = os.cpu_count()
    try:
        info["ram_gb"] = round(_total_ram() / 2**30, 1)
    except Exception:
        pass
    return info


def _total_ram():
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                    ("ap", ctypes.c_ulonglong), ("tf", ctypes.c_ulonglong), ("af", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ex", ctypes.c_ulonglong)]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.tp


UNITS = {"single_int": "it/s", "single_float": "it/s", "multi_int": "it/s", "multi_float": "it/s",
         "read": "GB/s", "write": "GB/s", "copy": "GB/s", "latency": "ns", "seq_read": "MB/s", "seq_write": "MB/s",
         "rnd_read": "MB/s", "rnd_write": "MB/s", "rnd_read_iops": "IOPS", "rnd_write_iops": "IOPS",
         "gflops": "GFLOPS", "bandwidth": "GB/s"}


def fmt_value(name, v):
    if v is None:
        return "-"
    if name in ("single_int", "single_float", "multi_int", "multi_float"):
        return "%.2f M" % (v / 1e6)
    if name in ("rnd_read_iops", "rnd_write_iops"):
        return "%.0f" % v
    if name in ("gflops", "seq_read", "seq_write") or v >= 1000:
        return "%.0f" % v
    return "%.1f" % v


def run_to_csv(run):
    """CSV text (; separated, Excel friendly) with one row per metric of every test of the run."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["time", "test", "metric", "value", "unit", "metric_score", "test_score", "overall", "avg_power_w",
                "max_temp_c", "avg_clock_mhz", "throttled"])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.get("ts", 0)))
    for test in TESTS:
        r = (run.get("results") or {}).get(test)
        if not r:
            continue
        s = r.get("sensors") or {}
        tmax = s.get("gpu_temp_max") if test == "gpu" else s.get("cpu_temp_max")
        clk = s.get("gpu_clock_avg") if test == "gpu" else s.get("cpu_clock_avg")
        for name, v in r["metrics"].items():
            ms = metric_score(test, name, v) if name in REF[test] else ""
            w.writerow([stamp, test, name, "%.4f" % v, UNITS.get(name, ""), "" if ms == "" else "%.0f" % ms,
                        "%.0f" % r["score"], "%.0f" % run.get("overall", 0),
                        "" if r.get("power_avg") is None else "%.1f" % r["power_avg"],
                        "" if tmax is None else "%.0f" % tmax, "" if clk is None else "%.0f" % clk,
                        "" if s.get("throttled") is None else ("yes" if s["throttled"] else "no")])
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- headless
def _headless(which):
    import backend
    cfg = {"backend": "auto", "lhm_url": "auto", "presentmon_path": ""}
    mgr = backend.Manager(cfg)
    log = SensorLog()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                log.add(mgr.read_all())
            except Exception:
                pass
            stop.wait(1.0)
    threading.Thread(target=pump, daemon=True).start()
    results = {}
    for name in (TESTS if which == "all" else (which,)):
        ctl = Ctl(progress=lambda f, tx, n=name: print("\r%s %3d%% %-22s" % (n, f * 100, tx), end="", flush=True),
                  sensors=log)
        t = time.time()
        try:
            r = RUNNERS[name](ctl)
        except Cancelled:
            print("\ncancelled")
            break
        print("\r%s done in %.1fs%s" % (name, time.time() - t, " " * 30))
        results[name] = r
        print(json.dumps(r, indent=1, default=str))
    stop.set()
    mgr.closed.set()
    mgr.close_source()
    good = {k: v for k, v in results.items() if v.get("score")}
    if good:
        print("OVERALL %.0f  (%s)" % (overall(good), ", ".join("%s %.0f" % (k, v["score"]) for k, v in good.items())))
    return results


if __name__ == "__main__":
    mp.freeze_support()
    _headless(sys.argv[1] if len(sys.argv) > 1 else "all")
