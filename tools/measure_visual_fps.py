"""What the eye gets: frames per second of the row hover swell as they reach the SCREEN (not the animation loop).

    python tools/measure_visual_fps.py                 # your theme / view (copied from your config), 12 s
    python tools/measure_visual_fps.py --theme aurora --mode basic

Starts src/panel.py with a temporary data folder holding a copy of your settings (your own file is never written),
lets it swell and shrink the first row name every 0.3 s (METRIQ_FXBENCH=row0) and copies that screen region with
BitBlt as fast as it can (~1 ms a copy). Every change of the pixels is one presented frame. Per swell / shrink it
prints how many different pictures the motion had and over how long: a smooth 60 fps motion of ~0.15 s has ~9.
"""
import argparse
import ctypes
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))     # physical pixels, like the panel
except Exception:
    pass
u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
gdi.CreateCompatibleDC.restype = W.HDC
gdi.CreateDIBSection.restype = W.HBITMAP
gdi.SelectObject.restype = W.HGDIOBJ
u32.GetDC.restype = W.HDC
u32.GetDC.argtypes = [W.HWND]
gdi.CreateCompatibleDC.argtypes = [W.HDC]
gdi.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
gdi.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int,
                       ctypes.c_int, W.DWORD]


class BMIH(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


class Grabber:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.sdc = u32.GetDC(None)
        self.mdc = gdi.CreateCompatibleDC(self.sdc)
        bi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bmp = gdi.CreateDIBSection(self.mdc, ctypes.byref(bi), 0, ctypes.byref(self.bits), None, 0)
        gdi.SelectObject(self.mdc, self.bmp)

    def grab(self):
        gdi.BitBlt(self.mdc, 0, 0, self.w, self.h, self.sdc, self.x, self.y, 0x00CC0020)
        return hashlib.md5(ctypes.string_at(self.bits, self.w * self.h * 4)).digest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme")
    ap.add_argument("--mode", choices=("basic", "advanced"))
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument("--src", default=os.path.join(ROOT, "src"))
    a = ap.parse_args()
    d = tempfile.mkdtemp(prefix="metriq_vfps_")
    try:
        cfg = json.load(open(os.path.join(os.environ["LOCALAPPDATA"], "metriQ", "config.json"), encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.update(check_updates=False, manage_hwinfo=False, sync_hwinfo_interval=False, bg_hint_shown=True,
               ui_mode_hint_shown=True)
    if a.theme:
        cfg["theme"] = a.theme
    if a.mode:
        cfg["ui_mode"] = a.mode
    json.dump(cfg, open(os.path.join(d, "config.json"), "w", encoding="utf-8"))
    where = os.path.join(d, "row.txt")
    env = dict(os.environ, METRIQ_DIR=d, METRIQ_FXBENCH="row0", METRIQ_FXBENCH_OUT=where,
               METRIQ_QUIT_AFTER=str(a.secs + 6))
    p = subprocess.Popen([sys.executable, os.path.join(a.src, "panel.py")], env=env, cwd=a.src)
    t_end = time.time() + 20
    while not os.path.isfile(where) and time.time() < t_end:
        time.sleep(0.05)
    if not os.path.isfile(where):
        print("no row found")
        p.kill()
        return
    time.sleep(0.2)
    x0, y0, x1, y1 = map(int, open(where).read().split())
    g = Grabber(x0 - 4, y0 - 6, (x1 - x0) + 60, (y1 - y0) + 12)
    times, prev = [], None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < a.secs:
        h = g.grab()
        if h != prev:
            times.append(time.perf_counter())
            prev = h
    p.wait(30)
    # bursts: changes closer than 60 ms belong to one motion
    bursts, cur = [], [times[0]] if times else []
    for t in times[1:]:
        if t - cur[-1] > 0.06:
            bursts.append(cur)
            cur = [t]
        else:
            cur.append(t)
    if cur:
        bursts.append(cur)
    bursts = [b for b in bursts if len(b) > 1]
    if not bursts:
        print("no motion seen")
        return
    frames = [len(b) for b in bursts]
    durs = [b[-1] - b[0] for b in bursts]
    gaps = [(y - x) * 1000 for b in bursts for x, y in zip(b, b[1:])]
    fps = [(len(b) - 1) / (b[-1] - b[0]) for b in bursts if b[-1] > b[0]]
    if os.environ.get("VFPS_DEBUG"):
        for b_ in bursts[:8]:
            print("   ", " ".join("%.0f" % ((y - x) * 1000) for x, y in zip(b_, b_[1:])))
    lat = []
    try:
        gos = [float(x) for x in open(where + ".go").read().split()]
        for g0 in gos:
            if g0 < t0:                                   # asked before the capture ran
                continue
            first = next((t for t in times if t > g0), None)
            if first is not None and first - g0 < 0.3:
                lat.append((first - g0) * 1000)
    except OSError:
        pass
    if lat:
        if os.environ.get("VFPS_DEBUG"):
            print("    latencies", " ".join("%.0f" % x for x in lat))
        print(f"swell asked -> first change on screen: median {statistics.median(lat):.1f} ms, max {max(lat):.1f} ms")
    print(f"theme {cfg.get('theme')} / {cfg.get('ui_mode')}: {len(bursts)} motions, pictures per motion median "
          f"{statistics.median(frames):.0f}, motion {statistics.median(durs) * 1000:.0f} ms, on-screen fps median "
          f"{statistics.median(fps):.0f}, frame gap median {statistics.median(gaps):.1f} ms, max {max(gaps):.1f} ms, "
          f"gaps over 25 ms {sum(1 for g in gaps if g > 25)} of {len(gaps)}")


if __name__ == "__main__":
    main()
