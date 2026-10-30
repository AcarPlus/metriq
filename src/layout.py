"""Panel layout: config rows -> compiled rows, matching against sensor readings, hardware auto-detection.

All matching uses the ORIGINAL (English) sensor / label names (HWiNFO shared memory, LibreHardwareMonitor or the
built-in source, see backend.py), so a layout works the same on any Windows language. Patterns are
case-insensitive regular expressions (re.search). Label alternatives below: HWiNFO first, then LHM.
"""
import re
import sys

import battery
import themes
import throttle
from i18n import t

# default section colours as stored in config files (the dark theme's accents); a section that still carries
# one of the built-in colours follows the active theme (themes.accent_for)
ICON_COLORS = {"gauge": "#58a6ff", "bolt": "#e3b341", "temp": "#ff7b72", "fan": "#39c5cf", "net": "#bc8cff",
               "fps": "#7ee787"}
ICONS = tuple(ICON_COLORS)

# CSV decimals / display decimals by unit
_DEC = {"°c": 0, "w": 1, "v": 3, "mhz": 0, "%": 1, "mb": 0, "rpm": 0, "kb/s": 0, "mb/s": 1, "fps": 0, "ms": 1, "a": 2}


def decimals_for(unit):
    return _DEC.get((unit or "").lower(), 2)


def default_format(unit):
    return "{:.%df}" % decimals_for(unit)


def format_decimals(fmt, unit):
    m = re.search(r"\.(\d+)f", fmt or "")
    return int(m.group(1)) if m else decimals_for(unit)


# "format" values starting with "@" are not Python formats but named text formatters (value -> text)
SPECIAL = {"@bstate": battery.fmt_state, "@hm": battery.fmt_hm, "@throttle": throttle.fmt}
TEXTUAL = ("@bstate", "@throttle")          # the value is a code: Min / Max / Avg make no sense for these rows


class Row:
    """One panel row. Build with Row.from_cfg(dict)."""

    def __init__(self, name, sensor, label, rtype, fmt, unit, limits, bar, visible=True, help=None):
        self.name, self.sensor, self.label, self.rtype = name, sensor, label, rtype
        self.fmt, self.unit, self.limits, self.bar = fmt, unit, limits, bar
        self.visible = visible
        self.help = help or None          # i18n key "hp_<help>": balloon on the row name in the panel
        self.special = fmt in SPECIAL
        self.textual = fmt in TEXTUAL
        self.sre = re.compile(sensor, re.I) if sensor else None
        self.lre = re.compile(label, re.I)
        self.key = f"{name}|{sensor or ''}|{label}|{'' if rtype is None else rtype}"
        self._idx = None
        self._ms = self._ml = None
        self.unit_eff = unit or ""        # filled in from the matched reading when no unit is configured

    @classmethod
    def from_cfg(cls, d):
        name = str(d.get("name") or d.get("label") or "?")
        label = d.get("label")
        if not label:
            raise ValueError("row without 'label'")
        rtype = d.get("type")
        rtype = int(rtype) if rtype not in (None, "") else None
        lim = d.get("limits")
        limits = None
        if isinstance(lim, (list, tuple)) and len(lim) == 2 and lim[0] != lim[1]:
            limits = (float(lim[0]), float(lim[1]))
        bar = d.get("bar")
        bar = (limits is not None) if bar is None else bool(bar and limits is not None)
        visible = d.get("visible", True)
        visible = not (visible is False or visible in (0, "false", "False", "no"))
        return cls(name, d.get("sensor") or None, label, rtype, d.get("format") or None, d.get("unit") or "",
                   limits, bar, visible, d.get("help"))

    def match(self, rd):
        i = self._idx
        if i is not None and i < len(rd) and rd[i]["sensor"] == self._ms and rd[i]["label"] == self._ml:
            return rd[i]
        for i, r in enumerate(rd):
            if self.sre and not self.sre.search(r["sensor"]):
                continue
            if self.rtype is not None and r["type"] != self.rtype:
                continue
            if self.lre.search(r["label"]):
                self._idx, self._ms, self._ml = i, r["sensor"], r["label"]
                if not self.unit and not self.special:
                    self.unit_eff = r["unit"]
                return r
        self._idx = None
        return None

    def fmt_value(self, v):
        if self.special:
            try:
                return SPECIAL[self.fmt](v)
            except Exception:
                return f"{v:.0f}"
        f = self.fmt or default_format(self.unit_eff)
        try:
            return f.format(v)
        except Exception:
            return f"{v:.1f}"

    def csv_decimals(self):
        return 0 if self.special else format_decimals(self.fmt, self.unit_eff)


