"""Laptop battery for the panel (nothing appears on a PC without a system battery).

Built-in reader (no driver, no admin rights):
    GetSystemPowerStatus                         AC line, charge %, remaining time
    battery IOCTLs (setupapi + DeviceIoControl)  charge / discharge rate (mW, signed), voltage, full-charge and
                                                 design capacity -> wear level
The IOCTL part is read-only (IOCTL_BATTERY_QUERY_TAG / _QUERY_INFORMATION / _QUERY_STATUS) and is polled at most
every POLL_S seconds. A desktop is recognised by one cheap GetSystemPowerStatus call (BatteryFlag 128) and never
enumerates battery devices; a USB UPS (a battery without the "system battery" capability) is not a laptop battery.

Readings use HWiNFO's names ("Charge Level", "Charge Rate", "Wear Level", "Voltage") under the sensor
"Battery: System", so the same layout rows also find the battery sensors of HWiNFO and LibreHardwareMonitor.
augment() adds the built-in readings the active source does not deliver (state and remaining time always come from
here) and puts the sign of a source's charge rate right (+ charging, - discharging).
"""
import ctypes
import re
import struct
import threading
import time
from ctypes import wintypes

import i18n

POLL_S = 2.0              # battery IOCTLs are cheap, but there is no need for more
ABSENT_S = 10.0           # a PC without a battery: look again this rarely
INFO_S = 30.0             # capacities change slowly
SENSOR = "Battery: System"
SENSOR_RE = re.compile(r"^(Smart )?Battery", re.I)

# state codes of the "State" reading (see fmt_state)
ST_AC, ST_CHARGING, ST_DISCHARGING, ST_FULL = 0, 1, 2, 3

# ------------------------------------------------------------------ rows (shared by layout.autodetect / the report)
# key -> (label regex, matches HWiNFO / LHM / built-in names). State and remaining time come from the built-in
# reader only, so their patterns are exact.
LABELS = {
    "level": r"^(Battery )?Charge (Level|Percent(age)?)\b",
    "state": r"^State$",
    "rate": r"^(Battery )?(Charge|Discharge|Charge/Discharge) Rate\b",
    "time": r"^Remaining Time$",
    "wear": r"^(Battery )?(Wear|Degradation)( Level)?\b",
    "volt": r"^(Battery )?Voltage\b",
}
LABEL_RE = {k: re.compile(v, re.I) for k, v in LABELS.items()}
# key, i18n name, unit, limits (green, red), format, bar, help key
ROWS = (
    ("level", "bat_level", "%", (60, 10), None, True, None),
    ("state", "bat_state", "", None, "@bstate", False, "bat_state"),
    ("rate", "bat_rate", "W", None, "{:+.1f}", False, "bat_rate"),
    ("time", "bat_time", "", None, "@hm", False, None),
    ("wear", "bat_wear", "%", (10, 40), None, True, None),
    ("volt", "bat_volt", "V", None, "{:.2f}", False, None),
)

i18n.register({
    "en": {
        "sec_battery": "BATTERY",
        "bat_level": "Battery Level", "bat_state": "Battery State", "bat_rate": "Charge / Discharge",
        "bat_time": "Time Remaining", "bat_wear": "Battery Wear", "bat_volt": "Battery Voltage",
        "bat_s_ac": "On AC", "bat_s_charging": "Charging", "bat_s_discharging": "Discharging", "bat_s_full": "Full",
        "hp_bat_state": "On AC: plugged in but not charging. Charging, Discharging or Full otherwise. "
                        "Comes from Windows, whichever data source is active.",
        "hp_bat_rate": "Power flowing into (+, charging) or out of (-, discharging) the battery, in watts. "
                       "While discharging it is roughly what the whole laptop draws.",
    },
    "tr": {
        "sec_battery": "BATARYA",
        "bat_level": "Şarj Düzeyi", "bat_state": "Batarya Durumu", "bat_rate": "Şarj / Deşarj Gücü",
        "bat_time": "Kalan Süre", "bat_wear": "Batarya Aşınması", "bat_volt": "Batarya Voltajı",
        "bat_s_ac": "Fişte", "bat_s_charging": "Şarj oluyor", "bat_s_discharging": "Deşarj", "bat_s_full": "Dolu",
        "hp_bat_state": "Fişte: takılı ama şarj olmuyor. Aksi halde Şarj oluyor, Deşarj ya da Dolu. "
                        "Hangi veri kaynağı seçili olursa olsun Windows'tan gelir.",
        "hp_bat_rate": "Bataryaya giren (+, şarj) ya da bataryadan çıkan (-, deşarj) güç, watt olarak. "
                       "Deşarj sırasında yaklaşık olarak tüm dizüstünün çektiği güçtür.",
    },
})


