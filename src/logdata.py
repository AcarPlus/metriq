"""Data side of the recording viewer (no tkinter): reads our CSV recordings, sorts the columns into categories,
cuts a time range into per-pixel envelopes and looks up the programs running at a given moment.

    probe(path)          -> quick summary of one file for the list (cached; never raises)
    Recording(path)      -> every row's text + times; numeric columns are parsed on demand (ensure())
    Recording.window()   -> row range of a time range;  .envelope() -> per-pixel min / mean / max of a column
    Recording.programs_at(ts) -> [(program, {metric: value})] from the side file or the top-N columns

Times are kept as seconds of the naive local clock written in the CSV (the file has no time zone), so "12:54:08"
stays "12:54:08" whatever the PC's time zone is."""
import csv
import os
import re
import statistics
import time
from array import array
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

import crashscan

EPOCH = datetime(1970, 1, 1)
NAN = float("nan")
BIG_FILE = 250 * 1024 * 1024            # above this every k-th row is read (keeps memory in check)
NO_SCAN = 150 * 1024 * 1024             # above this the list does not scan the whole file for gaps

CATS = ("temp", "clock", "power", "usage", "fan", "volt", "io", "mem", "fps", "prog", "other")
COMPS = ("cpu", "gpu", "ram", "disk", "net", "mb", "fan", "fps", "prog", "other")     # hardware groups of the picker
PROG_RE = re.compile(r"^(CPU|RAM|GPU|VRAM|Disk) #(\d+) (program|%|MB|MB/s)$", re.I)
_UNIT_RE = re.compile(r"^(.*?)\s*[\[(]([^\[\]()]*)[\])]\s*$")
_FAN_RE = re.compile(r"\bfan\b|fan\d|pwm|fan speed|duty", re.I)
_FRAME_RE = re.compile(r"frame|present|latency|display|render|fps", re.I)
_TEMP_UNIT = re.compile(r"^\W{1,3}C$")
_DT_RE = re.compile(r"\d,\d")


# ------------------------------------------------------------------ times
def to_secs(dt):
    return (dt - EPOCH).total_seconds()


def from_secs(s):
    return EPOCH + timedelta(seconds=s)


def parse_secs(text):
    """'YYYY-MM-DD HH:MM:SS' -> seconds, None when it is not a time."""
    try:
        return (datetime.fromisoformat(text[:19]) - EPOCH).total_seconds()
    except ValueError:
        return None


# ------------------------------------------------------------------ columns
class Series:
    __slots__ = ("idx", "key", "name", "section", "unit", "cat", "comp", "label", "short")

    def __init__(self, idx, header):
        self.idx, self.key = idx, header
        section, _, rest = header.rpartition(" | ")
        m = _UNIT_RE.match(rest)
        name, unit = (m.group(1), m.group(2).strip()) if m else (rest, "")
        if _TEMP_UNIT.match(unit) or unit.endswith("°C") or unit == "℃":
            unit = "°C"
        self.name = name.strip() or rest
        self.section = section.strip()
        self.unit = unit
        pm = PROG_RE.match(header.strip())
        if pm:
            self.cat = "prog"
            self.name = f"{pm.group(1).upper() if pm.group(1).lower() != 'disk' else 'Disk'} #{pm.group(2)}"
            self.unit = {"%": "%", "mb": "MB", "mb/s": "MB/s"}.get(pm.group(3).lower(), "")
        else:
            self.cat = categorize(self.section, self.name, self.unit)
        self.comp = component(self.section, self.name, self.unit, self.cat)
        self.label = self.name
        self.short = short_section(self.section)


def short_section(section):
    """'CPU [#0]: <model>: Enhanced' -> 'CPU: <model>'."""
    if not section:
        return ""
    parts = [re.sub(r"\s*\[#\d+\]", "", p).strip() for p in section.split(": ")]
    out = parts[0]
    if len(parts) > 1 and len(parts[1]) <= 34 and not re.match(r"^(Enhanced|C-State)", parts[1]):
        out += ": " + parts[1]
    return out


def categorize(section, name, unit):
    u = unit.strip().lower()
    n = name.lower()
    if u == "yes/no":
        return "other"
    if "presentmon" in section.lower() or u == "fps" or "fps" in n or (u == "ms" and _FRAME_RE.search(name)):
        return "fps"
    if "°" in u or _TEMP_UNIT.match(unit) or u in ("℃",):
        return "temp"
    if u == "rpm":
        return "fan"
    if u == "%" and _FAN_RE.search(name):
        return "fan"
    if u in ("w", "mw", "kw"):
        return "power"
    if u in ("mhz", "ghz", "khz"):
        return "clock"
    if u in ("v", "mv", "a", "ma"):
        return "volt"
    if u == "%":
        return "usage"
    if re.search(r"[kmgt]?b/s$|[kmg]bps|[kmg]bit/s", u):
        return "io"
    if u in ("mb", "gb", "kb", "tb", "t", "b", "pages"):
        return "mem"
    return "other"