class Section:
    def __init__(self, title, color, icon, rows):
        self.title, self.color, self.icon, self.rows = title, color, icon, rows


def build_sections(cfg_sections, include_hidden=False):
    """Config list -> [Section]. Bad entries are skipped with a message on stderr.
    Rows with "visible": false are left out (they stay in the config) unless include_hidden is set."""
    out = []
    for si, s in enumerate(cfg_sections if isinstance(cfg_sections, list) else []):
        try:
            icon = s.get("icon") if s.get("icon") in ICONS else "gauge"
            rows = []
            for rd_ in s.get("rows", []):
                try:
                    row = Row.from_cfg(rd_)
                    if row.visible or include_hidden:
                        rows.append(row)
                except Exception as ex:
                    print(f"config: section {si}, row {rd_!r} ignored: {ex}", file=sys.stderr)
            if rows:
                out.append(Section(str(s.get("title", "")), s.get("color") or ICON_COLORS[icon], icon, rows))
        except Exception as ex:
            print(f"config: section {si} ignored: {ex}", file=sys.stderr)
    return out


def all_rows(sections):
    return [r for s in sections for r in s.rows]


def count_matches(sections, rd):
    return sum(1 for r in all_rows(sections) if r.match(rd))


# ------------------------------------------------------------------ multi-column geometry (pure, no tkinter)
HEAD, FOOT, STATUS = 1.75, 0.35, 2.0     # section header / footer / status bar, in row heights
GAP_UNITS = 0.3                           # typical gap between cards in row heights (partition weight only)
MIN_RH = 14.0
RH_CAP = 0.08                             # row height never exceeds this share of the column width
BAR_MIN_COL_W = 520                       # narrower columns drop the bar column


def resolve_columns(setting, W, H):
    """'auto' | 1..4 -> number of columns. Auto: 1 when taller than wide, else 3 for aspect >= 2.0, else 2."""
    try:
        n = int(setting)
    except (TypeError, ValueError):
        n = 0
    if n in (1, 2, 3, 4):
        return n
    if H > W:
        return 1
    return 3 if W / max(1.0, H) >= 2.0 else 2


def section_units(sec, sp=1.0):
    """Height of a section in row heights; sp = row spacing factor (theme)."""
    return HEAD + FOOT + len(sec.rows) * sp


def _partition_dp(sections, n):
    """Split the section list into at most n consecutive, non-empty groups with the smallest possible tallest
    group (ties: most even). A section is never split here. Always returns at least one (possibly empty) group."""
    if not sections:
        return [[]]
    w = [section_units(s) + GAP_UNITS for s in sections]
    N = len(w)
    k = max(1, min(int(n), N))
    pre = [0.0]
    for x in w:
        pre.append(pre[-1] + x)
    best = [[None] * (N + 1) for _ in range(k + 1)]
    best[0][0] = (0.0, 0.0, None)
    for j in range(1, k + 1):
        for i in range(j, N + 1):
            cand = None
            for p in range(j - 1, i):
                prev = best[j - 1][p]
                if prev is None:
                    continue
                seg = pre[i] - pre[p]
                val = (round(max(prev[0], seg), 6), round(prev[1] + seg * seg, 6), p)
                if cand is None or val[:2] < cand[:2]:
                    cand = val
            best[j][i] = cand
    cuts, i = [], N
    for j in range(k, 0, -1):
        p = best[j][i][2]
        cuts.append((p, i))
        i = p
    return [sections[a:b] for a, b in reversed(cuts)]


