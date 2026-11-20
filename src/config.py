"""User configuration (JSON in %LOCALAPPDATA%\\metriQ) and monitor enumeration."""
import copy
import ctypes
import glob
import json
import os
import shutil
import sys
from ctypes import wintypes

APP_NAME = "metriQ"
APP_ID = "metriQ.App"
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))            # built with PyInstaller (packaging/build.ps1)
# folder with the bundled resources (metriq.ico, ...): PyInstaller's _internal folder, else the source folder
APP_DIR = getattr(sys, "_MEIPASS", HERE) if FROZEN else HERE

INTERVAL_MIN, INTERVAL_MAX = 0.5, 30.0

DEFAULTS = {
    "backend": "auto",               # "auto" | "hwinfo" | "lhm" | "builtin" (sensor data source, see README)
    "language": "auto",              # "auto" (Windows UI language) | "en" "tr" "de" "es" "ru" "pt"
    "monitor": "auto",               # "auto" | monitor index (see tools/list_monitors.py)
    "window_mode": "maximized",      # "maximized" | "fullscreen" | "window"
    "columns": "auto",               # "auto" | 1..4 (auto: 1 on portrait screens, else 2 or 3 by aspect ratio)
    "theme": "dark",                 # built-in theme, see themes.py (dark, oled, nord, dracula, catppuccin, tokyo, ...)
    "theme_overrides": {},           # any theme token on top of the theme, e.g. {"bg": "#101010", "radius": 8}
    "value_color_mode": "smooth",    # "smooth" (gradient) | "stepped" (3-5 flat colour bands)
    "value_steps": [],               # stepped: [] = 4 equal bands, or 2-4 thresholds in % of each row's range
    "ui_mode": "basic",              # "basic" (few plain rows, plain words) | "advanced" (everything; your own layout)
    "ui_mode_hint_shown": False,     # the one-time "Basic view is on" notice was shown
    "topmost": True,
    "diag_errors": True,             # diagnostics tab: show the error-detection section
    "fan_risk_ok": False,            # fan page: the risk notice was accepted once
    "bg_keep_logging": False,        # closing the panel (X) keeps metriQ running in the tray while a recording runs
    "bg_keep_mini": False,           # ... or while mini mode is shown (otherwise closing the panel ends the program)
    "bg_hint_shown": False,          # the one-time "metriQ moved to the tray" notice was shown
    "check_updates": True,           # look for a newer release on GitHub at start and every 24 h (README: Privacy)
    "interval": 5.0,                 # CSV logging interval, seconds (0.5 - 30)
    "logging": False,                # CSV logging on/off
    "viewer_series": [],             # series keys ticked in the recording viewer (remembered)
    "log_all": False,                # False: only the rows shown, True: every sensor reading
    "log_dir": "",                   # "" = %LOCALAPPDATA%\metriQ\logs
    "csv_delimiter": "auto",         # "auto" (Windows list separator) or e.g. ";" / ","
    "csv_decimal": "auto",           # "auto" (Windows decimal symbol) or "," / "."
    "sync_hwinfo_interval": True,    # write the logging interval to HWiNFO's SensorInterval (needs install.ps1)
    "manage_hwinfo": True,           # start HWiNFO with the panel, stop it on close (needs install.ps1)
    "hwinfo_path": "auto",           # "auto" or full path of HWiNFO64.EXE
    "lhm_url": "auto",               # LibreHardwareMonitor web server; "auto" = http://127.0.0.1:8085/data.json
    "bench_ssd_drive": "auto",       # SSD benchmark: "auto" (the Temp folder's drive) or a drive letter like "D:"
    "presentmon_path": "",           # optional: PresentMon console exe for FPS with the lhm / builtin source
    "sections": "auto",              # "auto" (detected from your hardware) or a list, see README
                                     # (the lhm / builtin sources use "sections_lhm" / "sections_builtin")
    "minimode": {                    # in-game overlay strip (minimode.py, README: Mini mode)
        "enabled": False,            # show it when the panel starts (the hotkey / M key toggle it any time)
        "hotkey": "ctrl+shift+m",    # "ctrl+shift+m" | "ctrl+alt+m" | "ctrl+shift+f10" | "off"
        "corner": "tl",              # "tl" | "tr" | "bl" | "br"
        "x": 20, "y": 20,            # distance from that corner in pixels
        "monitor": "primary",        # "primary" or a monitor index (see "monitor" above)
        "orientation": "horizontal", # "horizontal" | "vertical"
        "opacity": 85,               # 30 - 100 (%)
        "font_size": 12,             # 8 - 40 (pt)
        "labels": True,              # short label in front of each value
        "background": "plate",       # "plate" (card colour) | "none" (only the text, colour-keyed)
        "metrics": "auto",           # "auto" (FPS, CPU / GPU temperature, usage, power) or a list of row keys (max 8)
    },
    "alerts": {                      # value alerts (alerts.py, README: Alerts)
        "enabled": False,            # watch the rules below
        "notify": True,              # Windows notification (the tray icon shows while one is pending)
        "sound": True,               # Windows "Exclamation" sound
        "rules": "auto",             # "auto" (CPU > 90 °C, GPU > 85 °C for 10 s) or a list, see alerts.py
    },
}

