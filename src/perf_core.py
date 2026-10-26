"""metriQ benchmark engine (no GUI): CPU, RAM, SSD and GPU tests, stress / stability test, sensor statistics, scores,
history, export.

    python perf_core.py [cpu|ram|ssd|gpu|all] [--detailed]     headless benchmark (METRIQ_BENCH_QUICK=1: very short)
    python perf_core.py stress [cpu,ram,gpu,ssd|all] [secs]    headless stress test for `secs` seconds (default 10)

Everything runs in a SEPARATE PROCESS (BenchJob -> bench_main) so the Tk thread of the panel never competes with the
load: the child starts the CPU workers (more processes), RAM threads, the SSD file I/O, the GPU helper and the sensor
sampling, and streams small messages back through a multiprocessing.Queue. Messages (tuples):
    ("start", test) ("p", test, fraction, text) ("s", test, sample) ("done", test, result) ("cancel", test)
    ("err", test, text) ("live", dict)  (stress test, once a second)  ("end",)
A test is  run_<name>(ctl) -> result dict  and can be cancelled at any time (ctl.check() raises Cancelled).
Scores are normalised against the dev PC (see REF) and are only comparable inside metriQ - they are NOT a
replacement for 3DMark, Cinebench & co.
"""
import contextlib
import csv
import ctypes
import hashlib
import io
import json
import math
import multiprocessing as mp
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib

import config

TESTS = ("cpu", "ram", "ssd", "gpu")
MODES = ("quick", "detailed")
MIN_FREE_GB = 4                      # SSD test: skip below this much free space on the temp drive
HISTORY_MAX = 60
GPU_EXE = "GpuBench.exe"
CPU_LIMIT_DEFAULT, GPU_LIMIT_DEFAULT = 95, 90          # stress test: auto-stop temperatures (deg C)
LIMIT_MIN, LIMIT_MAX = 60, 110
SENSOR_LOST_S = 8.0                  # stress test: no temperature reading for this long (after having had one) -> stop
# rough run time per test in seconds (used by the confirmation dialog)
DURATION = {"quick": {"cpu": 60, "ram": 16, "ssd": 30, "gpu": 60},
            "detailed": {"cpu": 190, "ram": 60, "ssd": 105, "gpu": 185}}

# ---------------------------------------------------------------------------------------------- reference
# Scores are normalised so that the metriQ developer's PC (AMD Ryzen 7 9700X, 32 GB DDR5, NVMe SSD, GeForce RTX 5070 Ti,
# Python 3.13, 2026-10-27) gets about 1000 points per test. Units: CPU kernel iterations / s, GB/s, ns, MB/s, GFLOPS.
# Scores are only comparable inside metriQ (same version).
REF = {
    "cpu": {"single_int": 5.52e6, "single_float": 14.95e6, "multi_int": 48.9e6, "multi_float": 125.1e6},
    "ram": {"read": 60.5, "write": 82.2, "copy": 29.8, "latency": 44.2},
    "ssd": {"seq_read": 4700.0, "seq_write": 5220.0, "rnd_read": 72.6, "rnd_write": 225.5},
    "gpu": {"gflops": 37100.0, "bandwidth": 970.0},
}
LOWER_IS_BETTER = {("ram", "latency")}


class Cancelled(Exception):
    pass


def quick():
    """Developer switch METRIQ_BENCH_QUICK=1: every duration x 0.1 (the Quick/Detailed mode is a separate thing)."""
    return os.environ.get("METRIQ_BENCH_QUICK", "") not in ("", "0")


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


