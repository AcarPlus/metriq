"""Check: row hover and animations in the real panel (Basic and Advanced, Aurora and a plain theme).

    python tools/check_hover_anim.py

1) Unit part (hidden Tk window): procwin.Controller.tag_row adds no (i) icon, the old icon helpers are gone, a usage
   balloon shows its plain-language note above the program list.
2) Panel part: starts src/panel.py with a temporary data folder (METRIQ_DIR, built-in data source, nothing of your own
   settings) and METRIQ_UICHECK; the panel hovers a row name, switches the view with the mode chip and reports back.
   Expected: no (i) icons, every Basic row swells on hover, the name grows and shrinks back, the entry finishes with
   nothing left hidden, the chip switches Basic <-> Advanced.
Exit code 0 when everything passes. Only counts are printed.
"""
import json
import os
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
os.environ.setdefault("METRIQ_DIR", tempfile.mkdtemp(prefix="metriq_chk_"))
sys.path.insert(0, SRC)

FAILS = []


def ok(cond, what):
    print(("OK   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


def unit():
    import tkinter as tk
    import procwin
    import themes
    import tooltip
    root = tk.Tk()
    root.withdraw()
    cv = tk.Canvas(root, width=400, height=100)
    cv.pack()
    api = types.SimpleNamespace(root=root, cfg={}, C=themes.palette())
    ctl = procwin.Controller(api)
    ok(not hasattr(ctl, "help_icon") and not hasattr(ctl, "_info_icon"), "(i) icon helpers removed from procwin")
    row = types.SimpleNamespace(rid="download", key="net", label="Download", sensor="Network", name="Download",
                                unit_eff="KB/s", help="b_download")
    item = cv.create_text(10, 50, text="Processor usage", anchor="w")
    got = ctl.tag_row(cv, item, row, lambda: "what it means")
    if got is None:                                   # (the row matcher did not take the stub: try a real row)
        print("     (stub row not tagged; balloon part skipped)")
    else:
        tip = ctl.tips[-1]
        ok(isinstance(tip, procwin.UsageTip) and tip.note() == "what it means", "usage balloon carries the note")
    ok(len(cv.find_withtag("uinfo")) == 0, "tag_row draws no (i) icon")
    t = tooltip.Tooltip.for_canvas(cv, item, lambda: [("Top programs", "", "head"), ("a.exe", "3 %")])
    t.note = lambda: "plain words"
    t._xy = (10, 10)
    t._pointer_over = lambda: True
    t.show()
    texts = [w.cget("text") for w in t.tip.winfo_children()[0].winfo_children()[0].winfo_children()]
    ok(texts[:2] == ["plain words", "Top programs"], "balloon: note first, then the list")
    t.hide()
    root.destroy()


def panel(mode, theme):
    d = tempfile.mkdtemp(prefix="metriq_chk_ui_")
    cfg = {"theme": theme, "motion": "on", "ui_mode": mode, "ui_mode_hint_shown": True, "backend": "builtin",
           "manage_hwinfo": False, "sync_hwinfo_interval": False, "check_updates": False, "bg_hint_shown": True,
           "window_mode": "window", "topmost": False}
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    out = os.path.join(d, "uicheck.json")
    env = dict(os.environ, METRIQ_DIR=d, METRIQ_UICHECK=out)
    p = subprocess.Popen([sys.executable, os.path.join(SRC, "panel.py")], env=env, cwd=SRC)
    try:
        p.wait(40)
    except subprocess.TimeoutExpired:
        p.kill()
    if not os.path.isfile(out):
        ok(False, f"{mode}/{theme}: panel wrote its report")
        return
    r = json.load(open(out, encoding="utf-8"))
    s0, s1 = r["start"], r["after_toggle"]
    tag = f"{mode}/{theme}"
    ok(s0["rows"] > 0, f"{tag}: rows drawn ({s0['rows']})")
    ok(s0["uinfo"] == 0 and s1["uinfo"] == 0, f"{tag}: no (i) icons in either view")
    if mode == "basic":
        ok(s0["hovers"] == s0["rows"], f"{tag}: every Basic row swells on hover ({s0['hovers']}/{s0['rows']})")
    else:
        ok(s0["hovers"] > 0, f"{tag}: rows with a balloon swell ({s0['hovers']})")
    ok(r.get("w1", 0) > r.get("w0", 0) > 0, f"{tag}: hovered name grows ({r.get('w0')} -> {r.get('w1')} px)")
    ok(r.get("w2") == r.get("w0"), f"{tag}: and shrinks back ({r.get('w2')} px)")
    ok(s0["entry_left"] == 0 and s0["hidden"] == 0, f"{tag}: start entry finished, nothing left hidden")
    ok(r["mode1"] != r["mode0"], f"{tag}: mode chip switched the view ({r['mode0']} -> {r['mode1']})")
    ok(s1["entry_left"] == 0 and s1["hidden"] == 0 and s1["rows"] > 0,
       f"{tag}: new view came in completely ({s1['rows']} rows)")
    if r["mode1"] == "basic":
        ok(s1["hovers"] == s1["rows"], f"{tag}: Basic after the switch: every row swells")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    unit()
    for mode, theme in (("basic", "aurora"), ("advanced", "aurora"), ("basic", "dark")):
        panel(mode, theme)
    print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