_RAM_SEC = re.compile(r"^(ddr\d?|dimm|memory)|dimm|memory timings|page file|virtual memory|physical memory", re.I)
_RAM_NAME = re.compile(r"^(ram|dimm|bellek|memory)\b|\bdimm\b|physical memory|virtual memory|page file", re.I)
_DISK_NAME = re.compile(r"^(disk|drive|s\.m\.a\.r\.t)|\bssd\b|nvme|\bhdd\b|read activity|write activity", re.I)
_NET_NAME = re.compile(r"^(download|upload|network|ağ|total (dl|up))|\b(rx|tx)\b", re.I)
_MB_NAME = re.compile(r"^(anakart|motherboard|mainboard|system \d|chipset|pch|vrm)|chipset|\bpch\b|vrm mos", re.I)


def component(section, name, unit, cat):
    """Hardware group a column belongs to (one of COMPS): by the sensor section of a full recording
    ('CPU [#0]: ...', 'Drive: ...', 'Network: ...'), else by the name used in the panel's own recordings."""
    if cat in ("prog", "fps", "fan"):
        return cat
    sec = section.lower().strip()
    if sec:
        if sec.startswith("cpu"):
            return "cpu"
        if re.match(r"^(dgpu|igpu|gpu)\b", sec):
            return "gpu"
        if _RAM_SEC.search(sec):
            return "ram"
        if sec.startswith(("drive", "s.m.a.r.t")):
            return "disk"
        if sec.startswith("network"):
            return "net"
        if sec.startswith("system:"):
            return "ram" if _RAM_NAME.search(name) else "mb"
        if "hardware errors" in sec or "whea" in sec:
            return "other"
        return "mb"                                           # the board's own sensor chips
    n = name.strip()
    if re.match(r"^cpu\b", n, re.I):
        return "cpu"
    if re.match(r"^(gpu|vram)\b", n, re.I):
        return "gpu"
    if _RAM_NAME.search(n):
        return "ram"
    if _DISK_NAME.search(n):
        return "disk"
    if _NET_NAME.search(n):
        return "net"
    if _MB_NAME.search(n):
        return "mb"
    if cat == "io":
        return "net" if re.search(r"kb/s|kbps|kbit", unit, re.I) else "disk"
    return "other"


def name_series(series):
    """Legend labels: the plain name when it is unique in the file, else name + short section."""
    count = {}
    for s in series:
        count[s.name] = count.get(s.name, 0) + 1
    for s in series:
        s.label = s.name if count[s.name] == 1 or not s.short else f"{s.name} · {s.short}"
    return series


def _to_float(tok, comma):
    try:
        return float(tok.replace(",", ".")) if comma else float(tok)
    except ValueError:
        tl = tok.strip().lower()
        if tl in ("yes", "evet", "true"):
            return 1.0
        if tl in ("no", "hayır", "hayir", "false"):
            return 0.0
        return NAN


def detect_decimal(lines, delim, hint=None):
    """',' or '.' as written in the first rows (the config of that day may differ from today's)."""
    if delim == ",":
        return "."
    comma = dot = 0
    for ln in lines[:20]:
        for tok in ln.split(delim)[1:]:
            if _DT_RE.search(tok) and tok.replace(",", "").replace("-", "").isdigit():
                comma += 1
            elif re.fullmatch(r"-?\d+\.\d+", tok):
                dot += 1
    if comma != dot:
        return "," if comma > dot else "."
    return hint or ("," if delim == ";" else ".")


def _split(ln, delim):
    if '"' in ln:
        return next(csv.reader([ln], delimiter=delim))
    return ln.split(delim)


# ------------------------------------------------------------------ quick probe for the list
_PROBES = {}


def _probe_gaps(times):
    itv = _interval(times)
    gaps = [(times[i - 1], times[i]) for i in range(1, len(times)) if times[i] - times[i - 1] > crashscan.GAP_FACTOR * itv]
    return itv, gaps


