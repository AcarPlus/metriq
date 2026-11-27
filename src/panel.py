"""metriQ: a tkinter sensor dashboard (HWiNFO64, LibreHardwareMonitor or built-in Windows sensors, see README)."""
import copy
import csv
import math
import os
import sys
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import types
import webbrowser
import winsound

import alerts
import aurora
import backend
import basicmode
import config
import crashscan
import gfx
import hwsm
import helptexts
import hwctl
import i18n
import layout
import proclog
import logview
import minimode
import perf_core
import procmon
import textsprite
import procwin
import settings_ui
import themes
import throttle
import tray
import updater
import vsync
import winframe
from tooltip import Tooltip
from i18n import t

HERE = config.APP_DIR        # source folder, or the bundled resources when frozen (PyInstaller)
ICO = os.path.join(HERE, "metriq.ico")


# ---------------------------------------------------------------- CSV logger
class Logger:
    """Every recording session (on -> off) goes to its own CSV file."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.f = self.w = self.path = None
        self.last = 0
        self.ext = None                 # proclog.Extras: per-process columns / side file / size limit
        self.delim, self.dec = config.csv_format(cfg)

    def num(self, v, dec):
        """Locale friendly number, e.g. 64.555 -> '64,6' when the decimal symbol is a comma."""
        return f"{v:.{dec}f}".replace(".", self.dec)

    def close(self):
        if self.f:
            self.f.close()
            crashscan.mark_closed(self.path)      # clean stop (the crash detective looks for recordings without it)
        self.f = self.w = self.path = None
        if self.ext:
            self.ext.closed()

    def write(self, stamp, header, values):
        if self.f is not None and self.ext and self.ext.rotate(self.f):
            self.close()                # log_max_mb reached: continue in a new file
        if self.f is None:
            d = config.log_dir(self.cfg)
            os.makedirs(d, exist_ok=True)
            base = os.path.join(d, stamp.replace(" ", "_").replace(":", "-"))
            self.path, n = base + ".csv", 1
            while os.path.exists(self.path):          # a file of this very second already exists (size limit)
                n += 1
                self.path = f"{base}_{n}.csv"
            self.f = open(self.path, "a", newline="", encoding="utf-8-sig")
            crashscan.mark_open(self.path)
            self.w = csv.writer(self.f, delimiter=self.delim)
            self.w.writerow([t("csv_time")] + header)
            if self.ext:
                self.ext.opened(self.path, len(header) + 1)
        self.w.writerow([stamp] + values)
        self.f.flush()
        try:
            # straight to disk, not only to the Windows cache: after a freeze / power loss every row written
            # so far is on disk (used to find out what happened right before a lock-up)
            os.fsync(self.f.fileno())
        except OSError:
            pass
        if self.ext:
            self.ext.written(self.f, stamp)


def repair_csv_tails(folder, newest=5):
    """After a freeze or power loss the last CSV can end in NUL bytes or a half row: cut that tail off so the
    file opens cleanly. Only the newest few files are checked; complete files are left untouched."""
    try:
        files = sorted((os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(".csv")),
                       key=os.path.getmtime, reverse=True)[:newest]
    except OSError:
        return
    for p in files:
        try:
            with open(p, "rb") as f:
                data = f.read()
            good = data.rstrip(b"\x00")
            if good and not good.endswith(b"\n"):
                cut = good.rfind(b"\n")
                good = good[:cut + 1] if cut >= 0 else b""
            if good != data:
                with open(p, "r+b") as f:
                    f.truncate(len(good))
                    f.flush()
                    os.fsync(f.fileno())
        except OSError:
            pass


# ---------------------------------------------------------------- design
# every colour / style comes from the active theme (themes.py); C is refreshed in place when the theme changes
C = themes.palette()
mix = themes.mix
FXPROF = os.environ.get("METRIQ_FXPROF")
FXBENCH = os.environ.get("METRIQ_FXBENCH")       # tools/bench_fx.py --hover: sweep the row hover over every row
THEME_KEYS = {"theme", "theme_overrides", "value_color_mode", "value_steps", "motion"}


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


GFX = gfx.AVAILABLE      # Pillow present: anti-aliased images instead of canvas primitives
TOAST_RESET_S, TOAST_INFO_S, TOAST_ERR_S, FADE_S = 1.5, 2.0, 4.0, 0.35     # toast lifetimes (seconds), fade-out length
TOAST_HINT_S = 9.0                 # the one-time "Basic view is on" notice


def draw_icon(cv, kind, cx, cy, s, col, bg, tags=()):
    """s: half size of the icon. Simple vector icons (no font dependency).
    "gear" (settings button): coloured parts also carry the tag "gear_fill", the hole "gear_hole"."""
    lw = max(2, s / 5)
    if kind == "gear":
        for k in range(8):
            a = math.radians(k * 45 + 22.5)
            ux, uy = math.cos(a), math.sin(a)
            px, py = -uy, ux
            r0, r1, w0, w1 = 0.55 * s, 1.0 * s, 0.30 * s, 0.19 * s
            cv.create_polygon(cx + ux * r0 + px * w0, cy + uy * r0 + py * w0, cx + ux * r1 + px * w1,
                              cy + uy * r1 + py * w1, cx + ux * r1 - px * w1, cy + uy * r1 - py * w1,
                              cx + ux * r0 - px * w0, cy + uy * r0 - py * w0, fill=col, outline="",
                              tags=tuple(tags) + ("gear_fill",))
        cv.create_oval(cx - s * 0.74, cy - s * 0.74, cx + s * 0.74, cy + s * 0.74, fill=col, outline="",
                       tags=tuple(tags) + ("gear_fill",))
        cv.create_oval(cx - s * 0.30, cy - s * 0.30, cx + s * 0.30, cy + s * 0.30, fill=bg, outline="",
                       tags=tuple(tags) + ("gear_hole",))
        return
    if kind == "procs":
        t_ = tuple(tags) + ("procs_fill",)
        w_ = max(2, s * 0.2)
        for yy, ln in ((-0.62, 1.0), (0.0, 0.72), (0.62, 0.46)):
            r_ = w_ * 0.72
            cv.create_oval(cx - s * 0.78 - r_, cy + yy * s - r_, cx - s * 0.78 + r_, cy + yy * s + r_, fill=col,
                           outline="", tags=t_)
            cv.create_line(cx - s * 0.36, cy + yy * s, cx - s * 0.36 + ln * s * 1.26, cy + yy * s, fill=col, width=w_,
                           capstyle="round", tags=t_)
        return
    if kind == "chart":
        t_ = tuple(tags) + ("chart_fill",)
        w_ = max(2, s * 0.2)
        cv.create_line(cx - s * 0.85, cy - s * 0.85, cx - s * 0.85, cy + s * 0.85, cx + s * 0.95, cy + s * 0.85,
                       fill=col, width=w_, tags=t_)
        cv.create_line(cx - s * 0.5, cy + s * 0.35, cx - s * 0.1, cy - s * 0.1, cx + s * 0.25, cy + s * 0.2,
                       cx + s * 0.8, cy - s * 0.6, fill=col, width=w_, joinstyle="round", tags=t_)
        return
    if kind == "reset":
        cv.create_arc(cx - s * 0.78, cy - s * 0.78, cx + s * 0.78, cy + s * 0.78, start=70, extent=290,
                      style="arc", outline=col, width=max(2, s * 0.22), tags=tuple(tags) + ("reset_fill",))
        a = math.radians(70)
        tx, ty = cx + s * 0.78 * math.cos(a), cy - s * 0.78 * math.sin(a)
        cv.create_polygon(tx - s * 0.42, ty - s * 0.30, tx + s * 0.18, ty - s * 0.02, tx - s * 0.30, ty + s * 0.42,
                          fill=col, outline="", tags=tuple(tags) + ("reset_fill",))
        return
    if kind == "gauge":
        cv.create_arc(cx - s, cy - s, cx + s, cy + s, start=-30, extent=240, style="arc", outline=col, width=lw)
        ang = math.radians(50)
        cv.create_line(cx, cy, cx + s * 0.7 * math.cos(ang), cy - s * 0.7 * math.sin(ang), fill=col,
                       width=lw, capstyle="round")
        cv.create_oval(cx - lw, cy - lw, cx + lw, cy + lw, fill=col, outline="")
    elif kind == "bolt":
        pts = [(0.2, -1), (-0.6, 0.15), (-0.05, 0.15), (-0.25, 1), (0.6, -0.2), (0.05, -0.2)]
        cv.create_polygon([v for x, y in pts for v in (cx + x * s, cy + y * s)], fill=col, outline="")
    elif kind == "temp":
        cv.create_line(cx - s * 0.15, cy - s * 0.75, cx - s * 0.15, cy + s * 0.3, fill=col, width=s * 0.5,
                       capstyle="round")
        cv.create_oval(cx - s * 0.57, cy + s * 0.08, cx + s * 0.27, cy + s * 0.92, fill=col, outline="")
        for k in (-0.6, -0.3, 0.0):
            cv.create_line(cx + s * 0.3, cy + k * s, cx + s * 0.7, cy + k * s, fill=col, width=max(1, lw * 0.6))
    elif kind == "fan":
        for k in range(3):
            a0 = math.radians(k * 120 + 90)
            pts = [cx, cy]
            for tt in range(1, 10):
                f = tt / 10
                ang = a0 - f * math.radians(80)
                rr = s * (0.35 + 0.65 * math.sin(f * math.pi) ** 0.5)
                pts += [cx + rr * math.cos(ang), cy - rr * math.sin(ang)]
            cv.create_polygon(pts, fill=col, outline="", smooth=True)
        cv.create_oval(cx - s * 0.24, cy - s * 0.24, cx + s * 0.24, cy + s * 0.24, fill=bg, outline=col,
                       width=max(1, lw * 0.7))
    elif kind == "net":
        for dx, up in ((-0.42, False), (0.42, True)):
            xx = cx + dx * s
            y0, y1 = (cy + s * 0.9, cy - s * 0.9) if up else (cy - s * 0.9, cy + s * 0.9)
            cv.create_line(xx, y0, xx, y1, fill=col, width=lw, arrow="last",
                           arrowshape=(s * 0.55, s * 0.55, s * 0.38))
    elif kind == "fps":
        round_rect(cv, cx - s, cy - s * 0.75, cx + s, cy + s * 0.5, s * 0.25, fill="", outline=col, width=lw)
        cv.create_polygon(cx - s * 0.22, cy - s * 0.42, cx - s * 0.22, cy + s * 0.17, cx + s * 0.33,
                          cy - s * 0.12, fill=col, outline="")
        cv.create_line(cx - s * 0.45, cy + s * 0.92, cx + s * 0.45, cy + s * 0.92, fill=col, width=lw,
                       capstyle="round")


def pick_font(*names):
    fams = set(tkfont.families())
    return next((n for n in names if n in fams), "TkDefaultFont")


def theme_font(token, *auto):
    """The font family chosen in the theme (token), or the first installed one of the automatic choices."""
    want = themes.current()[token]
    return want if want and want in set(tkfont.families()) else pick_font(*auto)


def _fit_status_bar(family, lx, lbl_r, sw_w, rs, fs0, mode_labels):
    """Advanced status bar: font size (negative = pixels) and what to show so that
    [Paneldeki veriler (o) Tüm veriler] .. [Basit | Gelişmiş] [interval label] do not overlap. Returns
    (font size, show interval label, show left switch caption, show right switch caption, compact mode chip).
    Long translations or a narrow panel shrink the text only down to a readable size (12 px or more) while dropping, in
    order, the interval label (the spinbox keeps its tooltip), the second word of the mode chip (current mode only), the
    left caption (knob position + right caption still read) and last the right caption. Only when even that does not fit
    does the text go down to 8 px. The chip itself always stays (it is the way back)."""
    gap = max(rs * 0.6, 8.0)
    cpad = max(6.0, rs * 0.45)

    def need(fs, with_lbl, with_left, with_right, compact):
        fb = tkfont.Font(family=family, size=fs, weight="bold")
        w = sw_w
        if with_left:
            w += fb.measure(t("sw_required")) + rs * 0.35
        if with_right:
            w += rs * 0.35 + fb.measure(t("sw_all"))
        w += gap + (max(fb.measure(x) for x in mode_labels) + 2 * cpad if compact
                    else sum(fb.measure(x) + 2 * cpad for x in mode_labels))
        if with_lbl:
            w += gap + tkfont.Font(family=family, size=fs).measure(t("interval_lbl"))
        return lx + w

    # without the label the chip may end a few px before the spinbox; 2 px slack for the GFX rounding of the track
    stages = ((True, True, True, False, lbl_r - 2), (False, True, True, False, lbl_r + 2),
              (False, True, True, True, lbl_r + 2), (False, False, True, False, lbl_r + 2),
              (False, False, True, True, lbl_r + 2), (False, False, False, True, lbl_r + 2))
    # first pass: never below a readable size (drop parts instead); only if nothing fits, shrink down to 8 px
    readable = max(fs0, -max(12, int(rs * 0.32)))
    fs = fs0
    for floor in (readable, -8):
        for with_lbl, with_left, with_right, compact, limit in stages:
            fs = fs0
            while fs < floor and need(fs, with_lbl, with_left, with_right, compact) > limit:
                fs += 1
            if need(fs, with_lbl, with_left, with_right, compact) <= limit:
                return fs, with_lbl, with_left, with_right, compact
    return fs, False, False, False, True


def launch_command():
    """Command line that starts this panel again (used by Windows when the taskbar button is pinned)."""
    if config.FROZEN:
        return f'"{sys.executable}"'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() == "python.exe" and os.path.isfile(w):
        exe = w
    return f'"{exe}" "{os.path.join(HERE, "panel.py")}"'


def _end_process(code=0):
    """End the process now, after our own clean-up, WITHOUT Tk's and Python's teardown.
    Tk 9 (the frozen build) can corrupt its memory while it destroys the windows and images at exit: Tcl then
    panics ("alloc: invalid block", the "Fatal Error" box, 0x80000003 in tcl90.dll) on nearly every close.
    TerminateProcess skips all of that (no DLL detach either); the logs and benchmarks are closed by then."""
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.WinDLL("kernel32")
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            k.TerminateProcess(k.GetCurrentProcess(), code)
        except Exception:
            pass
    os._exit(code)


def _arm_hard_exit(seconds):
    """Last resort: end the process when a clean exit takes longer than `seconds` (a stuck child process or driver)."""
    def go():
        time.sleep(seconds)
        _end_process(0)
    threading.Thread(target=go, name="hard-exit", daemon=True).start()


def main():
    if not tray.acquire_instance():              # one panel per data folder: ask the running one to show itself
        if tray.signal_existing():
            sys.exit(0)
    # tray thread + hidden message window: also answers the "show yourself" of a second launch, so it starts first
    tr = tray.Tray(ICO)
    cfg, note = config.load_config()
    i18n.set_language(cfg["language"])
    themes.configure(cfg)
    C.clear()
    C.update(themes.palette())
    try:  # own taskbar identity instead of "python"
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(config.APP_ID)
    except Exception:
        pass

    root = tk.Tk()
    root.title(config.APP_NAME)
    try:
        root.iconbitmap(default=ICO)
    except Exception:
        pass
    try:  # packaged (Store) Python: set identity + icon on the window itself before it is shown
        import winicon
        root.withdraw()
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        winicon.set_window_identity(hwnd, config.APP_ID, ICO + ",0", launch_command(), config.APP_NAME)
        winicon.set_window_icons(hwnd, ICO)
        root.deiconify()
    except Exception as ex:
        print("icon could not be set:", ex, file=sys.stderr)
        root.deiconify()
    root.configure(bg=C["bg"])
    root.attributes("-topmost", bool(cfg["topmost"]))
    root.minsize(400, 480)
    if FXPROF:                     # diagnostics: name every root.after callback that holds the UI thread > 12 ms
        _after = root.after

        def _timed_after(ms, func=None, *args):
            if func is None:
                return _after(ms)

            def run(*a):
                t0_ = time.perf_counter()
                try:
                    return func(*a)
                finally:
                    d_ = time.perf_counter() - t0_
                    if d_ > 0.012:
                        with open(FXPROF, "a", encoding="utf-8") as f_:
                            f_.write("   slow %s %.1f ms\n" % (getattr(func, "__qualname__", func), d_ * 1000))
            return _after(ms, run, *args)
        root.after = _timed_after

    F_TXT = F_HEAD = F_NUM = "TkDefaultFont"

    def apply_fonts():
        nonlocal F_TXT, F_HEAD, F_NUM
        F_TXT = theme_font("font_text", "Segoe UI Variable Text", "Segoe UI")
        F_HEAD = theme_font("font_head", "Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI")
        F_NUM = theme_font("font_num", "Cascadia Mono", "Consolas")
    apply_fonts()
    cv = tk.Canvas(root, bg=C["bg"], highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)

    logger = Logger(cfg)
    logger.ext = proclog.Extras(cfg, logger)
    repair_csv_tails(config.log_dir(cfg))      # a recording cut short by a freeze opens cleanly again
    stats = {}          # row.key -> [min, max, sum, count]; reset at panel start and when recording starts
    alert_eng = alerts.Engine()                 # value alerts: Settings > Alerts
    st = {"alert_rows": {}, "alert_icon": None, "alert_key": None, "items": {},"last": {}, "status": {}, "size": None, "ok": False, "sections": [], "sections_cfg": [],
          "auto": True, "auto_n": -1, "checked": False, "empty": None, "note": note, "settings": None,
          "hw_exe": hwctl.detect_hwinfo(cfg["hwinfo_path"]), "tasks": hwctl.tasks_installed(),
          "mon": config.pick_monitor(cfg["monitor"]), "mode": str(cfg["window_mode"]).lower(),
          "upd": None, "upd_busy": False, "upd_prog": 0, "upd_err": 0, "upd_gen": 0, "upd_after": None,
          "closing": False, "t0": time.time(), "upd_dismissed": None, "tips": {},
          "bsections": [], "bauto_n": -1, "bauto_t": 0.0,
          "fx": {"on": False, "pal": None, "motion": False, "sky": None, "gauges": {}, "entry": [], "keep": [],
                 "anims": {}, "job": None, "t_bg": 0.0, "last": 0.0, "t0": aurora.clock()},
          "entry_next": True, "hovers": []}
    upd_q = queue.Queue()          # worker threads -> Tk (drained by upd_pump via root.after)

    def hwinfo_managed():
        """The panel starts / stops HWiNFO itself (option on, install.ps1 tasks present, HWiNFO installed)."""
        return bool(cfg["manage_hwinfo"]) and st["tasks"] and os.path.isfile(st["hw_exe"] or "")

    src = backend.Manager(cfg, hwinfo_managed)
    st["gen"] = src.gen

    def hw_active():
        return src.active == "hwinfo"

    def manage_on():
        return bool(cfg["manage_hwinfo"]) and st["tasks"] and hw_active()

    def sync_on():
        return bool(cfg["sync_hwinfo_interval"]) and st["tasks"] and hw_active()

    def place(final=False):
        """Put the window on the chosen monitor, then maximise / full-screen it."""
        mon, mode = st["mon"], st["mode"]
        if mon:
            w, h = min(900, mon["ww"] - 40), min(1400, mon["wh"] - 60)
            x, y = mon["wx"] + 20, mon["wy"] + 20
        else:
            w, h = min(900, root.winfo_screenwidth() - 40), min(1400, root.winfo_screenheight() - 80)
            x, y = 20, 20
        set_fullscreen(False)
        root.state("normal")
        root.geometry(f"{w}x{h}+{x}+{y}")
        if not final:
            return
        root.update_idletasks()
        if mode == "fullscreen":
            set_fullscreen(True)
        elif mode != "window":
            root.state("zoomed")

    pacer = vsync.Pacer(lambda: int(root.wm_frame(), 16))     # frames locked to the refresh of the panel's monitor
    frame = winframe.Frameless.inactive()            # replaced by the real one once the window is placed (below)

    def is_fullscreen():
        if frame.active:
            return bool(st.get("fs"))
        try:
            return bool(root.attributes("-fullscreen"))
        except tk.TclError:
            return False

    def set_fullscreen(on):
        """Full screen = the whole monitor the panel is on (taskbar included). Windows: done here (winframe), since
        Tk's own full screen always opens on the primary monitor; leaving it brings back the window as it was
        (maximised again if it was)."""
        on = bool(on)
        if on == is_fullscreen():
            return
        if frame.active:
            if on:
                zoomed = root.state() == "zoomed"
                if zoomed:
                    root.state("normal")
                    root.update_idletasks()
                st["fs_back"] = (zoomed, root.geometry())      # the normal size, kept for a later restore
                st["fs"] = frame.fullscreen(True)
            else:
                st["fs"] = False
                frame.fullscreen(False)
                zoomed, geo = st.pop("fs_back", (False, None))
                if geo:
                    root.geometry(geo)
                if zoomed:
                    root.state("zoomed")
            return
        try:
            root.attributes("-fullscreen", on)
        except tk.TclError:
            pass

    place()
    # no Windows title bar: the status row at the top is the panel's own title bar (window buttons at its right end,
    # its empty parts move the window). Aero Snap, Win+arrows, the system menu and the shadow stay (winframe.py)
    frame = winframe.Frameless(root)
    if frame.active:
        frame.set_colors(themes.current().is_dark(), C["border"], C["bg"])
        root.bind("<Map>", lambda e: frame.ensure() if e.widget is root else None, add="+")
        try:                                     # started in Tk's full screen (before this frame existed): ours
            if bool(root.attributes("-fullscreen")):
                root.attributes("-fullscreen", False)
                set_fullscreen(True)
        except tk.TclError:
            pass

    spin = tk.Spinbox(root, from_=config.INTERVAL_MIN, to=config.INTERVAL_MAX, increment=0.5, format="%.1f",
                      width=5, bg=C["track"], fg=C["fg"], insertbackground=C["fg"], buttonbackground=C["track"],
                      justify="center", relief="flat", highlightthickness=0, bd=0)
    spin.delete(0, "end")
    spin.insert(0, f"{cfg['interval']:.1f}")
    Tooltip(spin, lambda: helptexts.text("interval_lbl"))

    def apply_theme():
        """Take the theme settings from cfg: palette, fonts, window colours. The caller rebuilds the canvas."""
        themes.configure(cfg)
        C.clear()
        C.update(themes.palette())
        apply_fonts()
        root.configure(bg=C["bg"])
        cv.configure(bg=C["bg"])
        if frame.active:
            frame.set_colors(themes.current().is_dark(), C["border"], C["bg"])
        spin.config(bg=C["track"], fg=C["fg"], insertbackground=C["fg"], buttonbackground=C["track"])

    def current_hint():
        """Why there is no data (empty when everything is fine)."""
        if st["ok"]:
            return ""
        if src.active == "lhm":
            return t("hint_lhm")
        if not hw_active():
            return ""
        now = time.time()
        if now - st.get("pr_t", 0) > 3:              # a process snapshot per tick is wasted work while nothing changes
            st["pr_t"], st["pr_v"] = now, hwctl.proc_running()
        h = t("hint_no_shm") if st["pr_v"] else t("hint_not_running")
        if not st["tasks"]:
            h += " · " + t("hint_readonly_app" if hwctl.task_script() else "hint_readonly")
        return h

    def conn_text():
        if src.active == "lhm":
            return t("conn_lhm_wmi") if src.via() == "wmi" else t("conn_lhm")
        if src.active == "builtin":
            return t("conn_builtin") + " (" + t("conn_builtin_short") + ")"
        return t("conn_ok")

    def wait_text():
        return t("conn_wait_lhm") if src.active == "lhm" else t("conn_wait")

    # ------------------------------------------------------------ drawing
    def meas_font(family, size, weight="normal"):
        return tkfont.Font(family=family, size=size, weight=weight)

    def fit_text(font, text, avail):
        """text, shortened with an ellipsis when it is wider than avail pixels."""
        if avail <= 0 or font.measure(text) <= avail:
            return text
        while len(text) > 1 and font.measure(text + "…") > avail:
            text = text[:-1]
        return text + "…"

    # ------------------------------------------------------------ one animation loop for everything that moves
    # Every animation (hover swell, number count, bar glide, row entry, mode chip, Aurora gauges) is a step of
    # fx_step, the single root.after loop: it runs at ~60 fps only while something moves and stops when all is still.
    # Steps only reconfigure their own canvas items, so Tk repaints just those regions over the baked background.
    NUM_S = 0.35                                    # number count-up / count-down length (seconds)
    HOVER_S = 0.16                                  # row name swell length (seconds)
    FONTS = {}                                      # (family, px) -> tkfont.Font (px_font)

    def anim_now():
        """Current time of the animation clock (fx_step): start times of animations are taken from it."""
        return st["fx"].get("vnow", 0.0)

    def anim_wake():
        FX = st["fx"]
        if FX["on"] and not st["closing"] and not FX.get("job"):
            # the first frame right after the event (no timer: a Windows timer at rest rounds up to ~16 ms)
            FX["job"] = root.after_idle(fx_step)

    def anim_set(key, fn):
        """fn(now, dt) -> still moving? is called once per frame until it returns False (same key: replaced)."""
        st["fx"]["anims"][key] = fn
        anim_wake()

    def px_font(family, px):
        """A named Tk font of `px` pixels, kept alive: a hover step only switches between ready fonts (no font is
        created or freed per frame)."""
        key = (family, px)
        f = FONTS.get(key)
        if f is None:
            f = FONTS[key] = tkfont.Font(root=root, family=family, size=-px)
        return f

    def ease_out(x):
        return 1 - (1 - x) ** 3

    # ------------------------------------------------------------ hover: row names with a balloon swell a little
    CLOSE_RED = "#c42b1c"

    def title_row(W, sy, sb_h, wb_x0, wb_w, R, pal):
        """The status row is the window's title bar (no Windows caption, winframe.Frameless):
        * every empty part of it, and the margin above it, moves the window (Windows' own move loop, started by a
          POSTED message - see winframe: Aero Snap, drag to the top to maximise, shake); double-click maximises /
          restores; right-click opens the system menu; the window's own thin top border resizes it;
        * [ – ] [ □ ] [ ✕ ] at its right end, fading into their hover colour (close turns red)."""
        y1 = sy + sb_h
        ph = tk.PhotoImage(master=root, width=max(1, int(W)), height=max(1, int(y1)))   # transparent, still picked
        st.setdefault("hit_imgs", []).append(ph)
        drag = cv.create_image(0, 0, anchor="nw", image=ph, tags=("tdrag",))

        def zoomed():
            try:
                return root.state() == "zoomed" or is_fullscreen()
            except tk.TclError:
                return False

        cv.tag_bind(drag, "<Button-1>", lambda e: frame.start_move(cv, e))
        cv.tag_bind(drag, "<Double-Button-1>", lambda e: toggle_max())
        cv.tag_bind(drag, "<Button-3>", lambda e: frame.system_menu(e.x_root, e.y_root))
        # ---- window buttons
        inset = max(3.0, sb_h * 0.10)
        by0, by1 = sy + inset, y1 - inset
        g = max(4.5, sb_h * 0.13)                                    # half size of a glyph
        rest = C["fg"]
        for i, kind in enumerate(("min", "max", "close")):
            x0 = wb_x0 + i * wb_w + 1
            x1 = x0 + wb_w - 2 - (inset if kind == "close" else 0)
            cx, cy = (x0 + x1) / 2, (by0 + by1) / 2
            tag = "wbtn_" + kind
            bg_id = round_rect(cv, x0, by0, x1, by1, min(R, sb_h * 0.30), fill="", outline="", tags=(tag,))
            w = max(1, int(round(sb_h / 30)))
            ids = []
            if kind == "min":
                ids.append(cv.create_line(cx - g, cy, cx + g, cy, fill=rest, width=w, tags=(tag,)))
            elif kind == "max":
                if zoomed():                                         # restore glyph: two overlapping squares
                    d = g * 0.35
                    ids.append(cv.create_rectangle(cx - g, cy - g + d, cx + g - d, cy + g, outline=rest, width=w,
                                                   tags=(tag,)))
                    ids.append(cv.create_line(cx - g + d, cy - g + d, cx - g + d, cy - g, cx + g, cy - g, cx + g,
                                              cy + g - d, cx + g - d, cy + g - d, fill=rest, width=w, tags=(tag,)))
                else:
                    ids.append(cv.create_rectangle(cx - g, cy - g, cx + g, cy + g, outline=rest, width=w, tags=(tag,)))
            else:
                ids.append(cv.create_line(cx - g, cy - g, cx + g, cy + g, fill=rest, width=w, tags=(tag,)))
                ids.append(cv.create_line(cx - g, cy + g, cx + g, cy - g, fill=rest, width=w, tags=(tag,)))
            # the pointer target: a transparent image over the whole button (an unfilled shape is not picked)
            bph = tk.PhotoImage(master=root, width=max(1, int(x1 - x0)), height=max(1, int(by1 - by0)))
            st["hit_imgs"].append(bph)
            cv.create_image(x0, by0, anchor="nw", image=bph, tags=(tag,))
            hov = CLOSE_RED if kind == "close" else mix(C["card"], C["fg"], 0.14)
            glyph_on = "#ffffff" if kind == "close" else C["fg"]
            a = {"v": 0.0, "to": 0.0}

            def step(now, dt, a=a, bg_id=bg_id, ids=ids, hov=hov, glyph_on=glyph_on):
                d = a["to"] - a["v"]
                a["v"] = a["to"] if abs(d) < 0.03 else a["v"] + d * (1 - math.exp(-dt / 0.05))
                v = a["v"]
                cv.itemconfig(bg_id, fill="" if v <= 0.01 else mix(C["card"], hov, v))
                col = mix(rest, glyph_on, v)
                for i_ in ids:
                    try:
                        cv.itemconfig(i_, fill=col) if cv.type(i_) == "line" else cv.itemconfig(i_, outline=col)
                    except tk.TclError:
                        pass
                return a["v"] != a["to"]

            def hover(on, a=a, step=step, tag=tag):
                a["to"] = 1.0 if on else 0.0
                anim_set(("wbtn", tag), step)
            cv.tag_bind(tag, "<Enter>", lambda e, h=hover: h(True))
            cv.tag_bind(tag, "<Leave>", lambda e, h=hover: h(False))
            act = {"min": lambda e: root.iconify(), "max": lambda e: toggle_max(), "close": lambda e: on_close()}[kind]
            cv.tag_bind(tag, "<ButtonRelease-1>", act)

    def toggle_max():
        """Maximise / restore (the title bar's double-click and its middle button); full screen is left first."""
        try:
            if is_fullscreen():
                set_fullscreen(False)
                return
            root.state("normal" if root.state() == "zoomed" else "zoomed")
        except tk.TclError:
            pass

    def hit_area(nm_id, tags):
        """An invisible image over a row name that takes its pointer events (hover swell, balloon, click). The name
        itself can then be swapped for its swell image without the pointer "leaving" it. Images are picked by their
        box, transparent or not."""
        bb = cv.bbox(nm_id)
        if not bb:
            return nm_id
        ph = tk.PhotoImage(master=root, width=max(1, bb[2] - bb[0]), height=max(1, bb[3] - bb[1]))
        st.setdefault("hit_imgs", []).append(ph)
        return cv.create_image(bb[0], bb[1], anchor="nw", image=ph, tags=tags)

    def hover_grow(nm_id, fs, accent, avail, hit=None):
        """Name of a row with a hover balloon (top programs, plain-language help): on hover it grows (~0.2 s, eased in
        and out) and lights up. Never wider than `avail` px (the bar column).
        While it moves the name is an anti-aliased image at a fractional size (textsprite.py): Tk fonts only have whole
        pixel sizes, which gave 2 - 3 different pictures per swell (stutter at any frame rate). At rest the Tk text item
        is shown again. The text item stays under the image (invisible) and keeps the pointer events."""
        size0 = float(-fs)                              # (font sizes are negative = pixels)
        size1 = size0 + max(2.0, size0 * 0.16)
        try:
            w0 = meas_font(F_TXT, fs).measure(cv.itemcget(nm_id, "text"))
        except tk.TclError:
            w0 = 0
        if w0 > 0 and avail > 0:
            size1 = max(size0, min(size1, size0 * avail / w0))
        fill0, fill1 = C["name"], mix(C["fg"], accent, 0.35)
        a = {"e": 0.0, "to": 0.0, "e0": 0.0, "t": 0.0, "size": int(size0), "img": None}
        sprite = textsprite.Sprite.for_family(F_TXT) if GFX else None

        def rest():
            if a["img"] is not None:
                cv.delete(a["img"])
                a["img"] = None
            cv.itemconfig(nm_id, fill=fill0, font=px_font(F_TXT, int(size0)), state="normal")
            a["size"] = int(size0)

        def step(now, dt):
            FX = st["fx"]
            e0, to = a["e0"], a["to"]
            a["t"] += dt
            # from wherever it is now (a reversal never jumps): grows at once (ease-out), settles back softly
            x = min(1.0, a["t"] / (HOVER_S * max(0.35, abs(to - e0)))) if FX["motion"] else 1.0
            k = 1 - (1 - x) ** 3 if to > e0 else x * x * (3 - 2 * x)
            a["e"] = e = e0 + (to - e0) * k
            col = mix(fill0, fill1, e)
            if e <= 0.0:
                rest()
                return x < 1.0
            sz = size0 + (size1 - size0) * e
            ph = None
            if sprite is not None:
                try:
                    xy = cv.coords(nm_id)
                    if xy:
                        ph = sprite.photo(cv.itemcget(nm_id, "text"), sz, col, size1)
                except tk.TclError:
                    ph = None
            if ph is not None:
                img, dx = ph
                if a["img"] is None or not cv.type(a["img"]):
                    # (disabled: never the pointer's item; the hit area above the name keeps the events)
                    a["img"] = cv.create_image(xy[0] - dx, xy[1], anchor="w", image=img, state="disabled")
                    cv.itemconfig(nm_id, state="hidden")
                else:
                    cv.coords(a["img"], xy[0] - dx, xy[1])
                    cv.itemconfig(a["img"], image=img)
            else:                                       # no sprite: whole-pixel Tk font steps
                size = int(round(sz))
                if size != a["size"]:
                    a["size"] = size
                    cv.itemconfig(nm_id, font=px_font(F_TXT, size))
                cv.itemconfig(nm_id, fill=col)
            return x < 1.0

        def go(to):
            if to == a["to"]:
                return
            a["to"], a["e0"], a["t"] = to, a["e"], 0.0
            anim_set(("hover", nm_id), step)
        src = hit if hit is not None else nm_id
        cv.tag_bind(src, "<Enter>", lambda e: go(1.0), add="+")
        cv.tag_bind(src, "<Leave>", lambda e: go(0.0), add="+")
        go.item = nm_id
        go.shown = lambda: a["img"] if a["img"] is not None and cv.type(a["img"]) else nm_id    # what is on screen
        st["hovers"].append(go)
        return go

    def tween_num(it, row, v, fmt, col):
        """Show a new value: the number counts from what is on screen to it (~0.35 s, eased) in the animation loop.
        Set at once: textual / special rows, the first value, motion off, and small steps (a count would show fewer
        than four different numbers: 45 -> 46 °C just changes, 3 -> 48 % counts)."""
        key = ("num", it["now"])
        old = it.get("v_shown")
        FX = st["fx"]
        if (old is None or old == v or not FX["motion"] or not FX["on"] or row.textual or row.special
                or data_snap() or len({fmt(old + (v - old) * k / 3) for k in range(4)}) < 4):
            FX["anims"].pop(key, None)
            it["v_shown"] = v
            set_text(it["now"], fmt(v), col)
            return
        t0, v0 = anim_now(), old

        def step(now, dt):
            x = min(1.0, (now - t0) / NUM_S)
            val = v if x >= 1.0 else v0 + (v - v0) * ease_out(x)
            it["v_shown"] = val
            set_text(it["now"], fmt(val), col)
            return x < 1.0
        anim_set(key, step)

    def data_snap():
        """The pointer is on the panel: new readings take their place at once instead of gliding / counting.
        A glide moves gauges and numbers all over the panel, so every frame repaints nearly the whole window
        (13 - 30 ms with ~50 rows on a tall screen); with the pointer there that time goes to what it is doing
        (the row swell, the balloon), which then stay at 60 fps."""
        return bool(st.get("ptr_in"))

    def hover_moving():
        return any(k[0] == "hover" for k in st["fx"]["anims"] if isinstance(k, tuple))

    def bar_glide(it, frac, col):
        """Plain (non-Aurora) value bar: glides to its new length in the animation loop (motion on)."""
        x0, x1, y0, y1 = it["bargeo"]
        tgt = x0 + (x1 - x0) * min(1.0, max(0.0, frac or 0.0))
        if it.get("bar_tgt") == (tgt, col):
            return
        it["bar_tgt"] = (tgt, col)
        cv.itemconfig(it["bar"], fill=col)
        FX = st["fx"]
        if not FX["motion"] or not FX["on"] or "bar_x" not in it or data_snap():
            it["bar_x"] = tgt
            FX["anims"].pop(("bar", it["bar"]), None)
            cv.coords(it["bar"], x0, y0, int(round(tgt)), y1)
            return

        def step(now, dt):
            cur = it["bar_x"]
            d = tgt - cur
            cur = tgt if abs(d) < 0.6 else cur + d * (1 - math.exp(-dt / 0.14))
            it["bar_x"] = cur
            px = int(round(cur))
            if px != it.get("bar_px"):
                it["bar_px"] = px
                cv.coords(it["bar"], x0, y0, px, y1)
            return cur != tgt
        anim_set(("bar", it["bar"]), step)

    def bench_hover():
        """METRIQ_FXBENCH (tools/bench_fx.py): sweep the hover swell over the rows to measure its frame cost."""
        if st["closing"]:
            return
        hv = st["hovers"]
        if hv:
            n = st.get("bench_i", 0) % len(hv)
            st["bench_i"] = n + 1
            if FXBENCH == "row0":                    # tools/measure_visual_fps.py: always the first row, and say where
                n = 0
                out = os.environ.get("METRIQ_FXBENCH_OUT")
                if out and not st.get("bench_said"):
                    b_ = cv.bbox(hv[0].item)
                    if b_:
                        st["bench_said"] = True
                        rx, ry = cv.winfo_rootx(), cv.winfo_rooty()
                        with open(out, "w", encoding="utf-8") as f_:
                            f_.write("%d %d %d %d" % (rx + b_[0], ry + b_[1], rx + b_[2], ry + b_[3]))
            if FXBENCH == "row0" and os.environ.get("METRIQ_FXBENCH_OUT"):
                with open(os.environ["METRIQ_FXBENCH_OUT"] + ".go", "a", encoding="utf-8") as f_:
                    f_.write("%.6f " % time.perf_counter())          # when the swell was asked for (latency)
            hv[n](1.0)
            hold = 500 if FXBENCH == "row0" else 260       # row0: a whole swell and a whole shrink every second
            root.after(hold, lambda g=hv[n]: g(0.0))
        root.after(1000 if FXBENCH == "row0" else 300, bench_hover)

    def ui_check(out_path):
        """METRIQ_UICHECK=<file> (tools/check_hover_anim.py): drive the real panel once and write what happened as
        JSON: (i) icons left, rows with a hover swell, does a hovered name grow and shrink back, does the entry finish,
        does the mode chip switch the view. Then the program ends."""
        import json
        res = {"mode0": cfg["ui_mode"]}

        def snap(key):
            res[key] = {"rows": len(st["items"]), "uinfo": len(cv.find_withtag("uinfo")),
                        "hovers": len(st["hovers"]), "entry_left": len(st["fx"]["entry"]),
                        "hidden": sum(1 for i in cv.find_all() if cv.itemcget(i, "state") == "hidden"),
                        "anims": len(st["fx"]["anims"]), "names": sum(1 for r in layout.all_rows(view_sections()))}

        def width(i):
            b = cv.bbox(i)
            return (b[2] - b[0]) if b else 0

        def s1():
            snap("start")
            hv = st["hovers"]
            if hv:
                res["w0"] = width(hv[0].shown())
                hv[0](1.0)
            root.after(450, s2)

        def s2():
            hv = st["hovers"]
            if hv:
                res["w1"] = width(hv[0].shown())
                hv[0](0.0)
            root.after(450, s3)

        def s3():
            hv = st["hovers"]
            if hv:
                res["w2"] = width(hv[0].shown())
            toggle_ui_mode()
            root.after(1800, s4)

        def s4():
            res["mode1"] = cfg["ui_mode"]
            snap("after_toggle")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f)
            full_exit()
        root.after(3500, s1)

    # ------------------------------------------------------------ Aurora effects (aurora.py)
    def fx_begin(W, H):
        """Start of a canvas build: the effect palette when the theme is an Aurora one (sky placed first, at the
        bottom of the stacking order), else None."""
        FX = st["fx"]
        if FX.get("job"):
            try:
                root.after_cancel(FX["job"])
            except tk.TclError:
                pass
        pal = aurora.fx() if (GFX and aurora.AVAILABLE) else None
        # the loop serves every theme (hover, numbers, bars, entry); gauges / sky / glass only with Aurora (pal)
        FX.update(on=True, pal=pal, gauges={}, entry=[], keep=[], cards=[], anims={}, job=None, W=W, H=H,
                  ring_row=None, t_bg=0.0, last=aurora.clock(), frame=0)
        FX["motion"] = aurora.motion_on(cfg.get("motion"))
        st["hovers"] = []
        st["hit_imgs"] = []
        st["mode_sliding"] = False
        if not pal:
            return None
        if FX.get("sky") is None or FX["sky"].pal is not pal:
            FX["sky"] = aurora.Sky(pal)
        # the background (sky + every glass card) is baked into this one opaque image at the end of the build
        FX["bg"] = cv.create_image(0, 0, anchor="nw")
        return pal

    def fx_bar_box(bar_x0, bar_x1, my, rh):
        """Whole-pixel geometry of a bar track: (x, y, length, height)."""
        bh = int(round(max(4, rh * 0.22)))
        return int(round(bar_x0)), int(round(my - bh / 2)), int(round(bar_x1 - bar_x0)), bh

    def fx_section(sec, accent, cx0, cx1, yy, ch, hh, rp, R, inset, col_name, isz, hy, bar_x0, bar_x1, with_bar, rh,
                   pal):
        """Glass card + ring gauge of one section. Returns the x where the title starts."""
        FX = st["fx"]
        bx0, by0 = int(round(cx0)), int(round(yy))
        w, h = int(round(cx1)) - bx0, int(round(yy + ch)) - by0
        stripes, tracks = [], []
        ry = yy + hh
        for i, row in enumerate(sec.rows):
            if i % 2:
                stripes.append((ry - by0, ry + rp - by0, inset))
            if row.bar and with_bar:
                x, y, L, bh = fx_bar_box(bar_x0, bar_x1, ry + rp / 2, rh)
                tracks.append((x - bx0, y - by0, x - bx0 + L, y - by0 + bh))
            ry += rp
        d = int(round(isz * 3.4))
        rcx = col_name + isz * 1.7
        img, gpad = aurora.glass_card_pil(w, h, R, pal, accent, (int(rcx - bx0), int(hy - by0), int(d * 1.5)),
                                          stripes, int(round(yy + hh - 1 - by0)), tracks)
        FX["cards"].append((img, bx0 - gpad, by0 - gpad))
        FX["box"] = (bx0 - gpad - 1, by0 - gpad - 1, bx0 + w + gpad + 1, by0 + h + gpad + 1)
        # the ring shows the first row with a scale (load before temperature: rows with a bar first)
        scaled = [r for r in sec.rows if r.limits and not r.textual]
        rrow = next((r for r in scaled if r.bar), scaled[0] if scaled else None)
        key = ("ring", id(sec))
        draw = (lambda q, col, d=d, a=accent, ic=sec.icon: aurora.ring(d, q, col, a, ic, pal))
        photo = draw(0, accent)
        item = cv.create_image(rcx, hy, image=photo)
        FX["gauges"][key] = {"item": item, "cur": 0.0, "tgt": 0.0, "col": accent, "shown": (0, accent),
                             "photo": photo, "q": aurora.ring_q, "draw": draw, "hold": 0.0}
        FX["ring_row"], FX["ring_key"] = rrow, key
        return col_name + isz * 4.1

    def fx_bar(row, bar_x0, bar_x1, my, rh, accent, pal):
        """Glowing value bar (an image over the track baked into the card). Returns (gauge key, item, bargeo)."""
        FX = st["fx"]
        x, y, L, bh = fx_bar_box(bar_x0, bar_x1, my, rh)
        g = aurora.bar_pad(bh)
        key = ("bar", id(row))
        # the strip carries the value colours along its length (the end of the bar shows the value's colour)
        if (is_basic() and row.status != "temp") or not row.limits:
            colors = [accent] * 2
        else:
            green, red = row.limits
            sty = themes.current()
            colors = [sty.color_at(f if red >= green else 1 - f) for f in (i / 63 for i in range(64))]
            colors = [mix(accent, c, 0.35 + 0.65 * i / 63) for i, c in enumerate(colors[:8])] + colors[8:]
        sb = aurora.StripBar(L, bh, colors, pal)
        item = cv.create_image(x - g, y - g, image=sb.dst, anchor="nw")
        FX["gauges"][key] = {"item": item, "cur": 0.0, "tgt": 0.0, "col": accent, "shown": None, "strip": sb,
                             "q": lambda f, L=L: int(round(f * L)), "hold": 0.0}
        return key, item, (x, x + L, y, y + bh)

    def fx_end(boxes, rh):
        """End of a build: cards rise into place one after another (motion on), then the effect loop runs."""
        FX = st["fx"]
        now = anim_now()
        sky = FX["sky"].frame(FX["W"], FX["H"], 40.0)
        if frame.active:                        # the thin native top border in the colour of the sky's top edge
            try:
                px_ = sky.convert("RGB").resize((1, 1), box=(0, 0, FX["W"], 2)).getpixel((0, 0))
                frame.set_colors(themes.current().is_dark(), C["border"], "#%02x%02x%02x" % px_[:3])
            except Exception:
                pass
        FX["photo"] = aurora.bake(sky, FX["cards"])
        FX["cards"] = []
        cv.itemconfig(FX["bg"], image=FX["photo"])
        if FX["motion"]:            # entry: the gauges of each card draw themselves in from zero, card after card
            by_item = {g["item"]: g for g in FX["gauges"].values()}
            for i, box in enumerate(boxes):
                for iid in cv.find_enclosed(*box):
                    g = by_item.get(iid)
                    if g is not None:
                        g["hold"] = now + 0.1 + 0.07 * i
        fx_kick()

    def fx_target(key, frac, col):
        """New value for a ring / bar gauge (0..1); the effect loop glides it there."""
        g = st["fx"]["gauges"].get(key)
        if g is None:
            return
        g["tgt"], g["col"] = min(1.0, max(0.0, frac or 0.0)), col

    def fx_kick(delay=15):
        FX = st["fx"]
        if not FX["on"] or st["closing"]:
            return
        if FX.get("job"):
            try:
                root.after_cancel(FX["job"])
            except tk.TclError:
                pass
        FX["job"] = root.after(delay, fx_step)

    def fx_prof(t0, busy):
        """METRIQ_FXPROF=<file>: per-frame cost of the effect step and of Tk's repaint (diagnostics only)."""
        P = st.setdefault("prof", {"n": 0, "step": 0.0, "paint": 0.0, "gap": 0.0, "last": t0, "t": t0, "max": 0.0})
        t1 = aurora.clock()
        root.update_idletasks()
        t2 = aurora.clock()
        P["n"] += 1
        P["step"] += t1 - t0
        P["paint"] += t2 - t1
        P["gap"] += t0 - P["last"]
        P.setdefault("late", []).append(t0 - st["fx"].get("due", t0))
        P.setdefault("dn", []).append(t0 - P.get("prev_t0", t0))
        P["prev_t0"] = t0
        P["max"] = max(P["max"], t2 - t0)
        P.setdefault("cost", []).append(t2 - t0)
        P["last"] = t2
        if t2 - P["t"] >= 3.0:
            n = P["n"]
            with open(FXPROF, "a", encoding="utf-8") as f:
                f.write("fps %.1f  step %.1f ms  paint %.1f ms  gap %.1f ms  max %.1f ms  busy %s  gauges %d\n" % (
                    n / (t2 - P["t"]), P["step"] / n * 1000, P["paint"] / n * 1000, P["gap"] / n * 1000, P["max"] * 1000,
                    busy, len(st["fx"]["gauges"])))
                dn = sorted(P["dn"])
                f.write("   start-to-start median %.1f ms  p10 %.1f  p90 %.1f  max %.1f  >25ms %d\n" % (
                    dn[len(dn)//2]*1000, dn[len(dn)//10]*1000, dn[int(len(dn)*0.9)]*1000, dn[-1]*1000,
                    sum(1 for x in dn if x > 0.025)))
                f.write("   pacer %s\n" % pacer.info())
                tk_ = st.pop("tick_cost", [])
                if tk_:
                    f.write("   tick n %d  avg %.1f ms  max %.1f ms\n" % (len(tk_), sum(tk_) / len(tk_) * 1000,
                                                                     max(tk_) * 1000))
                P["dn"] = []
                L_ = sorted(P["late"])
                f.write("   late median %.1f ms  p90 %.1f ms\n" % (L_[len(L_) // 2] * 1000, L_[int(len(L_) * 0.9)] * 1000))
                c_ = sorted(P["cost"])
                f.write("   frame cost p50 %.1f ms  p95 %.1f ms\n" % (c_[len(c_) // 2] * 1000,
                                                                   c_[int(len(c_) * 0.95)] * 1000))
            P["late"] = []
            P["cost"] = []
            P.update(n=0, step=0.0, paint=0.0, gap=0.0, t=t2, max=0.0)

    def fx_hires(on):
        """Windows timers tick every 15.6 ms by default, so after(16) waits ~31 ms (30 fps at best): ask for 1 ms
        resolution only while an animation runs, give it back when everything is still."""
        FX = st["fx"]
        if bool(FX.get("hires")) == on or os.name != "nt":
            return
        try:
            import ctypes
            (ctypes.windll.winmm.timeBeginPeriod if on else ctypes.windll.winmm.timeEndPeriod)(1)
            FX["hires"] = on
        except Exception:
            pass

    def fx_step():
        """One frame of the animation loop: row entry (spring), Aurora gauge glides, and every step registered with
        anim_set (hover swell, number counts, plain bars, mode chip). Runs at ~60 fps only while something moves,
        not at all otherwise (still picture, minimised, tray)."""
        FX = st["fx"]
        FX["job"] = None
        if st["closing"] or not FX["on"]:
            fx_hires(False)
            return
        real = aurora.clock()
        # animation time runs on its own clock: a frame never advances it by more than 1/30 s, so a hitch (a
        # rebuild, a slow data read) only pauses the motion instead of making it jump ahead
        dt = min(1 / 30, max(0.0, real - FX["last"]))
        if FX.get("idle", True):
            dt = min(dt, 1 / 60)                         # first frame after a still period
            FX["idle"] = False
            pacer.start()
        FX["last"] = real
        FX["vnow"] = now = FX.get("vnow", 0.0) + dt
        paused = bool(st.get("mini") or st.get("hidden"))
        busy = False
        try:
            for e in list(FX["entry"]):
                x = (now - e["t0"]) / e["dur"]
                if x < 0:
                    busy = True
                    continue
                if e["hidden"]:
                    cv.itemconfig(e["tag"], state="normal")
                    e["hidden"] = False
                off = int(round(e["dy"] * (1 - aurora.spring(x))))
                if off != e["applied"]:
                    cv.move(e["tag"], 0, off - e["applied"])
                    e["applied"] = off
                if x >= 1 and off == 0:
                    FX["entry"].remove(e)
                else:
                    busy = True
            for g in FX["gauges"].values():
                if now < g["hold"]:
                    busy = busy or g["tgt"] != g["cur"]
                    continue
                g["cur"] = aurora.approach(g["cur"], g["tgt"], dt) if FX["motion"] and not data_snap() else g["tgt"]
                if g["cur"] != g["tgt"]:
                    busy = True
                if paused:
                    continue
                if "strip" in g:                         # bar: two Tk copies into its own picture
                    g["strip"].show(g["q"](g["cur"]))
                    continue
                if FX["frame"] % 2 and g["cur"] != g["tgt"]:
                    continue                             # rings (PIL work) glide at half rate
                shown = (g["q"](g["cur"]), g["col"])
                if shown != g["shown"]:
                    g["shown"] = shown
                    g["photo"] = g["draw"](*shown)
                    cv.itemconfig(g["item"], image=g["photo"])
            if not paused:
                anims = FX["anims"]
                for k, fn in list(anims.items()):
                    try:
                        more = fn(now, dt)
                    except (tk.TclError, TypeError, ValueError):
                        more = False                     # its items are gone (canvas rebuilt)
                    if more:
                        busy = True
                    elif anims.get(k) is fn:
                        del anims[k]
            FX["frame"] += 1
        except tk.TclError:
            return
        if FXPROF:
            fx_prof(real, busy)
        fx_hires(busy and not paused)
        if busy and not paused:          # every k-th refresh of the panel's monitor while something moves (vsync.py)
            _d = pacer.delay_ms()
            FX["due"] = aurora.clock() + _d / 1000
            FX["job"] = root.after(_d, fx_step)
        else:                                            # nothing moves: no frames at all
            FX["idle"] = True
            pacer.stop()

    def build(W, H):
        cv.delete("all")
        if GFX:
            gfx.begin()
        st["items"].clear()
        st["last"].clear()
        st["need_draw"] = True
        st["empty"] = None
        basic = is_basic()
        sections = view_sections()
        HEAD, FOOT, STATUS = layout.HEAD, layout.FOOT, layout.STATUS    # in row heights
        sty = themes.current()
        R, sp = sty.radius, sty.spacing                  # card corner radius, row pitch factor
        pal = fx_begin(W, H)                              # Aurora: sky + glass + glowing gauges (None otherwise)
        FX = st["fx"]
        sec_boxes = []
        # entry (start, view / theme / language change; not a resize): titles and rows come in one after another
        entry = bool(sections) and FX["motion"] and st["entry_next"]
        if sections:
            st["entry_next"] = False
        entry_tags, ent_k, ent_n = [], 0, 0             # (tag, column, order in its column)
        p = layout.plan(sections, W, H, cfg["columns"], 100, sp)
        rh, m, g = p.rh, p.m, p.g
        rp = rh * sp                                      # row pitch (rows keep the font size of rh)
        inset = max(6.0, R * 0.4)                         # zebra stripes stay inside rounded corners
        fs_name0, fs_num = -max(10, int(rh * 0.50)), -max(10, int(rh * 0.52))
        fs_small, fs_head = -max(9, int(rh * 0.38)), -max(11, int(rh * 0.62))
        # ----- column geometry (fractions of the column width); no bar column in narrow columns
        cw = p.cwc
        pad = cw * 0.035
        if basic:       # no MIN / MAX / AVG: a status word takes their place (right-aligned at the card edge)
            fr = dict(bar0=0.40, bar1=0.54, now=0.70) if p.with_bar else dict(now=0.56)
        elif p.with_bar:
            fr = dict(bar0=0.40, bar1=0.53, now=0.69, mn=0.795, mx=0.885)
        else:
            fr = dict(now=0.54, mn=0.725, mx=0.85)
        # ----- fonts shrink (numbers first) when a column is too narrow for them
        sample = "8888" if p.with_bar else "88888"        # narrow columns must also fit 5 digits
        nw0 = meas_font(F_NUM, fs_num).measure(sample)
        uw0 = meas_font(F_TXT, fs_small).measure("KB/s")
        if basic:
            fm = 1.0
        else:
            fm = min(1.0, (min(fr["mx"] - fr["mn"], 1 - pad / cw - fr["mx"]) * cw - 8) / nw0,
                     ((fr["mn"] - fr["now"]) * cw - 14) / (nw0 + uw0))
            fm = max(0.55, fm)
        fs_num_e, fs_small_e = -max(8, int(-fs_num * fm)), -max(8, int(-fs_small * fm))
        f_num, f_unit = (F_NUM, fs_num_e), (F_TXT, fs_small_e)
        f_col = (F_TXT, fs_small_e, "bold")
        unit_w = meas_font(F_TXT, fs_small_e).measure("KB/s")
        fs_stat = -max(9, int(-fs_small_e * 1.15))
        f_stat, fnt_stat = (F_TXT, fs_stat, "bold"), meas_font(F_TXT, fs_stat, "bold")
        st["fnt_stat"] = fnt_stat
        nw = meas_font(F_NUM, fs_num_e).measure(sample)
        name_right = (fr["bar0"] * cw - 8) if p.with_bar else (fr["now"] * cw - nw - 10)
        name_avail = name_right - pad
        fs_name = fs_name0
        names = [r.name for s in sections for r in s.rows]
        longest = max((meas_font(F_TXT, fs_name).measure(n) for n in names), default=0)
        if longest > name_avail > 0:
            fs_name = -max(9, int(-fs_name * max(0.75, name_avail / longest)))
        f_name = (F_TXT, fs_name)
        fnt_name = meas_font(F_TXT, fs_name)
        isz = rh * 0.40
        lbl_now_w = meas_font(F_TXT, fs_small_e, "bold").measure(t("col_now"))
        # ----- sections, column by column
        if not sections:
            st["empty"] = cv.create_text(W / 2, H * 0.4, text="", justify="center", width=W * 0.8,
                                         font=(F_HEAD, fs_head, "bold"), fill=C["dim"])
        for ci, col_secs in enumerate(p.cols):
            cx0 = m + ci * (cw + p.gut)
            cx1 = cx0 + cw
            col_name = cx0 + pad
            bar_x0, bar_x1 = cx0 + cw * fr.get("bar0", 0), cx0 + cw * fr.get("bar1", 0)
            col_now = cx0 + cw * fr["now"]                  # right edge of the number, unit follows
            col_avg = cx1 - pad
            col_min, col_max = (cx0 + cw * fr["mn"], cx0 + cw * fr["mx"]) if not basic else (0, 0)
            stat_w = col_avg - (col_now + 6 + unit_w) - 8   # room for the status word (Basic)
            yy = m + STATUS * rh                          # the status row is at the top (it is the title bar)
            ent_n = 0
            rp, gc = p.pitch[ci], p.gaps[ci]
            for sec in col_secs:
                accent = themes.accent_for(sec.icon, sec.color)
                hh, pb = rh * HEAD, rh * FOOT
                ch = hh + len(sec.rows) * rp + pb
                hy = yy + hh * 0.5
                bubble = mix(C["card"], accent, 0.16)
                if pal:
                    title_x = fx_section(sec, accent, cx0, cx1, yy, ch, hh, rp, R, inset, col_name, isz, hy,
                                         bar_x0, bar_x1, p.with_bar, rh, pal)
                    sec_boxes.append(FX["box"])
                elif GFX:
                    bx0, by0 = int(round(cx0)), int(round(yy))
                    cv.create_image(bx0, by0, image=gfx.card(int(round(cx1)) - bx0, int(round(yy + ch)) - by0, R,
                                                             C["card"], C["border"], accent, under=C["bg"]),
                                    anchor="nw")
                    cv.create_image(col_name + isz * 1.6, hy, image=gfx.icon(sec.icon, isz, accent, bubble, bubble))
                else:
                    round_rect(cv, cx0, yy, cx1, yy + ch, R, fill=C["card"], outline=C["border"])
                    cv.create_line(cx0 + 1.5, yy + R, cx0 + 1.5, yy + ch - R, fill=accent, width=3)
                    cv.create_oval(col_name, hy - isz * 1.6, col_name + isz * 3.2, hy + isz * 1.6, fill=bubble,
                                   outline="")
                    draw_icon(cv, sec.icon, col_name + isz * 1.6, hy, isz, accent, bubble)
                if not pal:
                    title_x = col_name + isz * 3.9
                title_avail = (col_now + 6 + unit_w - lbl_now_w) - title_x - 10
                hs = -fs_head
                f_title = meas_font(F_HEAD, -hs, "bold")
                if f_title.measure(sec.title) > title_avail > 0:
                    hs = max(10, int(hs * max(0.7, title_avail / f_title.measure(sec.title))))
                    f_title = meas_font(F_HEAD, -hs, "bold")
                ent_k += 1
                ent_tag = "ent%d" % ent_k
                entry_tags.append((ent_tag, ci, ent_n))
                ent_n += 1
                cv.create_text(title_x, hy, text=fit_text(f_title, sec.title, title_avail), anchor="w",
                               font=(F_HEAD, -hs, "bold"), fill=C["fg"], tags=(ent_tag,))
                heads = ((("now", t("col_now"), col_now + 6 + unit_w), ("status", t("col_status"), col_avg)) if basic else
                         (("now", t("col_now"), col_now + 6 + unit_w), ("min", t("col_min"), col_min),
                          ("max", t("col_max"), col_max), ("avg", t("col_avg"), col_avg)))
                for hk, lbl, xx in heads:
                    cv.create_text(xx, hy, text=lbl, anchor="e", font=f_col, fill=mix(C["dim"], accent, 0.45),
                                   tags=("hdr_" + hk, ent_tag))
                if not pal:                                  # (Aurora: header line, stripes and tracks are in the glass)
                    cv.create_line(cx0 + pad * 0.6, yy + hh - 1, cx1 - pad * 0.6, yy + hh - 1, fill=C["border"])
                ry = yy + hh
                for i, row in enumerate(sec.rows):
                    if i % 2 and not pal:
                        cv.create_rectangle(cx0 + inset, ry, cx1 - inset, ry + rp, fill=C["stripe"], outline="")
                    my = ry + rp / 2
                    ent_k += 1
                    ent_tag = "ent%d" % ent_k
                    entry_tags.append((ent_tag, ci, ent_n))
                    ent_n += 1
                    hint_row = basic and row.rid == "hint_cpu_temp"      # "CPU temperature needs HWiNFO": no value, so the name may use the whole row
                    nm_right = (fr["now"] * cw - nw - 10 if not p.with_bar else fr["now"] * cw - 10) if hint_row else name_right
                    nm_id = cv.create_text(col_name, my, text=fit_text(fnt_name, row.name, name_avail + nm_right - name_right),
                                           anchor="w", font=f_name, fill=C["name"], tags=(ent_tag,))
                    hit = hit_area(nm_id, ent_tag)               # pointer target of the name (hover, balloon)
                    # one balloon per row name, no (i) icons: CPU / RAM / GPU / VRAM / disk / network rows list their
                    # top programs (rows are not clickable: the process window has its own button); a row with an explanation (every Basic
                    # row, e.g. throttling in Advanced) shows it, above the program list when it has one
                    note = (lambda k=row.help: t("hp_" + k)) if row.help else None
                    tagged = procs.tag_row(cv, hit, row, note)
                    if note and not tagged:
                        Tooltip.for_canvas(cv, hit, note, delay_ms=50)
                    if hint_row:                                    # the hint row leads to the HWiNFO setting
                        cv.tag_bind(hit, "<Button-1>", lambda e: show_hw_setting(), add="+")
                        cv.tag_bind(hit, "<Enter>", lambda e: cv.config(cursor="hand2"), add="+")
                        cv.tag_bind(hit, "<Leave>", lambda e: cv.config(cursor=""), add="+")
                    it = {"accent": accent}
                    if row is FX.get("ring_row"):
                        it["ring"] = FX["ring_key"]
                    if row.bar and p.with_bar and pal:
                        it["gauge"], it["bar"], it["bargeo"] = fx_bar(row, bar_x0, bar_x1, my, rh, accent, pal)
                    elif row.bar and p.with_bar:
                        bh = max(4, rh * 0.22)
                        round_rect(cv, bar_x0, my - bh / 2, bar_x1, my + bh / 2, bh / 2, fill=C["track"], outline="")
                        it["bar"] = cv.create_rectangle(bar_x0, my - bh / 2, bar_x0, my + bh / 2, fill=accent,
                                                        outline="")
                        it["bargeo"] = (bar_x0, bar_x1, my - bh / 2, my + bh / 2)
                        if entry:
                            it["bar_x"] = bar_x0                     # entry: the bar draws itself in from zero
                    it["now"] = cv.create_text(col_now, my, text="" if hint_row else "–", anchor="e", font=f_num,
                                               fill=C["fg"], tags=(ent_tag,))
                    if entry and not hint_row and not row.textual and not row.special:
                        it["v_shown"] = 0.0                         # entry: the number counts up from zero
                    it["unit"] = cv.create_text(col_now + 6, my, text=row.unit_eff, anchor="w", font=f_unit,
                                                fill=C["faint"], tags=(ent_tag,))
                    if basic:
                        if row.status:
                            it["status"] = cv.create_text(col_avg, my, text="", anchor="e", font=f_stat, fill=C["dim"],
                                                          tags=(ent_tag,))
                            it["stat_w"] = stat_w
                    else:
                        for k, xx in (("min", col_min), ("max", col_max), ("avg", col_avg)):
                            it[k] = cv.create_text(xx, my, text="–", anchor="e", font=f_num, fill=C["dim"],
                                                   tags=(ent_tag,))
                    # rows with a hover balloon swell; a state row (throttling: yes / no) only explains itself
                    if (tagged or note) and not row.textual:
                        hover_grow(nm_id, fs_name, accent, nm_right - pad, hit)
                    st["items"][id(row)] = it
                    ry += rp
                yy += ch + gc
        # ----- status bar (full width, a single row: record pill, mode switch, interval, reset, gear);
        # it is at the top and is the window's title bar; messages are small toasts just below it (draw_toasts)
        sx0, sx1 = m, W - m
        sw_span = sx1 - sx0
        rs = min(rh, max(layout.MIN_RH, sw_span * 0.042))      # its text must not grow with a huge row height
        spad = min(sw_span * 0.035, rs * 1.0)
        col_name = sx0 + spad
        sb_h = rs * (STATUS - 0.2)
        sy = m
        wb_w = max(32.0, sb_h * 0.82) if frame.active else 0.0     # one window button (minimise / maximise / close)
        wb_x0 = sx1 - 3 * wb_w                                       # where the window buttons start
        s = st["status"]
        s.clear()
        s["gfx"] = GFX
        hx = aurora.hexa
        if pal:
            bx0, by0 = int(round(sx0)), int(round(sy))
            img, gpad = aurora.glass_card_pil(int(round(sx1)) - bx0, int(round(sy + sb_h)) - by0, R, pal)
            FX["cards"].append((img, bx0 - gpad, by0 - gpad))
        elif GFX:
            bx0, by0 = int(round(sx0)), int(round(sy))
            cv.create_image(bx0, by0, image=gfx.card(int(round(sx1)) - bx0, int(round(sy + sb_h)) - by0, R,
                                                     C["card"], C["border"], under=C["bg"]), anchor="nw")
        else:
            round_rect(cv, sx0, sy, sx1, sy + sb_h, R, fill=C["card"], outline=C["border"])
        my = sy + sb_h / 2
        if frame.active:
            title_row(W, sy, sb_h, wb_x0, wb_w, R, pal)
        px0 = col_name - 6
        f_pill = (F_TXT, -max(10, int(rs * 0.50)), "bold")
        # Basic has no recording: no record pill (and no log viewer button, see below)
        fp_ = tkfont.Font(family=F_TXT, size=f_pill[1], weight="bold")
        pill_fit = 6 + rs * 0.8 + max(fp_.measure(t("log_on")), fp_.measure(t("log_off"))) + rs * 0.7  # dot + text + pad
        pw, ph = min(sw_span * 0.25, max(rs * 6.8, 130), max(pill_fit, 130)), rs * 1.1
        if basic:
            pw = -6.0                                # nothing drawn; the mode chip may start at the bar's left edge
        elif GFX:
            s["pill_fn"] = lambda on: gfx.pill(pw, ph, hx(C["green"] if on else C["red"], 0x2c) if pal else
                                               mix(C["card"], C["green"] if on else C["red"], 0.18),
                                               C["green"] if on else C["red"], 6 + rs * 0.38, rs * 0.18)
            s["pill"] = cv.create_image(int(round(px0)), int(round(my - ph / 2)), image=s["pill_fn"](cfg["logging"]),
                                        anchor="nw", tags="logbtn")
        else:
            s["pill"] = round_rect(cv, px0, my - ph / 2, px0 + pw, my + ph / 2, rs * 0.52, fill=C["track"], outline="",
                                   tags="logbtn")
            s["dot"] = cv.create_oval(col_name + rs * 0.2, my - rs * 0.18, col_name + rs * 0.56, my + rs * 0.18,
                                      fill=C["red"], outline="", tags="logbtn")
        if not basic:
            s["txt"] = cv.create_text(col_name + rs * 0.8, my, text="", anchor="w",
                                      font=f_pill, fill=C["fg"], tags="logbtn")
        # right end geometry first: [interval label] [spinbox] [chart] [reset] [gear]; the switch must fit before it.
        # Basic: [gear] only (no recording, so no log viewer; no MIN / MAX / AVG to reset, no interval)
        gs = max(rs * 0.42, 8.0)
        hit = gs * 1.45
        gx = (wb_x0 - hit - 8) if frame.active else (sx1 - spad - hit)
        rx = gx - hit * 2 - 4
        vx = rx - hit * 2 - 4
        if basic:
            vx = gx                                  # no viewer button: the chip ends just before the gear
        qx = vx - hit * 2 - 4                        # programs window button
        spin_w = max(44.0, min(sw_span * 0.1, rs * 3.2))
        spin_r = qx - hit - 6
        lbl_r = spin_r - spin_w - 8                                  # right edge of the interval label
        mode_labels = (t("ui_basic"), t("ui_advanced"))
        mode_cur = 0 if basic else 1
        fs_sw0 = -max(9, int(rs * 0.38))
        show_right, chip_compact = True, False
        # recording mode switch: required rows <-> all sensors (Advanced)
        sw_w, sw_h = max(rs * 1.7, 22.0), max(rs * 0.8, 10.0)      # never smaller than a track that still holds a knob
        lx = px0 + pw + min(sw_span * 0.05, rs * 1.2)
        if basic:
            fs_sw, show_lbl, show_left = fs_sw0, False, False
            chip_r = qx - hit - 10
            fb_ = tkfont.Font(family=F_TXT, size=fs_sw, weight="bold")
            cpad_ = max(6.0, rs * 0.45)
            chip_compact = chip_r - sum(fb_.measure(x) + 2 * cpad_ for x in mode_labels) < px0 + pw + 8
        else:
            fs_sw, show_lbl, show_left, show_right, chip_compact = _fit_status_bar(F_TXT, lx, lbl_r, sw_w, rs, fs_sw0,
                                                                                  mode_labels)
            chip_r = lbl_r - (tkfont.Font(family=F_TXT, size=fs_sw).measure(t("interval_lbl")) + max(rs * 0.6, 8.0)
                              if show_lbl else 0)
        f_sw = (F_TXT, fs_sw, "bold")
        if not basic:
            s["sw_l"] = cv.create_text(lx, my, text=t("sw_required") if show_left else "", anchor="w", font=f_sw,
                                       fill=C["fg"], tags="modebtn")
            tx0 = lx + (tkfont.Font(family=F_TXT, size=fs_sw, weight="bold").measure(t("sw_required")) + rs * 0.35
                        if show_left else 0)
            kmy = my
            if GFX:                                  # whole pixels: the track image and the knob stay aligned
                tx0, sw_w, sw_h = float(round(tx0)), float(round(sw_w)), float(2 * round(sw_h / 2))
                kmy = round(my - sw_h / 2) + sw_h / 2
                s["track_fn"] = (lambda all_: gfx.track(sw_w, sw_h, hx(C["ui"], 0x80) if all_ else hx(C["fg"], 0x16),
                                                        hx(C["fg"], 0x34))) if pal else                     (lambda all_: gfx.track(sw_w, sw_h, mix(C["track"], C["ui"], 0.55) if all_ else C["track"], C["border"]))
                s["sw_track"] = cv.create_image(tx0, round(my - sw_h / 2), image=s["track_fn"](cfg.get("log_all", False)),
                                                anchor="nw", tags="modebtn")
            else:
                s["sw_track"] = round_rect(cv, tx0, my - sw_h / 2, tx0 + sw_w, my + sw_h / 2, sw_h / 2,
                                           fill=C["track"], outline=C["border"], tags="modebtn")
            kr = max(2.0, sw_h / 2 - 3)
            s["sw_geo"] = (tx0 + 3 + kr, tx0 + sw_w - 3 - kr, kmy, kr)
            kx = s["sw_geo"][1] if cfg.get("log_all") else s["sw_geo"][0]
            if GFX:
                s["sw_knob"] = cv.create_image(kx, kmy, image=gfx.knob(kr, C["fg"]), tags="modebtn")
            else:
                s["sw_knob"] = cv.create_oval(kx - kr, my - kr, kx + kr, my + kr, fill=C["fg"], outline="",
                                              tags="modebtn")
            s["sw_r"] = cv.create_text(tx0 + sw_w + rs * 0.35, my, text=t("sw_all") if show_right else "", anchor="w",
                                       font=f_sw, fill=C["faint"], tags="modebtn")
        # Basic | Advanced toggle: both words with the current one lit; only the current word when space is short
        fb_chip = tkfont.Font(family=F_TXT, size=fs_sw, weight="bold")
        cpad = max(6.0, rs * 0.45)
        seg = [mode_cur] if chip_compact else [0, 1]
        seg_w = [fb_chip.measure(mode_labels[i]) + 2 * cpad for i in seg]
        ch_h = ph * 0.92
        chx0, chy0 = chip_r - sum(seg_w), my - ch_h / 2
        acc_chip = themes.accent("gauge")
        if GFX:
            cv.create_image(int(round(chx0)), int(round(chy0)),
                            image=gfx.card(int(round(sum(seg_w))), int(round(ch_h)), ch_h / 2,
                                           *((hx(C["fg"], 0x10), hx(C["fg"], 0x2a)) if pal else (C["track"], C["border"]))),
                            anchor="nw", tags="uimodebtn")
        else:
            round_rect(cv, chx0, chy0, chx0 + sum(seg_w), chy0 + ch_h, ch_h / 2, fill=C["track"], outline=C["border"],
                       tags="uimodebtn")
        xx = chx0
        if len(seg) == 2:                            # the lit half slides to the other word on a click (toggle_ui_mode)
            s["chip_slide"] = seg_w[0] if mode_cur == 0 else -seg_w[0]
        for i, w_ in zip(seg, seg_w):
            on = i == mode_cur
            if on:
                ix0, iy0, iw, ih = int(round(xx + 2)), int(round(chy0 + 2)), int(round(w_ - 4)), int(round(ch_h - 4))
                fill_, line_ = ((hx(acc_chip, 0x4c), hx(acc_chip, 0xb0)) if pal else
                                (mix(C["card"], acc_chip, 0.30), mix(C["card"], acc_chip, 0.75)))
                if GFX:
                    cv.create_image(ix0, iy0, image=gfx.card(iw, ih, ih / 2, fill_, line_), anchor="nw",
                                    tags=("uimodebtn", "uimode_hl"))
                else:
                    round_rect(cv, ix0, iy0, ix0 + iw, iy0 + ih, ih / 2, fill=fill_, outline=line_,
                               tags=("uimodebtn", "uimode_hl"))
            cv.create_text(xx + w_ / 2, my, text=mode_labels[i], anchor="center", font=f_sw,
                           fill=C["fg"] if on else C["faint"], tags=("uimodebtn", "uimode_txt"))
            xx += w_
        cv.tag_raise("uimode_txt")                   # both words stay above the sliding lit half
        # right end: [interval label] [spinbox] [chart] [reset button] [gear button]
        btn_rest = "#00000000" if pal else C["card"]           # Aurora: the button disc is clear glass until hovered
        if GFX:
            s["gear_bg"] = cv.create_image(gx, my, image=gfx.button("gear", hit, gs, C["dim"], btn_rest),
                                           tags="gearbtn")
            if not basic:
                s["reset_bg"] = cv.create_image(rx, my, image=gfx.button("reset", hit, gs, C["dim"], btn_rest),
                                                tags="resetbtn")
            if not basic:
                s["view_bg"] = cv.create_image(vx, my, image=gfx.button("chart", hit, gs, C["dim"], btn_rest),
                                               tags="viewbtn")
            s["proc_bg"] = cv.create_image(qx, my, image=gfx.button("procs", hit, gs, C["dim"], btn_rest),
                                           tags="procbtn")
        else:
            s["gear_bg"] = cv.create_oval(gx - hit, my - hit, gx + hit, my + hit, fill=C["card"], outline="",
                                          tags="gearbtn")
            draw_icon(cv, "gear", gx, my, gs, C["dim"], C["card"], tags=("gearbtn",))
            if not basic:
                s["reset_bg"] = cv.create_oval(rx - hit, my - hit, rx + hit, my + hit, fill=C["card"], outline="",
                                               tags="resetbtn")
                draw_icon(cv, "reset", rx, my, gs, C["dim"], C["card"], tags=("resetbtn",))
            if not basic:
                s["view_bg"] = cv.create_oval(vx - hit, my - hit, vx + hit, my + hit, fill=C["card"], outline="",
                                              tags="viewbtn")
                draw_icon(cv, "chart", vx, my, gs, C["dim"], C["card"], tags=("viewbtn",))
            s["proc_bg"] = cv.create_oval(qx - hit, my - hit, qx + hit, my + hit, fill=C["card"], outline="",
                                          tags="procbtn")
            draw_icon(cv, "procs", qx, my, gs, C["dim"], C["card"], tags=("procbtn",))
        if show_lbl:                             # hidden when it would overlap the switch; the spinbox keeps its tooltip
            cv.create_text(lbl_r, my, text=t("interval_lbl"), anchor="e", font=(F_TXT, fs_sw),
                           fill=C["dim"], tags="intlbl")
        if not basic:                            # Basic: no interval box (the recording keeps its interval)
            spin.config(font=(F_NUM, -max(10, int(rs * 0.52))))
            cv.create_window(spin_r, my, window=spin, anchor="e", width=spin_w, height=rs * 1.0)
        # toasts sit centred just below the bar
        ft = -max(10, int(rs * 0.42))
        s["toast_geo"] = ((sx0 + sx1) / 2, sy + sb_h + max(4.0, rs * 0.25), sw_span - 2 * spad)
        s["f_toast"], s["f_toast_t"] = meas_font(F_TXT, ft, "bold"), (F_TXT, ft, "bold")
        for tag, fn in (("logbtn", toggle), ("modebtn", toggle_mode), ("gearbtn", open_settings),
                        ("uimodebtn", toggle_ui_mode), ("resetbtn", reset_stats), ("viewbtn", lambda e=None: logview.show(api)), ("procbtn", lambda e=None: procs.open()), ("updbtn", upd_click), ("updx", upd_dismiss), ("hwrestart", hw_restart_click)):
            cv.tag_bind(tag, "<Button-1>", fn)
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor=""))

        acc = themes.accent("gauge")

        def gear_hover(on):
            bgc = (hx(acc, 0x30) if on else btn_rest) if pal else (mix(C["card"], acc, 0.16) if on else C["card"])
            if GFX:
                cv.itemconfig(s["gear_bg"], image=gfx.button("gear", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("gear_fill", fill=acc if on else C["dim"])
            cv.itemconfig("gear_hole", fill=bgc)
            cv.itemconfig(s["gear_bg"], fill=bgc)

        def reset_hover(on):
            bgc = (hx(acc, 0x30) if on else btn_rest) if pal else (mix(C["card"], acc, 0.16) if on else C["card"])
            if GFX:
                cv.itemconfig(s["reset_bg"], image=gfx.button("reset", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("reset_fill", fill=acc if on else C["dim"], outline=acc if on else C["dim"])
            cv.itemconfig(s["reset_bg"], fill=bgc)
        def view_hover(on):
            bgc = (hx(acc, 0x30) if on else btn_rest) if pal else (mix(C["card"], acc, 0.16) if on else C["card"])
            if GFX:
                cv.itemconfig(s["view_bg"], image=gfx.button("chart", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("chart_fill", fill=acc if on else C["dim"])
            cv.itemconfig(s["view_bg"], fill=bgc)
        def proc_hover(on):
            bgc = (hx(acc, 0x30) if on else btn_rest) if pal else (mix(C["card"], acc, 0.16) if on else C["card"])
            if GFX:
                cv.itemconfig(s["proc_bg"], image=gfx.button("procs", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("procs_fill", fill=acc if on else C["dim"])
            cv.itemconfig(s["proc_bg"], fill=bgc)
        cv.tag_bind("procbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), proc_hover(True)))
        cv.tag_bind("procbtn", "<Leave>", lambda e: (cv.config(cursor=""), proc_hover(False)))
        cv.tag_bind("viewbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), view_hover(True)))
        cv.tag_bind("viewbtn", "<Leave>", lambda e: (cv.config(cursor=""), view_hover(False)))
        cv.tag_bind("resetbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), reset_hover(True)))
        cv.tag_bind("resetbtn", "<Leave>", lambda e: (cv.config(cursor=""), reset_hover(False)))
        cv.tag_bind("gearbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), gear_hover(True)))
        cv.tag_bind("gearbtn", "<Leave>", lambda e: (cv.config(cursor=""), gear_hover(False)))
        s["hover"] = (gear_hover, reset_hover, view_hover)
        # hover help (after the binds above, which replace earlier <Enter> / <Leave> handlers of the same tags)
        for tag, key in (("hdr_now", "hdr_now"), ("hdr_min", "hdr_min"), ("hdr_max", "hdr_max"),
                         ("hdr_avg", "hdr_avg"), ("logbtn", "logbtn"), ("modebtn", "modebtn"),
                         ("resetbtn", "resetbtn"), ("viewbtn", "viewbtn"), ("procbtn", "procbtn"), ("gearbtn", "gearbtn"), ("intlbl", "interval_lbl"),
                         ("uimodebtn", "uimodebtn")):
            help_tip(tag, key)
        if pal:
            fx_end(sec_boxes, rh)
        if entry and entry_tags:
            t0 = anim_now()
            dy = max(6, int(round(rh * 0.45)))
            gap = min(0.035, 0.6 / max(1, max(n for _, _, n in entry_tags)))     # whole entry stays under ~1 s
            for tag, ci, n in entry_tags:
                cv.itemconfig(tag, state="hidden")
                FX["entry"].append({"tag": tag, "t0": t0 + 0.06 + gap * n + 0.05 * ci, "dur": 0.5, "dy": dy,
                                    "applied": 0, "hidden": True})
            anim_wake()
        refresh_status()

    def help_tip(tag, key):
        """Hover balloon on every canvas item tagged `tag`; safe to call again after each rebuild."""
        old = st["tips"].pop(tag, None)
        if old:
            old.hide()
        cv.tag_unbind(tag, "<Motion>")
        if tag.startswith("hdr_") or tag == "intlbl":          # no other handlers on these tags
            cv.tag_unbind(tag, "<Enter>")
            cv.tag_unbind(tag, "<Leave>")
        st["tips"][tag] = Tooltip.for_canvas(cv, tag, lambda: helptexts.text(key))

    def set_text(iid, txt, color=None):
        key = (txt, color)
        if st["last"].get(iid) == key:
            return
        st["last"][iid] = key
        if color:
            cv.itemconfig(iid, text=txt, fill=color)
        else:
            cv.itemconfig(iid, text=txt)

    def status_msgs():
        """Messages for the toast stack above the status bar: [(text, colour, kind)], kind "upd" = update notice."""
        now = time.time()
        msgs = []
        soon = st["soon"] = []          # seconds until the next change (fade step / expiry): refresh_status re-arms

        def timed(text, col, t0, dur):
            """A notice shown for dur seconds from t0; its last FADE_S seconds fade towards the card colour."""
            left = dur - (now - t0)
            if left <= 0 or not text:
                return
            if left >= FADE_S:
                soon.append(left - FADE_S + 0.01)
            else:
                soon.append(0.05)
                col = mix(C["card"], col, max(0.0, left / FADE_S))
            msgs.append((text, col, None))
        timed("✓ " + t("reset_done"), C["green"], st.get("reset_note", 0), TOAST_RESET_S)
        timed(t("st_write_err"), C["red"], st.get("err_note", 0), TOAST_ERR_S)
        timed(t("log_err"), C["red"], st.get("log_err", 0), TOAST_ERR_S)
        timed(st.get("hk_msg", ""), C["yellow"], st.get("hk_note", 0), TOAST_ERR_S)
        timed(st.get("thr_msg", ""), C["yellow"], st.get("thr_note", 0), TOAST_ERR_S)
        timed(st.get("mode_msg", ""), themes.accent("gauge"), st.get("mode_note", 0), TOAST_HINT_S)
        if cfg["alerts"]["enabled"]:                 # persistent while the value is still past the limit
            for rule, v in alert_eng.active():
                msgs.append(("⚠ " + alert_text(rule, v), C["red"], None))
        ap = st.get("applying")
        hint = current_hint()
        if ap and now - ap[0] < 12:
            msgs.append((t("applying", ms=ap[1]), C["dim"], None))
        elif st.get("frozen") and st["items"]:
            msgs.append((t("frozen", src=backend.display_name(src.active)) + (" · " + hint if hint else ""),
                         C["red"] if not st["ok"] else C["yellow"], None))
        elif not st["ok"] and not st["empty"]:          # (with no rows at all the hint is shown mid-window)
            msgs.append((hint or wait_text(), C["red"], None))
        elif src.why and src.active == "builtin" and src.setting() == "auto":
            # fell back to the built-in source: say why there are no temperatures / power (gone once HWiNFO delivers)
            # (the first sentence; what to do is in Settings > Data source)
            msgs.append((t(src.why).split(". ")[0], C["yellow"], None))
        if st.get("stale"):                              # persistent: stays until HWiNFO delivers again
            if now - (st.get("hw_rst") or 0) < 40:
                msgs.append((t("hw_restarting"), C["yellow"], None))
                soon.append(1.0)
            else:
                msgs.append((t("hw_restart_btn"), C["yellow"], "hwbtn"))
                msgs.append((t("hw_stale"), C["yellow"], "hwstale"))
        note = st.get("note")
        if note and note != "created":
            timed(note, C["yellow"], st["t0"], TOAST_ERR_S)
        utxt, ucol, uclick = upd_state()
        if utxt:
            msgs.append((utxt, ucol, "upd" if uclick else "updbusy"))
        ext = logger.ext.toasts(C)
        if ext:
            soon.append(0.1)
        msgs.extend(ext)
        return msgs

    def wrap_text(f, text, width):
        """Greedy word wrap of text to `width` px with font f."""
        lines, cur = [], ""
        for wd in text.split(" "):
            trial = (cur + " " + wd).strip()
            if cur and f.measure(trial) > width:
                lines.append(cur)
                cur = wd
            else:
                cur = trial
        return lines + [cur]

    def hw_restart_click(e=None):
        """The button of the "HWiNFO stopped sharing" notice: restart HWiNFO now. Only ever from a click."""
        now = time.time()
        if now - (st.get("hw_rst") or 0) < 40:
            return
        st["hw_rst"] = st["last_restart"] = now         # (also keeps the automatic repair of a vanished HWiNFO quiet)
        src.hold_until = now + 90                       # statistics and the recording stay on HWiNFO through the gap
        refresh_status()

        def work():
            st["hw_rst_res"] = hwctl.restart_hwinfo(cfg["interval"], st["hw_exe"])
        threading.Thread(target=work, daemon=True).start()

    def draw_toasts(msgs):
        """Small rounded pills stacked upwards from just above the status bar; rebuilt only when they change."""
        s = st["status"]
        key = tuple(msgs)
        if s.get("toast_key") == key:
            return
        s["toast_key"] = key
        cv.delete("toast")
        cx, y, maxw = s["toast_geo"]
        f = s["f_toast"]
        th = f.metrics("linespace") + 10
        for text, col, kind in msgs:
            xw = f.measure("  ✕") if kind == "upd" else 0
            ph, lines = th, None
            if kind == "hwstale":           # the long notice wraps instead of being cut off
                lines = wrap_text(f, text, maxw - 2 * th)
                text = "\n".join(lines)
                ph = th + (len(lines) - 1) * f.metrics("linespace")
                tw = max(f.measure(x) for x in lines)
            else:
                tw = min(maxw - 2 * th, f.measure(text) + xw)
                text = fit_text(f, text, tw - xw)
            w = int(tw + th)
            x0, y0 = int(round(cx - w / 2)), int(round(y))           # below the status row, stacking down
            fill, line = mix(C["card"], col, 0.22), mix(C["card"], col, 0.6)
            if st["fx"]["pal"]:                 # Aurora: frosted toast (nearly opaque: it must stay readable)
                fill, line = aurora.hexa(fill, 0xe8), aurora.hexa(line, 0xc0)
            tags = ("toast", "updbtn") if kind == "upd" else ("toast", "hwrestart") if kind == "hwbtn" else ("toast",)
            if GFX:
                cv.create_image(x0, y0, image=gfx.card(w, int(ph), th / 2, fill, line), anchor="nw", tags=tags)
            else:
                round_rect(cv, x0, y0, x0 + w, y0 + ph, th / 2, fill=fill, outline=line, tags=tags)
            cv.create_text(x0 + w / 2 if lines else x0 + th / 2, y0 + ph / 2, text=text,
                           anchor="center" if lines else "w", justify="center", font=s["f_toast_t"], fill=col,
                           tags=tags)
            if kind == "upd":
                cv.create_text(x0 + w - th / 2, y0 + th / 2, text="✕", anchor="e", font=s["f_toast_t"], fill=C["dim"],
                               tags=("toast", "updx"))
            y += ph + 6

    def refresh_status():
        s = st["status"]
        if not s:
            return
        on = cfg["logging"]
        all_ = cfg.get("log_all", False)
        if "pill" not in s:                          # Basic: no record pill / mode switch
            pass
        elif s["gfx"]:
            if s.get("pill_on") != on:
                s["pill_on"] = on
                cv.itemconfig(s["pill"], image=s["pill_fn"](on))
            if "sw_track" in s and s.get("track_all") != all_:
                s["track_all"] = all_
                cv.itemconfig(s["sw_track"], image=s["track_fn"](all_))
        else:
            cv.itemconfig(s["pill"], fill=mix(C["card"], C["green"] if on else C["red"], 0.18))
            cv.itemconfig(s["dot"], fill=C["green"] if on else C["red"])
            if "sw_track" in s:
                cv.itemconfig(s["sw_track"], fill=mix(C["track"], C["ui"], 0.55) if all_ else C["track"])
        sk = (on, all_, i18n._lang)
        if s.get("static_key") != sk:
            s["static_key"] = sk
            if "txt" in s:
                cv.itemconfig(s["txt"], text=t("log_on") if on else t("log_off"))
            if "sw_l" in s:
                cv.itemconfig(s["sw_l"], fill=C["faint"] if all_ else C["fg"])
                cv.itemconfig(s["sw_r"], fill=C["fg"] if all_ else C["faint"])
        draw_toasts(status_msgs())
        if st.get("soon_after"):
            try:
                root.after_cancel(st["soon_after"])
            except tk.TclError:
                pass
            st["soon_after"] = None
        if st.get("soon") and not st["closing"]:
            st["soon_after"] = root.after(max(30, int(min(st["soon"]) * 1000)), refresh_status)
        if st["empty"]:
            hint = current_hint()
            cv.itemconfig(st["empty"], text=t("hint_empty") + ("\n\n" + hint if hint else ""))

    def animate_knob(target_x, steps=8):
        s = st["status"]
        if "sw_geo" not in s:                       # Basic: no recording-mode switch
            return
        x0, x1, my, kr = s["sw_geo"]
        cur = cv.coords(s["sw_knob"])
        if not cur:
            return
        img = len(cur) == 2                       # anti-aliased knob is an image (centre point), else an oval
        cx = cur[0] if img else (cur[0] + cur[2]) / 2

        def put(x):
            if img:
                cv.coords(s["sw_knob"], x, my)
            else:
                cv.coords(s["sw_knob"], x - kr, my - kr, x + kr, my + kr)
        if steps <= 1 or abs(target_x - cx) < 0.5:
            put(target_x)
            return
        put(cx + (target_x - cx) * 0.45)
        root.after(16, lambda: animate_knob(target_x, steps - 1))

    def save_cfg(*keys):
        if not config.save_keys(cfg, *keys):
            st["err_note"] = time.time()          # shown as a toast: config.json could not be written

    def toggle_mode(e=None):
        cfg["log_all"] = not cfg.get("log_all", False)
        logger.close()          # new file when the mode changes
        logger.last = 0
        save_cfg("log_all")
        s = st["status"]
        if s and "sw_geo" in s:
            animate_knob(s["sw_geo"][1] if cfg["log_all"] else s["sw_geo"][0])
        refresh_status()

    def toggle(e=None):
        cfg["logging"] = not cfg["logging"]
        if cfg["logging"]:
            stats.clear()       # new recording: min/max/avg start over
        else:
            logger.close()
        logger.last = 0
        save_cfg("logging")
        refresh_status()

    pending = {"id": None, "apply": None}

    def set_interval(e=None):
        try:
            v = float(spin.get().replace(",", "."))
        except ValueError:
            v = cfg["interval"]
        v = config.clamp_interval(v)
        cfg["interval"] = v
        spin.delete(0, "end")
        spin.insert(0, f"{v:.1f}")
        save_cfg("interval")
        if sync_on():
            # wait 1.5 s so that clicking the spinbox arrows does not restart HWiNFO on every step
            if pending.get("apply"):
                root.after_cancel(pending["apply"])
            pending["apply"] = root.after(1500, push_interval)

    def push_interval():
        pending["apply"] = None
        ms = hwctl.apply_hwinfo_interval(cfg["interval"], st["hw_exe"])
        if ms:
            st["last_restart"] = time.time()      # no automatic recovery while HWiNFO restarts
            st["applying"] = (time.time(), ms)

    def key_toggle(e):
        if root.focus_get() is not spin and not is_basic():       # Basic has no recording
            toggle()

    def reset_stats(e=None):
        """MIN / MAX / AVG start over (the next tick fills them again); recording is not affected."""
        stats.clear()
        st["reset_note"] = time.time()
        refresh_status()

    def key_reset(e):
        if root.focus_get() is not spin:
            reset_stats()

    def key_settings(e):
        if root.focus_get() is not spin:
            open_settings()

    def key_viewer(e):
        if root.focus_get() is not spin and not is_basic():       # the log viewer belongs to recording (Advanced)
            logview.show(api)

    def key_mini(e):
        if root.focus_get() is not spin:
            mm.toggle()

    def toggle_ui_mode(e=None):
        """Basic <-> Advanced, live (status bar chip and the B key). Motion on: the lit half of the chip slides over
        first (~0.16 s), then the new view is built and comes in row by row."""
        to = "advanced" if is_basic() else "basic"
        s = st["status"]
        if st.get("mode_sliding"):
            return
        geo = s.get("chip_slide")
        if not geo or not st["fx"]["motion"] or not st["fx"]["on"]:
            apply_settings({"ui_mode": to})
            return
        dx = geo
        t0 = anim_now()
        a = {"applied": 0}
        st["mode_sliding"] = True

        def done():
            st["mode_sliding"] = False
            apply_settings({"ui_mode": to})

        def step(now, dt):
            x = min(1.0, (now - t0) / 0.16)
            e = 1 - (1 - x) ** 3
            off = int(round(dx * e))
            if off != a["applied"]:
                cv.move("uimode_hl", off - a["applied"], 0)
                a["applied"] = off
            if x >= 1.0:
                root.after(0, done)               # (never rebuild the canvas inside the frame that walks it)
                return False
            return True
        anim_set("mode_chip", step)

    def key_ui_mode(e):
        if root.focus_get() is not spin:
            toggle_ui_mode()

    spin.config(command=set_interval)
    spin.bind("<Return>", lambda e: (set_interval(), cv.focus_set()))
    spin.bind("<FocusOut>", set_interval)
    root.bind("<KeyPress-l>", key_toggle)
    root.bind("<KeyPress-L>", key_toggle)
    root.bind("<KeyPress-r>", key_reset)
    root.bind("<KeyPress-R>", key_reset)
    root.bind("<KeyPress-s>", key_settings)
    root.bind("<KeyPress-S>", key_settings)
    root.bind("<KeyPress-v>", key_viewer)
    root.bind("<KeyPress-V>", key_viewer)
    root.bind("<KeyPress-m>", key_mini)
    root.bind("<KeyPress-M>", key_mini)
    root.bind("<KeyPress-b>", key_ui_mode)
    root.bind("<KeyPress-B>", key_ui_mode)
    cv.bind("<Button-1>", lambda e: cv.focus_set(), add="+")
    root.bind("<F11>", lambda e: set_fullscreen(not is_fullscreen()))
    root.bind("<Escape>", lambda e: set_fullscreen(False))

    def on_resize(e):
        size = (e.width, e.height)
        if size == st["size"]:
            return
        st["size"] = size
        if pending["id"]:
            root.after_cancel(pending["id"])
        pending["id"] = root.after(120, lambda: build(*size))
    cv.bind("<Configure>", on_resize)

    # ------------------------------------------------------------ layout resolution
    def set_layout_cfg(cfg_list):
        st["sections_cfg"] = cfg_list
        st["sections"] = layout.build_sections(cfg_list)

    def is_basic():
        return cfg.get("ui_mode") != "advanced"

    def view_sections():
        """What the panel shows: Basic = the curated plain rows (built from the auto-detected layout, the stored
        layout is not touched), Advanced = the user's layout."""
        return st["bsections"] if is_basic() else st["sections"]

    def ensure_basic(rd):
        """(Re)build the Basic rows when the set of readings changed in size (same rule as the auto layout).
        True when the rows changed."""
        if not is_basic() or len(rd) == st["bauto_n"] or (st["bauto_n"] >= 0 and time.time() - st["bauto_t"] <= 5):
            return False
        st["bauto_n"], st["bauto_t"] = len(rd), time.time()
        new = layout.build_sections(basicmode.view(layout.autodetect(rd, rids=True), rd))
        if [r.key for r in layout.all_rows(new)] == [r.key for r in layout.all_rows(st["bsections"])]:
            return False
        st["bsections"] = new
        return True

    def load_layout():
        """The layout configured for the active data source, or auto-detection."""
        lay = cfg.get(config.layout_key(src.active), "auto")
        explicit = lay if isinstance(lay, list) else None
        if explicit is not None and layout.build_sections(explicit, include_hidden=True):
            st["auto"], st["checked"] = False, False
            set_layout_cfg(explicit)
        else:
            st["auto"], st["checked"] = True, True
            set_layout_cfg([])
        st["auto_n"] = -1

    load_layout()

    def source_changed():
        """Another data source is active: switch to its layout, new statistics, new CSV file."""
        st["gen"] = src.gen
        load_layout()
        st["bauto_n"] = -1                           # Basic rows are detected again for the new source
        st["was_down"] = False
        st["warm_until"] = 0
        stats.clear()
        logger.close()
        if hw_active():
            st["hw_used"] = True
        if st["size"]:
            build(*st["size"])

    def ensure_layout(rd):
        """Explicit layout: if none of its rows exists on this PC, fall back to auto-detection.
        Auto layout: (re)detect whenever the set of readings changes in size."""
        changed = False
        if not st["checked"]:
            st["checked"] = True
            if layout.count_matches(layout.build_sections(st["sections_cfg"], include_hidden=True), rd) == 0:
                st["auto"] = True
                set_layout_cfg([])
                changed = True
        if st["auto"] and len(rd) != st["auto_n"] and (st["auto_n"] < 0 or time.time() - st.get("auto_t", 0) > 5):
            # (a reading count that jitters, e.g. dynamic HWiNFO sensors, must not re-run the detection every tick)
            st["auto_n"], st["auto_t"] = len(rd), time.time()
            new_cfg = layout.autodetect(rd)
            new = layout.build_sections(new_cfg)
            old_sig = [r.key for r in layout.all_rows(st["sections"])]
            if [r.key for r in layout.all_rows(new)] != old_sig:
                st["sections_cfg"], st["sections"] = new_cfg, new
                changed = True
        if changed:
            logger.close()
        if (ensure_basic(rd) or changed) and st["size"]:
            build(*st["size"])

    # ------------------------------------------------------------ settings window
    def read_now():
        try:
            rd = src.read_all()
        except Exception:
            return []
        if src.gen != st["gen"]:
            source_changed()
        return rd

    def layout_snapshot():
        """(is_auto, deep copy of the effective layout in config form incl. hidden rows)."""
        return st["auto"], copy.deepcopy(st["sections_cfg"])

    def panel_monitor():
        """The monitor the panel window is on (for centring dialogs)."""
        try:
            cx = root.winfo_rootx() + root.winfo_width() // 2
            cy = root.winfo_rooty() + root.winfo_height() // 2
            for mo in config.monitors():
                if mo["x"] <= cx < mo["x"] + mo["w"] and mo["y"] <= cy < mo["y"] + mo["h"]:
                    return mo
        except Exception:
            pass
        return st["mon"]

    def refresh_tasks():
        st["tasks"] = hwctl.tasks_installed(refresh=True)
        return st["tasks"]

    def cached_tasks():
        return st["tasks"]

    def apply_settings(changes, lay_mode="keep", lay_cfg=None):
        """Apply settings live. changes: {config key: new value}; lay_mode: 'keep' | 'auto' | 'explicit'.
        Returns False when config.json could not be written (the settings are still applied)."""
        real = {k: v for k, v in changes.items() if cfg.get(k) != v}
        old_mini = cfg.get("minimode")
        cfg.update(real)
        keys = list(real)
        need_build = bool({"columns", "language"} & set(real))
        if "language" in real:
            i18n.set_language(cfg["language"])
            if st["auto"]:
                st["auto_n"] = -1          # titles / names of the detected layout are language dependent
                rd = read_now()
                if rd:
                    ensure_layout(rd)
            st["bauto_n"] = -1             # ... and so are the names of the Basic rows
            rd = read_now()
            if rd:
                ensure_basic(rd)
        if "ui_mode" in real:              # Basic <-> Advanced: only the view changes, the stored layout is untouched
            need_build = True
            if is_basic() and cfg["logging"]:
                toggle()                   # Basic has no recording controls: a running recording stops
            st["bauto_n"], st["mode_msg"] = -1, ""
            if is_basic():
                rd = read_now()
                if rd:
                    ensure_basic(rd)
            w = st["settings"]
            try:
                if w is not None and w.winfo_exists():
                    if w.rebuild_later():  # the settings pages differ between the modes; a running benchmark keeps them
                        st["mode_msg"], st["mode_note"] = t("ui_mode_deferred"), time.time()
            except tk.TclError:
                pass
        lay_key = config.layout_key(src.active)          # the layout tab edits the active source's layout
        if lay_mode == "auto":
            cfg[lay_key] = "auto"
            keys.append(lay_key)
            st["auto"], st["checked"], st["auto_n"] = True, True, -1
            set_layout_cfg([])
            logger.close()
            need_build = True
            ensure_layout(read_now())
        elif lay_mode == "explicit":
            cfg[lay_key] = copy.deepcopy(lay_cfg or [])
            keys.append(lay_key)
            st["auto"], st["checked"] = False, True
            set_layout_cfg(cfg[lay_key])
            logger.close()
            need_build = True
        if THEME_KEYS & set(real):
            apply_theme()
            need_build = True
        if "motion" in real:
            need_build = True
        if {"ui_mode", "language", "motion"} & set(real) or THEME_KEYS & set(real):
            st["entry_next"] = True        # the new view comes in row by row (motion on)
        if "topmost" in real:
            root.attributes("-topmost", bool(cfg["topmost"]))
        if "minimode" in real:
            mm.reconfigure(old_mini)
        elif (THEME_KEYS | {"language"}) & set(real):
            mm.reconfigure()                       # colours / fonts / texts of the strip follow
        if "check_updates" in real:
            upd_reschedule(1000)
        if "alerts" in real:                       # new rules start from scratch (no old "already notified")
            alert_eng.reset()
            st["alert_key"] = None
            if not cfg["alerts"]["enabled"]:
                alert_eng.set_rules([])
        if "hwinfo_path" in real:
            st["hw_exe"] = hwctl.detect_hwinfo(cfg["hwinfo_path"])
        if {"backend", "lhm_url", "presentmon_path"} & set(real):
            if src.reconfigure():
                source_changed()
                if manage_on() and not hwctl.proc_running():
                    hwctl.run_task(hwctl.TASK_START)
                    st["last_restart"] = time.time()
        if {"log_dir", "csv_delimiter", "csv_decimal", "log_all", "logging", "proc_top", "proc_detail"} & set(real):
            if "logging" in real and cfg["logging"]:
                stats.clear()
            logger.close()                 # next row starts a new file with the new folder / format / mode
            logger.delim, logger.dec = config.csv_format(cfg)
            logger.last = 0
        if "log_all" in real and st["status"] and "sw_geo" in st["status"]:
            animate_knob(st["status"]["sw_geo"][1] if cfg["log_all"] else st["status"]["sw_geo"][0])
        if "interval" in real or ("sync_hwinfo_interval" in real and cfg["sync_hwinfo_interval"]) or                 ("backend" in real and sync_on()):
            spin.delete(0, "end")
            spin.insert(0, f"{cfg['interval']:.1f}")
            set_interval()
        ok = config.save_keys(cfg, *keys) if keys else True
        if "monitor" in real or "window_mode" in real:
            st["mon"] = config.pick_monitor(cfg["monitor"])
            st["mode"] = str(cfg["window_mode"]).lower()
            place(final=True)
        if need_build and st["size"]:
            build(*st["size"])
        refresh_status()
        return ok

    api = types.SimpleNamespace(root=root, cfg=cfg, C=C, fonts=(F_TXT, F_HEAD, F_NUM), fonts_now=lambda: (F_TXT, F_HEAD, F_NUM),
                                apply=apply_settings, layout=layout_snapshot, readings=read_now,
                                monitor=panel_monitor, refresh_tasks=refresh_tasks, tasks=cached_tasks,
                                detect=hwctl.detect_hwinfo, monitors=config.monitors, source=src,
                                mini_rows=lambda: layout.all_rows(st["sections"]))

    procs = procwin.Controller(api)
    api.procs = procs

    # the pointer on the panel warms the process sampler up (its first round is the costly one), so the top-programs
    # balloon of a row opens with numbers and the hover swell never waits for it
    def _warm(on):
        st["ptr_in"] = on
        try:
            (procmon.MON.acquire if on else procmon.MON.release)("panel", *((1.0,) if on else ()))
        except Exception:
            pass
        # 1 ms timers while the pointer is on the panel: the animation loop and the balloons react without the
        # ~16 ms rounding of Windows' default timer
        if os.name == "nt" and bool(st.get("hires_ptr")) != on:
            try:
                import ctypes
                (ctypes.windll.winmm.timeBeginPeriod if on else ctypes.windll.winmm.timeEndPeriod)(1)
                st["hires_ptr"] = on
            except Exception:
                pass
    cv.bind("<Enter>", lambda e: _warm(True), add="+")
    cv.bind("<Leave>", lambda e: _warm(False), add="+")

    def mm_notice(text):
        """Short notice in the toast stack above the status bar (e.g. the mini mode hotkey is taken)."""
        st["hk_msg"], st["hk_note"] = text, time.time()
        refresh_status()

    mm = minimode.MiniMode(api, mm_notice)         # the in-game strip (starts shown when "enabled" is set)

    def open_settings(e=None):
        w = st["settings"]
        try:
            if w is not None and w.winfo_exists():
                w.deiconify()
                w.lift()
                w.focus_force()
                return
        except tk.TclError:
            pass
        try:
            st["settings"] = settings_ui.SettingsWindow(api)
        except Exception:
            traceback.print_exc()

    def show_hw_setting():
        """Basic hint "CPU temperature needs HWiNFO": switch to Advanced and open Settings > HWiNFO."""
        if is_basic():
            apply_settings({"ui_mode": "advanced"})
        open_settings()

        def goto(n=0):
            w = st["settings"]
            try:
                if w is not None and w.winfo_exists() and 2 in w.nav_map:
                    w.select(2)
                elif n < 8:
                    root.after(250, goto, n + 1)
            except tk.TclError:
                pass
        root.after(400, goto)

    # ------------------------------------------------------------ updates
    def upd_state():
        """(text, colour, clickable) of the update notice in the status bar; text '' = nothing to show."""
        info = st["upd"]
        if st["upd_busy"]:
            return t("upd_downloading", p=st["upd_prog"]), themes.accent("gauge"), False
        if time.time() - st["upd_err"] < 5:
            return t("upd_failed"), C["red"], False
        if info and cfg.get("check_updates", True) and info["version"] != st["upd_dismissed"]:
            return t("upd_available", v=info["version"]), themes.accent("gauge"), True
        return "", None, False

    def upd_worker(gen):
        try:
            res = updater.check(updater.current_version())
        except Exception:
            res = None                                  # offline / rate limit / bad JSON: silent
        upd_q.put(("found", gen, res))

    def upd_cycle():
        """Runs 5 s after start and then every 24 h while the option is on."""
        st["upd_after"] = None
        if st["closing"] or not cfg.get("check_updates", True):
            return
        threading.Thread(target=upd_worker, args=(st["upd_gen"],), daemon=True).start()
        st["upd_after"] = root.after(24 * 3600 * 1000, upd_cycle)

    def upd_reschedule(first_ms):
        """(Re)starts or stops the periodic check (option toggled in Settings, or start-up)."""
        if st["upd_after"] is not None:
            try:
                root.after_cancel(st["upd_after"])
            except Exception:
                pass
            st["upd_after"] = None
        st["upd_gen"] += 1                              # results of a check that is still running are dropped
        if cfg.get("check_updates", True):
            st["upd_after"] = root.after(first_ms, upd_cycle)
        else:
            st["upd"] = None
            refresh_status()

    def upd_pump():
        """Hands worker results to the Tk thread."""
        if st["closing"]:
            return
        try:
            while True:
                kind, *a = upd_q.get_nowait()
                if kind == "found":
                    gen, res = a
                    if gen == st["upd_gen"] and cfg.get("check_updates", True):
                        st["upd"] = res
                elif kind == "dl_ok":
                    st["upd_busy"] = False
                    ok = False
                    try:
                        ok = updater.install(a[0])
                    except Exception:
                        pass
                    if ok:
                        full_exit()                     # the Setup replaces the files: leave cleanly (never just hide)
                        return
                    st["upd_err"] = time.time()
                elif kind == "dl_err":
                    st["upd_busy"] = False
                    st["upd_err"] = time.time()
                refresh_status()
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
        root.after(600, upd_pump)

    def upd_download(info):
        def cb(p):
            st["upd_prog"] = p
        try:
            upd_q.put(("dl_ok", updater.download(info, cb)))
        except Exception:
            upd_q.put(("dl_err",))

    def upd_dismiss(e=None):
        """The small x on the update notice: hide it (until a still newer release appears)."""
        if st["upd"]:
            st["upd_dismissed"] = st["upd"]["version"]
            refresh_status()

    def upd_click(e=None):
        info = st["upd"]
        if not info or st["upd_busy"] or time.time() - st["upd_err"] < 5:
            return
        import tkinter.messagebox as mb
        cur = updater.current_version()
        if not config.FROZEN:                           # running from source: nothing to install, show the release
            if mb.askyesno(config.APP_NAME, t("upd_confirm_src", v=info["version"], cur=cur), parent=root):
                webbrowser.open(info["page_url"])
            return
        if not mb.askyesno(config.APP_NAME, t("upd_confirm", v=info["version"], cur=cur), parent=root):
            return
        st["upd_busy"], st["upd_prog"] = True, 0
        refresh_status()
        threading.Thread(target=upd_download, args=(info,), daemon=True).start()

    # ------------------------------------------------------------ data
    def tick():
        # a row swell is moving: the new readings (a repaint of the whole panel) wait the ~0.15 s until it ends
        if st.get("ptr_in") and hover_moving() and st.get("tick_wait", 0) < 12:
            st["tick_wait"] = st.get("tick_wait", 0) + 1
            root.after(20, tick)
            return
        st["tick_wait"] = 0
        if FXPROF:                                   # diagnostics: how long a data tick holds the UI thread
            t0_ = aurora.clock()
            try:
                return tick_()
            finally:
                st.setdefault("tick_cost", []).append(aurora.clock() - t0_)
        return tick_()

    def tick_():
        try:
            rd = src.read_all()
            ok = True
        except Exception:
            rd, ok = [], False
        if src.gen != st["gen"]:
            source_changed()
        st["ok"] = ok
        now = time.time()
        if ok:
            ensure_layout(rd)
        if ok and st.get("was_down"):
            # HWiNFO / LHM just started: first readings may be missing / zero; hold the old values a few rounds
            st["was_down"] = False
            ms = hwctl.hwinfo_interval_ms(st["hw_exe"]) if hw_active() else 1000
            st["warm_until"] = now + max(3.0, 2.5 * ms / 1000)
        if not ok:
            st["was_down"] = True
        frozen = (not ok) or now < st.get("warm_until", 0)
        st["frozen"] = frozen
        # HWiNFO still runs but its shared memory stopped updating (the free version does that after 12 h): the old
        # values would look fine, so show "-", record nothing and offer a restart (button only: never automatic)
        stale = False
        if ok and src.active == "hwinfo" and hwsm.monitor.stale:
            if now - st.get("hw_ivl_t", 0) > 30:
                st["hw_ivl_t"] = now
                st["hw_ivl"] = hwctl.hwinfo_interval_ms(st["hw_exe"]) / 1000.0 if st.get("hw_exe") else 2.0
            hwsm.monitor.limit = max(10.0, 3 * st.get("hw_ivl", 2.0))
            # frozen shared memory, or HWiNFO runs but has shared nothing for 20 s (e.g. the 12 h limit of the free
            # version): only a notice with a restart button, never an automatic restart (the user decides)
            down = (not ok and manage_on() and st.get("down_since") is not None and now - st["down_since"] > 20)
            stale = (hwsm.monitor.stale or down) and hwctl.proc_running()
        if stale != st.get("stale"):
            st["stale"], st["need_draw"] = stale, True
            if not stale:
                st["hw_rst"] = 0
        res = st.pop("hw_rst_res", None)
        if res == "failed":
            st["hw_rst"] = 0
            st["hk_msg"], st["hk_note"] = t("hw_rst_fail"), now
        if ok:
            st["down_since"] = None
        elif manage_on():
            t0 = st.get("down_since") or now
            st["down_since"] = t0
            # automatic repair ONLY when HWiNFO is not running (crashed / closed): start it again after 20 s
            # (at most every 5 min). If it runs but shares nothing (12 h limit of the free version, sharing switched
            # off) we never restart it ourselves: the stale notice above offers a button instead.
            if not hwctl.proc_running() and now - t0 > 20 and now - st.get("last_restart", 0) > 300:
                st["last_restart"] = now
                if st.get("hw_exe") and hwctl.tasks_installed():
                    hwctl.repair_hwinfo(cfg["interval"], st["hw_exe"])   # restart + SensorsSM=1
                else:
                    hwctl.run_task(hwctl.TASK_START)
        try:
            mini = root.state() in ("iconic", "withdrawn")       # minimised, or hidden to the tray: nothing to draw
        except tk.TclError:
            mini = False
        if st.get("mini") and not mini:
            st["last"].clear()                  # restored: the canvas content is stale, redraw everything once
        st["mini"] = mini
        rows = layout.all_rows(st["sections"])        # recording / mini mode: the user's layout in both modes
        shown = layout.all_rows(st["bsections"]) if is_basic() else []      # Basic: extra rows only drawn here
        if ok and not frozen and not stale and not st.get("thr_seen"):
            hot = throttle.active(rd)                    # HWiNFO only: the first time this session the CPU / GPU is held back
            if hot:
                st["thr_seen"] = True
                st["thr_msg"] = " · ".join(t("thr_toast", who=w, what=x) for w, x in hot)
                st["thr_note"] = now
        mm.update(rows, rd if ok and not stale else [], ok and not frozen)
        if cfg["alerts"]["enabled"] and ok and not frozen and not stale:
            try:
                check_alerts(rows, rd, now)
            except Exception:
                traceback.print_exc()
        alert_icon_cleanup(now)
        # HWiNFO has not polled since the last tick: nothing new to show (the values are identical)
        poll = hwsm.last_poll if (ok and src.active == "hwinfo") else None
        same = (poll is not None and poll == st.get("poll") and not st.get("need_draw") and not st["frozen"]
                and not cfg["logging"] and not mini)
        st["poll"] = poll
        if not same:
            st["need_draw"] = False
        st["nall"] = len(rd) if ok else st.get("nall", 0)
        st["nreq"] = len(rows)
        logvals = []
        for row, islog in (() if same else [(x, True) for x in rows] + [(x, False) for x in shown]):
            r = row.match(rd) if ok and not stale else None
            if islog:
                logvals.append(logger.num(r["value"], row.csv_decimals()) if r else "")
            it = st["items"].get(id(row))
            if not it or frozen:
                continue        # frozen: last values stay on screen, stats / bars do not move
            if mini:
                # minimised: keep the statistics current, skip every canvas update (redone on restore)
                if r:
                    v = r["value"]
                    s = stats.setdefault(row.key, [v, v, 0.0, 0])
                    s[0], s[1], s[2], s[3] = min(s[0], v), max(s[1], v), s[2] + v, s[3] + 1
                continue
            if stale:                            # frozen HWiNFO data: no values, statistics keep what they have
                st["fx"]["anims"].pop(("num", it["now"]), None)
                it.pop("v_shown", None)
                set_text(it["now"], "–", C["dim"])
                if "status" in it:
                    set_text(it["status"], "", C["dim"])
                if "gauge" in it:
                    fx_target(it["gauge"], 0.0, it["accent"])
                elif "bar" in it:
                    bar_glide(it, 0.0, it["accent"])
                if "ring" in it:
                    fx_target(it["ring"], 0.0, it["accent"])
                continue
            if r:
                v = r["value"]
                s = stats.setdefault(row.key, [v, v, 0.0, 0])
                s[0], s[1], s[2], s[3] = min(s[0], v), max(s[1], v), s[2] + v, s[3] + 1
                avg = s[2] / s[3]
                basic = is_basic()
                if basic:        # Basic: value + word share one status level (temperatures green / amber / red, rest neutral)
                    col = basicmode.color(row, v, C)
                    bt = basicmode.value_text(row, v)
                else:
                    col, bt = layout.value_color(row, v, C["fg"]), None
                set_text(it["unit"], bt[1] if bt else row.unit_eff)
                if bt:                                   # RAM / VRAM in Basic: "11,0 / 32" counts as well
                    tween_num(it, row, v, lambda x, r=row: (basicmode.value_text(r, x) or ("",))[0], col)
                else:
                    tween_num(it, row, v, row.fmt_value, col)
                if "status" in it:
                    set_text(it["status"], fit_text(st["fnt_stat"], basicmode.status_text(row, v), it["stat_w"]),
                             col)
                for k, val in (("min", s[0]), ("max", s[1]), ("avg", avg)):
                    if k not in it:
                        continue
                    if row.textual:
                        set_text(it[k], "")                  # a state / reason code: no min / max / average
                        continue
                    set_text(it[k], row.fmt_value(val), mix(layout.value_color(row, val, C["fg"]), C["card"], 0.18))
                bcol = it["accent"] if basic and row.status != "temp" else col        # Basic: calm accent bars
                if "ring" in it:
                    fx_target(it["ring"], layout.frac(row, v), bcol)
                if "gauge" in it:                                    # Aurora: glowing bar, glides (effect loop)
                    fx_target(it["gauge"], layout.frac(row, v), bcol)
                elif "bar" in it:                                    # plain themes: the bar glides too
                    bar_glide(it, layout.frac(row, v), bcol)
        logger.ext.update()                 # process sampling runs only while the recording needs it
        if not frozen and not stale and ok and cfg["logging"] and rows and now - logger.last >= cfg["interval"] - 0.05:
            logger.last = now
            try:
                if cfg.get("log_all"):
                    hdr = [f"{r['sensor']} | {r['label']} [{r['unit']}]" for r in rd]
                    vals = [logger.num(r["value"], 3) for r in rd]
                else:
                    hdr = [f"{r.name} ({r.unit_eff})" for r in rows]
                    vals = logvals
                hdr, vals = hdr + logger.ext.header(), vals + logger.ext.values()
                logger.write(time.strftime("%Y-%m-%d %H:%M:%S"), hdr, vals)
            except Exception:
                st["log_err"] = time.time()
        refresh_status()
        fx_tick(now)
        # HWiNFO: cheap (unchanged polls are skipped) -> 2 Hz; the other sources re-sample each tick -> 1 Hz
        slow = (mini and not cfg["logging"] and not mm.visible) or (src.active != "hwinfo" and not (cfg["logging"] and cfg["interval"] < 1))
        root.after(1000 if slow else 500, tick)

    def fx_tick(now):
        """Per data tick: wake the effect loop (new gauge values, back from the tray), and let the automatic Aurora
        theme follow a day / night switch of the Windows app mode (checked every 20 s)."""
        name = str(cfg.get("theme") or "").strip().lower()
        if name in themes.AUTO and now - st.get("fx_mode_t", 0) > 20:
            st["fx_mode_t"] = now
            if themes.resolve(name) != themes.current().variant:
                apply_theme()
                mm.reconfigure()
                if st["size"]:
                    build(*st["size"])
                return
        if st["fx"]["on"]:
            fx_kick()

    # ------------------------------------------------------------ alerts (alerts.py)
    def alert_text(rule, v, row=None):
        """"CPU Temperature 92 °C (limit > 90 °C)" in the current language."""
        row = row or st["alert_rows"].get(rule.get("metric"))
        name = row.name if row is not None else (rule.get("name") or "?")
        unit = row.unit_eff if row is not None else ""
        fmt = row.fmt_value if row is not None else (lambda x: f"{x:g}")
        sp = " " if unit and unit != "%" else ""
        return t("al_msg", name=name, value=f"{fmt(v)}{sp}{unit}",
                 limit=f"{rule.get('op', '>')} {alerts.fmt_limit(rule.get('value', 0))}{sp}{unit}")

    def check_alerts(rows, rd, now):
        conf = cfg["alerts"]
        key = (tuple(r.key for r in rows), repr(conf["rules"]))
        if st.get("alert_key") != key:               # layout or rules changed ("auto" rules follow the layout)
            st["alert_key"] = key
            alert_eng.set_rules(alerts.resolve_rules(conf, rows))
        want = {r.get("metric") for r in alert_eng.rules}
        vals = {}
        for row in rows:
            mk = minimode.metric_key(row)
            if mk in want and mk not in vals and alerts.usable(row):
                r = row.match(rd)
                if r is not None:
                    vals[mk] = (r["value"], row)
                    st["alert_rows"][mk] = row
        for rule, v, row in alert_eng.update(vals, now):
            alert_fire(t("al_title"), alert_text(rule, v, row), conf.get("notify", True), conf.get("sound", True))

    def alert_fire(title, text, notify, sound):
        """Sound and / or Windows notification (the tray icon is shown for it when the panel is not in the tray)."""
        if sound:
            try:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
        if notify and tr.ok:
            if not st.get("hidden") and not tr.added:
                tip, items = tray_state()
                if not tr.set_visible(True, tip, items):
                    return
                st["alert_icon"] = time.time()
            elif st.get("alert_icon"):
                st["alert_icon"] = time.time()
            tr.balloon(title, text, warn=True, quiet=True)      # (the sound above follows the user's choice)

    def alert_icon_cleanup(now):
        """The icon shown only for a notification goes again 2 minutes later (and never while the panel is in the tray:
        then it is the tray icon)."""
        t0 = st.get("alert_icon")
        if not t0 or st.get("hidden"):
            return
        if now - t0 > 120 and not (cfg["alerts"]["enabled"] and alert_eng.active()):
            st["alert_icon"] = None
            tr.set_visible(False)

    def alert_test(notify, sound):
        """Settings > Alerts > Test: the same sound / notification a real alert gives."""
        alert_fire(t("al_title"), t("al_test_text"), notify, sound)

    api.alert_test = alert_test

    # ------------------------------------------------------------ closing / running in the background
    def bg_wanted():
        """True when a situation ticked in Settings > Background is active: closing the panel then only hides it."""
        return bool(tr.ok) and ((bool(cfg.get("bg_keep_logging")) and bool(cfg["logging"]))
                                or (bool(cfg.get("bg_keep_mini")) and bool(mm.visible)))

    def tray_state():
        """(tooltip, menu items) for the icon: the menu follows what is running."""
        tip = t("tray_tip") + (" · " + t("tray_tip_log") if cfg["logging"] else "")
        items = [("show", t("tray_show"), False), ("mini", t("tray_mini"), bool(mm.visible))]
        if cfg["logging"]:
            items.append(("stoplog", t("tray_stoplog"), False))
        items += [("-", "", False), ("exit", t("tray_exit"), False)]
        return tip, items

    def hide_panel():
        """Close button with something still running: the window goes away, everything else keeps running."""
        tip, items = tray_state()
        st["tray_key"] = (tip, tuple(items))
        if not tr.set_visible(True, tip, items):
            root.iconify()                       # no icon possible (Explorer not running?): never lose the window
            return
        try:
            st["shown_state"] = root.state()                      # a maximised window must come back maximised
        except tk.TclError:
            st["shown_state"] = "normal"
        root.withdraw()
        st["hidden"] = True
        st["alert_icon"] = None                  # the icon is the tray icon now (it stays)
        if not cfg.get("bg_hint_shown"):         # the first time: tell the user where it went
            cfg["bg_hint_shown"] = True
            save_cfg("bg_hint_shown")
            tr.balloon(t("tray_balloon_title"), t("tray_balloon"))

    def show_panel():
        if st["closing"]:
            return
        st["hidden"] = False
        try:
            root.deiconify()
            if st.get("shown_state") == "zoomed" and root.state() != "zoomed":
                root.state("zoomed")
            root.lift()
            root.attributes("-topmost", True)                         # a launch from the tray / second launch must come to the front
            root.attributes("-topmost", bool(cfg["topmost"]))
            root.focus_force()
        except tk.TclError:
            pass
        tr.set_visible(False)
        st["alert_icon"] = None

    def guard_bench(then):
        """A benchmark / load test runs (only possible from the open Settings window): ask first, stop it, close the
        Settings window, then continue with then(). No: nothing happens."""
        w = st["settings"]
        try:
            busy = w is not None and w.winfo_exists() and w.bench_busy()
        except tk.TclError:
            busy = False
        if not busy:
            then()
            return
        w.confirm_stop(lambda: (w.close(), then()))

    def on_close():
        """The close button of the panel."""
        if st["closing"]:
            return
        guard_bench(lambda: hide_panel() if bg_wanted() else full_exit())

    def tray_exit():
        """Exit in the tray menu: the normal full close (with the question when a test is running)."""
        if st["closing"]:
            return
        if st.get("hidden"):
            full_exit()
        else:
            guard_bench(full_exit)

    def tray_poll():
        """Tk thread: act on what the tray thread queued (clicks, menu choices, a second launch)."""
        if st["closing"]:
            return
        try:
            for cmd in tr.drain():
                if cmd == "show":
                    show_panel()
                elif cmd == "mini":
                    mm.toggle()
                elif cmd == "stoplog":
                    if cfg["logging"]:
                        toggle()
                elif cmd == "exit":
                    tray_exit()
                elif cmd == "quit":
                    full_exit()
            if st.get("hidden") and not st["closing"]:            # tooltip / menu follow logging and mini mode
                tip, items = tray_state()
                if st.get("tray_key") != (tip, tuple(items)):
                    st["tray_key"] = (tip, tuple(items))
                    tr.set_info(tip, items)
        except Exception:
            traceback.print_exc()
        if not st["closing"]:
            root.after(150, tray_poll)

    def full_exit():
        """Really end the program: stop logging cleanly, HWiNFO (when this panel started it), benchmarks, the icon."""
        if st.get("closing"):
            return
        st["closing"] = True
        tray.release_instance()                          # a launch right after this (updater, Setup) must start a new panel
        try:
            root.withdraw()                              # the window is gone at once; the clean-up follows
        except tk.TclError:
            pass
        _arm_hard_exit(3.0)                              # something blocking below must not keep the process alive
        steps = [tr.close, mm.shutdown, procs.close, logger.ext.shutdown, logger.close, perf_core.kill_all_jobs,
                 src.close]
        if bool(cfg["manage_hwinfo"]) and st["tasks"] and (hw_active() or st.get("hw_used")):
            steps.append(lambda: hwctl.run_task(hwctl.TASK_STOP))
        for fn in steps:
            try:
                fn()
            except Exception:
                traceback.print_exc()
        _end_process(0)                                  # no Tk teardown: it crashes in Tk 9 (see _end_process)
        try:                                             # (only reached if ending the process failed)
            root.quit()
            root.destroy()
        except tk.TclError:
            pass
    root.protocol("WM_DELETE_WINDOW", on_close)

    st["last_restart"] = time.time()
    st["hw_used"] = hw_active()
    if sync_on() and hwctl.apply_hwinfo_interval(cfg["interval"], st["hw_exe"]):
        pass                                          # the task adjusted the interval and (re)started HWiNFO
    elif manage_on() and not hwctl.proc_running():
        hwctl.run_task(hwctl.TASK_START)
    # when opened from a shortcut Windows puts the window on the clicked screen: move it, then maximise
    root.after(200, lambda: place(final=True))

    def show_mode_hint():
        """First start in Basic (new install only; upgraded installs stay Advanced): say once how to get the details."""
        if st["closing"] or not is_basic() or cfg.get("ui_mode_hint_shown"):
            return
        cfg["ui_mode_hint_shown"] = True
        save_cfg("ui_mode_hint_shown")
        st["mode_msg"], st["mode_note"] = t("ui_mode_hint"), time.time()
        refresh_status()
    root.after(1800, show_mode_hint)

    def _prep_balloon():
        try:
            import tooltip
            tooltip.prepare(root)                    # the balloon window, made before the first hover needs it
        except Exception:
            pass
        try:                                         # the swell's font file and first images, likewise
            sp = textsprite.Sprite.for_family(F_TXT) if GFX else None
            if sp is not None:
                for k in range(9):
                    sp.photo("CPU", 15 + k * 0.25, C["name"], 17)
        except Exception:
            pass
    root.after(1200, _prep_balloon)
    if is_basic() and cfg["logging"]:                 # e.g. a hand-edited config: Basic never records
        toggle()
    root.after(400, tick)
    def bench_sweep():
        """METRIQ_FXBENCH=sweep: a 1000 Hz mouse moving down and up over the row names (synthetic <Motion> events,
        the real pointer is never touched), to measure what real mouse movement costs."""
        import tooltip as _tt
        _tt.Tooltip._pointer_over = lambda self: True
        hv = st["hovers"]
        if not hv:
            root.after(500, bench_sweep)
            return
        boxes = [cv.bbox(g.item) for g in hv if cv.bbox(g.item)]
        x = boxes[0][0] + 12
        y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
        S = {"y": float(y0), "d": 1.0, "n": 0}
        def mv():
            if st["closing"]:
                return
            S["y"] += S["d"] * 0.6                    # 600 px/s at 1000 events per second
            if S["y"] > y1 or S["y"] < y0:
                S["d"] = -S["d"]
            t0_ = time.perf_counter()
            if FXPROF and not S.get("fh"):
                import faulthandler
                S["fh"] = open(FXPROF + ".stacks", "w")
                S["fhm"] = faulthandler
            if FXPROF:
                S["fhm"].dump_traceback_later(0.3, repeat=False, file=S["fh"])
            cv.event_generate("<Motion>", x=x, y=int(S["y"]))
            if FXPROF:
                S["fhm"].cancel_dump_traceback_later()
            st.setdefault("ev_cost", []).append(time.perf_counter() - t0_)
            S["n"] += 1
            if S["n"] % 500 == 0 and FXPROF:
                c_ = sorted(st.pop("ev_cost"))
                with open(FXPROF, "a", encoding="utf-8") as f_:
                    f_.write("   motion event cost p50 %.2f ms p90 %.2f ms max %.1f ms" % (
                        c_[len(c_) // 2] * 1000, c_[int(len(c_) * 0.9)] * 1000, c_[-1] * 1000))
            st["sweep_n"] = S["n"]
            root.after(1, mv)
        mv()

    if FXBENCH:
        if FXBENCH == "sweep":
            st["ptr_in"] = True
            _warm(True)
            root.after(3000, bench_sweep)
        elif FXBENCH == "row0":
            st["ptr_in"] = True                      # measure as with the pointer on the panel (data_snap)
            root.after(3000, bench_hover)
    if os.environ.get("METRIQ_UICHECK"):
        ui_check(os.environ["METRIQ_UICHECK"])
    if os.environ.get("METRIQ_QUIT_AFTER"):           # tools/bench_fx.py: end cleanly after a measurement
        root.after(int(float(os.environ["METRIQ_QUIT_AFTER"]) * 1000), full_exit)
    root.after(300, upd_pump)
    root.after(150, tray_poll)
    upd_reschedule(5000)
    root.mainloop()
    if st.get("closing"):
        perf_core.kill_all_jobs()
        sys.exit(0)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()          # benchmark worker processes (perf_core.py) when frozen
    if "--bench-selftest" in sys.argv:        # hidden: short benchmark through the real process path (packaging test)
        import perf_core
        i = sys.argv.index("--bench-selftest")
        sys.exit(perf_core.selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        try:
            os.makedirs(config.data_dir(), exist_ok=True)
            with open(os.path.join(config.data_dir(), "panel.log"), "w", encoding="utf-8") as f:
                f.write(err)
        except OSError:
            pass
        try:
            import tkinter.messagebox as mb
            r = tk.Tk()
            r.withdraw()
            mb.showerror(config.APP_NAME, err[-1500:])
        except Exception:
            pass
        sys.exit(1)