def fmt_state(v):
    key = {ST_CHARGING: "bat_s_charging", ST_DISCHARGING: "bat_s_discharging", ST_FULL: "bat_s_full"}.get(
        int(round(v)), "bat_s_ac")
    return i18n.t(key)


def fmt_hm(v):
    """Seconds -> h:mm."""
    m = int(round(max(0.0, v) / 60.0))
    return "%d:%02d" % (m // 60, m % 60)


# ------------------------------------------------------------------ decoding (pure: tested with fake data)
UNKNOWN_RATE = -2147483648            # BATTERY_UNKNOWN_RATE 0x80000000 as a LONG
UNKNOWN = 0xFFFFFFFF                  # unknown capacity / voltage / time
CAP_SYSTEM, CAP_RELATIVE = 0x80000000, 0x40000000       # BATTERY_INFORMATION.Capabilities
PS_ONLINE, PS_DISCHARGING, PS_CHARGING = 1, 2, 4       # BATTERY_STATUS.PowerState
GS_NO_BATTERY, GS_UNKNOWN, GS_CHARGING = 128, 255, 8   # SYSTEM_POWER_STATUS.BatteryFlag


def decode_gsps(raw):
    """SYSTEM_POWER_STATUS bytes -> dict(ac True/False/None, flag, pct 0..100/None, life seconds/None)."""
    ac, flag, pct, _, life, _full = struct.unpack("<BBBBII", raw[:12])
    return {"ac": {0: False, 1: True}.get(ac), "flag": flag, "pct": None if pct > 100 else float(pct),
            "life": None if life == UNKNOWN else life}


def decode_info(raw):
    """BATTERY_INFORMATION bytes -> dict(caps, designed, full, cycles) (None for unknown)."""
    caps, _tech, _res, _chem, designed, full, _a1, _a2, _bias, cycles = struct.unpack("<IB3s4sIIIIII", raw[:36])
    return {"caps": caps, "designed": None if designed in (0, UNKNOWN) else designed,
            "full": None if full in (0, UNKNOWN) else full, "cycles": cycles}


def decode_status(raw):
    """BATTERY_STATUS bytes -> dict(state flags, capacity, volt mV, rate mW) (None for unknown)."""
    state, cap, volt, rate = struct.unpack("<IIIi", raw[:16])
    return {"state": state, "cap": None if cap == UNKNOWN else cap, "volt": None if volt == UNKNOWN else volt,
            "rate": None if rate == UNKNOWN_RATE else rate}


def compute(gs, bi=None, bs=None):
    """Decoded GetSystemPowerStatus (+ battery IOCTL data when there is any) -> the battery's state, or None
    when this PC has no system battery. Keys: ac, state (ST_*), pct, rate_w (signed, + charging), remain_s (only
    while discharging), wear (%), volt (V), designed_wh, full_wh."""
    if gs is None or gs["flag"] in (GS_NO_BATTERY, GS_UNKNOWN) and bs is None:
        return None
    relative = bool(bi and bi["caps"] & CAP_RELATIVE)
    pct = gs["pct"]
    if pct is None and bs and bs["cap"] is not None and bi and bi["full"]:
        pct = min(100.0, 100.0 * bs["cap"] / bi["full"])
    ac = gs["ac"]
    if ac is None and bs:
        ac = bool(bs["state"] & PS_ONLINE)
    charging = bool(gs["flag"] != GS_UNKNOWN and gs["flag"] & GS_CHARGING) or bool(bs and bs["state"] & PS_CHARGING)
    discharging = bool(bs and bs["state"] & PS_DISCHARGING)
    if charging:
        state = ST_CHARGING
    elif discharging or ac is False:
        state = ST_DISCHARGING
    elif pct is not None and pct >= 99:
        state = ST_FULL
    else:
        state = ST_AC
    rate_w = None
    if bs and bs["rate"] is not None and not relative:
        rate_w = bs["rate"] / 1000.0
        # Windows: + charging, - discharging; some firmware reports the magnitude only -> follow the state
        if state == ST_DISCHARGING and rate_w > 0 or state == ST_CHARGING and rate_w < 0:
            rate_w = -rate_w
    remain = None
    if state == ST_DISCHARGING:
        if gs["life"]:
            remain = gs["life"]
        elif rate_w and rate_w < 0 and bs and bs["cap"] is not None and not relative:
            remain = int(bs["cap"] / 1000.0 / -rate_w * 3600)           # mWh / W -> s
    wear = None
    if bi and bi["designed"] and bi["full"] and not relative:
        wear = max(0.0, 100.0 * (1.0 - bi["full"] / bi["designed"]))
    return {"ac": ac, "state": state, "pct": pct, "rate_w": rate_w, "remain_s": remain, "wear": wear,
            "volt": bs["volt"] / 1000.0 if bs and bs["volt"] is not None else None,
            "designed_wh": bi["designed"] / 1000.0 if bi and bi["designed"] and not relative else None,
            "full_wh": bi["full"] / 1000.0 if bi and bi["full"] and not relative else None}


def _rd(rtype, label, unit, value):
    v = float(value)
    return dict(type=rtype, sensor=SENSOR, sensor_user=SENSOR, id=0, label=label, label_user=label, unit=unit,
                value=v, min=v, max=v, avg=v)


def to_readings(info):
    """compute() result -> readings in the shape of hwsm.read_all()."""
    if not info:
        return []
    out = [_rd(8, "State", "", info["state"])]
    if info["pct"] is not None:
        out.append(_rd(7, "Charge Level", "%", info["pct"]))
    if info["rate_w"] is not None:
        out.append(_rd(5, "Charge Rate", "W", info["rate_w"]))
    if info["remain_s"]:
        out.append(_rd(8, "Remaining Time", "s", info["remain_s"]))
    if info["wear"] is not None:
        out.append(_rd(7, "Wear Level", "%", info["wear"]))
    if info["volt"] is not None:
        out.append(_rd(2, "Voltage", "V", info["volt"]))
    if info["designed_wh"]:
        out.append(_rd(8, "Designed Capacity", "Wh", info["designed_wh"]))
    if info["full_wh"]:
        out.append(_rd(8, "Full Charge Capacity", "Wh", info["full_wh"]))
    return out


def key_of(label):
    for k, rx in LABEL_RE.items():
        if rx.search(label or ""):
            return k
    return None


def augment(rd, info):
    """Readings of the active source -> the same list plus the built-in battery readings it lacks; a source's
    charge rate gets its sign corrected by the built-in state. `rd` may be changed (it is the caller's fresh list)."""
    if not info:
        return rd
    have = set()
    for i, r in enumerate(rd):
        if not SENSOR_RE.search(r["sensor"]) or r["sensor"] == SENSOR:
            continue
        k = key_of(r["label"])
        if k is None:
            continue
        have.add(k)
        if k == "rate":
            unit = (r.get("unit") or "").lower()
            v = r["value"] / 1000.0 if unit == "mw" else r["value"]
            if info["state"] == ST_DISCHARGING and v > 0 or info["state"] == ST_CHARGING and v < 0:
                v = -v
            if v != r["value"]:
                r = dict(r, value=v, min=v, max=v, avg=v, unit="W")
                rd[i] = r
    for r in to_readings(info):
        k = key_of(r["label"])
        if k is None or k not in have or k in ("state", "time"):
            rd.append(r)
    return rd


# ------------------------------------------------------------------ Win32
_IOCTL_TAG, _IOCTL_INFO, _IOCTL_STATUS = 0x294040, 0x294044, 0x29404C
_LEVEL_INFO, _LEVEL_NAME = 0, 4          # BATTERY_QUERY_INFORMATION_LEVEL: BatteryInformation, BatteryDeviceName
_DIGCF = 0x2 | 0x10                      # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
_INVALID = ctypes.c_void_p(-1).value


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


class _IfData(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("guid", _GUID), ("flags", wintypes.DWORD), ("res", ctypes.c_size_t)]


# GUID_DEVICE_BATTERY {72631E54-78A4-11D0-BCF7-00AA00B7B32A} (the same value as GUID_DEVCLASS_BATTERY)
_GUID_BATTERY = _GUID(0x72631E54, 0x78A4, 0x11D0, (ctypes.c_ubyte * 8)(0xBC, 0xF7, 0x00, 0xAA, 0x00, 0xB7, 0xB3, 0x2A))

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_s = ctypes.WinDLL("setupapi", use_last_error=True)
_k.GetSystemPowerStatus.argtypes = [ctypes.c_void_p]
_k.CreateFileW.restype = ctypes.c_void_p
_k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, ctypes.c_void_p]
_k.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k.CloseHandle.argtypes = [ctypes.c_void_p]
_s.SetupDiGetClassDevsW.restype = ctypes.c_void_p
_s.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD]
_s.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD,
                                           ctypes.POINTER(_IfData)]