def _col_units(col):
    return sum(section_units(s) + GAP_UNITS for s in col)


def _split_fill(sections, n):
    """Greedy fill of n columns to the same height; a long section (>= 6 rows) may be continued in the next column
    (each part keeps the title with a "(i/m)" suffix)."""
    T = sum(section_units(s) + GAP_UNITS for s in sections) / n
    cols, cur = [[]], 0.0
    for si, sec in enumerate(sections):
        rows = list(sec.rows)
        while rows:
            need = HEAD + FOOT + len(rows) + GAP_UNITS
            cap = T - cur
            if len(cols) >= n or not cols[-1] or need <= cap + 2.0:
                cols[-1].append((si, rows))
                cur += need
                break
            k = int(cap - HEAD - FOOT - GAP_UNITS)
            if len(rows) >= 6 and k >= 3 and len(rows) - k >= 3:
                cols[-1].append((si, rows[:k]))
                rows = rows[k:]
            cols.append([])
            cur = 0.0
    cnt = {}
    for c in cols:
        for si, _ in c:
            cnt[si] = cnt.get(si, 0) + 1
    seen, out = {}, []
    for c in cols:
        g = []
        for si, rows in c:
            sec = sections[si]
            if cnt[si] > 1:
                seen[si] = seen.get(si, 0) + 1
                sec = Section("%s (%d/%d)" % (sec.title, seen[si], cnt[si]), sec.color, sec.icon, rows)
            g.append(sec)
        if g:
            out.append(g)
    return out or [[]]


def partition(sections, n):
    """Split the sections into at most n columns of similar height; long sections may continue in the next column
    when that balances the columns clearly better than whole sections can."""
    base = _partition_dp(sections, n)
    if len(base) < 2 and n < 2 or not sections:
        return base
    n = max(1, min(int(n), 4))
    if n < 2:
        return base
    try:
        alt = _split_fill(sections, n)
    except Exception:
        return base
    hb = max(_col_units(c) for c in base)
    ha = max(_col_units(c) for c in alt)
    return alt if len(alt) >= len(base) and ha < hb * 0.92 else base


class Plan:
    """Result of plan(): everything build() needs to place the columns."""
    __slots__ = ("W", "H", "ncol", "cols", "m", "g", "gut", "cwc", "rh", "rh_fit", "with_bar", "heights", "fits", "sp", "pitch", "gaps")


def plan(sections, W, H, setting="auto", scale=100, spacing=1.0):
    p = Plan()
    p.W, p.H, p.sp = W, H, spacing
    p.cols = partition(sections, resolve_columns(setting, W, H))
    p.ncol = len(p.cols)
    p.m = max(10.0, min(W, H) * 0.016)
    p.g = max(8.0, H * 0.005)
    p.gut = p.m * 0.9 if p.ncol > 1 else 0.0
    p.cwc = (W - 2 * p.m - (p.ncol - 1) * p.gut) / p.ncol
    units = [sum(section_units(s, spacing) for s in c) for c in p.cols]
    nsec = [len(c) for c in p.cols]
    p.rh_fit = min((H - 2 * p.m - n * p.g) / (u + STATUS) for u, n in zip(units, nsec))
    base = min(p.rh_fit, max(MIN_RH, p.cwc * RH_CAP))
    try:
        sc = min(1.5, max(0.8, float(scale) / 100.0))
    except (TypeError, ValueError):
        sc = 1.0
    p.rh = max(MIN_RH, min(p.rh_fit, base * sc))
    # stretch each column to the available height: row pitch first (up to +60 %), then the gaps between the cards
    avail = H - 2 * p.m - STATUS * p.rh
    p.pitch, p.gaps = [], []
    for c, n in zip(p.cols, nsec):
        nrows = sum(len(s.rows) for s in c)
        fixed = n * (HEAD + FOOT) * p.rh
        free = avail - fixed - nrows * spacing * p.rh - n * p.g
        k = 1.0
        if nrows and free > 0:
            k = min(1.6, 1.0 + free / (nrows * spacing * p.rh))
        free2 = avail - fixed - nrows * spacing * p.rh * k - n * p.g
        gap = p.g + (min(free2 / max(1, n), p.g * 4) if free2 > 0 else 0.0)
        p.pitch.append(spacing * p.rh * k)
        p.gaps.append(gap)
    p.heights = [u * p.rh + n * p.g for u, n in zip(units, nsec)]
    p.fits = 2 * p.m + max(p.heights) + STATUS * p.rh <= H + 0.5
    p.with_bar = p.cwc >= BAR_MIN_COL_W
    return p


