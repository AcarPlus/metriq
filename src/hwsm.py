"""Read-only access to the HWiNFO64 sensor shared memory (``Global\\HWiNFO_SENS_SM2``).

Every reading is returned as a dict:
    type        1 temp, 2 volt, 3 fan, 4 current, 5 power, 6 clock, 7 usage, 8 other
    sensor      ORIGINAL (English) sensor name   -> use this for matching
    label       ORIGINAL (English) reading label -> use this for matching
    sensor_user / label_user   names as shown in HWiNFO (localised / user renamed)
    id, unit, value, min, max, avg
"""
import ctypes
import struct
import time
from ctypes import wintypes

SHM_NAME = "Global\\HWiNFO_SENS_SM2"
TYPE_NAMES = {1: "temp", 2: "volt", 3: "fan", 4: "current", 5: "power", 6: "clock", 7: "usage", 8: "other"}

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_k.OpenFileMappingW.restype = wintypes.HANDLE
_k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k.MapViewOfFile.restype = ctypes.c_void_p
_k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k.CloseHandle.argtypes = [wintypes.HANDLE]

FILE_MAP_READ = 4


def _s(b):
    return b.split(b"\0")[0].decode("mbcs", errors="replace")


_cache = None
last_poll = None            # HWiNFO's poll counter of the last read (consumers compare it to skip unchanged data)


def available():
    """True when the shared memory exists (cheap: no parsing)."""
    h = _k.OpenFileMappingW(FILE_MAP_READ, False, SHM_NAME)
    if not h:
        return False
    _k.CloseHandle(h)
    return True


def read_all():
    """All readings, or RuntimeError if the shared memory is not available."""
    h = _k.OpenFileMappingW(FILE_MAP_READ, False, SHM_NAME)
    if not h:
        raise RuntimeError("HWiNFO shared memory not available (error %d)" % ctypes.get_last_error())
    p = _k.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
    if not p:
        _k.CloseHandle(h)
        raise RuntimeError("MapViewOfFile failed (error %d)" % ctypes.get_last_error())
    try:
        sig, ver, rev, poll, so, ss, sc, ro, rs, rc = struct.unpack("<IIIqIIIIII", ctypes.string_at(p, 44))
        buf = ctypes.string_at(p, ro + rs * rc)
    finally:
        _k.UnmapViewOfFile(p)
        _k.CloseHandle(h)
    global _cache, last_poll
    last_poll = poll
    key = (sc, rc, so, ss, ro, rs)
    if _cache is not None and _cache[0] == key and _cache[1] == poll and time.time() - _cache[3] < 5.0:
        return [dict(d) for d in _cache[2]]          # HWiNFO has not polled since the last read: same values
    static = _cache[4] if _cache is not None and _cache[0] == key else {}
    sens = []
    for i in range(sc):
        o = so + i * ss
        sens.append((_s(buf[o + 8:o + 136]), _s(buf[o + 136:o + 264])))
    out = []
    unpack = struct.unpack_from
    for i in range(rc):
        o = ro + i * rs
        sb = buf[o:o + 284]                           # type, sensor index, id, labels, unit: parsed once while unchanged
        si = unpack("<I", sb, 4)[0]
        s_orig, s_user = sens[si] if si < len(sens) else ("", "")
        st = static.get(i)
        if st is None or st[0] != sb or st[1] != (s_orig, s_user):
            t, _, rid = unpack("<III", sb, 0)
            lab = _s(sb[12:140])
            usr = _s(sb[140:268])
            unit = _s(sb[268:284])
            st = static[i] = (sb, (s_orig, s_user), dict(type=t, sensor=s_orig, sensor_user=s_user or s_orig, id=rid,
                                                         label=lab or usr, label_user=usr or lab, unit=unit))
        val, vmin, vmax, vavg = unpack("<dddd", buf, o + 284)
        d = dict(st[2])
        d["value"], d["min"], d["max"], d["avg"] = val, vmin, vmax, vavg
        out.append(d)
    _cache = (key, poll, out, time.time(), static)
    return [dict(d) for d in out]