_s.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_IfData), ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_s.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]


def _gsps():
    buf = ctypes.create_string_buffer(12)
    if not _k.GetSystemPowerStatus(buf):
        return None
    return decode_gsps(buf.raw)


def _ioctl(h, code, inp, out_size):
    out = ctypes.create_string_buffer(out_size)
    n = wintypes.DWORD(0)
    if not _k.DeviceIoControl(h, code, inp, len(inp), out, out_size, ctypes.byref(n), None):
        return None
    return out.raw[:n.value]


def _paths():
    """Device paths of every battery (a laptop has one or two; a USB UPS shows up here too)."""
    out = []
    hdev = _s.SetupDiGetClassDevsW(ctypes.byref(_GUID_BATTERY), None, None, _DIGCF)
    if not hdev or hdev == _INVALID:
        return out
    try:
        i = 0
        while True:
            d = _IfData()
            d.size = ctypes.sizeof(_IfData)
            if not _s.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(_GUID_BATTERY), i, ctypes.byref(d)):
                break
            i += 1
            need = wintypes.DWORD(0)
            _s.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(d), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            struct.pack_into("<I", buf, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)   # cbSize of the header
            if _s.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(d), buf, need.value, None, None):
                out.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        _s.SetupDiDestroyDeviceInfoList(hdev)
    return out