def _interval(times):
    d = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not d:
        return 1.0
    if len(d) > 5000:
        d = d[::len(d) // 5000]
    return max(1.0, statistics.median(d))


def probe(path):
    """{'path','name','size','mtime','start','end','rows','interval','gaps','abrupt','live','side','ok'}"""
    try:
        st = os.stat(path)
    except OSError:
        return {"path": path, "name": os.path.basename(path), "ok": False}
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _PROBES.get(path)
    if hit and hit[0] == key:
        return hit[1]
    d = {"path": path, "name": os.path.basename(path), "size": st.st_size, "mtime": st.st_mtime, "start": None,
         "end": None, "rows": 0, "interval": 1.0, "gaps": [], "abrupt": False, "live": False, "ok": True,
         "side": os.path.exists(side_path(path))}
    try:
        times = []
        with open(path, "rb") as f:
            f.readline()
            if st.st_size <= NO_SCAN:
                for ln in f:
                    s = parse_secs(ln[:19].decode("ascii", "ignore"))
                    if s is not None:
                        times.append(s)
            else:
                first = parse_secs(f.readline()[:19].decode("ascii", "ignore"))
                f.seek(max(0, st.st_size - 65536))
                f.readline()
                tail = [parse_secs(ln[:19].decode("ascii", "ignore")) for ln in f]
                tail = [x for x in tail if x is not None]
                times = ([first] if first is not None else []) + tail
                d["rows"] = int(st.st_size / 4000)
        if times:
            d["start"], d["end"] = times[0], times[-1]
            if st.st_size <= NO_SCAN:
                d["rows"] = len(times)
                d["interval"], d["gaps"] = _probe_gaps(times)
            else:
                d["interval"] = _interval(times[-200:])
            folder, name = os.path.split(path)
            ent = crashscan._load_index(folder)["files"].get(name)
            fresh = time.time() - st.st_mtime < max(10.0, 3 * d["interval"] + 2)
            if fresh:
                d["live"] = True
            elif ent is not None and not ent.get("closed"):
                d["abrupt"] = True
    except OSError:
        d["ok"] = False
    _PROBES[path] = (key, d)
    return d


def side_path(path):
    return os.path.splitext(path)[0] + "_processes.csv"


def list_recordings(folder):
    """Paths of the recordings in the folder, newest first (side files and other CSVs are not recordings)."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".csv")
                 and not n.lower().endswith("_processes.csv")]
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(folder, n)
        try:
            out.append((os.path.getmtime(p), p))
        except OSError:
            pass
    return [p for _, p in sorted(out, reverse=True)]


# ------------------------------------------------------------------ the recording
class Recording:
    def __init__(self, path, hint_decimal=None, progress=None):
        self.path = path
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self.stride = 1 if self.size <= BIG_FILE else int(self.size / BIG_FILE) + 1
        self.lines, self.t = [], array("d")
        self.header, self.series, self.cols = [], [], {}
        self.nan_cols = set()
        self.delim, self.dec = ";", ","
        self.by_idx, self.interval, self.gaps, self.itv_gap = {}, 1.0, [], crashscan.GAP_FACTOR
        self.abrupt = self.live = False
        self.side, self.top_cols = None, {}
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            head = ""
            for ln in f:
                if ln.strip():
                    head = ln.rstrip("\r\n")
                    break
            if not head:
                return
            self.delim = max((";", ",", "\t"), key=head.count)
            self.header = _split(head, self.delim)
            k = 0
            for ln in f:
                k += 1
                if self.stride > 1 and k % self.stride:
                    continue
                s = parse_secs(ln)
                if s is None:
                    continue
                self.t.append(s)
                self.lines.append(ln.rstrip("\r\n").replace("\x00", ""))
        if any(b < a for a, b in zip(self.t, self.t[1:])):            # clock set back: keep the rows in time order
            order = sorted(range(len(self.t)), key=self.t.__getitem__)
            self.lines = [self.lines[i] for i in order]
            self.t = array("d", (self.t[i] for i in order))
        self.dec = detect_decimal(self.lines, self.delim, hint_decimal)
        # numeric columns: everything except the time and the text columns of the top-N program lists
        self.series = []
        for i, h in enumerate(self.header):
            if i == 0:
                continue
            m = PROG_RE.match(h.strip())
            if m and m.group(3).lower() == "program":
                continue
            self.series.append(Series(i, h))
        name_series(self.series)
        self.by_idx = {s.idx: s for s in self.series}
        self.interval, self.gaps = _probe_gaps(self.t) if len(self.t) > 1 else (1.0, [])
        self.itv_gap = crashscan.GAP_FACTOR * self.interval
        try:
            folder = os.path.dirname(path)
            ent = crashscan._load_index(folder)["files"].get(self.name)
            fresh = time.time() - os.path.getmtime(path) < max(10.0, 3 * self.interval + 2)
            self.live = fresh
            self.abrupt = (not fresh) and ent is not None and not ent.get("closed")
        except OSError:
            pass
        self.top_cols = self._find_top_cols()

    # ---- basic
    def __len__(self):
        return len(self.t)

    @property
    def start(self):
        return self.t[0] if self.t else 0.0

    @property
    def end(self):
        return self.t[-1] if self.t else 0.0

    def window(self, t0, t1):
        """Row range [i0, i1) covering t0..t1 plus one neighbour on each side (so lines reach the edges)."""
        i0 = max(0, bisect_left(self.t, t0) - 1)
        i1 = min(len(self.t), bisect_right(self.t, t1) + 1)
        return i0, i1

    def nearest(self, ts):
        i = bisect_left(self.t, ts)
        if i <= 0:
            return 0
        if i >= len(self.t):
            return len(self.t) - 1
        return i if self.t[i] - ts < ts - self.t[i - 1] else i - 1

    # ---- columns
    def ensure(self, idxs):
        """Parse the numeric columns not yet loaded (one pass over the rows). Safe to call from a worker thread."""
        todo = [i for i in dict.fromkeys(idxs) if i not in self.cols and 0 < i < len(self.header)]
        if not todo:
            return
        comma = self.dec == ","
        arrs = {i: array("d") for i in todo}
        delim, n = self.delim, len(self.header)
        nan = {i: False for i in todo}
        for ln in self.lines:
            parts = _split(ln, delim)
            for i in todo:
                tok = parts[i] if i < len(parts) else ""
                if tok:
                    v = _to_float(tok, comma)
                    if v != v:
                        nan[i] = True
                else:
                    v = NAN
                    nan[i] = True
                arrs[i].append(v)
        for i in todo:
            self.cols[i] = arrs[i]
            if nan[i]:
                self.nan_cols.add(i)

    def stats(self, idx, i0, i1):
        """(min, avg, max, n) of the finite values in rows [i0, i1), None when there are none."""
        col = self.cols.get(idx)
        if col is None or i1 <= i0:
            return None
        seg = col[i0:i1]
        if idx in self.nan_cols:
            seg = [v for v in seg if v == v]
        if not len(seg):
            return None
        return min(seg), sum(seg) / len(seg), max(seg), len(seg)

    def value(self, idx, i):
        col = self.cols.get(idx)
        if col is None or not 0 <= i < len(col):
            return None
        v = col[i]
        return None if v != v else v

    def edges(self, t0, t1, w):
        """Row index at the start of each of w pixel columns (+ the end) for the time range t0..t1."""
        dt = (t1 - t0) / w
        t = self.t
        return [bisect_left(t, t0 + k * dt) for k in range(w + 1)]

    def envelope(self, idx, edges):
        """Per pixel column: (k, min, mean, max, first_row, last_row) for columns that hold rows."""
        col = self.cols[idx]
        nan = idx in self.nan_cols
        out = []
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1]
            if b <= a:
                continue
            seg = col[a:b]
            if nan:
                seg = [v for v in seg if v == v]
                if not seg:
                    continue
            out.append((k, min(seg), sum(seg) / len(seg), max(seg), a, b - 1))
        return out

    # ---- programs
    def _find_top_cols(self):
        """{'CPU': [(rank, program_col, value_col)], ...} for the top-N program columns of the main file."""
        found = {}
        for i, h in enumerate(self.header):
            m = PROG_RE.match(h.strip())
            if m and m.group(3).lower() == "program":
                metric, rank = m.group(1), int(m.group(2))
                found.setdefault(metric.upper() if metric.lower() != "disk" else "Disk", {})[rank] = [i, None]
        for i, h in enumerate(self.header):
            m = PROG_RE.match(h.strip())
            if m and m.group(3).lower() != "program":
                key = m.group(1).upper() if m.group(1).lower() != "disk" else "Disk"
                ent = found.get(key, {}).get(int(m.group(2)))
                if ent:
                    ent[1] = i
        return {k: [(r, a, b) for r, (a, b) in sorted(v.items()) if b is not None] for k, v in found.items() if v}

    def has_programs(self):
        return bool(self.top_cols) or os.path.exists(side_path(self.path))

    def load_side(self):
        """Parse '<name>_processes.csv' (long format: time, program, CPU %, RAM MB, GPU %, VRAM MB, Disk MB/s)."""
        if self.side is not None:
            return
        self.side = {"t": [], "rows": []}
        p = side_path(self.path)
        if not os.path.exists(p):
            return
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
                head = f.readline()
                delim = max((";", ",", "\t"), key=head.count)
                lines = [ln.rstrip("\r\n") for ln in f if ln.strip()]
            comma = detect_decimal(lines, delim, self.dec) == ","
            times, rows = [], []
            for ln in lines:
                s = parse_secs(ln)
                if s is None:
                    continue
                parts = _split(ln, delim)
                if len(parts) < 3:
                    continue
                vals = [_to_float(x, comma) if x else NAN for x in parts[2:7]]
                vals += [NAN] * (5 - len(vals))
                if times and s < times[-1]:
                    continue
                times.append(s)
                rows.append((parts[1], vals))
            self.side = {"t": times, "rows": rows}
        except OSError:
            pass

    METRICS = ("CPU", "RAM", "GPU", "VRAM", "Disk")

    def programs_at(self, i):
        """Programs around row i: [(name, {'CPU': %, 'RAM': MB, ...})] and the source ('side'|'top'|None)."""
        ts = self.t[i]
        if self.side is None:
            self.load_side()
        st, rows = self.side["t"], self.side["rows"]
        if st:
            j = bisect_right(st, ts + 0.5) - 1
            if j >= 0 and ts - st[j] <= max(self.interval * 2, 2.0):
                t_group = st[j]
                a = bisect_left(st, t_group)
                out = []
                for r in range(a, len(st)):
                    if st[r] != t_group:
                        break
                    name, vals = rows[r]
                    out.append((name, {m: (None if v != v else v) for m, v in zip(self.METRICS, vals)}))
                return out, "side"
        if self.top_cols:
            parts = _split(self.lines[i], self.delim)
            comma = self.dec == ","
            merged = {}
            for metric, cols in self.top_cols.items():
                for _, pc, vc in cols:
                    name = parts[pc].strip() if pc < len(parts) else ""
                    if not name:
                        continue
                    tok = parts[vc] if vc < len(parts) else ""
                    v = _to_float(tok, comma) if tok else NAN
                    merged.setdefault(name, {})[metric] = None if v != v else v
            return list(merged.items()), "top"
        return [], None


# ------------------------------------------------------------------ presets
def _first(series, patterns, taken):
    for pat in patterns:
        rx = re.compile(pat, re.I)
        hits = [s for s in series if s.idx not in taken and rx.search(s.name + " " + s.section)]
        if hits:                                            # the discrete GPU before the integrated one
            return next((s for s in hits if re.search(r"dgpu|nvidia|geforce|rtx", s.section, re.I)), hits[0])
    return None


PRESETS = {
    "temp": ("temp", [r"tctl|tdie|cpu package|cpu die|cpu \(", r"^gpu temperature|gpu temp\b",
                      r"hot ?spot", r"memory junction", r"drive temperature|^temperature( 1)?$|composite",
                      r"motherboard|system temp|vrm|chipset|pch|mainboard"]),
    "power": ("power", [r"cpu package power|ppt|cpu total power|package power", r"gpu.*(total|board).*power|toplam|"
                        r"^gpu power|total board power|^tbp|gpu chip power", r"total system power|system power|platform",
                        r"core\+soc|soc power|cpu core power"]),
    "usage": ("usage", [r"total cpu usage|cpu total|cpu usage|cpu \(", r"gpu core load|gpu utilization|gpu load",
                        r"physical memory load", r"memory load|ram", r"gpu memory (controller|usage)|gpu d3d usage|"
                        r"gpu video engine load"]),
    "clock": ("clock", [r"average effective clock|cpu.*core.*clock|core 0 clock", r"gpu clock|core clock",
                        r"gpu memory clock|memory clock"]),
    "fan": ("fan", [r"cpu.*fan|^fan.?1|cpu_fan", r"gpu.*fan", r"system.*fan|fan.?2|chassis|case"]),
    "fps": ("fps", [r"^framerate$|^fps$|framerate \(presented\)|framerate", r"frame ?time|frametime",
                    r"1% ?low", r"0\.1% ?low|0,1% ?low"]),
}


def preset(rec, key):
    """Series of a preset in this recording: [Series] (empty when the file has no matching columns)."""
    cat, pats = PRESETS[key]
    pool = [s for s in rec.series if s.cat == cat]
    taken, out = set(), []
    for pat in pats:
        s = _first(pool, [pat], taken)
        if s:
            taken.add(s.idx)
            out.append(s)
    if not out and key in ("temp", "power", "usage", "fan"):
        out = pool[:3]
    return out[:6]