# ------------------------------------------------------------------ colours / limits (gradient)
# the gradient itself (smooth or stepped) belongs to the active theme, see themes.py


def frac(row, v):
    """Position of the value within the limits (0..1), for the bar; grows left->right even when reversed."""
    if not row.limits:
        return None
    lo, hi = min(row.limits), max(row.limits)
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))


def value_color(row, v, default):
    """Colour of a value between the row's green and red limit (theme gradient, smooth or stepped)."""
    return themes.value_color(row, v, default)


# ================================================================== auto-detection
_NON_MB = re.compile(r"^(CPU \[|dGPU|iGPU|GPU \[|Drive:|S\.M\.A\.R\.T\.:|Network:|System:|Memory Timings|PresentMon|"
                     r"DDR\d|DIMM|Windows Hardware|Battery|Smart Battery|metriQ|Enhanced)", re.I)
_VIRTUAL = re.compile(r"virtual|vpn|loopback|hyper-v|vmware|vbox|tap-|tap |tun|bluetooth|vethernet|wsl|npcap|"
                      r"miniport|pseudo|teredo|isatap|wan |ndis|zerotier|tailscale|wireguard", re.I)
MAX_CORE_ROWS = 16


def _R(name, sensor, label, rtype=None, unit=None, limits=None, fmt=None, bar=None, help=None):
    d = {"name": name, "label": label}
    if sensor:
        d["sensor"] = sensor
    if rtype is not None:
        d["type"] = rtype
    if fmt:
        d["format"] = fmt
    if unit:
        d["unit"] = unit
    if limits:
        d["limits"] = list(limits)
    if bar is not None:
        d["bar"] = bar
    if help:
        d["help"] = help
    return d


class _Index:
    def __init__(self, rd):
        self.rd = rd
        self.sensors = []
        for r in rd:
            if r["sensor"] not in self.sensors:
                self.sensors.append(r["sensor"])

    def readings(self, sensor_re, label_re=None, rtype=None):
        sre = re.compile(sensor_re, re.I) if sensor_re else None
        lre = re.compile(label_re, re.I) if label_re else None
        return [r for r in self.rd
                if (sre is None or sre.search(r["sensor"])) and (lre is None or lre.search(r["label"]))
                and (rtype is None or r["type"] == rtype)]

    def first(self, sensor_re, label_res, rtype=None):
        """(label_pattern, reading) for the first pattern in label_res that matches a reading."""
        if isinstance(label_res, str):
            label_res = [label_res]
        for lp in label_res:
            rs = self.readings(sensor_re, lp, rtype)
            if rs:
                return lp, rs[0]
        return None, None

    def sensors_like(self, pattern):
        p = re.compile(pattern, re.I)
        return [s for s in self.sensors if p.search(s)]