class _Dev:
    """One open battery device: tag + information + status (all read-only IOCTLs)."""

    def __init__(self, h):
        self.h = h
        self.bi = None
        self.bi_t = 0.0

    def close(self):
        if self.h:
            _k.CloseHandle(self.h)
            self.h = None

    def tag(self):
        r = _ioctl(self.h, _IOCTL_TAG, struct.pack("<I", 0), 4)          # wait 0 ms: answer now
        return struct.unpack("<I", r)[0] if r and len(r) >= 4 else None

    def information(self, tag):
        r = _ioctl(self.h, _IOCTL_INFO, struct.pack("<IIi", tag, _LEVEL_INFO, 0), 36)
        return decode_info(r) if r and len(r) >= 36 else None

    def name(self, tag):
        r = _ioctl(self.h, _IOCTL_INFO, struct.pack("<IIi", tag, _LEVEL_NAME, 0), 256)
        return r.decode("utf-16-le", "ignore").split("\0")[0] if r else ""

    def status(self, tag):
        r = _ioctl(self.h, _IOCTL_STATUS, struct.pack("<IIIII", tag, 0, 0, 0, 0), 16)
        return decode_status(r) if r and len(r) >= 16 else None


_UPS_RE = re.compile(r"\bUPS\b|uninterruptible", re.I)


def _find():
    """(device, tag, verdict): the first system battery (not a UPS) opened for reading, verdict 'system'; 'other'
    when batteries exist but none is a laptop battery (a USB UPS); 'unknown' when none could be opened. Slow: only
    called after GetSystemPowerStatus reported a battery, and then about once a minute."""
    opened = 0
    for p in _paths():
        h = _k.CreateFileW(p, 0xC0000000, 3, None, 3, 0, None)          # GENERIC_READ|WRITE, share R/W, OPEN_EXISTING
        if not h or h == _INVALID:
            continue
        opened += 1
        dev = _Dev(h)
        try:
            tag = dev.tag()
            bi = dev.information(tag) if tag else None
            if bi and bi["caps"] & CAP_SYSTEM and not _UPS_RE.search(dev.name(tag) or ""):
                dev.bi, dev.bi_t = bi, time.time()
                return dev, tag, "system"
        except Exception:
            pass
        dev.close()
    return None, None, "other" if opened else "unknown"


class _Reader:
    def __init__(self):
        self.lock = threading.Lock()
        self.dev = None
        self.tag = None
        self.t = -1e9                    # last poll
        self.info = None
        self.probe_t = -1e9              # last (slow) device search
        self.verdict = None              # _find(): 'system' | 'other' | 'unknown'

    def read(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if now - self.t < (POLL_S if self.info else ABSENT_S):
                return self.info
            self.t = now
            try:
                self.info = self._poll(now)
            except Exception:
                self.info = None
            return self.info

    def _poll(self, now):
        gs = _gsps()
        if gs is None or gs["flag"] == GS_NO_BATTERY:               # a desktop: nothing else to do
            self._drop()
            self.verdict, self.probe_t = None, -1e9
            return None
        if self.dev is None and now - self.probe_t >= 60.0:
            self.probe_t = now
            self.dev, self.tag, self.verdict = _find()
        if self.verdict == "other":
            return None
        bi = bs = None
        if self.dev is not None:
            try:
                tag = self.dev.tag()
                if tag is None:
                    raise OSError("no tag")                         # battery removed / device gone
                if tag != self.tag or now - self.dev.bi_t > INFO_S:
                    self.tag = tag
                    self.dev.bi, self.dev.bi_t = self.dev.information(tag), now
                bi, bs = self.dev.bi, self.dev.status(tag)
            except Exception:
                self._drop()
                self.probe_t = now - 55.0                           # search again in 5 s
        return compute(gs, bi, bs)

    def _drop(self):
        if self.dev is not None:
            self.dev.close()
            self.dev = None


_reader = _Reader()


def current():
    """Battery state (see compute()), None on a PC without a system battery. Cheap; polled at most every POLL_S."""
    try:
        return _reader.read()
    except Exception:
        return None


def exists():
    return current() is not None
