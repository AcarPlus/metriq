"""Client of the metriQ fan service (fan/service/FanService.cs, Windows service "metriQFan").

The service runs as LocalSystem and owns the fan controls; the panel only talks to it over the named pipe
\\\\.\\pipe\\metriQ.fan (one JSON line per connection, each way). Every value is validated by the service, so this
module only formats requests. Calls block for a moment: use them from a worker thread, not the Tk loop.
"""
import ctypes
import json
from ctypes import wintypes

PIPE = r"\\.\pipe\metriQ.fan"
SERVICE = "metriQFan"

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_k32.WaitNamedPipeW.restype = wintypes.BOOL


def request(cmd, timeout_ms=2000, **args):
    """Send one command -> reply dict, or None when the service is not reachable."""
    msg = dict(args, cmd=cmd)
    line = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
    for _ in range(2):                      # second try: all pipe instances were busy
        if not _k32.WaitNamedPipeW(PIPE, timeout_ms):
            return None
        try:
            with open(PIPE, "r+b", buffering=0) as f:
                f.write(line)
                buf = b""
                while b"\n" not in buf and len(buf) < 4 * 1024 * 1024:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    buf += chunk
            return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            continue
    return None


def status():
    """Live state: enabled, temperatures, headers (id, name, kind, duty, rpm, target, reason, alarm ...), temps."""
    return request("status")


def get_config():
    r = request("get_config")
    return r.get("config") if r and r.get("ok") else None


def set_config(cfg):
    """Store headers / limits (the enabled flag is switched only with enable())."""
    r = request("set_config", config=cfg)
    return r.get("config") if r and r.get("ok") else None


def enable(on):
    r = request("enable", on=bool(on))
    return bool(r and r.get("ok"))


def stop():
    """Stop all fan control at once: every header goes back to BIOS / driver."""
    r = request("stop")
    return bool(r and r.get("ok"))


def override(control_id, pct, seconds=8):
    """Short self-expiring duty on one header (identification, calibration); pct < 0 cancels."""
    return request("override", id=control_id, pct=pct, seconds=seconds)


class _SvcStatus(ctypes.Structure):
    _fields_ = [(n, wintypes.DWORD) for n in ("type", "state", "accepted", "exit", "svc_exit", "checkpoint", "wait")]


_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
_adv.OpenSCManagerW.restype = wintypes.HANDLE
_adv.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
_adv.OpenServiceW.restype = wintypes.HANDLE
_adv.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(_SvcStatus)]
_adv.CloseServiceHandle.argtypes = [wintypes.HANDLE]
_STATES = {1: "stopped", 2: "starting", 3: "stopping", 4: "running"}


def service_state():
    """'running' | 'stopped' | 'starting' | 'stopping' | 'missing' (not installed: portable build) | 'unknown'."""
    scm = _adv.OpenSCManagerW(None, None, 0x0001)              # SC_MANAGER_CONNECT
    if not scm:
        return "unknown"
    try:
        svc = _adv.OpenServiceW(scm, SERVICE, 0x0004)          # SERVICE_QUERY_STATUS
        if not svc:
            return "missing" if ctypes.get_last_error() == 1060 else "unknown"
        try:
            st = _SvcStatus()
            if not _adv.QueryServiceStatus(svc, ctypes.byref(st)):
                return "unknown"
            return _STATES.get(st.state, "unknown")
        finally:
            _adv.CloseServiceHandle(svc)
    finally:
        _adv.CloseServiceHandle(scm)
