"""CSV side of the per-process feature: config defaults, the extra "top programs" columns of the main CSV, the
"<name>_processes.csv" side file, file size limit / retention and the size estimate.

The panel's Logger calls the hooks of ``Extras`` (opened / closed / rotate / written); everything else lives here.
All user visible texts of the process feature (log, window, settings) are registered below with i18n.register().
"""
import csv
import os
import re
import time

import config
import i18n
import procmon
from i18n import t

# ------------------------------------------------------------------ config
ORDER = ("cpu", "gpu", "ram", "vram", "disk")              # column order of the top-N programs in the main CSV
UNIT = {"cpu": "%", "gpu": "%", "ram": "MB", "vram": "MB", "disk": "MB/s"}
DEC = {"cpu": 1, "gpu": 1, "ram": 0, "vram": 0, "disk": 2}
DEFAULT_TOP = {"cpu": 3, "gpu": 3, "ram": 0, "vram": 0, "disk": 0}
GB = 1024 ** 3
MB = 1024 ** 2

# registered here (not in config.py) so the process feature stays one self-contained piece
config.DEFAULTS.setdefault("proc_top", dict(DEFAULT_TOP))      # per metric: 0 = off, 1..99 = top programs as columns
config.DEFAULTS.setdefault("proc_detail", False)               # side file "<name>_processes.csv"
config.DEFAULTS.setdefault("proc_log_filter", "all")           # programs of the side file: "all" or [exe names]
config.DEFAULTS.setdefault("log_max_mb", 0)                    # start a new CSV file above this size (0 = no limit)
config.DEFAULTS.setdefault("log_keep_days", 0)                 # delete our CSV files older than this at start (0 = keep)

# a row of the side file is only written when the program is above one of these
DETAIL_MIN_PCT, DETAIL_MIN_MB, DETAIL_MIN_DISK = 0.5, 50.0, 0.5

NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?(_processes)?\.csv$")   # files the Logger creates


def top_cfg(cfg):
    """{'cpu': n, ...} with every value clamped to 0..99 (junk in config.json -> default)."""
    raw = cfg.get("proc_top")
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k in ORDER:
        try:
            v = int(float(raw.get(k, DEFAULT_TOP[k])))
        except (TypeError, ValueError):
            v = DEFAULT_TOP[k]
        out[k] = max(0, min(99, v))
    return out


def filter_cfg(cfg):
    """'all' or a list of lower-case exe names."""
    f = cfg.get("proc_log_filter", "all")
    if isinstance(f, str):
        return "all" if f.strip().lower() in ("all", "") else [f.strip().lower()]
    if isinstance(f, (list, tuple)):
        return sorted({str(x).strip().lower() for x in f if str(x).strip()})
    return "all"


def num_cfg(cfg, key, hi, default=0):
    try:
        v = float(str(cfg.get(key, default)).replace(",", "."))
    except (TypeError, ValueError):
        v = default
    return max(0.0, min(float(hi), v))


# ------------------------------------------------------------------ size estimate
def estimate(interval, nbase, top, detail, nprog):
    """(main_bytes_per_min, side_bytes_per_min), rough: columns x typical field width x rows per minute."""
    rows = 60.0 / max(0.5, float(interval))
    ntop = sum(top.values())
    line = 20 + nbase * 6.5 + ntop * 20            # time stamp + numbers + (program name + value) per top slot
    main = line * rows
    side = 0.0
    if detail:
        side = nprog * (20 + 16 + 5 * 6.5 + 7) * rows      # time ; name ; five numbers ; delimiters
    return main, side


def fmt_mb(b):
    v = b / MB
    if v < 10:
        s = f"{v:.1f}"
    else:
        s = f"{v:,.0f}".replace(",", " ")
    return s.replace(".", ",") if i18n.language() == "tr" else s


def estimate_text(interval, nbase, top, detail, filt, mon=procmon.MON):
    """'≈ X MB/dk · Y MB/saat (yaklaşık)' for main + side file, using the live programs list when sampling."""
    nprog = 0
    if detail:
        snap = mon.snapshot()
        if snap is not None:
            nprog = sum(1 for g in snap.groups if (filt == "all" or g.key in filt) and detail_row(g))
        elif filt == "all":
            nprog = 20
        else:
            nprog = len(filt)
    main, side = estimate(interval, nbase, top, detail, nprog)
    tot = main + side
    return t("pl_est", m=fmt_mb(tot), h=fmt_mb(tot * 60))


def detail_row(g):
    return (g.cpu > DETAIL_MIN_PCT or g.gpu > DETAIL_MIN_PCT or g.ram > DETAIL_MIN_MB or g.vram > DETAIL_MIN_MB
            or g.disk > DETAIL_MIN_DISK)