ALERT_MAX_RULES = 8

MINI_CORNERS = ("tl", "tr", "bl", "br")
MINI_HOTKEYS = ("ctrl+shift+m", "ctrl+alt+m", "ctrl+shift+f10", "off")
MINI_MAX_METRICS = 8


def _package_family():
    """Package family name when running as a packaged app (Microsoft Store Python), else None."""
    try:
        n = wintypes.UINT(0)
        k = ctypes.windll.kernel32
        if k.GetCurrentPackageFamilyName(ctypes.byref(n), None) != 122:     # ERROR_INSUFFICIENT_BUFFER
            return None
        buf = ctypes.create_unicode_buffer(n.value)
        if k.GetCurrentPackageFamilyName(ctypes.byref(n), buf) == 0:
            return buf.value
    except Exception:
        pass
    return None


def env_dir():
    """Data folder override (portable use / tests): METRIQ_DIR."""
    return os.environ.get("METRIQ_DIR") or ""


def data_dir():
    d = env_dir()
    if not d:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        fam = _package_family()
        if fam:
            # Store Python: writes to %LOCALAPPDATA% are silently redirected into the package folder, where the
            # scheduled tasks, Explorer and other programs can't see them. Use that real folder explicitly.
            base = os.path.join(base, "Packages", fam, "LocalCache", "Local")
        d = os.path.join(base, APP_NAME)
    return d


def config_path():
    return os.path.join(data_dir(), "config.json")


def shared_interval_file():
    """The shared file in ProgramData (created by the Setup, writable by all users)."""
    base = os.environ.get("ProgramData")
    return os.path.join(base, APP_NAME, "hwinfo_interval.txt") if base else None


def interval_file():
    """File the SetInterval task reads. The installer build uses the shared ProgramData file (works even when the
    installer ran as another account); otherwise (install.ps1, METRIQ_DIR) the per-user data folder."""
    if not env_dir():
        p = shared_interval_file()
        try:
            if p and os.path.isfile(p):
                open(p, "ab").close()                   # really writable for this user?
                return p
        except OSError:
            pass
    return os.path.join(data_dir(), "hwinfo_interval.txt")


def log_dir(cfg):
    d = (cfg.get("log_dir") or "").strip()
    if not d or d.lower() == "auto":
        return os.path.join(data_dir(), "logs")
    return os.path.expandvars(os.path.expanduser(d))


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _migrate_store_config(path):
    """Frozen app, first run: take over config.json written by the Store-Python source version (its data
    lived in the Python package folder). Only config.json is copied, never logs."""
    if not FROZEN or env_dir() or os.path.exists(path):
        return
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return
    pat = os.path.join(glob.escape(base), "Packages", "PythonSoftwareFoundation.Python.3.*",
                       "LocalCache", "Local", APP_NAME, "config.json")
    old = [p for p in glob.glob(pat) if os.path.isfile(p)]
    if not old:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(max(old, key=os.path.getmtime), path)
    except OSError:
        pass