# ---------------------------------------------------------------------------------------------- statistics
def agg(vals, how="mean"):
    """(centre, +-percent) of repeated measurements: mean (or median) and half the range relative to the centre."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return 0.0, None
    s = sorted(vals)
    c = s[len(s) // 2] if (how == "median" and len(s) % 2) else \
        ((s[len(s) // 2 - 1] + s[len(s) // 2]) / 2 if how == "median" else sum(s) / len(s))
    pm = None
    if len(s) > 1 and c:
        pm = (s[-1] - s[0]) / 2.0 / abs(c) * 100.0
    return c, pm


def _drift(vals):
    """Change in percent between the first and the last fifth of a series (None when too short / empty)."""
    v = [x for x in vals if x is not None]
    if len(v) < 5:
        return None
    n = max(1, len(v) // 5)
    a, b = sum(v[:n]) / n, sum(v[-n:]) / n
    return (b / a - 1.0) * 100.0 if a else None


# ---------------------------------------------------------------------------------------------- sensors
SCALARS = ("cpu_temp", "cpu_power", "cpu_clock", "gpu_temp", "gpu_hot", "gpu_power", "gpu_clock", "ram_clock",
           "ssd_temp", "drive_read", "drive_write")


def _num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _kind(sen):
    """Which part of the PC a sensor belongs to (from HWiNFO's / LHM's / the built-in source's sensor names)."""
    if sen.startswith(("ddr", "dimm")):
        return "dimm"
    if sen.startswith("drive:"):
        return "drive"
    if sen.startswith("s.m.a.r.t") or "nvme" in sen or " ssd" in sen or sen.startswith("ssd"):
        return "smart"
    if sen.startswith("memory timings"):
        return "mem"
    if "gpu" in sen:
        return "gpu"
    if sen.startswith("cpu"):
        return "cpu"
    return "other"


_CPU_TEMP_KEYS = ("cpu package", "tctl/tdie", "cpu die", "core max", "tdie", "tctl", "cpu temperature", "package",
                  "core average")
_GPU_TEMP = ("gpu temperature", "gpu core temperature")
_GPU_HOT = ("gpu hot spot temperature", "gpu hotspot", "gpu hot spot", "gpu hotspot temperature")


def _rate_mbs(v, unit):
    u = (unit or "").lower()
    if u.startswith("kb"):
        return v / 1024.0
    if u.startswith("gb"):
        return v * 1024.0
    return v


def _dimm_name(sen_o):
    m = re.search(r"CHANNEL (\w+)/DIMM (\d+)", sen_o, re.I)
    if m:
        return m.group(1) + m.group(2)
    m = re.search(r"\[#(\d+)\]", sen_o)
    return "#" + m.group(1) if m else sen_o[:12]


def extract(rd, drive=""):
    """Pull the readings a benchmark cares about from a backend reading list -> sample dict (None = not available).

    cpu_*: CPU package temperature / power, average core clock.   gpu_*: the discrete GPU (iGPU only if there is none).
    ram_clock: memory clock.  dimm_temps: SPD hub temperature of every DIMM.  ssd_temp: drive temperature (of the
    drive `drive` = "C:" when given, else the hottest).  drive_read / drive_write: MB/s of that drive."""
    out = {k: None for k in SCALARS}
    out.update(throttle_cpu=False, throttle_cpu_name="", throttle_gpu=False, throttle_gpu_name="",
               dimm_temps=[], dimm_names=[])
    rd = rd or []
    has_dgpu = any((r.get("sensor") or "").lower().startswith("dgpu") for r in rd)
    cand = {k: [] for k in ("cpu_temp", "cpu_power", "cpu_clock", "core_clocks", "gpu_temp", "gpu_hot", "gpu_power",
                            "gpu_clock", "ram_clock")}
    dimms = {}
    smart = {}                                     # sensor -> (priority, value)
    rates = {"read": {}, "write": {}}              # sensor -> MB/s
    dl = ("[%s]" % drive.rstrip("\\/")).lower() if drive else ""
    for r in rd:
        v = _num(r.get("value"))
        if v is None:
            continue
        ty = r.get("type")
        sen_o = r.get("sensor") or ""
        sen, lab_o = sen_o.lower(), r.get("label") or ""
        lab, unit = lab_o.lower(), (r.get("unit") or "").lower()
        kind = _kind(sen)
        if kind == "gpu":
            if has_dgpu and not sen.startswith("dgpu"):
                continue
            if ty == 1 and v > 0:
                if lab in _GPU_TEMP:
                    cand["gpu_temp"].append((_GPU_TEMP.index(lab), v))
                elif lab in _GPU_HOT:
                    cand["gpu_hot"].append((0, v))
            elif ty == 5 and v >= 0:
                if lab == "gpu power":
                    cand["gpu_power"].append((0, v))
                elif lab in ("gpu board power", "total board power", "gpu chip power", "gpu total board power"):
                    cand["gpu_power"].append((1, v))
            elif ty == 6 and v > 0 and lab in ("gpu clock", "gpu core clock"):
                cand["gpu_clock"].append((0 if lab == "gpu clock" else 1, v))
            elif ty == 8 and v >= 1 and unit != "%" and "thermal" in lab and ("limit" in lab or "throttl" in lab):
                out["throttle_gpu"], out["throttle_gpu_name"] = True, lab_o
        elif kind == "dimm":
            if ty == 1 and v > 0 and ("spd hub" in lab or lab in ("temperature", "dimm temperature")):
                dimms[sen_o] = v
        elif kind == "smart":
            if ty == 1 and 0 < v < 150:
                for i, key in enumerate(("drive temperature", "composite temperature", "temperature")):
                    if lab == key or (i == 0 and lab.startswith("drive temperature") and lab[-1:] != "2"
                                      and lab[-1:] != "3"):
                        old = smart.get(sen_o)
                        if old is None or i < old[0]:
                            smart[sen_o] = (i, v)
                        break
        elif kind == "drive":
            if lab in ("read rate", "write rate") and v >= 0:
                rates["read" if lab == "read rate" else "write"][sen_o] = _rate_mbs(v, unit)
        elif kind == "mem":
            if ty == 6 and v > 0 and lab == "memory clock":
                cand["ram_clock"].append((0, v))
        else:                                       # CPU (and motherboard sensors as a fallback)
            off = 0 if kind == "cpu" else 20
            if ty == 1 and v > 0:
                for i, key in enumerate(_CPU_TEMP_KEYS):
                    if key in lab:
                        cand["cpu_temp"].append((i + off, v))
                        break
            elif ty == 5 and v >= 0:
                for i, key in enumerate(("cpu package power", "package power", "ppt", "cpu power")):
                    if key in lab:
                        cand["cpu_power"].append((i + off, v))
                        break
            elif ty == 6 and v > 0:
                if not any(x in lab for x in ("memory", "bus", "uncore", "fabric")):
                    if "core clocks (avg)" in lab or "average effective clock" in lab or "average core clock" in lab:
                        cand["cpu_clock"].append((off, v))
                    elif re.match(r"^core\s*\d+.*clock", lab) and "effective" not in lab:
                        cand["core_clocks"].append((off, v))
            elif ty == 8 and kind == "cpu" and v >= 1 and unit != "%" and ("throttl" in lab or "prochot" in lab):
                out["throttle_cpu"], out["throttle_cpu_name"] = True, lab_o
    for key in ("cpu_temp", "cpu_power", "cpu_clock", "gpu_temp", "gpu_hot", "gpu_power", "gpu_clock", "ram_clock"):
        c = cand[key]
        if c:
            best = min(p for p, _ in c)
            vals = [v for p, v in c if p == best]
            out[key] = sum(vals) / len(vals) if key in ("cpu_clock", "ram_clock", "gpu_clock") else max(vals)
    if out["cpu_clock"] is None and cand["core_clocks"]:
        vals = [v for _, v in cand["core_clocks"]]
        out["cpu_clock"] = sum(vals) / len(vals)
    if dimms:
        names = sorted(dimms)
        out["dimm_temps"] = [dimms[n] for n in names]
        out["dimm_names"] = [_dimm_name(n) for n in names]
    if smart:
        mine = [v for s, (_, v) in smart.items() if dl and dl in s.lower()]
        out["ssd_temp"] = max(mine) if mine else max(v for _, v in smart.values())
    for key, col in (("read", "drive_read"), ("write", "drive_write")):
        d = rates[key]
        if d:
            mine = [v for s, v in d.items() if dl and dl in s.lower()]
            out[col] = max(mine) if mine else max(d.values())
    return out


def has_temp(s):
    return any(s.get(k) is not None for k in ("cpu_temp", "gpu_temp", "ssd_temp")) or bool(s.get("dimm_temps"))


class SensorLog:
    """Collects extract() samples; add(readings) is called about once per second by whoever owns the backend."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
        self.on = False
        self.last = {}
        self.drive = ""
        self.ok_t = 0.0                 # time of the last sample that had a temperature
        self.ever_temp = False

    def start(self, drive=""):
        with self.lock:
            self.rows, self.on, self.drive = [], True, drive

    def add(self, rd):
        s = extract(rd, self.drive)
        s["t"] = time.time()
        with self.lock:
            self.last = s
            if has_temp(s):
                self.ok_t, self.ever_temp = s["t"], True
            if self.on:
                self.rows.append(s)
        return s

    def since(self, t0):
        with self.lock:
            return [r for r in self.rows if r["t"] >= t0]

    def stop(self, test=None):
        with self.lock:
            self.on = False
            rows = list(self.rows)
        return summarize(test, rows)


def _stat(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals), max(vals), min(vals)) if vals else (None, None, None)


# which sample keys describe which test
_VIEW = {
    "cpu": dict(temp="cpu_temp", clock="cpu_clock", power="cpu_power", thr="cpu"),
    "ram": dict(clock="ram_clock", power="cpu_power"),
    "ssd": dict(temp="ssd_temp"),
    "gpu": dict(temp="gpu_temp", hot="gpu_hot", clock="gpu_clock", power="gpu_power", thr="gpu"),
}


def summarize(test, rows):
    """Statistics of the sensors that matter for `test` (cpu / ram / ssd / gpu):
    temp_avg/max, hot_max, clock_avg/min/max, power_avg/max, throttled (+notes), dimm_max (RAM), read/write_peak (SSD)."""
    v = _VIEW.get(test, {})
    s = {"samples": len(rows)}
    if v.get("temp"):
        s["temp_avg"], s["temp_max"], _ = _stat([r[v["temp"]] for r in rows])
    if v.get("hot"):
        _, s["hot_max"], _ = _stat([r[v["hot"]] for r in rows])
    if v.get("clock"):
        s["clock_avg"], s["clock_max"], s["clock_min"] = _stat([r[v["clock"]] for r in rows])
    if v.get("power"):
        s["power_avg"], s["power_max"], _ = _stat([r[v["power"]] for r in rows])
    notes = []
    thr = v.get("thr")
    if thr:
        flagged = [r for r in rows if r.get("throttle_" + thr)]
        if flagged:
            notes.append(flagged[0].get("throttle_%s_name" % thr) or "thermal limit")
        series = [r[v["clock"]] for r in rows if r.get(v["clock"])]
        if len(series) >= 9:                      # clock sag: last third clearly slower than the first while hot
            n = len(series) // 3
            a, b = sum(series[:n]) / n, sum(series[-n:]) / n
            hot = s.get("temp_max") or 0
            if a > 0 and b < 0.88 * a and hot >= (80 if thr == "cpu" else 75):
                notes.append("%s clock dropped %.0f %%" % (thr.upper(), 100 * (1 - b / a)))
        s["throttled"] = bool(notes) if rows and any(r.get(v["temp"]) for r in rows) else None
    else:
        s["throttled"] = None
    s["throttle_notes"] = notes
    if test == "ram":
        best = {}
        for r in rows:
            for n, t in zip(r.get("dimm_names") or [], r.get("dimm_temps") or []):
                best[n] = max(best.get(n, 0.0), t)
        s["dimm_names"], s["dimm_max"] = list(best), list(best.values())
        s["temp_max"] = max(best.values()) if best else None
    if test == "ssd":
        s["read_peak"] = _stat([r["drive_read"] for r in rows])[1]
        s["write_peak"] = _stat([r["drive_write"] for r in rows])[1]
    return s


def make_series(rows, keys, t0, maxpts=90):
    """Down-sampled time series {t: seconds since t0, key: [...]} of the rows from t0 on (None = no data)."""
    rs = [r for r in rows if r["t"] >= t0]
    if not rs:
        return {}
    step = max(1, math.ceil(len(rs) / float(maxpts)))
    out = {"t": []}
    out.update({k: [] for k in keys})
    for i in range(0, len(rs), step):
        grp = rs[i:i + step]
        out["t"].append(round(grp[-1]["t"] - t0, 1))
        for k in keys:
            vals = [g[k] for g in grp if g.get(k) is not None]
            out[k].append(round(sum(vals) / len(vals), 1) if vals else None)
    return out


# ---------------------------------------------------------------------------------------------- control
class Ctl:
    """What a test gets: cancel flag, progress callback, sensor log, mode."""

    def __init__(self, progress=None, sensors=None, stop=None, mode="quick", emit=None):
        self.stop = stop if stop is not None else threading.Event()
        self._progress = progress
        self.emit = emit or (lambda *m: None)
        self.sensors = sensors or SensorLog()
        self.mode = mode if mode in MODES else "quick"
        self.detailed = self.mode == "detailed"
        self.k = 0.1 if quick() else 1.0               # developer switch: shorten every duration

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


def _finish(test, ctl, metrics, t0, extra=None, spread=None, rows_from=None):
    sens = ctl.sensors.stop(test)
    score, parts = score_of(test, metrics)
    res = {"test": test, "ts": time.time(), "duration": time.time() - t0, "metrics": metrics, "score": score,
           "parts": parts, "sensors": sens, "quick": quick(), "mode": ctl.mode}
    if spread:
        res["spread"] = {k: v for k, v in spread.items() if v is not None}
    if extra:
        res.update(extra)
    return res


def _lower_priority():
    """Below-normal priority for the whole benchmark process tree: the panel (normal priority) always gets a CPU
    slot, so its window stays smooth even while every core is loaded."""
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:
        pass


def fault_set():
    """Developer switch for testing the error path of the stress test: METRIQ_FAULT=cpu,ram,gpu,ssd."""
    return set(x.strip() for x in os.environ.get("METRIQ_FAULT", "").lower().split(",") if x.strip())


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
_VBUF = bytes((i * 131 + 7) & 255 for i in range(1 << 16)) * 16        # 1 MiB of known data (stress test)


def _stress_loop(conn, stop):
    """Stress worker: deterministic integer, float and hash computations with known answers. Any difference from
    the answer (computed once at the start) is a calculation error -> reported at once."""
    fault = "cpu" in fault_set()
    exp_i = _k_int(12345, 20000)
    exp_f = _k_float(1.0, 0.5, 20000)
    exp_h = hashlib.sha256(_VBUF).digest()
    exp_c = zlib.crc32(_VBUF)
    checks = errors = 0
    t_start = last = time.perf_counter()
    first_err = ""
    while not stop.is_set():
        r_i = _k_int(12345, 20000)
        r_f = _k_float(1.0, 0.5, 20000)
        r_h = hashlib.sha256(_VBUF).digest()
        r_c = zlib.crc32(_VBUF)
        if fault and time.perf_counter() - t_start > 2.0 and (checks % 7) == 0:
            r_i ^= 1                                     # injected failure (METRIQ_FAULT=cpu)
        for ok, what in ((r_i == exp_i, "integer"), (r_f == exp_f, "float"), (r_h == exp_h, "sha256"),
                         (r_c == exp_c, "crc32")):
            checks += 1
            if not ok:
                errors += 1
                first_err = first_err or what
        now = time.perf_counter()
        if now - last >= 1.0 or errors:
            last = now
            try:
                conn.send(("s", checks, errors, first_err))
            except (OSError, ValueError):
                return
            if errors:
                break
    with contextlib.suppress(OSError, ValueError):
        conn.send(("s", checks, errors, first_err))


def _worker(conn, stop):
    """Child process: runs ("int"|"float"|"stress", seconds, tick) jobs. Benchmark jobs answer ("t", n, dt) every
    `tick` seconds (when > 0) and ("e", iterations, seconds) at the end; the stress job reports ("s", checks, errors,
    what) and ends when `stop` is set."""
    _lower_priority()
    x, a, b = 12345, 1.0, 0.5
    while True:
        try:
            job = conn.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        kind, secs, tick = job
        if kind == "stress":
            _stress_loop(conn, stop)
            with contextlib.suppress(OSError, ValueError):
                conn.send(("e", 0, 0.0))
            continue
        t0 = last_t = time.perf_counter()
        end, n, last_n = t0 + secs, 0, 0
        while not stop.is_set():
            if kind == "int":
                x = _k_int(x, CHUNK)
            else:
                a, b = _k_float(a, b, CHUNK)
            n += CHUNK
            now = time.perf_counter()
            if tick and now - last_t >= tick:
                try:
                    conn.send(("t", n - last_n, now - last_t))
                except (OSError, ValueError):
                    return
                last_t, last_n = now, n
            if now >= end:
                break
        try:
            conn.send(("e", n, time.perf_counter() - t0))
        except (OSError, ValueError):
            return


def _spawn_workers(mpctx, nproc, stop):
    procs, conns = [], []
    for _ in range(nproc):
        a, b = mpctx.Pipe()
        p = mpctx.Process(target=_worker, args=(b, stop), daemon=True)
        p.start()
        procs.append(p)
        conns.append(a)
    return procs, conns


def _stop_workers(procs, conns, stop):
    stop.set()
    for c in conns:
        with contextlib.suppress(Exception):
            c.send(None)
    for p in procs:
        p.join(1.5)
        if p.is_alive():
            p.terminate()


def _cpu_phase(ctl, use, kind, secs, tick, prog):
    """Run one job on the connections `use`; -> (rates per worker, [ticks per worker])."""
    for c in use:
        c.send((kind, secs, tick))
    pending = set(range(len(use)))
    res, ticks = [0.0] * len(use), [[] for _ in use]
    end = time.time() + secs
    t_start = time.time()
    while pending:
        ctl.check()
        for i in list(pending):
            c = use[i]
            try:
                while c.poll(0):
                    m = c.recv()
                    if m[0] == "t":
                        ticks[i].append((m[1], m[2]))
                    else:
                        res[i] = m[1] / m[2] if m[2] > 0 else 0.0
                        pending.discard(i)
                        break
            except (EOFError, OSError):
                raise RuntimeError("CPU worker died")
        prog(min(secs, time.time() - t_start))
        if time.time() > end + 20:
            raise RuntimeError("CPU worker did not answer")
        if pending:
            time.sleep(0.05)
    return res, ticks


def run_cpu(ctl):
    t0 = time.time()
    k, det = ctl.k, ctl.detailed
    if det:
        rounds, per = 3, (("single", "int", 6), ("single", "float", 6), ("multi", "int", 10), ("multi", "float", 10))
        sustain = 90 * k
    else:
        rounds, per = 1, (("single", "int", 8), ("single", "float", 8), ("multi", "int", 22), ("multi", "float", 22))
        sustain = 0
    plan = [(m, kd, s * k, False) for _ in range(rounds) for (m, kd, s) in per]
    if sustain:
        plan.append(("multi", "float", sustain, True))
    total = sum(p[2] for p in plan)
    nproc = max(1, os.cpu_count() or 1)
    mpctx = mp.get_context("spawn")
    wstop = mpctx.Event()
    runs = {}
    ctl.progress(0.0, "start")
    ctl.sensors.start()
    procs, conns = [], []
    extra = {"threads": nproc}
    try:
        procs, conns = _spawn_workers(mpctx, nproc, wstop)
        done = 0.0
        for mode, kind, secs, is_sustain in plan:
            ctl.check()
            use = conns[:1] if mode == "single" else conns
            label = ("sustained " if is_sustain else "") + "%s %s" % (mode, kind)
            t_phase = time.time()
            rates, ticks = _cpu_phase(ctl, use, kind, secs, 1.0 if is_sustain else 0.0,
                                      lambda el, d=done: ctl.progress((d + el) / total, label))
            if is_sustain:
                n = min((len(t) for t in ticks), default=0)
                perf = [sum(t[i][0] / t[i][1] for t in ticks if t[i][1] > 0) for i in range(n)]
                rows = ctl.sensors.since(t_phase)
                ser = make_series(rows, ("cpu_clock", "cpu_temp", "cpu_power"), t_phase)
                if perf:                                  # down-sample the throughput like the sensor series
                    step = max(1, math.ceil(len(perf) / 90.0))
                    ser["perf"] = [round(sum(perf[i:i + step]) / len(perf[i:i + step]) / 1e6, 3)
                                   for i in range(0, len(perf), step)]
                ssum = summarize("cpu", rows)
                extra["sustain"] = {
                    "secs": round(secs, 1), "series": ser, "perf_drift": _drift(perf),
                    "clock_drift": _drift(ser.get("cpu_clock") or []), "temp_start": (ser.get("cpu_temp") or [None])[0],
                    "temp_end": (ser.get("cpu_temp") or [None])[-1], "temp_max": ssum.get("temp_max"),
                    "clock_min": ssum.get("clock_min"), "clock_max": ssum.get("clock_max"),
                    "throttled": ssum.get("throttled"), "notes": ssum.get("throttle_notes")}
            else:
                runs.setdefault("%s_%s" % (mode, kind), []).append(sum(rates))
            done += secs
        ctl.progress(1.0, "done")
        metrics, spread = {}, {}
        for name, vals in runs.items():
            metrics[name], spread[name] = agg(vals, "median")
        if det:
            extra["runs"] = {k2: [round(x, 1) for x in v] for k2, v in runs.items()}
        res = _finish("cpu", ctl, metrics, t0, extra, spread)
        mt, st = metrics["multi_int"] + metrics["multi_float"], metrics["single_int"] + metrics["single_float"]
        res["scaling"] = mt / st if st else None
        return res
    finally:
        ctl.sensors.stop()
        _stop_workers(procs, conns, wstop)


# ---------------------------------------------------------------------------------------------- RAM
def _crt():
    lib = ctypes.cdll.msvcrt
    lib.memchr.restype = ctypes.c_void_p
    lib.memchr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    lib.memset.restype = ctypes.c_void_p
    lib.memset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    lib.memmove.restype = ctypes.c_void_p
    lib.memmove.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.memcmp.restype = ctypes.c_int
    lib.memcmp.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    return lib


class _MS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                ("ap", ctypes.c_ulonglong), ("tf", ctypes.c_ulonglong), ("af", ctypes.c_ulonglong),
                ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ex", ctypes.c_ulonglong)]


def _mem_status():
    m = _MS()
    m.l = ctypes.sizeof(_MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m


def _free_ram():
    return _mem_status().ap


def _total_ram():
    return _mem_status().tp


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


def _read_bw(ctl, crt, nth, size, secs):
    """Aggregate read bandwidth (GB/s) with nth threads that each scan their own buffer of `size` bytes."""
    bufs = []
    for _ in range(nth):
        b = (ctypes.c_char * size)()
        crt.memset(ctypes.addressof(b), 1, size)
        bufs.append(b)
    addrs = [ctypes.addressof(b) for b in bufs]

    def reader(i, end):
        n, a = 0, addrs[i]
        while time.perf_counter() < end:
            crt.memchr(a, 0xFF, size)
            n += size
        return n
    nbytes, dt = _threaded(ctl, secs, reader, nth)
    return nbytes / dt / 1e9


def run_ram(ctl):
    t0 = time.time()
    k, det = ctl.k, ctl.detailed
    crt = _crt()
    nth = max(1, min(4, os.cpu_count() or 1))
    per = 128 << 20
    while per > (16 << 20) and per * nth * 2 > _free_ram() * 0.5:
        per //= 2
    rounds = 3 if det else 1
    sizes = [128 << 10, 1 << 20, 8 << 20, 64 << 20, 256 << 20] if det else []
    sizes = [s for s in sizes if s * nth <= _free_ram() * 0.4]
    t_bw, t_curve, t_lat = 9.0 * rounds * k, 1.6 * len(sizes) * k, (4.0 * rounds) * k
    total = t_bw + t_curve + t_lat
    ctl.progress(0.0, "alloc")
    ctl.sensors.start()
    vals = {"read": [], "write": [], "copy": [], "latency": []}
    extra = {}
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

        done = 0.0
        for rnd in range(rounds):
            for name, fn in (("read", reader), ("write", writer), ("copy", copier)):
                secs = 3.0 * k
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
                vals[name].append(nbytes / dt / 1e9)               # copy: bytes copied / s
                done += secs
        del bufs
        if sizes:                                                   # cache <-> DRAM curve (read bandwidth per block size)
            curve = []
            for s in sizes:
                ctl.check()
                ctl.progress(done / total, "RAM %d KB" % (s >> 10))
                curve.append([s, _read_bw(ctl, crt, nth, s, 1.6 * k)])
                done += 1.6 * k
            extra["curve"] = curve
        lat_secs = max(0.8, 4.0 * k)
        for rnd in range(rounds):
            vals["latency"].append(_latency(ctl, lat_secs, lambda f, b=done: ctl.progress((b + lat_secs * f) / total,
                                                                                         "RAM latency")))
            done += 4.0 * k
        ctl.progress(1.0, "done")
        metrics, spread = {}, {}
        for name, v in vals.items():
            metrics[name], spread[name] = agg(v, "mean")
        extra.update(threads=nth, buffer_mb=per * nth * 2 >> 20)
        return _finish("ram", ctl, metrics, t0, extra, spread if det else None)
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
F_NO_BUFFERING, F_WRITE_THROUGH, F_DELETE_ON_CLOSE, F_OVERLAPPED = 0x20000000, 0x80000000, 0x04000000, 0x40000000
ERROR_IO_PENDING = 997


class _OVL(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", ctypes.c_uint32),
                ("OffsetHigh", ctypes.c_uint32), ("hEvent", ctypes.c_void_p)]


def free_space_gb(path=None):
    try:
        return shutil.disk_usage(path or tempfile.gettempdir()).free / 1e9
    except OSError:
        return 0.0


def ssd_file_gb(mode, free=None):
    """Size of the temporary test file in GB: Quick 2 GB, Detailed 8 GB when there is room (4 / 2 GB otherwise)."""
    free = free_space_gb() if free is None else free
    if quick():
        return 0.5
    if mode == "detailed":
        return 8 if free >= 20 else (4 if free >= 12 else 2)
    return 2


class _Disk:
    """Unbuffered temporary file with overlapped I/O: run() keeps `qd` requests in flight from one thread."""

    def __init__(self, path, blk_max=2 << 20, nbuf=32):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k
        k.CreateFileW.restype = ctypes.c_void_p
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                  ctypes.c_uint32, ctypes.c_void_p]
        k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        k.WriteFile.argtypes = k.ReadFile.argtypes
        k.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                                          ctypes.c_int]
        k.CreateEventW.restype = ctypes.c_void_p
        k.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        k.WaitForMultipleObjects.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        k.VirtualAlloc.restype = ctypes.c_void_p
        k.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
        k.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.ResetEvent.argtypes = [ctypes.c_void_p]
        self.path, self.blk_max, self.nbuf = path, blk_max, nbuf
        invalid = ctypes.c_void_p(-1).value
        self.cached = False
        self.h = k.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, CREATE_ALWAYS,
                               F_NO_BUFFERING | F_WRITE_THROUGH | F_DELETE_ON_CLOSE | F_OVERLAPPED, None)
        if self.h in (None, invalid):
            self.cached = True                  # e.g. a RAM disk: fall back to write-through only (numbers then too high)
            self.h = k.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, CREATE_ALWAYS,
                                   F_WRITE_THROUGH | F_DELETE_ON_CLOSE | F_OVERLAPPED, None)
        if self.h in (None, invalid):
            raise OSError("cannot create %s (error %d)" % (path, ctypes.get_last_error()))
        self.base = k.VirtualAlloc(None, blk_max * nbuf, 0x3000, 4)
        if not self.base:
            k.CloseHandle(self.h)
            raise MemoryError("VirtualAlloc")
        self.events = [k.CreateEventW(None, 1, 0, None) for _ in range(nbuf)]
        chunk = os.urandom(blk_max)
        ctypes.memmove(self.base, chunk, blk_max)
        for i in range(1, nbuf):
            ctypes.memmove(self.base + i * blk_max, chunk, blk_max)
        self.chunk = chunk

    def buf(self, i):
        return self.base + i * self.blk_max

    def close(self):
        k = self.k
        with contextlib.suppress(Exception):
            k.CancelIoEx(self.h, None)
        for e in self.events:
            k.CloseHandle(e)
        k.VirtualFree(self.base, 0, 0x8000)
        k.CloseHandle(self.h)                        # DELETE_ON_CLOSE removes the file
        with contextlib.suppress(OSError):
            os.remove(self.path)

    def run(self, write, step, qd, secs, size, rnd, ctl=None, prog=None, fill=False, fill_cap=20.0):
        """Keep `qd` requests of `step` bytes in flight for about `secs` seconds -> (bytes/s, ops/s). fill: do not stop
        before `size` bytes were written (sequential write that creates the file)."""
        k = self.k
        fn = k.WriteFile if write else k.ReadFile
        blocks = max(1, size // step)
        ovs = [_OVL() for _ in range(qd)]
        nb = ctypes.c_uint32(0)
        arr = (ctypes.c_void_p * qd)(*self.events[:qd])
        state = {"n": 0}                              # next block number

        def nxt():
            n = state["n"]
            state["n"] = n + 1
            return (random.randrange(blocks) if rnd else n % blocks) * step

        def submit(i):
            ov = ovs[i]
            off = nxt()
            ov.Internal = ov.InternalHigh = 0
            ov.Offset, ov.OffsetHigh, ov.hEvent = off & 0xFFFFFFFF, off >> 32, self.events[i]
            if not fn(self.h, self.buf(i), step, None, ctypes.byref(ov)):
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    raise OSError("I/O error %d at %d" % (ctypes.get_last_error(), off))

        t_start = time.perf_counter()
        end_t = t_start + secs
        flight = set()
        nops = 0
        try:
            for i in range(qd):
                submit(i)
                flight.add(i)
            while flight:
                if qd == 1:
                    idx = 0
                    k.GetOverlappedResult(self.h, ctypes.byref(ovs[0]), ctypes.byref(nb), 1)
                else:
                    idx = k.WaitForMultipleObjects(qd, arr, 0, 20000)
                    if idx >= qd:
                        raise OSError("I/O wait failed (%d)" % idx)
                    if not k.GetOverlappedResult(self.h, ctypes.byref(ovs[idx]), ctypes.byref(nb), 0):
                        raise OSError("I/O error %d" % ctypes.get_last_error())
                    # the event stays signalled until the next request on this slot starts: drop it from the wait set
                nops += 1
                now = time.perf_counter()
                if (nops & 15) == 0:
                    if ctl:
                        ctl.check()
                    if prog:
                        prog(min(secs, now - t_start))
                more = now < end_t or (fill and nops * step < size and now < end_t + fill_cap)
                if more:
                    submit(idx)
                else:
                    flight.discard(idx)
                    k.ResetEvent(self.events[idx])          # manual-reset event: do not wake the next wait again
        finally:
            for i in list(flight):                     # drain what is still pending (cancel / error)
                with contextlib.suppress(Exception):
                    k.CancelIoEx(self.h, ctypes.byref(ovs[i]))
                    k.GetOverlappedResult(self.h, ctypes.byref(ovs[i]), ctypes.byref(nb), 1)
        dt = max(1e-6, time.perf_counter() - t_start)
        return nops * step / dt, nops / dt


def ssd_tmp_dir():
    return tempfile.gettempdir()


def run_ssd(ctl):
    t0 = time.time()
    det = ctl.detailed
    kk = 0.2 if quick() else 1.0
    tmpdir = ssd_tmp_dir()
    free = free_space_gb(tmpdir)
    drive = os.path.splitdrive(tmpdir)[0]
    if free < MIN_FREE_GB:
        return {"test": "ssd", "ts": time.time(), "skipped": "only %.1f GB free on %s (need %d GB)" %
                (free, drive or tmpdir, MIN_FREE_GB)}
    size = int(ssd_file_gb(ctl.mode, free) * (1 << 30))
    blk = 2 << 20
    path = os.path.join(tmpdir, "metriq_bench_%d.tmp" % os.getpid())
    disk = _Disk(path)
    ctl.sensors.start(drive)
    ctl.progress(0.0, "SSD")
    try:
        rounds = 3 if det else 1
        ph = (6.0 if det else 7.0) * kk
        scored = (("seq_write", True, blk, False), ("seq_read", False, blk, False),
                  ("rnd_read", False, 4096, True), ("rnd_write", True, 4096, True))
        extras = (("seq_read_q8", False, 1 << 20, 8, False), ("seq_write_q8", True, 1 << 20, 8, False),
                  ("rnd_read_q4", False, 4096, 4, True), ("rnd_read_q32", False, 4096, 32, True)) if det else ()
        total = ph * (rounds * len(scored) + len(extras))
        done = [0.0]
        vals, iops = {n: [] for n, *_ in scored}, {}
        extra_v = {}

        def phase(name, write, step, qd, rnd, fill=False):
            label = "SSD " + name
            b = done[0]
            bps, ops = disk.run(write, step, qd, ph, size, rnd, ctl, lambda el: ctl.progress((b + el) / total, label),
                                fill=fill, fill_cap=20 * kk if size <= (2 << 30) else 60.0)
            done[0] += ph
            return bps / 1e6, ops

        for rnd in range(rounds):
            for name, write, step, r in scored:
                mbs, ops = phase(name, write, step, 1, r, fill=(name == "seq_write" and rnd == 0))
                vals[name].append(mbs)
                if r:
                    iops.setdefault(name + "_iops", []).append(ops)
        for name, write, step, qd, r in extras:
            mbs, ops = phase(name, write, step, qd, r)
            extra_v[name] = {"v": mbs, "iops": ops, "qd": qd}
        ctl.progress(1.0, "done")
        metrics, spread = {}, {}
        for name, v in vals.items():
            metrics[name], spread[name] = agg(v, "mean")
        for name, v in iops.items():
            metrics[name], _ = agg(v, "mean")
        extra = {"drive": drive, "file_mb": size >> 20, "cached": disk.cached}
        if extra_v:
            extra["extra"] = extra_v
        return _finish("ssd", ctl, metrics, t0, extra, spread if det else None)
    finally:
        ctl.sensors.stop()
        disk.close()


# ---------------------------------------------------------------------------------------------- GPU
def gpu_exe():
    for d in (config.APP_DIR, os.path.join(config.APP_DIR, "..", "gpu", "build"), os.path.join(config.APP_DIR, "gpu")):
        p = os.path.normpath(os.path.join(d, GPU_EXE))
        if os.path.isfile(p):
            return p
    return None


def _gpu_once(ctl, exe, secs, prog):
    """One GpuBench.exe run -> result dict, or a string (why it failed)."""
    proc = subprocess.Popen([exe, "--seconds", str(secs)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=0x08000000)               # CREATE_NO_WINDOW
    state = {"result": None, "err": None}

    def reader():
        for raw in proc.stdout:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "progress" in d:
                prog(float(d["progress"]), str(d.get("phase", "")))
            elif "result" in d:
                state["result"] = d["result"]
            elif "error" in d:
                state["err"] = d["error"]
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    t0 = time.time()
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
            return "GPU helper failed: %s" % (err or proc.returncode)
        return r
    finally:
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(3)


def run_gpu(ctl):
    t0 = time.time()
    exe = gpu_exe()
    if not exe:
        return {"test": "gpu", "ts": time.time(), "skipped": "helper %s not found" % GPU_EXE}
    rounds = 3 if ctl.detailed else 1
    secs = 6 if quick() else 60
    ctl.sensors.start()
    ctl.progress(0.0, "GPU")
    vals = {"gflops": [], "bandwidth": []}
    info = {}
    try:
        for rnd in range(rounds):
            ctl.check()
            r = _gpu_once(ctl, exe, secs, lambda f, ph, rnd=rnd: ctl.progress((rnd + f) / rounds, "GPU " + ph))
            if isinstance(r, str):
                return {"test": "gpu", "ts": time.time(), "skipped": r}
            vals["gflops"].append(float(r["gflops"]))
            vals["bandwidth"].append(float(r["bandwidth_gbs"]))
            info = {"adapter": r.get("adapter", ""), "vram_mb": r.get("vram_mb")}
        ctl.progress(1.0, "done")
        metrics, spread = {}, {}
        for name, v in vals.items():
            metrics[name], spread[name] = agg(v, "mean")
        if ctl.detailed:
            rows = ctl.sensors.since(t0)
            ser = make_series(rows, ("gpu_clock", "gpu_temp", "gpu_power"), t0)
            ssum = summarize("gpu", rows)
            info["sustain"] = {"secs": round(time.time() - t0, 1), "series": ser,
                               "clock_drift": _drift(ser.get("gpu_clock") or []),
                               "temp_start": (ser.get("gpu_temp") or [None])[0],
                               "temp_end": (ser.get("gpu_temp") or [None])[-1], "temp_max": ssum.get("temp_max"),
                               "clock_min": ssum.get("clock_min"), "clock_max": ssum.get("clock_max"),
                               "throttled": ssum.get("throttled"), "notes": ssum.get("throttle_notes")}
            info["runs"] = {k: [round(x, 1) for x in v] for k, v in vals.items()}
        return _finish("gpu", ctl, metrics, t0, info, spread if ctl.detailed else None)
    finally:
        ctl.sensors.stop()


RUNNERS = {"cpu": run_cpu, "ram": run_ram, "ssd": run_ssd, "gpu": run_gpu}


# ---------------------------------------------------------------------------------------------- stress / stability test
STRESS_PARTS = ("cpu", "ram", "gpu", "ssd")


class _CpuPart:
    name = "cpu"

    def __init__(self, ctl):
        self.checks = self.errors = 0
        self.err_text = ""
        self.stopped = False
        mpctx = mp.get_context("spawn")
        self.wstop = mpctx.Event()
        self.procs, self.conns = _spawn_workers(mpctx, max(1, os.cpu_count() or 1), self.wstop)
        self.last = [(0, 0)] * len(self.conns)
        self.what = ""
        for c in self.conns:
            c.send(("stress", 0, 0))

    def poll(self):
        for i, c in enumerate(self.conns):
            try:
                while c.poll(0):
                    m = c.recv()
                    if m[0] == "s":
                        self.last[i] = (m[1], m[2])
                        self.what = self.what or m[3]
            except (EOFError, OSError):
                if not self.stopped:
                    self.err_text = self.err_text or "CPU worker died"
        self.checks = sum(a for a, _ in self.last)
        self.errors = sum(b for _, b in self.last)
        if self.errors:
            self.err_text = "calculation error (%s)" % (self.what or "?")

    def stop(self):
        self.stopped = True
        _stop_workers(self.procs, self.conns, self.wstop)


class _RamPart:
    name = "ram"
    CHUNK = 1 << 20

    def __init__(self, ctl):
        self.checks = self.errors = 0
        self.err_text = ""
        self.halt = threading.Event()
        self.crt = _crt()
        nth = max(1, min(4, os.cpu_count() or 1))
        per = 512 << 20
        while per > (16 << 20) and per * nth > _free_ram() * 0.4:
            per //= 2
        self.per, self.nth = per, nth
        self.bufs = [(ctypes.c_char * per)() for _ in range(nth)]
        for b in self.bufs:
            self.crt.memset(ctypes.addressof(b), 0, per)
        rnd = os.urandom(self.CHUNK)
        inv = bytes(x ^ 0xFF for x in rnd)
        inc = b"".join((i * 0x9E3779B97F4A7C15 & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
                       for i in range(self.CHUNK // 8))
        self.patterns = [bytes(self.CHUNK), b"\xff" * self.CHUNK, b"\xaa" * self.CHUNK, b"\x55" * self.CHUNK, rnd, inv,
                         inc]
        self.refs = [(ctypes.c_char * self.CHUNK).from_buffer_copy(p) for p in self.patterns]
        self.fault = "ram" in fault_set()
        self.t0 = time.time()
        self.passes = 0
        self.ths = [threading.Thread(target=self._run, args=(i,), daemon=True) for i in range(nth)]
        for th in self.ths:
            th.start()

    def _run(self, i):
        crt, ch = self.crt, self.CHUNK
        base = ctypes.addressof(self.bufs[i])
        n = self.per // ch
        p = i
        while not self.halt.is_set():
            ref = ctypes.addressof(self.refs[p % len(self.refs)])
            p += 1
            for c in range(n):
                crt.memmove(base + c * ch, ref, ch)
                if self.halt.is_set():
                    return
            if self.fault and i == 0 and time.time() - self.t0 > 2.0:
                crt.memset(base + 4097, 0x5A ^ (p & 0x7F), 1)               # injected bit flip (METRIQ_FAULT=ram)
            for c in range(n):
                if crt.memcmp(base + c * ch, ref, ch) != 0:
                    self.errors += 1
                    self.err_text = self.err_text or "memory pattern mismatch (thread %d, MB %d)" % (i, c)
                    return
                self.checks += 1
                if self.halt.is_set():
                    return
            self.passes += 1

    def poll(self):
        if not self.err_text and not any(th.is_alive() for th in self.ths):
            self.err_text = "RAM test threads ended"

    def stop(self):
        self.halt.set()
        for th in self.ths:
            th.join(3)
        self.bufs = []


class _GpuPart:
    name = "gpu"

    def __init__(self, ctl):
        self.checks = self.errors = 0
        self.err_text = ""
        exe = gpu_exe()
        if not exe:
            raise RuntimeError("helper %s not found" % GPU_EXE)
        args = [exe, "--stress"] + (["--fault"] if "gpu" in fault_set() else [])
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        self.lines = {}
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "stress" in d:
                self.checks, self.errors = int(d["stress"]["checks"]), int(d["stress"]["errors"])
            elif "error" in d:
                self.err_text = "GPU helper: %s" % d["error"]

    def poll(self):
        if self.errors:
            self.err_text = "GPU computation error"
        elif self.proc.poll() is not None and not self.err_text:
            self.err_text = "GPU helper ended (code %s)" % self.proc.returncode

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(3)


class _SsdPart:
    name = "ssd"
    BLK = 1 << 20

    def __init__(self, ctl):
        self.checks = self.errors = 0
        self.err_text = ""
        self.halt = threading.Event()
        tmp = ssd_tmp_dir()
        free = free_space_gb(tmp)
        if free < MIN_FREE_GB:
            raise RuntimeError("only %.1f GB free on %s (need %d GB)" % (free, os.path.splitdrive(tmp)[0], MIN_FREE_GB))
        self.drive = os.path.splitdrive(tmp)[0]
        self.size = (256 << 20) if quick() else (2 << 30)
        self.disk = _Disk(os.path.join(tmp, "metriq_stress_%d.tmp" % os.getpid()), blk_max=self.BLK, nbuf=2)
        self.fault = "ssd" in fault_set()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _one(self, write, off, buf):
        d = self.disk
        k = d.k
        ov = _OVL()
        nb = ctypes.c_uint32(0)
        ov.Offset, ov.OffsetHigh, ov.hEvent = off & 0xFFFFFFFF, off >> 32, d.events[0]
        fn = k.WriteFile if write else k.ReadFile
        if not fn(d.h, buf, self.BLK, None, ctypes.byref(ov)) and ctypes.get_last_error() != ERROR_IO_PENDING:
            raise OSError("I/O error %d" % ctypes.get_last_error())
        if not k.GetOverlappedResult(d.h, ctypes.byref(ov), ctypes.byref(nb), 1):
            raise OSError("I/O error %d" % ctypes.get_last_error())

    def _run(self):
        d = self.disk
        wbuf, rbuf = d.buf(0), d.buf(1)
        n = self.size // self.BLK
        gen = 0
        t0 = time.time()
        try:
            while not self.halt.is_set():
                gen += 1
                base = os.urandom(self.BLK)
                ctypes.memmove(wbuf, base, self.BLK)
                for j in range(n):                                          # write: block j carries its own number
                    if self.halt.is_set():
                        return
                    ctypes.memmove(wbuf, (gen * 1000003 + j).to_bytes(8, "little"), 8)
                    self._one(True, j * self.BLK, wbuf)
                if self.fault and time.time() - t0 > 2.0:
                    ctypes.memmove(wbuf, b"\x00" * 8, 8)
                    self._one(True, 5 * self.BLK, wbuf)                   # injected: block 5 gets the wrong number
                for j in range(n):                                          # read back and verify
                    if self.halt.is_set():
                        return
                    self._one(False, j * self.BLK, rbuf)
                    head = ctypes.string_at(rbuf, 8)
                    ok = head == (gen * 1000003 + j).to_bytes(8, "little") and \
                        ctypes.string_at(rbuf + 8, self.BLK - 8) == base[8:]
                    self.checks += 1
                    if not ok:
                        self.errors += 1
                        self.err_text = self.err_text or "data read back differs (block %d)" % j
                        return
        except Exception as ex:
            self.err_text = self.err_text or "SSD I/O failed: %s" % ex

    def poll(self):
        pass

    def stop(self):
        self.halt.set()
        self.th.join(5)
        self.disk.close()


_PART_CLS = {"cpu": _CpuPart, "ram": _RamPart, "gpu": _GpuPart, "ssd": _SsdPart}


def run_stress(ctl, parts, cpu_limit=CPU_LIMIT_DEFAULT, gpu_limit=GPU_LIMIT_DEFAULT):
    """Load the chosen parts until ctl is cancelled (= the user pressed Stop), a temperature limit is exceeded, the
    sensors disappear or every part has failed. Returns the summary dict. Emits ("live", dict) once a second."""
    t0 = time.time()
    parts = [p for p in STRESS_PARTS if p in parts]
    log = ctl.sensors
    log.start(os.path.splitdrive(ssd_tmp_dir())[0])
    active, failed, finished = {}, {}, {}
    stop_reason = ("user", "")
    notes = []
    try:
        for p in parts:                                       # start one by one: a part that cannot start only fails itself
            try:
                active[p] = _PART_CLS[p](ctl)
            except Exception as ex:
                failed[p] = "%s: %s" % (type(ex).__name__, ex) if not isinstance(ex, RuntimeError) else str(ex)
        last_live = 0.0
        warned = False
        while active and not ctl.cancelled():
            time.sleep(0.2)
            now = time.time()
            for p, obj in list(active.items()):
                obj.poll()
                if obj.err_text:
                    failed[p] = obj.err_text
                    finished[p] = (obj.checks, obj.errors)
                    obj.stop()
                    del active[p]
            s = log.last if log.last and log.last.get("t", 0) >= t0 else {}
            if s:
                ct, gt = s.get("cpu_temp"), s.get("gpu_temp")
                if ct is not None and ct > cpu_limit:
                    stop_reason = ("temp", "CPU %.0f|%d" % (ct, cpu_limit))
                    break
                if gt is not None and gt > gpu_limit:
                    stop_reason = ("temp", "GPU %.0f|%d" % (gt, gpu_limit))
                    break
            if log.ever_temp and now - log.ok_t > SENSOR_LOST_S:
                stop_reason = ("sensors", "")
                break
            if not warned and now - t0 > 12 and not log.ever_temp:
                warned = True
                notes.append("no temperature sensor: the automatic temperature protection is inactive")
            if now - last_live >= 1.0:
                last_live = now
                live = {"elapsed": now - t0, "sample": {k: v for k, v in s.items() if k != "t"},
                        "parts": {p: {"checks": o.checks, "errors": o.errors} for p, o in active.items()},
                        "done": {p: {"checks": c, "errors": e} for p, (c, e) in finished.items()},
                        "failed": dict(failed), "notes": list(notes)}
                ctl.emit("live", live)
        if not active and failed and stop_reason[0] == "user" and not ctl.cancelled():
            stop_reason = ("failed", "")
    finally:
        for p, obj in list(active.items()):
            obj.poll()
            finished[p] = (obj.checks, obj.errors)
            if obj.err_text and p not in failed:
                failed[p] = obj.err_text
            with contextlib.suppress(Exception):
                obj.stop()
        rows = log.since(t0)
        log.on = False
    dur = time.time() - t0
    errs = {p: finished.get(p, (0, 0))[1] for p in parts}
    for p in failed:
        if p not in finished:
            finished[p] = (0, 0)
    ser = make_series(rows, ("cpu_clock", "cpu_temp", "gpu_clock", "gpu_temp"), t0)
    sens = {p: summarize(p, rows) for p in parts}
    return {"test": "stress", "ts": time.time(), "duration": dur, "parts": parts, "stop": {"reason": stop_reason[0],
            "info": stop_reason[1]}, "errors": errs, "checks": {p: finished.get(p, (0, 0))[0] for p in parts},
            "error_total": sum(errs.values()) + sum(1 for p in failed if not errs.get(p)),
            "failed": failed, "sensors": sens, "series": ser, "limits": {"cpu": cpu_limit, "gpu": gpu_limit},
            "notes": notes, "clock_drift": {"cpu": _drift(ser.get("cpu_clock") or []),
                                            "gpu": _drift(ser.get("gpu_clock") or [])}}


# ---------------------------------------------------------------------------------------------- the benchmark process
class _Sampler(threading.Thread):
    """Reads the sensor backend once a second into the SensorLog (inside the benchmark process)."""

    def __init__(self, cfg, log, on_sample):
        super().__init__(daemon=True)
        self.cfg, self.log, self.on_sample = cfg or {}, log, on_sample
        self.halt = threading.Event()
        self.mgr = None

    def run(self):
        try:
            import backend
            c = {"backend": self.cfg.get("backend", "auto"), "lhm_url": self.cfg.get("lhm_url", "auto"),
                 "presentmon_path": ""}
            self.mgr = backend.Manager(c)
        except Exception:
            return
        while not self.halt.is_set():
            try:
                s = self.log.add(self.mgr.read_all())
                self.on_sample(s)
            except Exception:
                pass
            self.halt.wait(1.0)

    def close(self):
        self.halt.set()
        m = self.mgr
        if m is not None:
            with contextlib.suppress(Exception):
                m.closed.set()
                m.close()


def bench_main(tests, mode, cfg, q, stop, opts=None):
    """Entry point of the benchmark process (spawned by BenchJob). mode: quick / detailed / stress."""
    _lower_priority()
    pp = mp.parent_process()

    def watchdog():                      # the panel was killed: do not keep the PC loaded
        while True:
            time.sleep(1.0)
            if pp is not None and not pp.is_alive():
                stop.set()
                time.sleep(4)
                os._exit(1)
    threading.Thread(target=watchdog, daemon=True).start()
    log = SensorLog()
    cur = {"test": ""}

    def put(*m):
        with contextlib.suppress(Exception):
            q.put(m)
    sampler = _Sampler(cfg, log, lambda s: put("s", cur["test"], {k: v for k, v in s.items() if k != "t"}))
    sampler.start()
    last_p = {"t": 0.0}

    def progress(f, text, test=None):
        now = time.time()
        if f >= 1.0 or now - last_p["t"] >= 0.12:
            last_p["t"] = now
            put("p", cur["test"], f, text)
    try:
        ctl = Ctl(progress=progress, sensors=log, stop=stop, mode="quick" if mode == "stress" else mode, emit=put)
        if mode == "stress":
            opts = opts or {}
            cur["test"] = "stress"
            put("start", "stress")
            try:
                res = run_stress(ctl, opts.get("parts") or list(STRESS_PARTS), opts.get("cpu_limit", CPU_LIMIT_DEFAULT),
                                 opts.get("gpu_limit", GPU_LIMIT_DEFAULT))
                put("done", "stress", res)
            except Exception as ex:
                put("err", "stress", "%s: %s" % (type(ex).__name__, ex))
        else:
            for test in tests:
                if stop.is_set():
                    break
                cur["test"] = test
                put("start", test)
                try:
                    put("done", test, RUNNERS[test](ctl))
                except Cancelled:
                    put("cancel", test)
                    break
                except Exception as ex:
                    put("err", test, "%s: %s" % (type(ex).__name__, ex))
    finally:
        sampler.close()
        put("end")


class BenchJob:
    """Panel side of the benchmark process: start(), poll() (never blocks), cancel(), kill()."""
    _jobs = []

    def __init__(self, tests, mode="quick", cfg=None, stress=None):
        ctx = mp.get_context("spawn")
        self.q = ctx.Queue()
        self.stop = ctx.Event()
        self.proc = ctx.Process(target=bench_main, args=(list(tests), mode, dict(cfg or {}), self.q, self.stop, stress),
                                name="metriq-bench")
        self.started = False
        self.error = ""
        self.ended = False
        self.t0 = time.time()
        BenchJob._jobs.append(self)
        threading.Thread(target=self._start, daemon=True).start()       # spawning takes ~0.2 s: not on the Tk thread

    def _start(self):
        try:
            self.proc.start()
            self.started = True
        except Exception as ex:
            self.error = "%s: %s" % (type(ex).__name__, ex)

    def poll(self, limit=300):
        """New messages (list of tuples); a synthetic ("err", ...) / ("end",) when the process is gone."""
        msgs = []
        while len(msgs) < limit:
            try:
                msgs.append(self.q.get_nowait())
            except queue.Empty:
                break
            except Exception:
                break
        if any(m[0] == "end" for m in msgs):
            self.ended = True
        elif not self.ended and not msgs:
            gone = bool(self.error) or (self.started and not self.proc.is_alive())
            if gone:
                try:
                    msgs.append(self.q.get(timeout=0.3))          # a last message may still be in the pipe
                    return msgs
                except Exception:
                    pass
                self.ended = True
                msgs.append(("err", "", self.error or "benchmark process ended (exit code %s)" % self.proc.exitcode))
                msgs.append(("end",))
        return msgs

    def cancel(self):
        self.stop.set()

    def alive(self):
        return not self.ended

    def kill(self):
        self.stop.set()
        if self.started and self.proc.pid:
            with contextlib.suppress(Exception):
                subprocess.Popen(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=0x08000000)

    def join(self, timeout=5.0):
        if self.started:
            self.proc.join(timeout)


def _kill_all_jobs():
    for j in BenchJob._jobs:
        if not j.ended:
            j.kill()


import atexit  # noqa: E402
atexit.register(_kill_all_jobs)


def run_job(tests, mode="quick", cfg=None, stress=None, on_msg=None, timeout=3600, stop_after=None):
    """Blocking helper for the command line / tests: runs a BenchJob, returns {test: result}. stop_after: seconds
    after which the stop event is set (stress test)."""
    job = BenchJob(tests, mode, cfg, stress)
    results, t_stop = {}, time.time() + stop_after if stop_after else None
    t_end = time.time() + timeout
    while job.alive() and time.time() < t_end:
        if t_stop and time.time() >= t_stop:
            job.cancel()
            t_stop = None
        for m in job.poll():
            if on_msg:
                on_msg(m)
            if m[0] == "done":
                results[m[1]] = m[2]
            elif m[0] == "err":
                results[m[1] or "?"] = {"error": m[2]}
        time.sleep(0.05)
    if job.alive():
        job.kill()
    job.join(10)
    return results


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


def new_run(results, system=None, mode="quick"):
    good = {k: v for k, v in results.items() if v and v.get("score")}
    return {"ts": time.time(), "version": 2, "system": system or system_info(), "results": good,
            "overall": overall(good) if good else 0.0, "quick": quick(), "mode": mode}


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


UNITS = {"single_int": "it/s", "single_float": "it/s", "multi_int": "it/s", "multi_float": "it/s",
         "read": "GB/s", "write": "GB/s", "copy": "GB/s", "latency": "ns", "seq_read": "MB/s", "seq_write": "MB/s",
         "rnd_read": "MB/s", "rnd_write": "MB/s", "rnd_read_iops": "IOPS", "rnd_write_iops": "IOPS",
         "gflops": "GFLOPS", "bandwidth": "GB/s", "seq_read_q8": "MB/s", "seq_write_q8": "MB/s",
         "rnd_read_q4": "MB/s", "rnd_read_q32": "MB/s"}


def fmt_value(name, v):
    if v is None:
        return "-"
    if name in ("single_int", "single_float", "multi_int", "multi_float"):
        return "%.2f M" % (v / 1e6)
    if name in ("rnd_read_iops", "rnd_write_iops"):
        return "%.0f" % v
    if name in ("gflops", "seq_read", "seq_write", "seq_read_q8", "seq_write_q8") or v >= 1000:
        return "%.0f" % v
    return "%.1f" % v


def sens_view(test, s):
    """Sensor summary of a result in the current format (older history entries used cpu_/gpu_ prefixed keys)."""
    s = s or {}
    if "temp_max" in s or "clock_avg" in s or "power_avg" in s or "dimm_max" in s or "read_peak" in s:
        return s
    pre = "gpu" if test == "gpu" else "cpu"
    out = {"samples": s.get("samples"), "throttled": s.get("throttled") if test in ("cpu", "gpu") else None,
           "throttle_notes": s.get("throttle_notes") or []}
    if test in ("cpu", "gpu"):
        out.update(temp_max=s.get(pre + "_temp_max"), temp_avg=s.get(pre + "_temp_avg"),
                   clock_avg=s.get(pre + "_clock_avg"), power_avg=s.get(pre + "_power_avg"),
                   power_max=s.get(pre + "_power_max"))
    return out


def run_to_csv(run):
    """CSV text (; separated, Excel friendly) with one row per metric of every test of the run."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["time", "mode", "test", "metric", "value", "unit", "variation_pct", "metric_score", "test_score",
                "overall", "max_temp_c", "avg_clock_mhz", "avg_power_w", "throttled"])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.get("ts", 0)))
    for test in TESTS:
        r = (run.get("results") or {}).get(test)
        if not r:
            continue
        s = sens_view(test, r.get("sensors"))
        for name, v in r["metrics"].items():
            ms = metric_score(test, name, v) if name in REF[test] else ""
            pm = (r.get("spread") or {}).get(name)
            w.writerow([stamp, r.get("mode") or run.get("mode") or "quick", test, name, "%.4f" % v,
                        UNITS.get(name, ""), "" if pm is None else "%.1f" % pm, "" if ms == "" else "%.0f" % ms,
                        "%.0f" % r["score"], "%.0f" % run.get("overall", 0),
                        "" if s.get("temp_max") is None else "%.0f" % s["temp_max"],
                        "" if s.get("clock_avg") is None else "%.0f" % s["clock_avg"],
                        "" if s.get("power_avg") is None else "%.1f" % s["power_avg"],
                        "" if s.get("throttled") is None else ("yes" if s["throttled"] else "no")])
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- command line / self test
def selftest(out_path=None, tests=("cpu", "ram"), mode="quick"):
    """Used by `metriQ.exe --bench-selftest <file>`: a very short benchmark through the real process path (the
    frozen build must be able to spawn the benchmark process and its CPU workers). Returns an exit code."""
    os.environ.setdefault("METRIQ_BENCH_QUICK", "1")
    t0 = time.time()
    msgs = []
    res = run_job(list(tests), mode, {"backend": "builtin"}, on_msg=lambda m: msgs.append(m[0]), timeout=240)
    ok = all(res.get(t, {}).get("score") for t in tests)
    st = run_job([], "stress", {"backend": "builtin"}, {"parts": ["cpu", "ram", "gpu"]}, stop_after=4, timeout=90)
    sr = st.get("stress") or {}
    ok = ok and bool(sr) and sr.get("error_total") == 0 and not sr.get("failed")
    info = {"ok": ok, "seconds": round(time.time() - t0, 1), "frozen": bool(getattr(sys, "frozen", False)),
            "messages": len(msgs), "scores": {t: res.get(t, {}).get("score") for t in tests},
            "errors": {t: res[t].get("error") for t in res if res[t].get("error")},
            "stress": {"checks": sr.get("checks"), "errors": sr.get("errors"), "failed": sr.get("failed"),
                       "duration": sr.get("duration")}}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(info, f)
    else:
        print(json.dumps(info))
    return 0 if ok else 1