# ------------------------------------------------------------------ retention
def cleanup(cfg, keep=()):
    """Delete the CSVs the panel itself created that are older than log_keep_days. Returns how many were removed."""
    days = num_cfg(cfg, "log_keep_days", 36500)
    if days <= 0:
        return 0
    d = config.log_dir(cfg)
    limit = time.time() - days * 86400
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    keep = {os.path.normcase(os.path.abspath(k)) for k in keep if k}
    for name in names:
        if not NAME_RE.match(name):
            continue
        p = os.path.join(d, name)
        try:
            if os.path.normcase(os.path.abspath(p)) in keep or not os.path.isfile(p) or os.path.getmtime(p) >= limit:
                continue
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


# ------------------------------------------------------------------ the Logger extension
class Extras:
    """Hooks for panel.Logger; ``logger`` must provide .path, .num(v, dec), .delim, .dec and .f."""

    def __init__(self, cfg, logger, mon=procmon.MON):
        self.cfg, self.logger, self.mon = cfg, logger, mon
        self.sf = self.sw = self.spath = None             # side file
        self.sbytes = 0
        self.sampling = False
        self.warned = False                               # 1 GB warning shown for the current file
        self.notes = []                                   # [(until, text, colour key)]
        self.err = 0.0
        cleanup(cfg)

    # ---- what is wanted right now
    def top(self):
        return top_cfg(self.cfg)

    def detail(self):
        return bool(self.cfg.get("proc_detail"))

    def wanted(self):
        return bool(self.cfg.get("logging")) and (self.detail() or any(self.top().values()))

    def update(self):
        """Called every panel tick: sampling runs only while the recording needs it."""
        want = self.wanted()
        if want and not self.sampling:
            self.sampling = True
            self.mon.acquire("csv", min(5.0, max(1.0, float(self.cfg.get("interval") or 5))))
        elif not want and self.sampling:
            self.sampling = False
            self.mon.release("csv")
        elif want:
            self.mon.acquire("csv", min(5.0, max(1.0, float(self.cfg.get("interval") or 5))))

    def shutdown(self):
        self.closed()
        if self.sampling:
            self.sampling = False
            self.mon.release("csv")

    # ---- columns of the main file
    def header(self):
        out = []
        for m in ORDER:
            n = self.top()[m]
            label = m.upper() if m != "disk" else "Disk"
            for i in range(1, n + 1):
                out += [f"{label} #{i} program", f"{label} #{i} {UNIT[m]}"]
        return out

    def values(self):
        snap = self.mon.snapshot()
        out = []
        for m in ORDER:
            n = self.top()[m]
            if not n:
                continue
            tops = snap.top(m, n) if snap is not None else []
            for i in range(n):
                if i < len(tops):
                    out += [tops[i].name, self.logger.num(tops[i].value(m), DEC[m])]
                else:
                    out += ["", ""]
        return out

    # ---- hooks called by Logger
    def opened(self, path, ncols):
        self.warned = False
        self.close_side()
        cleanup(self.cfg, keep=(path, self.side_path(path)))
        top = self.top()
        nbase = max(0, ncols - 1 - 2 * sum(top.values()))
        self.note(t("pl_started") + " " + estimate_text(self.cfg.get("interval", 5), nbase, top, self.detail(),
                                                         filter_cfg(self.cfg), self.mon), 9, "dim")

    def closed(self):
        self.close_side()

    def rotate(self, f):
        """True when the current file(s) passed log_max_mb: the Logger then starts a new file."""
        limit = num_cfg(self.cfg, "log_max_mb", 10 ** 7) * MB
        if limit <= 0:
            return False
        try:
            return os.fstat(f.fileno()).st_size >= limit or self.sbytes >= limit
        except OSError:
            return False

    def written(self, f, stamp):
        try:
            if self.detail():
                self.write_side(stamp)
            size = os.fstat(f.fileno()).st_size
        except Exception:
            self.err = time.time()
            return
        if not self.warned and max(size, self.sbytes) >= GB:
            self.warned = True
            self.note(t("pl_big"), 12, "yellow")

    # ---- side file
    @staticmethod
    def side_path(path):
        return os.path.splitext(path)[0] + "_processes.csv" if path else None

    def close_side(self):
        if self.sf:
            try:
                self.sf.close()
            except OSError:
                pass
        self.sf = self.sw = self.spath = None
        self.sbytes = 0

    def write_side(self, stamp):
        snap = self.mon.snapshot()
        if snap is None or not self.logger.path:
            return
        filt = filter_cfg(self.cfg)
        rows = [g for g in snap.groups if (filt == "all" or g.key in filt) and detail_row(g)]
        if not rows:
            return
        rows.sort(key=lambda g: (-g.cpu, -g.ram))
        if self.sf is None:
            self.spath = self.side_path(self.logger.path)
            new = not os.path.exists(self.spath)
            self.sf = open(self.spath, "a", newline="", encoding="utf-8-sig")
            self.sw = csv.writer(self.sf, delimiter=self.logger.delim)
            if new:
                self.sw.writerow([t("csv_time"), "program", "CPU %", "RAM MB", "GPU %", "VRAM MB", "Disk MB/s"])
        num = self.logger.num
        for g in rows:
            self.sw.writerow([stamp, g.name, num(g.cpu, DEC["cpu"]), num(g.ram, DEC["ram"]), num(g.gpu, DEC["gpu"]),
                              num(g.vram, DEC["vram"]), num(g.disk, DEC["disk"])])
        self.sf.flush()
        try:
            os.fsync(self.sf.fileno())                    # same durability as the main file
        except OSError:
            pass
        try:
            self.sbytes = os.fstat(self.sf.fileno()).st_size
        except OSError:
            pass

    # ---- toasts
    def note(self, text, secs, colour):
        self.notes.append((time.time() + secs, text, colour))

    def toasts(self, C):
        now = time.time()
        self.notes = [n for n in self.notes if n[0] > now]
        out = [(text, C.get(col, C["dim"]), None) for _, text, col in self.notes]
        if now - self.err < 6:
            out.append((t("pl_side_err"), C["red"], None))
        return out


