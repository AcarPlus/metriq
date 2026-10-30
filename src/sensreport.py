"""Sensor report: a plain text file a user can send us, to see what metriQ gets on hardware we cannot test.

Contents: app version, data source, Windows version, whether a system battery exists, every reading the active
source publishes (group, name, value, unit), which panel rows found a reading and which found nothing, and how the
battery / throttling rows were fed. Privacy: scrub() removes the user name, the computer name, user folder paths,
MAC addresses and serial-number-like tokens from every line (the readings themselves are numbers).
"""
import os
import platform
import re
import socket
import sys
import time

import battery
import i18n
import layout
import throttle
from version import __version__

i18n.register({
    "en": {
        "sr_title": "Sensor report",
        "sr_intro": "Saves a text file with every sensor the current data source reports and which panel rows found "
                    "a value. It contains no user name, computer name, folder paths or serial numbers. Handy when "
                    "something is missing on your hardware: send us the file.",
        "sr_btn": "Export sensor report…", "sr_saved": "Saved: {f}", "sr_nodata": "No sensor data yet.",
        "sr_err": "Could not save the report: {e}",
    },
    "tr": {
        "sr_title": "Sensör raporu",
        "sr_intro": "Şu anki veri kaynağının bildirdiği tüm sensörleri ve hangi panel satırlarının değer bulduğunu "
                    "içeren bir metin dosyası kaydeder. Kullanıcı adı, bilgisayar adı, klasör yolu ya da seri "
                    "numarası içermez. Donanımınızda bir şey eksikse işe yarar: dosyayı bize gönderin.",
        "sr_btn": "Sensör raporunu dışa aktar…", "sr_saved": "Kaydedildi: {f}",
        "sr_nodata": "Henüz sensör verisi yok.", "sr_err": "Rapor kaydedilemedi: {e}",
    },
})

_SERIAL = re.compile(r"(?i)\b(s/n|sn|serial(?: ?(?:no|number))?)\b[\s:=#]*\S+")
_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{14,}(?![A-Za-z0-9])")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_GUID = re.compile(r"\{?\b[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\b\}?")
_USERS = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]*[^\\/\s|\"')\]]*", re.I)


def _private_names():
    names = set()
    for k in ("USERNAME", "USER", "COMPUTERNAME", "USERDOMAIN", "LOGONSERVER"):
        v = os.environ.get(k)
        if v:
            names.add(v.strip("\\"))
    try:
        names.add(socket.gethostname())
    except Exception:
        pass
    try:
        import getpass
        names.add(getpass.getuser())
    except Exception:
        pass
    return sorted({n for n in names if len(n) >= 2}, key=len, reverse=True)


def scrub(text, names=None):
    """Removes what identifies the person or the machine from `text` (see the module docstring)."""
    s = text
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        s = re.sub(re.escape(home), "<user folder>", s, flags=re.I)
    s = _USERS.sub("<user folder>", s)
    s = re.sub(r"(?i)[A-Za-z]:[\\/]+Users\b", "<users>", s)
    for n in (_private_names() if names is None else names):
        s = re.sub(r"\b%s\b" % re.escape(n) if len(n) < 4 else re.escape(n), "<removed>", s, flags=re.I)
    s = _SERIAL.sub(lambda m: m.group(1) + " <removed>", s)
    s = _MAC.sub("<mac>", s)
    s = _GUID.sub("<id>", s)
    s = _TOKEN.sub("<id>", s)
    return s


def _num(v):
    try:
        return ("%.3f" % v).rstrip("0").rstrip(".") or "0"
    except Exception:
        return str(v)


def _windows():
    try:
        v = sys.getwindowsversion()
        edition = ""
        try:
            edition = platform.win32_edition()
        except Exception:
            pass
        return "Windows %s (%d.%d build %d%s)" % (platform.release(), v.major, v.minor, v.build,
                                                  ", " + edition if edition else "")
    except Exception:
        return platform.platform()