def _headless(argv):
    tests = [a for a in argv if a in TESTS or a == "all"]
    mode = "detailed" if "--detailed" in argv else "quick"
    if argv and argv[0] == "stress":
        parts = [p for p in (argv[1].split(",") if len(argv) > 1 else ["all"]) if p]
        parts = list(STRESS_PARTS) if "all" in parts else parts
        secs = float(argv[2]) if len(argv) > 2 else 10.0
        live_n = [0]

        def on_msg(m):
            if m[0] == "live":
                live_n[0] += 1
                print("live %5.1fs %s" % (m[1]["elapsed"], json.dumps({k: v for k, v in m[1]["parts"].items()})))
        res = run_job([], "stress", {"backend": "auto"}, {"parts": parts}, on_msg, stop_after=secs, timeout=secs + 120)
        print(json.dumps(res, indent=1, default=str))
        return res
    which = tests[0] if tests else "all"
    names = TESTS if which == "all" else (which,)
    t_start = {}

    def on_msg(m):
        if m[0] == "p":
            print("\r%s %3d%% %-22s" % (m[1], m[2] * 100, m[3]), end="", flush=True)
        elif m[0] == "start":
            t_start[m[1]] = time.time()
        elif m[0] == "done":
            print("\r%s done in %.1fs%s" % (m[1], time.time() - t_start.get(m[1], time.time()), " " * 30))
    results = run_job(list(names), mode, {"backend": "auto"}, on_msg=on_msg)
    for k, r in results.items():
        print(json.dumps(r, indent=1, default=str))
    good = {k: v for k, v in results.items() if v.get("score")}
    if good:
        print("OVERALL %.0f  (%s)" % (overall(good), ", ".join("%s %.0f" % (k, v["score"]) for k, v in good.items())))
    return results


if __name__ == "__main__":
    mp.freeze_support()
    _headless(sys.argv[1:] or ["all"])
