"""Regression check of the value alerts (alerts.py, alerts_ui.py, config.norm_alerts).

    python tools/check_alerts.py

1. Engine: fires only after the hold time, once; not again while hovering at the limit; again only after the value
   went back past the hysteresis AND REARM_S passed; "below" rules; a missing reading.
2. config.norm_alerts: bad / partial input.
3. Settings page (hidden window, stub panel API): untouched suggested rules stay "auto"; add / edit / remove give a
   list; an invalid limit blocks validate(); the page is shown in Basic.
"""
import os
import sys
import tempfile
import types

os.environ["METRIQ_DIR"] = tempfile.mkdtemp(prefix="metriq_chk_")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import tkinter as tk  # noqa: E402

import alerts  # noqa: E402
import alerts_ui  # noqa: E402
import config  # noqa: E402
import layout  # noqa: E402
import settings_ui  # noqa: E402
import themes  # noqa: E402

FAIL = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


def engine():
    R = {"metric": "m", "op": ">", "value": 90.0, "secs": 10}
    e = alerts.Engine()
    e.set_rules([R])
    feed = lambda v, t: [x[1] for x in e.update({"m": (v, None)}, t)]
    check("no fire before the hold time", feed(95, 0) == [] and feed(95, 9) == [])
    check("fires after 10 s", feed(95, 10) == [95])
    check("fires once", feed(96, 11) == [] and feed(97, 60) == [])
    check("active while past the limit", [v for _, v in e.active()] == [97])
    check("hovering just under the limit does not re-arm", feed(89.5, 61) == [] and feed(95, 62) == [] and feed(95, 80) == [])
    check("not active under the limit", feed(89.5, 81) == [] and e.active() == [])
    feed(80, 100)                                    # back past the hysteresis: re-armed, but REARM_S not over
    check("re-armed but waits for REARM_S", feed(95, 110) == [] and feed(95, 125) == [])
    check("fires again after REARM_S", feed(95, 10 + alerts.REARM_S) == [95])
    e2 = alerts.Engine()
    e2.set_rules([{"metric": "m", "op": "<", "value": 10.0, "secs": 0}])
    check("'below' rule fires at once", [x[1] for x in e2.update({"m": (5, None)}, 0)] == [5])
    e3 = alerts.Engine()
    e3.set_rules([R])
    e3.update({"m": (95, None)}, 0)
    e3.update({}, 5)                                 # no reading for a moment
    check("gap restarts the hold time", e3.update({"m": (95, None)}, 12) == [] and len(e3.update({"m": (95, None)}, 22)) == 1)
    e3.set_rules([R, {"metric": "x", "op": ">", "value": 1, "secs": 0}])
    check("unchanged rule keeps its state over set_rules", e3.update({"m": (95, None)}, 30) == [])
    check("fmt_limit", alerts.fmt_limit(90.0) == "90" and alerts.fmt_limit(1.25) == "1.25")


def norm():
    d = config.norm_alerts(None)
    check("norm: default", d == config.DEFAULTS["alerts"])
    d = config.norm_alerts({"enabled": "yes", "sound": False, "rules": [
        {"metric": "a", "op": "<", "value": "12,5", "secs": "7"}, {"metric": "", "value": 1}, {"metric": "b", "value": "x"},
        {"metric": "c", "value": float("nan")}, "junk"] + [{"metric": f"r{i}", "value": i} for i in range(20)]})
    check("norm: bad flag ignored", d["enabled"] is False and d["sound"] is False)
    check("norm: comma decimal, op, secs", d["rules"][0] == {"metric": "a", "name": "", "op": "<", "value": 12.5, "secs": 7})
    check("norm: bad rules dropped, max kept", len(d["rules"]) == config.ALERT_MAX_RULES and d["rules"][1]["metric"] == "r0")


def rows():
    mk = lambda name, rid, label, unit: layout.Row(name, "CPU", label, None, None, unit, (0, 100), True, rid=rid)
    return [mk("CPU Temperature", "cpu_temp", "Tctl", "°C"), mk("GPU Temperature", "gpu_temp", "GPU Temp", "°C"),
            mk("CPU Usage", "cpu_usage", "Total CPU Usage", "%")]


def page(mode="advanced", alerts_cfg=None):
    cfg, _ = config.load_config()
    cfg["ui_mode"] = mode
    if alerts_cfg is not None:
        cfg["alerts"] = config.norm_alerts(alerts_cfg)
    root = ROOT
    fonts = ("Segoe UI", "Segoe UI", "Consolas")
    rs = rows()
    tested = []
    api = types.SimpleNamespace(root=root, cfg=cfg, C=themes.palette(), fonts=fonts, fonts_now=lambda: fonts,
                                apply=lambda *a, **k: True, layout=lambda: [], readings=lambda: [],
                                monitor=lambda: 0, refresh_tasks=lambda *a: None, tasks=lambda: False,
                                detect=lambda s="auto": s, monitors=config.monitors,
                                source=types.SimpleNamespace(via=lambda: "", notes=lambda: [], active="builtin"),
                                mini_rows=lambda: rs, alert_test=lambda n, s: tested.append((n, s)))
    w = settings_ui.SettingsWindow(api)
    root.update()
    return root, w, tested


def ui():
    root, w, tested = page("basic")
    check("page shown in Basic", alerts_ui.PAGE in w.nav_map)
    w.select(alerts_ui.PAGE)
    root.update()
    tab = w.alert_tab
    check("suggested rules shown", [ln["cb_m"].current() for ln in tab.lines] == [0, 1])
    check("untouched -> 'auto'", w.read_ui()["alerts"]["rules"] == "auto")
    tab.test()
    check("test button calls the panel", tested == [(True, True)])
    tab.lines[0]["v"].set("85")
    r = w.read_ui()["alerts"]["rules"]
    check("edited -> list", isinstance(r, list) and r[0]["value"] == 85.0 and r[0]["op"] == ">")
    tab.add()
    root.update()
    r = w.read_ui()["alerts"]["rules"]
    check("add picks an unused row", len(r) == 3 and r[2]["metric"].endswith("|Total CPU Usage|"))
    tab.lines[2]["v"].set("abc")
    check("invalid limit blocks saving", w.validate() == alerts_ui.PAGE)
    tab.remove(tab.lines[2])
    root.update()
    check("removed line no longer blocks", w.validate() is None and len(w.read_ui()["alerts"]["rules"]) == 2)
    tab.suggested()
    check("suggested again -> 'auto'", w.read_ui()["alerts"]["rules"] == "auto")
    w._closing = True
    w.win.destroy()

    keep = {"enabled": True, "rules": [{"metric": "gone|x|", "name": "Old row", "op": "<", "value": 3, "secs": 30}]}
    root, w, _ = page("advanced", keep)
    w.select(alerts_ui.PAGE)
    root.update()
    a = w.read_ui()["alerts"]
    check("rule of a removed row kept as it was", a["rules"] == config.norm_alerts(keep)["rules"] and a["enabled"])
    w._closing = True
    w.win.destroy()

    root, w, _ = page("advanced", keep)               # never visit the page: the stored value comes back unchanged
    check("page not visited: unchanged", w.read_ui()["alerts"] == config.norm_alerts(keep))
    w._closing = True
    w.win.destroy()


ROOT = tk.Tk()
ROOT.withdraw()
engine()
norm()
ui()
ROOT.destroy()
print("\nALL OK" if not FAIL else f"\n{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
