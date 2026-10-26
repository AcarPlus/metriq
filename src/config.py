"""User configuration (JSON in %LOCALAPPDATA%\\HWiNFO Panel) and monitor enumeration."""
import copy
import ctypes
import glob
import json
import os
import shutil
import sys
from ctypes import wintypes

APP_NAME = "HWiNFO Panel"
APP_ID = "HWiNFOPanel.App"
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))            # built with PyInstaller (packaging/build.ps1)
# folder with the bundled resources (panel.ico, ...): PyInstaller's _internal folder, else the source folder
APP_DIR = getattr(sys, "_MEIPASS", HERE) if FROZEN else HERE

INTERVAL_MIN, INTERVAL_MAX = 0.5, 30.0
SCALE_MIN, SCALE_MAX = 80, 150

DEFAULTS = {
    "backend": "auto",               # "auto" | "hwinfo" | "lhm" | "builtin" (sensor data source, see README)
    "language": "auto",              # "auto" (Windows UI language) | "tr" | "en"
    "monitor": "auto",               # "auto" | monitor index (see tools/list_monitors.py)
    "window_mode": "maximized",      # "maximized" | "fullscreen" | "window"
    "columns": "auto",               # "auto" | 1 | 2 | 3 (auto: 1 on portrait screens, else 2 or 3 by aspect ratio)
    "ui_scale": 100,                 # row height / font scale in percent (80 - 150), never larger than fits
    "topmost": True,
    "check_updates": True,           # look for a newer release on GitHub at start and every 24 h (README: Privacy)
    "interval": 5.0,                 # CSV logging interval, seconds (0.5 - 30)
    "logging": False,                # CSV logging on/off
    "log_all": False,                # False: only the rows shown, True: every sensor reading
    "log_dir": "",                   # "" = %LOCALAPPDATA%\HWiNFO Panel\logs
    "csv_delimiter": "auto",         # "auto" (Windows list separator) or e.g. ";" / ","
    "csv_decimal": "auto",           # "auto" (Windows decimal symbol) or "," / "."
    "sync_hwinfo_interval": True,    # write the logging interval to HWiNFO's SensorInterval (needs install.ps1)
    "manage_hwinfo": True,           # start HWiNFO with the panel, stop it on close (needs install.ps1)
    "hwinfo_path": "auto",           # "auto" or full path of HWiNFO64.EXE
    "lhm_url": "auto",               # LibreHardwareMonitor web server; "auto" = http://127.0.0.1:8085/data.json
    "presentmon_path": "",           # optional: PresentMon console exe for FPS with the lhm / builtin source
    "sections": "auto",              # "auto" (detected from your hardware) or a list, see README
                                     # (the lhm / builtin sources use "sections_lhm" / "sections_builtin")
}


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


def data_dir():
    d = os.environ.get("HWINFO_PANEL_DIR")
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
    installer ran as another account); otherwise (install.ps1, HWINFO_PANEL_DIR) the per-user data folder."""
    if not os.environ.get("HWINFO_PANEL_DIR"):
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
    if not FROZEN or os.environ.get("HWINFO_PANEL_DIR") or os.path.exists(path):
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
    except Exception as ex:
        note = f"config.json could not be read ({ex}); using defaults"
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
    cfg["interval"] = clamp_interval(cfg.get("interval"))
    cfg["columns"] = norm_columns(cfg.get("columns"))
    cfg["ui_scale"] = clamp_scale(cfg.get("ui_scale"))
    cfg["backend"] = norm_backend(cfg.get("backend"))
    return cfg, note


def clamp_interval(v):
    try:
        v = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        v = DEFAULTS["interval"]
    return round(min(INTERVAL_MAX, max(INTERVAL_MIN, v)), 1)


def norm_columns(v):
    """'auto' | 1 | 2 | 3 (anything else -> 'auto')."""
    try:
        n = int(v)
        if n in (1, 2, 3):
            return n
    except (TypeError, ValueError):
        pass
    return "auto"


def layout_key(source):
    """Config key of the layout of a data source: "sections" (HWiNFO), "sections_lhm", "sections_builtin".
    The sources name their sensors differently, so each one keeps its own layout."""
    return "sections" if source in (None, "", "hwinfo") else "sections_" + str(source)


def norm_backend(v):
    v = str(v or "auto").strip().lower()
    return v if v in ("auto", "hwinfo", "lhm", "builtin") else "auto"


def clamp_scale(v):
    try:
        v = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        v = DEFAULTS["ui_scale"]
    return int(round(min(SCALE_MAX, max(SCALE_MIN, v))))


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