# ------------------------------------------------------------------ texts
i18n.register({
    "en": {
        "pl_est": "≈ {m} MB/min · {h} MB/hour (approximate)",
        "pl_started": "Recording started:",
        "pl_big": "The CSV file is over 1 GB: consider a size limit in Settings > Logging",
        "pl_side_err": "recording failed: the process CSV file cannot be written",
        # process window
        "pw_title": "Processes",
        "pw_search": "Search",
        "pw_min": "Hide below",
        "pw_min_tip": "CPU / GPU %, RAM / VRAM as % of memory, disk MB/s",
        "pw_all": "All programs",
        "pw_name": "Program",
        "pw_log": "Log",
        "pw_log_hdr": "Add to log",
        "pw_apps": "Apps", "pw_background": "Background", "pw_windows": "Windows processes",
        "pw_nogpu": "GPU counters unavailable",
        "pw_wait": "sampling…",
        "pw_count": "{n} programs shown",
        "pw_proc": "PID {pid}",
        "pw_hint": "Click a column header to sort; click ☐ to include a program in the process CSV",
        # settings
        "ps_top": "Top programs in the CSV",
        "ps_top_hint": "0 = off; 1–99 = number of programs added as columns",
        "ps_detail": "Process CSV (<name>_processes.csv)",
        "ps_detail_hint": "one row per program and sample",
        "ps_filter": "Programs in the process CSV",
        "ps_filter_all": "all programs",
        "ps_filter_n": "{n} selected: {names}",
        "ps_filter_none": "none selected",
        "ps_choose": "Choose…",
        "ps_maxmb": "Maximum file size (MB)",
        "ps_maxmb_hint": "0 = no limit; a new file is started above it",
        "ps_keep": "Keep CSV files (days)",
        "ps_keep_hint": "0 = keep all; older files are deleted at start",
        "ps_est": "Estimated size",
    },
    "tr": {
        "pl_est": "≈ {m} MB/dk · {h} MB/saat (yaklaşık)",
        "pl_started": "Kayıt başladı:",
        "pl_big": "CSV dosyası 1 GB'ı aştı: Ayarlar > Kayıt'tan boyut sınırı belirlemeyi düşünün",
        "pl_side_err": "kayıt başarısız: işlem CSV dosyası yazılamıyor",
        "pw_title": "İşlemler",
        "pw_search": "Ara",
        "pw_min": "Altını gizle",
        "pw_min_tip": "CPU / GPU %, RAM / VRAM belleğin %'si, disk MB/s",
        "pw_all": "Tüm programlar",
        "pw_name": "Program",
        "pw_log": "Kayıt",
        "pw_log_hdr": "Kayda ekle",
        "pw_apps": "Uygulamalar", "pw_background": "Arka plan", "pw_windows": "Windows işlemleri",
        "pw_nogpu": "GPU sayaçları kullanılamıyor",
        "pw_wait": "örnekleniyor…",
        "pw_count": "{n} program gösteriliyor",
        "pw_proc": "PID {pid}",
        "pw_hint": "Sıralamak için sütun başlığına tıklayın; işlem CSV'sine eklemek için ☐ kutusuna tıklayın",
        "ps_top": "CSV'de en çok kullanan programlar",
        "ps_top_hint": "0 = kapalı; 1–99 = sütun olarak eklenen program sayısı",
        "ps_detail": "İşlem CSV'si (<ad>_processes.csv)",
        "ps_detail_hint": "her örnekte program başına bir satır",
        "ps_filter": "İşlem CSV'sindeki programlar",
        "ps_filter_all": "tüm programlar",
        "ps_filter_n": "{n} seçili: {names}",
        "ps_filter_none": "seçili program yok",
        "ps_choose": "Seç…",
        "ps_maxmb": "En büyük dosya boyutu (MB)",
        "ps_maxmb_hint": "0 = sınırsız; aşılınca yeni dosya açılır",
        "ps_keep": "CSV dosyalarını sakla (gün)",
        "ps_keep_hint": "0 = hepsini sakla; eski dosyalar açılışta silinir",
        "ps_est": "Tahmini boyut",
    },
})
