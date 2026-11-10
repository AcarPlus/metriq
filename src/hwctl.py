"""Controlling HWiNFO from the panel: detection, process check, elevated scheduled tasks (Setup / install.ps1)."""
import ctypes
import os
import shutil
import subprocess
import sys
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


def task_script():
    """register_tasks.ps1 of an installed metriQ (<app>/tasks, put there by Setup), or None (source checkout)."""
    if not config.FROZEN:
        return None
    p = os.path.join(os.path.dirname(sys.executable), "tasks", "register_tasks.ps1")
    return p if os.path.isfile(p) else None


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def register_tasks_elevated(hw_exe=None, timeout_s=180):
    """Installs the scheduled tasks after the fact (e.g. HWiNFO was installed after metriQ): runs Setup's
    register_tasks.ps1 elevated (one UAC prompt) and waits for it.
    Returns "ok" | "cancelled" (UAC declined) | "no_hwinfo" | "no_script" | "error"."""
    script = task_script()
    if not script:
        return "no_script"
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                      "powershell.exe")
    user = "\\".join(x for x in (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")) if x)
    params = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"'
    if user:
        params += f' -User "{user}"'
    if hw_exe and os.path.isfile(hw_exe):
        params += f' -HwinfoPath "{hw_exe}"'
    sei = _ShellExecuteInfo()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x00000040 | 0x00000100              # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    sei.lpVerb, sei.lpFile, sei.lpParameters, sei.nShow = "runas", ps, params, 0
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    sh.ShellExecuteExW.restype = wintypes.BOOL
    if not sh.ShellExecuteExW(ctypes.byref(sei)):
        return "cancelled" if ctypes.get_last_error() == 1223 else "error"     # ERROR_CANCELLED: UAC "No"
    if not sei.hProcess:
        return "error"
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        if k.WaitForSingleObject(sei.hProcess, int(timeout_s * 1000)) != 0:
            return "error"
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(sei.hProcess, ctypes.byref(code)):
            return "error"
    finally:
        k.CloseHandle(sei.hProcess)
    _tasks_cache.pop("v", None)
    return {0: "ok", 3: "no_hwinfo"}.get(code.value, "error")


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
