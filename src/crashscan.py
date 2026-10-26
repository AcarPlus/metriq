"""Crash detective: finds recordings that ended abruptly or contain time gaps, summarises the last seconds of
values and correlates them with Windows Event Log entries (Kernel-Power 41, EventLog 6008, BugCheck 1001, WHEA).

No UI here (see crashscan_ui.py). Clean closes are tracked in a small JSON index in the log folder
(".crash_index.json") so the CSV format stays untouched."""
import csv
import json
import os
import re
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import i18n
from i18n import t

INDEX_NAME = ".crash_index.json"
TAIL_SECONDS = 30
GAP_FACTOR = 3.0
CORR_MINUTES = 10
MAX_FILES = 60

i18n.register({
    "en": {
        "cr_tab": "Diagnostics", "cr_title": "Crash detective",
        "cr_intro": "Looks through your recordings for ones that ended abruptly (freeze, power loss, crash) or "
                    "contain gaps, and matches them with Windows event log entries.",
        "cr_scan": "Scan", "cr_scanning": "Scanning...", "cr_open_csv": "Open recording", "cr_open_dir": "Open folder",
        "cr_col_date": "Date", "cr_col_type": "Type", "cr_col_sum": "Summary",
        "cr_type_abrupt": "Ended abruptly", "cr_type_gap": "Gap",
        "cr_none": "No suspicious recordings found.", "cr_found": "{n} event(s) found in {f} recording(s).",
        "cr_select": "Select an event to see the details.", "cr_err": "Scan failed: {e}",
        "cr_sum_abrupt": "Recording stopped at {last}, no clean stop",
        "cr_sum_gap": "{gap} without data after {last}",
        "cr_d_file": "File: {f}", "cr_d_last": "Last row: {last}", "cr_d_gap": "Gap: {gap}, data resumed at {res}",
        "cr_d_interval": "Recording interval: about {i:.1f} s", "cr_d_tail": "Last {s} s before it:",
        "cr_d_cpu_t": "  CPU temperature max: {v}", "cr_d_gpu_t": "  GPU temperature max: {v}",
        "cr_d_cpu_u": "  CPU usage max: {v}", "cr_d_gpu_u": "  GPU usage max: {v}",
        "cr_d_cpu_p": "  CPU power max: {v}", "cr_d_gpu_p": "  GPU power max: {v}",
        "cr_d_hot": "  Unusual values (last value far above the file average):",
        "cr_d_progs": "  Top programs:", "cr_d_notail": "  (no rows in that time span)",
        "cr_d_win": "Windows events within +/-{m} min:", "cr_d_nowin": "No matching Windows events found.",
        "cr_d_winerr": "Windows event log could not be read.",
        "cr_ev_41": "Unexpected shutdown / restart (Kernel-Power 41)",
        "cr_ev_6008": "Unexpected shutdown (EventLog 6008)",
        "cr_ev_1001": "Bugcheck / blue screen (1001)", "cr_ev_whea": "Hardware error (WHEA)",
        "cr_dur_s": "{n} s", "cr_dur_m": "{m} min {s} s", "cr_dur_h": "{h} h {m} min",
    },
    "tr": {
        "cr_tab": "Tanılama", "cr_title": "Kilitlenme dedektifi",
        "cr_intro": "Kayıtlarda ani biten (donma, elektrik kesintisi, çökme) veya boşluk içeren olayları arar ve "
                    "Windows olay günlüğü kayıtlarıyla eşleştirir.",
        "cr_scan": "Tara", "cr_scanning": "Taranıyor...", "cr_open_csv": "Kaydı aç", "cr_open_dir": "Klasörü aç",
        "cr_col_date": "Tarih", "cr_col_type": "Tür", "cr_col_sum": "Özet",
        "cr_type_abrupt": "Ani bitti", "cr_type_gap": "Boşluk",
        "cr_none": "Şüpheli kayıt bulunamadı.", "cr_found": "{f} kayıtta {n} olay bulundu.",
        "cr_select": "Ayrıntı için bir olay seçin.", "cr_err": "Tarama başarısız: {e}",
        "cr_sum_abrupt": "Kayıt {last} anında durdu, temiz kapanış yok",
        "cr_sum_gap": "{last} sonrası {gap} veri yok",
        "cr_d_file": "Dosya: {f}", "cr_d_last": "Son satır: {last}", "cr_d_gap": "Boşluk: {gap}, veri {res} anında döndü",
        "cr_d_interval": "Kayıt aralığı: yaklaşık {i:.1f} sn", "cr_d_tail": "Öncesindeki son {s} sn:",
        "cr_d_cpu_t": "  CPU sıcaklığı en yüksek: {v}", "cr_d_gpu_t": "  GPU sıcaklığı en yüksek: {v}",
        "cr_d_cpu_u": "  CPU kullanımı en yüksek: {v}", "cr_d_gpu_u": "  GPU kullanımı en yüksek: {v}",
        "cr_d_cpu_p": "  CPU gücü en yüksek: {v}", "cr_d_gpu_p": "  GPU gücü en yüksek: {v}",
        "cr_d_hot": "  Olağandışı değerler (son değer dosya ortalamasının çok üstünde):",
        "cr_d_progs": "  En çok çalışan programlar:", "cr_d_notail": "  (bu aralıkta satır yok)",
        "cr_d_win": "Windows olayları (+/-{m} dk):", "cr_d_nowin": "Eşleşen Windows olayı bulunamadı.",
        "cr_d_winerr": "Windows olay günlüğü okunamadı.",
        "cr_ev_41": "Beklenmeyen kapanma / yeniden başlama (Kernel-Power 41)",
        "cr_ev_6008": "Beklenmeyen kapanma (EventLog 6008)",
        "cr_ev_1001": "Bugcheck / mavi ekran (1001)", "cr_ev_whea": "Donanım hatası (WHEA)",
        "cr_dur_s": "{n} sn", "cr_dur_m": "{m} dk {s} sn", "cr_dur_h": "{h} sa {m} dk",
    },
})


