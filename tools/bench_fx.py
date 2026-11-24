"""Frame-time benchmark of the panel's animation loop (Aurora theme), Basic and Advanced view.

    python tools/bench_fx.py                       # both views, 20 s each
    python tools/bench_fx.py --mode basic --secs 30
    python tools/bench_fx.py --hover               # also sweep the row hover animation over every row (METRIQ_FXBENCH)
    python tools/bench_fx.py --src <folder>/src     # measure another checkout (e.g. a worktree of the previous commit)

Starts src/panel.py with a temporary data folder (METRIQ_DIR: your own settings are never touched; built-in data
source, no HWiNFO management, no update check) and METRIQ_FXPROF, lets it run (it ends itself: METRIQ_QUIT_AFTER)
and prints per view: median frame interval (-> fps while something moves), frame cost (effect step + Tk repaint):
mean, 95th percentile and worst. The first 3 s window (start-up, entry) is left out. Only numbers are printed.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(mode, secs, hover, py, src=None):
    d = tempfile.mkdtemp(prefix="metriq_bench_")
    cfg = {"theme": "aurora", "motion": "on", "ui_mode": mode, "ui_mode_hint_shown": True, "backend": "builtin",
           "manage_hwinfo": False, "sync_hwinfo_interval": False, "check_updates": False, "bg_hint_shown": True,
           "window_mode": "maximized"}
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    prof = os.path.join(d, "fxprof.txt")
    env = dict(os.environ, METRIQ_DIR=d, METRIQ_FXPROF=prof, METRIQ_QUIT_AFTER=str(secs))
    if hover:
        env["METRIQ_FXBENCH"] = "1"
    src = src or os.path.join(ROOT, "src")
    p = subprocess.Popen([py, os.path.join(src, "panel.py")], env=env, cwd=src)
    try:
        p.wait(secs + 20)                              # the panel ends itself (METRIQ_QUIT_AFTER)
    except subprocess.TimeoutExpired:
        p.terminate()                                  # (an older src without METRIQ_QUIT_AFTER)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    txt = open(prof, encoding="utf-8").read() if os.path.isfile(prof) else ""
    # one block per 3 s window: "fps .. step S ms paint P ms gap .. max M ms" + "start-to-start median X ms"
    blocks = re.findall(r"step ([\d.]+) ms\s+paint ([\d.]+) ms.*?max ([\d.]+) ms.*?\n\s+start-to-start median ([\d.]+) ms",
                        txt)
    blocks = blocks[1:] or blocks                      # the first window holds the start-up (build, entry)
    if not blocks:
        return None
    cost = [float(s) + float(pn) for s, pn, _, _ in blocks]
    p95 = [float(x) for x in re.findall(r"frame cost p50 [\d.]+ ms\s+p95 ([\d.]+) ms", txt)][1:]
    worst = max(float(m) for _, _, m, _ in blocks)
    med = statistics.median(float(x) for *_, x in blocks)
    return {"windows": len(blocks), "median_interval_ms": med, "fps": 1000.0 / med if med else 0.0,
            "cost_ms": statistics.mean(cost), "worst_ms": worst, "p95_ms": statistics.median(p95) if p95 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("basic", "advanced", "both"), default="both")
    ap.add_argument("--secs", type=float, default=20.0)
    ap.add_argument("--hover", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--src", default=None, help="another src folder to measure (e.g. a worktree of an older commit)")
    a = ap.parse_args()
    for mode in (("basic", "advanced") if a.mode == "both" else (a.mode,)):
        r = run(mode, a.secs, a.hover, a.python, a.src)
        if r is None:
            print(f"{mode}: no frames measured")
            continue
        p95 = f"p95 {r['p95_ms']:.1f} ms, " if r["p95_ms"] is not None else ""
        print(f"{mode}{' +hover' if a.hover else ''}: {r['windows']} windows, median frame interval "
              f"{r['median_interval_ms']:.1f} ms (~{r['fps']:.0f} fps), frame cost {r['cost_ms']:.1f} ms avg, "
              f"{p95}worst {r['worst_ms']:.1f} ms")


if __name__ == "__main__":
    main()
