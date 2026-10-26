"""Controlling HWiNFO from the panel: detection, process check, elevated scheduled tasks (see install.ps1)."""
import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes

import config

TASK_START = r"metriQ\Start"
TASK_STOP = r"metriQ\Stop"
TASK_SETINT = r"metriQ\SetInterval"
NOWIN = 0x08000000  # CREATE_NO_WINDOW
EXE_NAME = "HWiNFO64.EXE"


def detect_hwinfo(setting="auto"):
    """Path of HWiNFO64.EXE: explicit setting, registry (install location), common folders, PATH."""
    if setting and str(setting).lower() != "auto" and os.path.isfile(setting):
        return setting
    cands = []
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                # vendor key (if present): any value that is a path to the install folder / exe
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\HWiNFO64", 0, winreg.KEY_READ | view) as k:
                        i = 0
                        while True:
                            try:
                                v = winreg.EnumValue(k, i)[1]
                            except OSError:
                                break
                            i += 1
                            if isinstance(v, str):
                                cands += [v, os.path.join(v, EXE_NAME)]
                except OSError:
                    pass
                # uninstall entries whose key name mentions HWiNFO
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0,
                                        winreg.KEY_READ | view) as k:
                        for i in range(winreg.QueryInfoKey(k)[0]):
                            name = winreg.EnumKey(k, i)
                            if "hwinfo" not in name.lower():
                                continue
                            with winreg.OpenKey(k, name) as sk:
                                for val in ("InstallLocation", "Inno Setup: App Path", "DisplayIcon"):
                                    try:
                                        v = str(winreg.QueryValueEx(sk, val)[0]).strip('"').split(",")[0]
                                    except OSError:
                                        continue
                                    cands += [v, os.path.join(v, EXE_NAME)]
                except OSError:
                    pass
    except ImportError:
        pass
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "HWiNFO64", EXE_NAME))
    for c in cands:
        if c and os.path.isfile(c) and os.path.basename(c).lower() == EXE_NAME.lower():
            return c
    w = shutil.which(EXE_NAME)
    if w:
        return w
    return os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "HWiNFO64", EXE_NAME)


# ------------------------------------------------------------------ processes (no subprocess, no console)
class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def proc_running(exe_name=EXE_NAME):
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)       # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return False
    try:
        pe = _PE32()
        pe.dwSize = ctypes.sizeof(_PE32)
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        want = exe_name.lower()
        while ok:
            if pe.szExeFile.lower() == want:
                return True
            ok = k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k.CloseHandle(snap)
    return False


# ------------------------------------------------------------------ scheduled tasks installed by install.ps1
_tasks_cache = {}


def tasks_installed(refresh=False):
    if refresh or "v" not in _tasks_cache:
        try:
            _tasks_cache["v"] = subprocess.run(["schtasks", "/query", "/tn", TASK_START], capture_output=True,
                                               creationflags=NOWIN, timeout=10).returncode == 0
        except Exception:
            _tasks_cache["v"] = False
    return _tasks_cache["v"]


def run_task(name):
    """Elevated scheduled task: start/stop HWiNFO or change its interval without a UAC prompt."""
    try:
        return subprocess.run(["schtasks", "/run", "/tn", name], capture_output=True,
                              creationflags=NOWIN, timeout=10).returncode == 0
    except Exception:
        return False


# ------------------------------------------------------------------ HWiNFO polling interval
def hwinfo_interval_ms(exe):
    """SensorInterval from HWiNFO64.INI (ms); 2000 if unknown."""
    try:
        ini = os.path.join(os.path.dirname(exe), "HWiNFO64.INI")
        with open(ini, encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("SensorInterval="):
                    return int(line.split("=", 1)[1].strip())
    except Exception:
        pass
    return 2000


# HWiNFO64.INI settings the panel relies on (same list as set_interval.ps1 / setup.iss):
# shared memory on, sensors-only, started minimised to the tray, no welcome / progress dialogs
HW_SETTINGS = {"SensorsSM": "1", "SensorsOnly": "1", "MinimalizeSensors": "1", "MinimalizeMainWnd": "1",
               "MinimalizeSensorsClose": "1", "ShowWelcomeAndProgress": "0"}


def hwinfo_settings_ok(exe):
    """True if HWiNFO64.INI already has every setting in HW_SETTINGS."""
    try:
        ini = os.path.join(os.path.dirname(exe), "HWiNFO64.INI")
        with open(ini, encoding="ascii", errors="ignore") as f:
            have = dict(line.strip().split("=", 1) for line in f if "=" in line)
        return all(have.get(k) == v for k, v in HW_SETTINGS.items())
    except Exception:
        return False


def sm_enabled(exe):
    """True if HWiNFO64.INI has Shared Memory Support switched on."""
    try:
        ini = os.path.join(os.path.dirname(exe), "HWiNFO64.INI")
        with open(ini, encoding="ascii", errors="ignore") as f:
            return any(line.strip() == "SensorsSM=1" for line in f)
    except Exception:
        return False


def repair_hwinfo(sec, exe):
    """Restart HWiNFO through the SetInterval task, which also switches Shared Memory Support on."""
    try:
        os.makedirs(config.data_dir(), exist_ok=True)
        with open(config.interval_file(), "w", encoding="ascii") as f:
            f.write(str(int(round(sec * 1000))))
    except Exception:
        return False
    return run_task(TASK_SETINT)


def apply_hwinfo_interval(sec, exe):
    """Hand the logging interval to HWiNFO (task restarts HWiNFO). Returns ms if a change was started."""
    ms = int(round(sec * 1000))
    if ms == hwinfo_interval_ms(exe) and hwinfo_settings_ok(exe):
        return None
    try:
        os.makedirs(config.data_dir(), exist_ok=True)
        with open(config.interval_file(), "w", encoding="ascii") as f:
            f.write(str(ms))
    except Exception:
        return None
    return ms if run_task(TASK_SETINT) else None