def load_config():
    """Returns (cfg, note). Creates the file on first run; a broken file is backed up, never lost."""
    path = config_path()
    _migrate_store_config(path)
    cfg = copy.deepcopy(DEFAULTS)
    note = None
    if not os.path.exists(path):
        try:
            _write(path, {k: v for k, v in DEFAULTS.items()})
            note = "created"
        except OSError:
            pass
        return cfg, note
    try:
        with open(path, encoding="utf-8-sig") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("config root must be an object")
        cfg.update(user)
        if "ui_mode" not in user:      # an existing install keeps its familiar full view; only fresh installs start Basic
            cfg["ui_mode"] = "advanced"
            try:
                _write(path, {**user, "ui_mode": "advanced"})
            except OSError:
                pass
    except Exception as ex:
        note = f"config.json could not be read ({ex}); using defaults"
        cfg["ui_mode"] = "advanced"
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
    cfg["interval"] = clamp_interval(cfg.get("interval"))
    cfg["columns"] = norm_columns(cfg.get("columns"))
    cfg.pop("ui_scale", None)        # removed feature: ignore old values
    cfg["backend"] = norm_backend(cfg.get("backend"))
    cfg["minimode"] = norm_minimode(cfg.get("minimode"))
    cfg["alerts"] = norm_alerts(cfg.get("alerts"))
    cfg["ui_mode"] = norm_ui_mode(cfg.get("ui_mode"))
    for k in ("bg_keep_logging", "bg_keep_mini", "bg_hint_shown", "ui_mode_hint_shown"):      # older / hand-edited files: anything but a boolean = default
        if not isinstance(cfg.get(k), bool):
            cfg[k] = DEFAULTS[k]
    return cfg, note


def clamp_interval(v):
    try:
        v = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        v = DEFAULTS["interval"]
    return round(min(INTERVAL_MAX, max(INTERVAL_MIN, v)), 1)


def norm_ui_mode(v):
    """'basic' | 'advanced' (a missing or unknown value means basic)."""
    return "advanced" if str(v or "").strip().lower() == "advanced" else "basic"


def norm_columns(v):
    """'auto' | 1..4 (anything else -> 'auto')."""
    try:
        n = int(v)
        if n in (1, 2, 3, 4):
            return n
    except (TypeError, ValueError):
        pass
    return "auto"


def _int_in(v, lo, hi, default):
    try:
        return int(min(hi, max(lo, round(float(str(v).replace(",", "."))))))
    except (TypeError, ValueError):
        return default


def norm_minimode(v):
    """The "minimode" dict with every key present and valid (older config files have none, hand edits may be partial)."""
    d = copy.deepcopy(DEFAULTS["minimode"])
    if not isinstance(v, dict):
        return d
    flag = lambda x, default: x if isinstance(x, bool) else default
    d["enabled"] = flag(v.get("enabled"), d["enabled"])
    d["labels"] = flag(v.get("labels"), d["labels"])
    hk = str(v.get("hotkey", d["hotkey"])).strip().lower()
    d["hotkey"] = hk if hk in MINI_HOTKEYS else d["hotkey"]
    cn = str(v.get("corner", d["corner"])).strip().lower()
    d["corner"] = cn if cn in MINI_CORNERS else d["corner"]
    d["x"] = _int_in(v.get("x"), 0, 9999, d["x"])
    d["y"] = _int_in(v.get("y"), 0, 9999, d["y"])
    mon = str(v.get("monitor", d["monitor"])).strip().lower()
    d["monitor"] = int(mon) if mon.isdigit() else "primary"
    d["orientation"] = "vertical" if str(v.get("orientation", "")).strip().lower() == "vertical" else "horizontal"
    d["opacity"] = _int_in(v.get("opacity"), 30, 100, d["opacity"])
    d["font_size"] = _int_in(v.get("font_size"), 8, 40, d["font_size"])
    d["background"] = "none" if str(v.get("background", "")).strip().lower() == "none" else "plate"
    m = v.get("metrics", "auto")
    if isinstance(m, list):
        keys = []
        for k in m:
            if isinstance(k, str) and k not in keys:
                keys.append(k)
        d["metrics"] = keys[:MINI_MAX_METRICS]
    return d