def build(rd, src, lay, cfg=None):
    """The report text. rd: readings of the active source; src: backend.Manager; lay: (is_auto, layout config)."""
    L = []
    add = L.append
    add("metriQ sensor report")
    add("")
    add("App version : " + __version__)
    try:
        import backend
        name = backend.display_name(src.active)
        via = src.via()
        add("Data source : %s%s (setting: %s)" % (name, " via " + via.upper() if via else "", src.setting()))
    except Exception:
        add("Data source : unknown")
    add("Windows      : " + _windows())
    add("Python       : " + platform.python_version())
    info = battery.current()
    add("System battery present : " + ("yes" if info else "no"))
    if info:
        add("  built-in reader: state=%d pct=%s rate_w=%s remain_s=%s wear=%s volt=%s designed_wh=%s full_wh=%s" % (
            info["state"], _num(info["pct"]) if info["pct"] is not None else "-",
            _num(info["rate_w"]) if info["rate_w"] is not None else "-", info["remain_s"] or "-",
            _num(info["wear"]) if info["wear"] is not None else "-",
            _num(info["volt"]) if info["volt"] is not None else "-",
            _num(info["designed_wh"]) if info["designed_wh"] else "-",
            _num(info["full_wh"]) if info["full_wh"] else "-"))
        add("  (state: 0 on AC, 1 charging, 2 discharging, 3 full)")
    add("Readings    : %d" % len(rd))
    add("Created     : " + time.strftime("%Y-%m-%d %H:%M"))
    add("")

    # ---- panel rows
    add("=" * 72)
    add("PANEL ROWS (section / row: reading, or what was searched for and not found)")
    add("=" * 72)
    found = missing = 0
    try:
        secs = layout.build_sections(lay[1], include_hidden=True)
    except Exception as ex:
        secs = []
        add("layout could not be read: %s" % ex)
    add("Layout: " + ("automatic" if lay and lay[0] else "custom"))
    for sec in secs:
        for row in sec.rows:
            r = row.match(rd)
            where = "%s / %s" % (sec.title, row.name)
            if r:
                found += 1
                add("[ok]      %s: %s | %s = %s %s" % (where, r["sensor"], r["label"], _num(r["value"]), r["unit"]))
            else:
                missing += 1
                add("[MISSING] %s: sensor ~ %r, label ~ %r%s" % (
                    where, row.sensor or "*", row.label, "" if row.rtype is None else ", type %d" % row.rtype))
    add("%d row(s) found a reading, %d found nothing." % (found, missing))
    add("")

    # ---- laptop rows
    add("=" * 72)
    add("BATTERY / THROTTLING")
    add("=" * 72)
    if not info:
        add("Battery: no system battery (the Battery section is not shown).")
    else:
        for key, name_key, unit, limits, fmt, bar, hlp in battery.ROWS:
            hit = next((r for r in rd if battery.SENSOR_RE.search(r["sensor"])
                        and battery.LABEL_RE[key].search(r["label"])), None)
            if hit:
                how = "built-in fallback" if hit["sensor"] == battery.SENSOR else "from the data source"
                add("[ok]      %-8s %s | %s = %s %s (%s)" % (key, hit["sensor"], hit["label"], _num(hit["value"]),
                                                              hit["unit"], how))
            else:
                add("[none]    %-8s no reading%s" % (key, " (normal while not discharging)" if key == "time" else ""))
    yn = [r for r in rd if throttle._flag(r) and r["unit"] and throttle._kind(r["sensor"], r["label"])]
    add("Throttling: %d yes/no sensor(s) recognised." % len(yn))
    for r in yn:
        add("  %s | %s = %s [%s]" % (r["sensor"], r["label"], _num(r["value"]), r["unit"]))
    for r in rd:
        if r["sensor"] == throttle.SENSOR:
            add("  -> %s: %s (mask %d)" % (r["label"], throttle.fmt(r["value"]), int(r["value"])))
    if not yn and getattr(src, "active", "") != "hwinfo":
        add("  (throttling needs HWiNFO as the data source)")
    add("")

    # ---- every reading
    add("=" * 72)
    add("ALL READINGS: group | name | value | unit")
    add("=" * 72)
    for r in rd:
        add("%s | %s | %s | %s" % (r["sensor"], r["label"], _num(r["value"]), r["unit"]))
    add("")
    return scrub("\n".join(L))