def _row_if(ix, name, sensor_re, label_res, rtype=None, limits=None, unit=None, fmt=None):
    lp, r = ix.first(sensor_re, label_res, rtype)
    return _R(name, sensor_re, lp, rtype, unit, limits, fmt) if lp else None


def _throttle_row(ix, name_key, label):
    """CPU / GPU throttling (HWiNFO yes/no sensors, combined by throttle.derive); no row without them."""
    sre, lre = "^" + re.escape(throttle.SENSOR) + "$", "^" + re.escape(label) + "$"
    if not ix.readings(sre, lre):
        return None
    return _R(t(name_key), sre, lre, 8, None, (0, 1), "@throttle", bar=False, help="throttle")


def _battery_rows(ix):
    """Battery section rows (laptops). Sensors: HWiNFO / LHM "Battery: ..." or the built-in "Battery: System";
    state and remaining time always come from the built-in reader (the time reading only exists while discharging,
    so its row stays and shows a dash otherwise)."""
    sre = r"^(Smart )?Battery"
    if not ix.sensors_like(sre):
        return []
    rows = []
    for key, name_key, unit, limits, fmt, bar, hlp in battery.ROWS:
        probe = "state" if key == "time" else key
        if ix.first(sre, battery.LABELS[probe])[0]:
            rows.append(_R(t(name_key), sre, battery.LABELS[key], None, unit, limits, fmt, bar, hlp))
    return rows


def _scaled(unit, lo, hi):
    """Limits given in KB/s, converted to the unit HWiNFO reports."""
    k = {"kb/s": 1.0, "mb/s": 1 / 1024.0, "gb/s": 1 / 1048576.0, "b/s": 1024.0}.get((unit or "").lower(), 1.0)
    return (lo * k, hi * k)