def norm_alerts(v):
    """The "alerts" dict with every key present and valid; rules: "auto" or a clean list (at most ALERT_MAX_RULES)."""
    d = copy.deepcopy(DEFAULTS["alerts"])
    if not isinstance(v, dict):
        return d
    for k in ("enabled", "notify", "sound"):
        if isinstance(v.get(k), bool):
            d[k] = v[k]
    rules = v.get("rules", "auto")
    if isinstance(rules, list):
        out = []
        for r in rules:
            if not isinstance(r, dict) or not isinstance(r.get("metric"), str) or not r["metric"]:
                continue
            try:
                val = float(str(r.get("value")).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if val != val or abs(val) > 1e9:            # NaN / absurd
                continue
            out.append({"metric": r["metric"], "name": str(r.get("name") or "")[:80],
                        "op": "<" if r.get("op") == "<" else ">", "value": round(val, 2),
                        "secs": _int_in(r.get("secs"), 0, 3600, 10)})
        d["rules"] = out[:ALERT_MAX_RULES]
    return d


def layout_key(source):
    """Config key of the layout of a data source: "sections" (HWiNFO), "sections_lhm", "sections_builtin".
    The sources name their sensors differently, so each one keeps its own layout."""
    return "sections" if source in (None, "", "hwinfo") else "sections_" + str(source)


def norm_backend(v):
    v = str(v or "auto").strip().lower()
    return v if v in ("auto", "hwinfo", "lhm", "builtin") else "auto"


def save_keys(cfg, *keys):
    """Update only the given keys in config.json (keeps the rest, e.g. hand-edited sections). True on success."""
    path = config_path()
    try:
        with open(path, encoding="utf-8-sig") as f:
            cur = json.load(f)
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {k: v for k, v in DEFAULTS.items()}
    for k in keys:
        cur[k] = cfg[k]
    try:
        _write(path, cur)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ locale (CSV separators)
def _locale_str(lctype, default):
    try:
        buf = ctypes.create_unicode_buffer(16)
        if ctypes.windll.kernel32.GetLocaleInfoW(0x0400, lctype, buf, 16):   # LOCALE_USER_DEFAULT
            return buf.value or default
    except Exception:
        pass
    return default


def csv_format(cfg):
    """(delimiter, decimal) so that Excel / LibreOffice open the file correctly on this PC."""
    dec = str(cfg.get("csv_decimal", "auto"))
    delim = str(cfg.get("csv_delimiter", "auto"))
    if dec == "auto":
        dec = _locale_str(0x0E, ".")                 # LOCALE_SDECIMAL
    if delim == "auto":
        delim = _locale_str(0x0C, ",")               # LOCALE_SLIST
    if delim == dec:                                  # would be ambiguous: fall back to the safe pair
        delim = ";" if dec == "," else ","
    return delim[:1] or ",", dec[:1] or "."


# ------------------------------------------------------------------ monitors
class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def monitors():
    """List of dicts: index, x, y, w, h (full monitor), wx, wy, ww, wh (work area), primary."""
    out = []
    try:
        u = ctypes.windll.user32
        proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(_RECT),
                                    wintypes.LPARAM)
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]

        def cb(hmon, hdc, rect, lparam):
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                m, w = mi.rcMonitor, mi.rcWork
                out.append(dict(index=len(out), x=m.left, y=m.top, w=m.right - m.left, h=m.bottom - m.top,
                                wx=w.left, wy=w.top, ww=w.right - w.left, wh=w.bottom - w.top,
                                primary=bool(mi.dwFlags & 1)))
            return True

        u.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    except Exception:
        pass
    return out


def pick_monitor(setting, mons=None):
    """'auto' = first non-primary portrait monitor, else the primary one. Returns a dict or None."""
    mons = monitors() if mons is None else mons
    if not mons:
        return None
    if str(setting).strip().lower() not in ("", "auto"):
        try:
            i = int(setting)
            if 0 <= i < len(mons):
                return mons[i]
        except (TypeError, ValueError):
            pass
    for m in mons:
        if not m["primary"] and m["h"] > m["w"]:
            return m
    return next((m for m in mons if m["primary"]), mons[0])