# ------------------------------------------------------------------ clean-close index
def _index_path(folder):
    return os.path.join(folder, INDEX_NAME)


def _load_index(folder):
    try:
        with open(_index_path(folder), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d.get("files"), dict) else {"files": {}}
    except (OSError, ValueError, AttributeError):
        return {"files": {}}


def _save_index(folder, d):
    p = _index_path(folder)
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, p)
    except OSError:
        pass


def _mark(path, closed):
    try:
        folder, name = os.path.split(path)
        d = _load_index(folder)
        ent = d["files"].setdefault(name, {})
        if closed:
            ent["closed"] = time.time()
        else:
            ent["open"] = time.time()
            ent["closed"] = None
        _save_index(folder, d)
    except Exception:
        pass


def mark_open(path):
    """Logger calls this when it creates a recording file."""
    _mark(path, False)


def mark_closed(path):
    """Logger calls this from close(): the recording ended cleanly."""
    if path:
        _mark(path, True)


# ------------------------------------------------------------------ CSV reading
def _parse_time(s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _num(s):
    s = s.strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _unit(name):
    m = re.search(r"[\[(]([^\[\]()]*)[\])]\s*$", name)
    return m.group(1).strip() if m else ""


_GPU = re.compile(r"gpu|nvidia|geforce|radeon|\brtx\b|\bgtx\b|\barc\b", re.I)
_CPU = re.compile(r"cpu|tctl|tdie|\bcore\b|package|ryzen|intel|\bccd\b", re.I)


def _kind(name):
    if _GPU.search(name):
        return "gpu"
    if _CPU.search(name):
        return "cpu"
    return None


def fmt_dur(sec):
    sec = int(round(sec))
    if sec < 60:
        return t("cr_dur_s", n=sec)
    if sec < 3600:
        return t("cr_dur_m", m=sec // 60, s=sec % 60)
    return t("cr_dur_h", h=sec // 3600, m=sec % 3600 // 60)


class Recording:
    """One CSV: header, raw rows (split lazily), timestamps."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            text = f.read()
        text = text.replace("\x00", "")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.header, self.rows, self.times = [], [], []
        if not lines:
            return
        head = lines[0]
        self.delim = max((";", ",", "\t"), key=head.count)
        self.header = next(csv.reader([head], delimiter=self.delim))
        for ln in lines[1:]:
            ts = _parse_time(ln[:19])
            if ts is None:
                continue
            self.times.append(ts)
            self.rows.append(ln)

    def row(self, i):
        return next(csv.reader([self.rows[i]], delimiter=self.delim))

    def interval(self):
        d = [(b - a).total_seconds() for a, b in zip(self.times, self.times[1:])]
        d = [x for x in d if x > 0]
        return max(1.0, statistics.median(d)) if d else 1.0


# ------------------------------------------------------------------ summary of the last seconds
def summarize_tail(rec, end_idx, seconds=TAIL_SECONDS):
    """Lines (already translated) describing the `seconds` before row end_idx (inclusive)."""
    t_end = rec.times[end_idx]
    lo = end_idx
    while lo > 0 and (t_end - rec.times[lo - 1]).total_seconds() <= seconds:
        lo -= 1
    tail = [rec.row(i) for i in range(lo, end_idx + 1)]
    out = [t("cr_d_tail", s=seconds)]
    if not tail:
        return out + [t("cr_d_notail")]
    ncol = len(rec.header)
    # file average / spread from <= 300 evenly spread rows
    base_n = lo if lo >= 10 else len(rec.rows)           # baseline = the recording before the tail window
    step = max(1, base_n // 300)
    sample = [rec.row(i) for i in range(0, base_n, step)]
    best = {}                                    # (metric, kind) -> (value, text)
    hot = []
    progs = {}
    for c in range(1, ncol):
        name = rec.header[c]
        tv = [_num(r[c]) if c < len(r) else None for r in tail]
        tnums = [v for v in tv if v is not None]
        if not tnums:
            if re.search(r"program|process|proces", name, re.I):
                for r in tail:
                    v = r[c].strip() if c < len(r) else ""
                    if v and _num(v) is None:
                        progs[v] = progs.get(v, 0) + 1
            continue
        u, kind = _unit(name), _kind(name)
        mx, lastv = max(tnums), tnums[-1]
        metric = None
        if u in ("°C", "C", "℃") or "°" in u:
            metric = "t"
        elif u == "%" and re.search(r"usage|load|utili|total", name, re.I):
            metric = "u"
        elif u == "W":
            metric = "p"
        if metric and kind and ((metric, kind) not in best or mx > best[(metric, kind)][0]):
            best[(metric, kind)] = (mx, f"{mx:.1f} {u}  ({name})")
        sv = [_num(r[c]) for r in sample if c < len(r)]
        sv = [v for v in sv if v is not None]
        if len(sv) >= 10 and u not in ("MHz", "GHz", "V"):
            mean, sd = statistics.fmean(sv), statistics.pstdev(sv)
            z = (lastv - mean) / max(sd, abs(mean) * 0.02, 1e-6)
            if z > 4 and lastv > mean * 1.3 and lastv - mean > 1:
                hot.append((z, f"    {name}: {lastv:.1f} (avg {mean:.1f})"))
    for metric, kind in (("t", "cpu"), ("t", "gpu"), ("u", "cpu"), ("u", "gpu"), ("p", "cpu"), ("p", "gpu")):
        if (metric, kind) in best:
            out.append(t(f"cr_d_{kind}_{metric}", v=best[(metric, kind)][1]))
    if hot:
        out.append(t("cr_d_hot"))
        out += [h for _, h in sorted(hot, reverse=True)[:5]]
    if progs:
        out.append(t("cr_d_progs"))
        out += [f"    {p} ({n}x)" for p, n in sorted(progs.items(), key=lambda kv: -kv[1])[:5]]
    return out


# ------------------------------------------------------------------ scanning recordings
def scan_recordings(folder, active_path=None, max_files=MAX_FILES):
    """Returns a list of event dicts (newest first). Never raises for single broken files."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".csv")]
    except OSError:
        return [], 0
    paths = sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)[:max_files]
    idx = _load_index(folder)["files"]
    events, now = [], time.time()
    ordered = sorted(paths, key=os.path.getmtime)           # old -> new, to know when the next recording began
    starts = {}
    for p in ordered:
        try:
            rec = Recording(p)
            starts[p] = (rec, rec.times[0] if rec.times else None)
        except OSError:
            continue
    for k, p in enumerate(ordered):
        if p not in starts:
            continue
        rec = starts[p][0]
        if not rec.times:
            continue
        itv = rec.interval()
        # (b) gaps
        for i in range(1, len(rec.times)):
            gap = (rec.times[i] - rec.times[i - 1]).total_seconds()
            if gap > GAP_FACTOR * itv:
                events.append(_event("gap", rec, i - 1, itv, gap=gap, resume=rec.times[i]))
        # (a) abrupt end
        ent = idx.get(rec.name)
        if ent is None or ent.get("closed"):
            continue                                        # old file (no marker known) or closed cleanly
        if active_path and os.path.normcase(active_path) == os.path.normcase(p):
            continue
        try:
            fresh = now - os.path.getmtime(p) < max(10.0, 3 * itv + 2)
        except OSError:
            fresh = False
        if fresh and k == len(ordered) - 1:
            continue                                        # still being written right now
        nxt = next((starts[q][1] for q in ordered[k + 1:] if q in starts and starts[q][1]), None)
        events.append(_event("abrupt", rec, len(rec.times) - 1, itv, next_start=nxt))
    events.sort(key=lambda e: e["ts_last"], reverse=True)
    return events, len(paths)


def _event(kind, rec, i, itv, gap=0.0, resume=None, next_start=None):
    last = rec.times[i]
    ev = {"type": kind, "file": rec.name, "path": rec.path, "ts_last": last, "last": last.strftime("%Y-%m-%d %H:%M:%S"),
          "gap": gap, "resume": resume, "next_start": next_start, "interval": itv, "windows": []}
    try:
        ev["tail"] = summarize_tail(rec, i)
    except Exception as e:                                  # one odd file must not break the scan
        ev["tail"] = [str(e)]
    return ev


# ------------------------------------------------------------------ Windows event log
_QUERY = ("*[System[(EventID=41 or EventID=6008 or EventID=1001 or "
          "Provider[@Name='Microsoft-Windows-WHEA-Logger'])]]")


def query_windows_events(count=300):
    """Read-only System log query via wevtutil (no admin needed). Returns (events, ok);
    events: [{'time': local naive datetime, 'id', 'kind', 'text'}]."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["wevtutil", "qe", "System", f"/q:{_QUERY}", "/f:xml", f"/c:{count}", "/rd:true"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return [], False
    if r.returncode != 0 and not r.stdout.strip():
        return [], False
    return parse_events_xml(r.stdout), True


def parse_events_xml(xml_text):
    out = []
    ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
    root = ET.fromstring("<r>" + re.sub(r"<\?xml[^>]*\?>", "", xml_text) + "</r>")
    for ev in root.iter(ns + "Event"):
        sysn = ev.find(ns + "System")
        if sysn is None:
            continue
        prov = (sysn.find(ns + "Provider").get("Name") or "") if sysn.find(ns + "Provider") is not None else ""
        try:
            eid = int(sysn.findtext(ns + "EventID") or 0)
        except ValueError:
            continue
        tc = sysn.find(ns + "TimeCreated")
        if tc is None:
            continue
        try:
            s = tc.get("SystemTime").rstrip("Z")
            s = re.sub(r"(\.\d{6})\d+", r"\1", s)
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue
        if "WHEA" in prov:
            kind = "whea"
        elif eid == 41 and "Kernel-Power" in prov:
            kind = "41"
        elif eid == 6008 and prov == "EventLog":
            kind = "6008"
        elif eid == 1001 and "SystemErrorReporting" in prov:
            kind = "1001"
        else:
            continue
        data = [d.text or "" for d in ev.iter(ns + "Data")]
        extra = ""
        if kind == "1001" and data:
            extra = " " + data[0][:120]
        elif kind == "whea":
            extra = f" (id {eid})"
        out.append({"time": dt, "id": eid, "kind": kind, "text": t("cr_ev_" + kind) + extra})
    return out


def correlate(events, win_events, minutes=CORR_MINUTES):
    """Attach matching Windows events to every event (+/- minutes; 41 / 6008 / 1001 are logged at the next
    boot, so for an abrupt end the window reaches to the start of the next recording)."""
    m = timedelta(minutes=minutes)
    for ev in events:
        lo, hi = ev["ts_last"] - m, ev["ts_last"] + m
        if ev["type"] == "gap" and ev["resume"]:
            hi = ev["resume"] + m
        elif ev["type"] == "abrupt" and ev["next_start"]:
            late = min(ev["next_start"], ev["ts_last"] + timedelta(hours=6)) + m
        else:
            late = hi
        late_kinds = ("41", "6008", "1001")
        ev["windows"] = sorted((w for w in win_events
                                if lo <= w["time"] <= (late if (ev["type"] == "abrupt" and w["kind"] in late_kinds)
                                                       else hi)), key=lambda w: w["time"])
    return events


def scan(folder, active_path=None):
    """Full scan. Returns {'events': [...], 'files': n, 'winlog': bool}."""
    events, nfiles = scan_recordings(folder, active_path)
    wins, ok = query_windows_events() if events else ([], True)
    correlate(events, wins)
    return {"events": events, "files": nfiles, "winlog": ok}


# ------------------------------------------------------------------ text for the UI
def short_summary(ev):
    if ev["type"] == "abrupt":
        return t("cr_sum_abrupt", last=ev["last"][11:])
    return t("cr_sum_gap", gap=fmt_dur(ev["gap"]), last=ev["last"][11:])


def details(ev, winlog_ok=True):
    L = [t("cr_d_file", f=ev["file"]), t("cr_d_last", last=ev["last"])]
    if ev["type"] == "gap":
        L.append(t("cr_d_gap", gap=fmt_dur(ev["gap"]), res=ev["resume"].strftime("%H:%M:%S")))
    L.append(t("cr_d_interval", i=ev["interval"]))
    L.append("")
    L += ev.get("tail", [])
    L.append("")
    L.append(t("cr_d_win", m=CORR_MINUTES))
    if not winlog_ok:
        L.append("  " + t("cr_d_winerr"))
    elif not ev["windows"]:
        L.append("  " + t("cr_d_nowin"))
    for w in ev["windows"]:
        L.append(f"  {w['time'].strftime('%Y-%m-%d %H:%M:%S')}  {w['text']}")
    return "\n".join(L)