def autodetect(rd):
    """Default sections (config schema, current language) built from the readings. Missing rows are omitted."""
    ix = _Index(rd)
    sections = []

    def add(title_key, icon, rows):
        rows = [r for r in rows if r]
        if rows:
            sections.append({"title": t(title_key), "color": ICON_COLORS[icon], "icon": icon, "rows": rows})

    cpu = r"^CPU \[#0\]"
    cpus = ix.sensors_like(cpu)
    gpus = [s for p in (r"^dGPU \[#", r"^GPU \[#", r"^iGPU \[#") for s in ix.sensors_like(p)]
    gm = re.match(r"^(\w+) \[#(\d+)\]", gpus[0]) if gpus else None
    gpu = r"^%s \[#%s\]" % (gm.group(1), gm.group(2)) if gm else None

    # ---------------- clock / usage
    rows = []
    if cpus:
        core_re = re.compile(r"^((?:P|E|LP E)-core|Core) ?(\d+)(?: T\d+)? Clock\b", re.I)
        seen = set()
        for r in ix.readings(cpu, rtype=6):
            m = core_re.match(r["label"])
            if not m or "effective" in r["label"].lower():
                continue
            base = f"{m.group(1)} {m.group(2)}"
            if base in seen:
                continue
            seen.add(base)
            if len(seen) > MAX_CORE_ROWS:
                break
            rows.append(_R(f"CPU {base} {t('clock')}", cpu, "^" + re.escape(base) + r"(?: T\d+)? Clock\b", 6,
                           "MHz", (1000, 6000)))
        if not seen:      # LibreHardwareMonitor: "Core #1" (clock group)
            for r in ix.readings(cpu, r"^Core #\d+$", 6)[:MAX_CORE_ROWS]:
                n = r["label"].split("#")[1]
                rows.append(_R(f"CPU Core {n} {t('clock')}", cpu, "^Core #" + n + "$", 6, "MHz", (1000, 6000)))
        rows.append(_row_if(ix, t("cpu_usage"), cpu, [r"^Total CPU Usage", r"^CPU Total$"], 7, (0, 100)))
        rows.append(_row_if(ix, t("cpu_maxthread"), cpu, [r"^Max CPU/Thread Usage", r"^CPU Core Max$"], 7,
                            (0, 100)))
        rows.append(_throttle_row(ix, "cpu_throttle", throttle.CPU_LABEL))
    if gpu:
        rows.append(_row_if(ix, t("gpu_clock"), gpu, [r"^GPU Clock$", r"^GPU Core$"], 6, (200, 3200)))
        rows.append(_row_if(ix, t("gpu_load"), gpu, [r"^GPU Core Load", r"^GPU Utili[sz]ation", r"^GPU Load",
                                                     r"^GPU Core$"], 7, (0, 100)))
        rows.append(_throttle_row(ix, "gpu_throttle", throttle.GPU_LABEL))
        lp, used = ix.first(gpu, [r"^GPU D3D Memory Dedicated$", r"^GPU Memory Allocated$", r"^GPU Memory Used$"], 8)
        if lp:
            _, alloc = ix.first(gpu, [r"^GPU Memory Allocated$", r"^GPU Memory Used$"], 8)
            _, avail = ix.first(gpu, [r"^GPU Memory Available$", r"^GPU Memory Free$"], 8)
            _, tot = ix.first(gpu, r"^GPU Memory Total$", 8)
            total = tot["value"] if tot else (alloc["value"] + avail["value"] if alloc and avail else 0)
            total = round(total / 1024) * 1024
            if total < 1024:
                total = 8192
            rows.append(_R(t("vram"), gpu, lp, 8, "MB", (0, total)))
    sysre = r"^System:"
    lp, used = ix.first(sysre, [r"^Physical Memory Used$", r"^Memory Used$"], 8)
    if lp:
        _, avail = ix.first(sysre, [r"^Physical Memory Available$", r"^Memory Available$"], 8)
        total = round((used["value"] + (avail["value"] if avail else 0)) / 1024) * 1024
        rows.append(_R(t("ram"), sysre, lp, 8, "MB", (0, total if total >= 2048 else 16384)))
    add("sec_clock", "gauge", rows)

    # ---------------- power
    rows = []
    if cpus:
        rows.append(_row_if(ix, t("cpu_pkg_power"), cpu,
                            [r"^CPU Package Power$", r"^CPU Total Power", r"^Total CPU Power", r"^CPU Package Power",
                             r"^CPU (?!.*SVI3)(?!PPT)(?!Core)(?!SoC).*Power", r"^CPU PPT$", r"^Package$",
                             r"^CPU Package$"], 5, (10, 150)))
        rows.append(_row_if(ix, t("cpu_core_power"), cpu, [r"^CPU Core Power", r"^IA Cores Power", r"^CPU Cores$"],
                            5, (5, 120)))
        rows.append(_row_if(ix, t("cpu_soc_power"), cpu, r"^CPU SoC Power", 5, (5, 30)))
        rows.append(_row_if(ix, t("cpu_core_volt"), cpu, [r"^CPU VDDCR_VDD Voltage", r"^Core \(SVI\d TFN\)$",
                                                          r"^CPU Core$"], 2, (0.7, 1.5)))
    if gpu:
        rows.append(_row_if(ix, t("gpu_power"), gpu, [r"^GPU Power$", r"^GPU (?:ASIC|Chip) Power", r"^GPU Power",
                                                      r"^GPU Package$"], 5, (20, 300)))
        rows.append(_row_if(ix, t("gpu_volt"), gpu, [r"^GPU Core Voltage$", r"^GPU Core$"], 2, (0.7, 1.2)))
    # de-duplicate (the same reading must not appear twice)
    seen_lbl, uniq = set(), []
    for r in rows:
        if r and (r["sensor"], r["label"]) not in seen_lbl:
            seen_lbl.add((r["sensor"], r["label"]))
            uniq.append(r)
    add("sec_power", "bolt", uniq)

    # ---------------- temperatures
    rows = []
    if cpus:
        rows.append(_row_if(ix, t("cpu_temp"), cpu,
                            [r"^CPU \(Tctl/Tdie\)", r"^CPU \(Tdie\)", r"^CPU Package$", r"^Core \(Tctl/Tdie\)$",
                             r"^Core \(Tdie\)$", r"^Core \(Tctl\)$"], 1, (35, 95)))
        ccd_pfx = "CPU CCD" if ix.readings(cpu, r"^CPU CCD\d+ \(Tdie\)", 1) else "CCD"    # HWiNFO / LHM
        for r in ix.readings(cpu, "^" + ccd_pfx + r"(\d+) \(Tdie\)", 1):
            n = re.search(r"CCD(\d+)", r["label"]).group(1)
            rows.append(_R(t("cpu_ccd", n=n), cpu, "^" + ccd_pfx + n + r" \(Tdie\)", 1, None, (30, 95)))
        rows.append(_row_if(ix, t("cpu_iod"), cpu, r"^CPU IOD Hotspot", 1, (35, 90)))
        rows.append(_row_if(ix, t("cpu_core_max"), cpu, [r"^Core Max$", r"^CCDs Max \(Tdie\)$"], 1, (35, 95)))
    mbs = [s for s in ix.sensors if not _NON_MB.search(s)]
    for s in mbs:
        sre = "^" + re.escape(s) + "$"
        for r in ix.readings(sre, rtype=1):
            lbl = r["label"]
            ok = re.search(r"VRM|MOS|PCH|Chipset|^System|Motherboard|Mainboard|Socket|^MB", lbl, re.I) or re.search(r"Chipset", s, re.I)
            if not ok or not (1 <= r["value"] < 150):
                continue
            lim = (30, 105) if re.search(r"VRM|MOS", lbl, re.I) else (30, 90)
            rows.append(_R(lbl if re.match(r"(?i)motherboard|mainboard", lbl) else t("mb", n=lbl), sre, "^" + re.escape(lbl) + "$", 1, None, lim))
    if gpu:
        rows.append(_row_if(ix, t("gpu_temp"), gpu, [r"^GPU Temperature", r"^GPU Core$"], 1, (30, 88)))
        rows.append(_row_if(ix, t("gpu_hot"), gpu, [r"^GPU Hot ?Spot Temperature", r"^GPU Hot ?Spot$"], 1,
                            (35, 100)))
        rows.append(_row_if(ix, t("gpu_memtemp"), gpu, [r"^GPU Memory Junction Temperature",
                                                        r"^GPU Memory Temperature", r"^GPU Memory Junction$",
                                                        r"^GPU Memory$"], 1, (40, 105)))
    for s in ix.sensors_like(r"^S\.M\.A\.R\.T\.:")[:4]:
        sre = "^" + re.escape(s) + "$"
        lp, _ = ix.first(sre, [r"^Drive Temperature$", r"^Drive Temperature", r"^Composite Temperature$",
                               r"^Temperature$", r"^Temperature #?1$"], 1)
        if lp:
            model = re.sub(r"^S\.M\.A\.R\.T\.:\s*", "", s)
            model = re.sub(r"\s*\[[^\]]*\]\s*$", "", model)
            model = re.sub(r"\s*\([^)]*\)\s*$", "", model) or t("drive")
            rows.append(_R(model, sre, lp, 1, None, (30, 80)))
    for s in ix.sensors_like(r"^DDR\d DIMM")[:4]:
        sre = "^" + re.escape(s) + "$"
        lp, _ = ix.first(sre, r"^SPD Hub", 1)
        if lp:
            m = re.search(r"CHANNEL (\w+)/DIMM (\d+)", s, re.I)
            n = (m.group(1) + m.group(2)) if m else re.search(r"\[#(\d+)\]", s).group(1) if "[#" in s else "?"
            rows.append(_R(t("dimm", n=n), sre, lp, 1, None, (30, 85)))
    for i, s in enumerate(ix.sensors_like(r"^DIMM: ")[:4]):       # LibreHardwareMonitor
        sre = "^" + re.escape(s) + "$"
        r = next(iter(ix.readings(sre, rtype=1)), None)
        if r:
            rows.append(_R(t("dimm", n=i + 1), sre, "^" + re.escape(r["label"]) + "$", 1, None, (30, 85)))
    add("sec_temp", "temp", rows)

    # ---------------- fans
    rows = []
    for s in mbs:
        sre = "^" + re.escape(s) + "$"
        for r in ix.readings(sre, rtype=3):
            if r["value"] > 0:
                rows.append(_R(r["label"], sre, "^" + re.escape(r["label"]) + "$", 3, "RPM", (0, 3000)))
    if gpu:
        r = next(iter(ix.readings(gpu, r"^GPU Fan", 3)), None)
        if r:      # GPU fans legitimately stop at idle: keep the row
            rows.append(_R(t("gpu_fan"), gpu, "^" + re.escape(r["label"]) + "$", 3, "RPM", (0, 3300)))
        else:      # only a duty cycle (built-in source with an NVML that has no RPM)
            r = next(iter(ix.readings(gpu, r"^GPU Fan\d* Speed$", 7)), None)
            if r:
                rows.append(_R(t("gpu_fan"), gpu, "^" + re.escape(r["label"]) + "$", 7, "%", (0, 100)))
    add("sec_fan", "fan", rows)

    # ---------------- network / disk
    rows = []
    nets = ix.sensors_like(r"^Network:")
    if nets:
        good = [s for s in nets if not _VIRTUAL.search(s)] or nets

        def traffic(s):
            sr = "^" + re.escape(s) + "$"
            return sum(r["value"] for r in ix.readings(sr, r"^Total (DL|UP)|^Data (Up|Down)loaded$")) + \
                sum(r["value"] for r in ix.readings(sr, r"^Current (DL|UP) rate|^(Up|Down)load Speed$"))
        best = max(good, key=lambda s: (traffic(s), -good.index(s)))
        sre = "^" + re.escape(best) + "$"
        lp, r = ix.first(sre, [r"^Current DL rate", r"^Download Speed$"], 8)
        unit = r["unit"] if r else "KB/s"
        rows.append(_row_if(ix, t("download"), sre, [r"^Current DL rate", r"^Download Speed$"], 8,
                            _scaled(unit, 0, 100000)))
        rows.append(_row_if(ix, t("upload"), sre, [r"^Current UP rate", r"^Upload Speed$"], 8,
                            _scaled(unit, 0, 50000)))
    drives = ix.sensors_like(r"^Drive:")
    if drives:
        d = next((s for s in drives if re.search(r"\[C:\]", s)), drives[0])
        sre = "^" + re.escape(d) + "$"
        rows.append(_row_if(ix, t("disk_read"), sre, r"^Read Rate$", 8, (0, 3000)))
        rows.append(_row_if(ix, t("disk_write"), sre, r"^Write Rate$", 8, (0, 3000)))
    add("sec_net", "net", rows)

    # ---------------- battery (only with a system battery)
    add("sec_battery", "bolt", _battery_rows(ix))

    # ---------------- fps
    pm = r"^PresentMon"
    rows = [
        _row_if(ix, t("fps"), pm, [r"^Framerate Presented \(avg\)", r"^Framerate \(avg\)", r"^Framerate$"], None,
                (165, 30), "fps"),
        _row_if(ix, t("fps_1"), pm, [r"^Framerate Presented \(1% low\)", r"^Framerate \(1% low\)"], None,
                (120, 20), "fps"),
        _row_if(ix, t("fps_01"), pm, [r"^Framerate Presented \(0\.1% low\)", r"^Framerate \(0\.1% low\)"], None,
                (90, 15), "fps"),
    ]
    add("sec_fps", "fps", rows)
    return sections
