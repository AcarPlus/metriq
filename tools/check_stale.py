"""Unit check of the HWiNFO staleness detector (src/hwsm.py Staleness) and of the Basic status levels.

    python tools/check_stale.py          # exit code 0 = all fine

No HWiNFO needed: the detector is fed synthetic (signature, poll time, now) triples.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import basicmode  # noqa: E402
import hwsm  # noqa: E402
import layout  # noqa: E402

ACTIVE = 0x53695748          # "HWiS"
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (expected {want!r})"))


# --- staleness
d = hwsm.Staleness(limit=10.0)
check("first read is fresh", d.update(ACTIVE, 100, 0.0), False)
check("poll advancing", d.update(ACTIVE, 102, 2.0), False)
check("same poll 8 s later: not yet", d.update(ACTIVE, 102, 10.0), False)
check("same poll 12 s later: stale", d.update(ACTIVE, 102, 14.5), True)
check("poll advances again: fresh", d.update(ACTIVE, 140, 16.0), False)
check("DEAD signature: stale at once", d.update(hwsm.SIG_DEAD, 150, 17.0), True)
check("signature back to active: fresh", d.update(ACTIVE, 160, 18.0), False)
d.reset()
check("reset: fresh", d.stale, False)
d = hwsm.Staleness(limit=30.0)
d.update(ACTIVE, 1, 0.0)
check("slow HWiNFO interval (limit 30 s): 20 s is fine", d.update(ACTIVE, 1, 20.0), False)

# --- Basic status levels: fixed absolute limits, independent of the row's colour scale
def row(rid, kind, limits):
    return layout.Row("x", None, "^x$", 1, None, "", limits, True, True, None, rid, kind)


cpu, gpu = row("cpu_temp", "temp", (30, 95)), row("gpu_temp", "temp", (30, 88))
for v, want in ((60, 0), (84.9, 0), (85, 1), (94.9, 1), (95, 2)):
    check(f"cpu temp {v}", basicmode.level(cpu, v), want)
for v, want in ((70, 0), (82.9, 0), (83, 1), (89.9, 1), (90, 2)):
    check(f"gpu temp {v}", basicmode.level(gpu, v), want)
hot = row("gpu_hot", "temp", (35, 100))
check("gpu hotspot 94 / 95 / 105", [basicmode.level(hot, v) for v in (94, 95, 105)], [0, 1, 2])
use = row("cpu_usage", "usage", (0, 100))
check("usage 10 / 50 / 90", [basicmode.level(use, v) for v in (10, 50, 90)], [0, 1, 2])
C = {"green": "g", "yellow": "y", "red": "r", "fg": "n"}
check("temp colours follow the level", [basicmode.color(cpu, v, C) for v in (60, 90, 99)], ["g", "y", "r"])
check("usage stays neutral at 100 %", basicmode.color(use, 100, C), "n")
ram = row("ram", "usage", (0, 32768))
check("ram text", basicmode.value_text(ram, 11264), ("11.0 / 32", "GB"))
sys.exit(1 if fails else 0)
