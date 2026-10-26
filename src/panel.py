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

import backend
import config
import crashscan
import gfx
import helptexts
import hwctl
import i18n
import layout
import proclog
import logview
import procwin
import settings_ui
import themes
import updater
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
THEME_KEYS = {"theme", "theme_overrides", "value_color_mode", "value_steps"}


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


GFX = gfx.AVAILABLE      # Pillow present: anti-aliased images instead of canvas primitives
TOAST_RESET_S, TOAST_INFO_S, TOAST_ERR_S, FADE_S = 1.5, 2.0, 4.0, 0.35     # toast lifetimes (seconds), fade-out length


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


def launch_command():
    """Command line that starts this panel again (used by Windows when the taskbar button is pinned)."""
    if config.FROZEN:
        return f'"{sys.executable}"'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() == "python.exe" and os.path.isfile(w):
        exe = w
    return f'"{exe}" "{os.path.join(HERE, "panel.py")}"'


def main():
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
    st = {"items": {}, "last": {}, "status": {}, "size": None, "ok": False, "sections": [], "sections_cfg": [],
          "auto": True, "auto_n": -1, "checked": False, "empty": None, "note": note, "settings": None,
          "hw_exe": hwctl.detect_hwinfo(cfg["hwinfo_path"]), "tasks": hwctl.tasks_installed(),
          "mon": config.pick_monitor(cfg["monitor"]), "mode": str(cfg["window_mode"]).lower(),
          "upd": None, "upd_busy": False, "upd_prog": 0, "upd_err": 0, "upd_gen": 0, "upd_after": None,
          "closing": False, "t0": time.time(), "upd_dismissed": None, "tips": {}}
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
        root.attributes("-fullscreen", False)
        root.state("normal")
        root.geometry(f"{w}x{h}+{x}+{y}")
        if not final:
            return
        root.update_idletasks()
        if mode == "fullscreen":
            root.attributes("-fullscreen", True)
        elif mode != "window":
            root.state("zoomed")

    place()

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
            h += " · " + t("hint_readonly")
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

    def build(W, H):
        cv.delete("all")
        if GFX:
            gfx.begin()
        st["items"].clear()
        st["last"].clear()
        st["empty"] = None
        sections = st["sections"]
        HEAD, FOOT, STATUS = layout.HEAD, layout.FOOT, layout.STATUS    # in row heights
        sty = themes.current()
        R, sp = sty.radius, sty.spacing                  # card corner radius, row pitch factor
        p = layout.plan(sections, W, H, cfg["columns"], cfg["ui_scale"], sp)
        rh, m, g = p.rh, p.m, p.g
        rp = rh * sp                                      # row pitch (rows keep the font size of rh)
        inset = max(6.0, R * 0.4)                         # zebra stripes stay inside rounded corners
        fs_name0, fs_num = -max(10, int(rh * 0.50)), -max(10, int(rh * 0.52))
        fs_small, fs_head = -max(9, int(rh * 0.38)), -max(11, int(rh * 0.62))
        # ----- column geometry (fractions of the column width); no bar column in narrow columns
        cw = p.cwc
        pad = cw * 0.035
        if p.with_bar:
            fr = dict(bar0=0.40, bar1=0.53, now=0.69, mn=0.795, mx=0.885)
        else:
            fr = dict(now=0.54, mn=0.725, mx=0.85)
        # ----- fonts shrink (numbers first) when a column is too narrow for them
        sample = "8888" if p.with_bar else "88888"        # narrow columns must also fit 5 digits
        nw0 = meas_font(F_NUM, fs_num).measure(sample)
        uw0 = meas_font(F_TXT, fs_small).measure("KB/s")
        fm = min(1.0, (min(fr["mx"] - fr["mn"], 1 - pad / cw - fr["mx"]) * cw - 8) / nw0,
                 ((fr["mn"] - fr["now"]) * cw - 14) / (nw0 + uw0))
        fm = max(0.55, fm)
        fs_num_e, fs_small_e = -max(8, int(-fs_num * fm)), -max(8, int(-fs_small * fm))
        f_num, f_unit = (F_NUM, fs_num_e), (F_TXT, fs_small_e)
        f_col = (F_TXT, fs_small_e, "bold")
        unit_w = meas_font(F_TXT, fs_small_e).measure("KB/s")
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
            col_min, col_max, col_avg = cx0 + cw * fr["mn"], cx0 + cw * fr["mx"], cx1 - pad
            yy = m
            for sec in col_secs:
                accent = themes.accent_for(sec.icon, sec.color)
                hh, pb = rh * HEAD, rh * FOOT
                ch = hh + len(sec.rows) * rp + pb
                hy = yy + hh * 0.5
                bubble = mix(C["card"], accent, 0.16)
                if GFX:
                    bx0, by0 = int(round(cx0)), int(round(yy))
                    cv.create_image(bx0, by0, image=gfx.card(int(round(cx1)) - bx0, int(round(yy + ch)) - by0, R,
                                                             C["card"], C["border"], accent), anchor="nw")
                    cv.create_image(col_name + isz * 1.6, hy, image=gfx.icon(sec.icon, isz, accent, bubble, bubble))
                else:
                    round_rect(cv, cx0, yy, cx1, yy + ch, R, fill=C["card"], outline=C["border"])
                    cv.create_line(cx0 + 1.5, yy + R, cx0 + 1.5, yy + ch - R, fill=accent, width=3)
                    cv.create_oval(col_name, hy - isz * 1.6, col_name + isz * 3.2, hy + isz * 1.6, fill=bubble,
                                   outline="")
                    draw_icon(cv, sec.icon, col_name + isz * 1.6, hy, isz, accent, bubble)
                title_x = col_name + isz * 3.9
                title_avail = (col_now + 6 + unit_w - lbl_now_w) - title_x - 10
                hs = -fs_head
                f_title = meas_font(F_HEAD, -hs, "bold")
                if f_title.measure(sec.title) > title_avail > 0:
                    hs = max(10, int(hs * max(0.7, title_avail / f_title.measure(sec.title))))
                    f_title = meas_font(F_HEAD, -hs, "bold")
                cv.create_text(title_x, hy, text=fit_text(f_title, sec.title, title_avail), anchor="w",
                               font=(F_HEAD, -hs, "bold"), fill=C["fg"])
                for hk, lbl, xx in (("now", t("col_now"), col_now + 6 + unit_w), ("min", t("col_min"), col_min),
                                    ("max", t("col_max"), col_max), ("avg", t("col_avg"), col_avg)):
                    cv.create_text(xx, hy, text=lbl, anchor="e", font=f_col, fill=mix(C["dim"], accent, 0.45),
                                   tags=("hdr_" + hk,))
                cv.create_line(cx0 + pad * 0.6, yy + hh - 1, cx1 - pad * 0.6, yy + hh - 1, fill=C["border"])
                ry = yy + hh
                for i, row in enumerate(sec.rows):
                    if i % 2:
                        cv.create_rectangle(cx0 + inset, ry, cx1 - inset, ry + rp, fill=C["stripe"], outline="")
                    my = ry + rp / 2
                    nm_id = cv.create_text(col_name, my, text=fit_text(fnt_name, row.name, name_avail), anchor="w",
                                           font=f_name, fill=C["name"])
                    procs.tag_row(cv, nm_id, row, cx0 + name_right)   # CPU / RAM / GPU / VRAM / disk rows open the process window (+ (i) balloon)
                    it = {"accent": accent}
                    if row.bar and p.with_bar:
                        bh = max(4, rh * 0.22)
                        round_rect(cv, bar_x0, my - bh / 2, bar_x1, my + bh / 2, bh / 2, fill=C["track"], outline="")
                        it["bar"] = cv.create_rectangle(bar_x0, my - bh / 2, bar_x0, my + bh / 2, fill=accent,
                                                        outline="")
                        it["bargeo"] = (bar_x0, bar_x1, my - bh / 2, my + bh / 2)
                    it["now"] = cv.create_text(col_now, my, text="–", anchor="e", font=f_num, fill=C["fg"])
                    it["unit"] = cv.create_text(col_now + 6, my, text=row.unit_eff, anchor="w", font=f_unit,
                                                fill=C["faint"])
                    for k, xx in (("min", col_min), ("max", col_max), ("avg", col_avg)):
                        it[k] = cv.create_text(xx, my, text="–", anchor="e", font=f_num, fill=C["dim"])
                    st["items"][id(row)] = it
                    ry += rp
                yy += ch + g
        # ----- status bar (full width, a single row: record pill, mode switch, interval, reset, gear);
        # messages are shown as small toasts just above it (see draw_toasts)
        sx0, sx1 = m, W - m
        sw_span = sx1 - sx0
        rs = min(rh, max(layout.MIN_RH, sw_span * 0.042))      # its text must not grow with a huge row height
        spad = min(sw_span * 0.035, rs * 1.0)
        col_name = sx0 + spad
        sb_h = rs * (STATUS - 0.2)
        sy = H - m - sb_h
        s = st["status"]
        s.clear()
        s["gfx"] = GFX
        if GFX:
            bx0, by0 = int(round(sx0)), int(round(sy))
            cv.create_image(bx0, by0, image=gfx.card(int(round(sx1)) - bx0, int(round(sy + sb_h)) - by0, R,
                                                     C["card"], C["border"]), anchor="nw")
        else:
            round_rect(cv, sx0, sy, sx1, sy + sb_h, R, fill=C["card"], outline=C["border"])
        my = sy + sb_h / 2
        pw, ph = min(sw_span * 0.25, max(rs * 6.8, 130)), rs * 1.1
        px0 = col_name - 6
        if GFX:
            s["pill_fn"] = lambda on: gfx.pill(pw, ph, mix(C["card"], C["green"] if on else C["red"], 0.18),
                                               C["green"] if on else C["red"], 6 + rs * 0.38, rs * 0.18)
            s["pill"] = cv.create_image(int(round(px0)), int(round(my - ph / 2)), image=s["pill_fn"](cfg["logging"]),
                                        anchor="nw", tags="logbtn")
        else:
            s["pill"] = round_rect(cv, px0, my - ph / 2, px0 + pw, my + ph / 2, rs * 0.52, fill=C["track"], outline="",
                                   tags="logbtn")
            s["dot"] = cv.create_oval(col_name + rs * 0.2, my - rs * 0.18, col_name + rs * 0.56, my + rs * 0.18,
                                      fill=C["red"], outline="", tags="logbtn")
        s["txt"] = cv.create_text(col_name + rs * 0.8, my, text="", anchor="w",
                                  font=(F_TXT, -max(10, int(rs * 0.50)), "bold"), fill=C["fg"], tags="logbtn")
        # recording mode switch: required rows <-> all sensors
        fs_sw = -max(9, int(rs * 0.38))
        f_sw = (F_TXT, fs_sw, "bold")
        sw_w, sw_h = max(rs * 1.7, 22.0), max(rs * 0.8, 10.0)      # never smaller than a track that still holds a knob
        lx = px0 + pw + min(sw_span * 0.05, rs * 1.2)
        s["sw_l"] = cv.create_text(lx, my, text=t("sw_required"), anchor="w", font=f_sw, fill=C["fg"],
                                   tags="modebtn")
        tx0 = lx + tkfont.Font(family=F_TXT, size=fs_sw, weight="bold").measure(t("sw_required")) + rs * 0.35
        kmy = my
        if GFX:                                  # whole pixels: the track image and the knob stay aligned
            tx0, sw_w, sw_h = float(round(tx0)), float(round(sw_w)), float(2 * round(sw_h / 2))
            kmy = round(my - sw_h / 2) + sw_h / 2
            s["track_fn"] = lambda all_: gfx.track(sw_w, sw_h, mix(C["track"], C["ui"], 0.55) if all_ else C["track"],
                                                   C["border"])
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
        s["sw_r"] = cv.create_text(tx0 + sw_w + rs * 0.35, my, text=t("sw_all"), anchor="w", font=f_sw,
                                   fill=C["faint"], tags="modebtn")
        # right end: [interval label] [spinbox] [reset button] [gear button]
        gs = max(rs * 0.42, 8.0)
        hit = gs * 1.45
        gx = sx1 - spad - hit
        rx = gx - hit * 2 - 4
        if GFX:
            s["gear_bg"] = cv.create_image(gx, my, image=gfx.button("gear", hit, gs, C["dim"], C["card"]),
                                           tags="gearbtn")
            s["reset_bg"] = cv.create_image(rx, my, image=gfx.button("reset", hit, gs, C["dim"], C["card"]),
                                            tags="resetbtn")
        else:
            s["gear_bg"] = cv.create_oval(gx - hit, my - hit, gx + hit, my + hit, fill=C["card"], outline="",
                                          tags="gearbtn")
            draw_icon(cv, "gear", gx, my, gs, C["dim"], C["card"], tags=("gearbtn",))
            s["reset_bg"] = cv.create_oval(rx - hit, my - hit, rx + hit, my + hit, fill=C["card"], outline="",
                                           tags="resetbtn")
            draw_icon(cv, "reset", rx, my, gs, C["dim"], C["card"], tags=("resetbtn",))
        spin_w = max(44.0, min(sw_span * 0.1, rs * 3.2))
        spin_r = rx - hit - 6
        cv.create_text(spin_r - spin_w - 8, my, text=t("interval_lbl"), anchor="e", font=(F_TXT, fs_sw),
                       fill=C["dim"], tags="intlbl")
        spin.config(font=(F_NUM, -max(10, int(rs * 0.52))))
        cv.create_window(spin_r, my, window=spin, anchor="e", width=spin_w, height=rs * 1.0)
        # toasts sit centred just above the bar
        ft = -max(10, int(rs * 0.42))
        s["toast_geo"] = ((sx0 + sx1) / 2, sy - max(4.0, rs * 0.25), sw_span - 2 * spad)
        s["f_toast"], s["f_toast_t"] = meas_font(F_TXT, ft, "bold"), (F_TXT, ft, "bold")
        for tag, fn in (("logbtn", toggle), ("modebtn", toggle_mode), ("gearbtn", open_settings),
                        ("resetbtn", reset_stats), ("updbtn", upd_click), ("updx", upd_dismiss)):
            cv.tag_bind(tag, "<Button-1>", fn)
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor=""))

        acc = themes.accent("gauge")

        def gear_hover(on):
            bgc = mix(C["card"], acc, 0.16) if on else C["card"]
            if GFX:
                cv.itemconfig(s["gear_bg"], image=gfx.button("gear", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("gear_fill", fill=acc if on else C["dim"])
            cv.itemconfig("gear_hole", fill=bgc)
            cv.itemconfig(s["gear_bg"], fill=bgc)

        def reset_hover(on):
            bgc = mix(C["card"], acc, 0.16) if on else C["card"]
            if GFX:
                cv.itemconfig(s["reset_bg"], image=gfx.button("reset", hit, gs, acc if on else C["dim"], bgc))
                return
            cv.itemconfig("reset_fill", fill=acc if on else C["dim"], outline=acc if on else C["dim"])
            cv.itemconfig(s["reset_bg"], fill=bgc)
        cv.tag_bind("resetbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), reset_hover(True)))
        cv.tag_bind("resetbtn", "<Leave>", lambda e: (cv.config(cursor=""), reset_hover(False)))
        cv.tag_bind("gearbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), gear_hover(True)))
        cv.tag_bind("gearbtn", "<Leave>", lambda e: (cv.config(cursor=""), gear_hover(False)))
        s["hover"] = (gear_hover, reset_hover)
        # hover help (after the binds above, which replace earlier <Enter> / <Leave> handlers of the same tags)
        for tag, key in (("hdr_now", "hdr_now"), ("hdr_min", "hdr_min"), ("hdr_max", "hdr_max"),
                         ("hdr_avg", "hdr_avg"), ("logbtn", "logbtn"), ("modebtn", "modebtn"),
                         ("resetbtn", "resetbtn"), ("gearbtn", "gearbtn"), ("intlbl", "interval_lbl")):
            help_tip(tag, key)
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
            if left <= 0:
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
        ap = st.get("applying")
        hint = current_hint()
        if ap and now - ap[0] < 12:
            msgs.append((t("applying", ms=ap[1]), C["dim"], None))
        elif st.get("frozen") and st["items"]:
            msgs.append((t("frozen", src=backend.display_name(src.active)) + (" · " + hint if hint else ""),
                         C["red"] if not st["ok"] else C["yellow"], None))
        elif not st["ok"] and not st["empty"]:          # (with no rows at all the hint is shown mid-window)
            msgs.append((hint or wait_text(), C["red"], None))
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
            tw = min(maxw - 2 * th, f.measure(text) + xw)
            text = fit_text(f, text, tw - xw)
            w = int(tw + th)
            x0, y0 = int(round(cx - w / 2)), int(round(y - th))
            fill, line = mix(C["card"], col, 0.22), mix(C["card"], col, 0.6)
            tags = ("toast", "updbtn") if kind == "upd" else ("toast",)
            if GFX:
                cv.create_image(x0, y0, image=gfx.card(w, int(th), th / 2, fill, line), anchor="nw", tags=tags)
            else:
                round_rect(cv, x0, y0, x0 + w, y0 + th, th / 2, fill=fill, outline=line, tags=tags)
            cv.create_text(x0 + th / 2, y0 + th / 2, text=text, anchor="w", font=s["f_toast_t"], fill=col, tags=tags)
            if kind == "upd":
                cv.create_text(x0 + w - th / 2, y0 + th / 2, text="✕", anchor="e", font=s["f_toast_t"], fill=C["dim"],
                               tags=("toast", "updx"))
            y -= th + 6

    def refresh_status():
        s = st["status"]
        if not s:
            return
        on = cfg["logging"]
        all_ = cfg.get("log_all", False)
        if s["gfx"]:
            if s.get("pill_on") != on:
                s["pill_on"] = on
                cv.itemconfig(s["pill"], image=s["pill_fn"](on))
            if s.get("track_all") != all_:
                s["track_all"] = all_
                cv.itemconfig(s["sw_track"], image=s["track_fn"](all_))
        else:
            cv.itemconfig(s["pill"], fill=mix(C["card"], C["green"] if on else C["red"], 0.18))
            cv.itemconfig(s["dot"], fill=C["green"] if on else C["red"])
            cv.itemconfig(s["sw_track"], fill=mix(C["track"], C["ui"], 0.55) if all_ else C["track"])
        sk = (on, all_, i18n._lang)
        if s.get("static_key") != sk:
            s["static_key"] = sk
            cv.itemconfig(s["txt"], text=t("log_on") if on else t("log_off"))
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
        if s:
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
        if root.focus_get() is not spin:
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
        if root.focus_get() is not spin:
            logview.show(api)

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
    cv.bind("<Button-1>", lambda e: cv.focus_set(), add="+")
    root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
    root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

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
            if st["size"]:
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
        cfg.update(real)
        keys = list(real)
        need_build = bool({"columns", "ui_scale", "language"} & set(real))
        if "language" in real:
            i18n.set_language(cfg["language"])
            if st["auto"]:
                st["auto_n"] = -1          # titles / names of the detected layout are language dependent
                rd = read_now()
                if rd:
                    ensure_layout(rd)
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
        if "topmost" in real:
            root.attributes("-topmost", bool(cfg["topmost"]))
        if "check_updates" in real:
            upd_reschedule(1000)
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
        if "log_all" in real and st["status"]:
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
                                detect=hwctl.detect_hwinfo, monitors=config.monitors, source=src)

    procs = procwin.Controller(api)
    api.procs = procs

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
                        on_close()                      # the Setup replaces the files: leave cleanly
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
        root.after(300, upd_pump)

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
        if ok:
            st["down_since"] = None
        elif manage_on():
            # e.g. the 12 h limit of the free version: no connection for 20 s -> restart HWiNFO (at most every 5 min)
            t0 = st.get("down_since") or now
            st["down_since"] = t0
            # shared memory switched off in HWiNFO: fix it after 5 s instead of waiting for the generic 20 s
            wait = 5 if (st.get("hw_exe") and not hwctl.sm_enabled(st["hw_exe"])) else 20
            if now - t0 > wait and now - st.get("last_restart", 0) > 300:
                st["last_restart"] = now
                if st.get("hw_exe") and hwctl.tasks_installed():
                    hwctl.repair_hwinfo(cfg["interval"], st["hw_exe"])   # restart + SensorsSM=1
                else:
                    hwctl.run_task(hwctl.TASK_START)
        rows = layout.all_rows(st["sections"])
        st["nall"] = len(rd) if ok else st.get("nall", 0)
        st["nreq"] = len(rows)
        logvals = []
        for row in rows:
            r = row.match(rd) if ok else None
            logvals.append(logger.num(r["value"], row.csv_decimals()) if r else "")
            it = st["items"].get(id(row))
            if not it or frozen:
                continue        # frozen: last values stay on screen, stats / bars do not move
            if r:
                v = r["value"]
                s = stats.setdefault(row.key, [v, v, 0.0, 0])
                s[0], s[1], s[2], s[3] = min(s[0], v), max(s[1], v), s[2] + v, s[3] + 1
                avg = s[2] / s[3]
                col = layout.value_color(row, v, C["fg"])
                set_text(it["unit"], row.unit_eff)
                set_text(it["now"], row.fmt_value(v), col)
                for k, val in (("min", s[0]), ("max", s[1]), ("avg", avg)):
                    set_text(it[k], row.fmt_value(val), mix(layout.value_color(row, val, C["fg"]), C["card"], 0.18))
                if "bar" in it:
                    x0, x1, y0, y1 = it["bargeo"]
                    cv.coords(it["bar"], x0, y0, x0 + (x1 - x0) * layout.frac(row, v), y1)
                    cv.itemconfig(it["bar"], fill=col)
        logger.ext.update()                 # process sampling runs only while the recording needs it
        if not frozen and ok and cfg["logging"] and rows and now - logger.last >= cfg["interval"] - 0.05:
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
        root.after(500, tick)

    def on_close():
        st["closing"] = True
        procs.close()
        logger.ext.shutdown()
        logger.close()
        src.close()
        if bool(cfg["manage_hwinfo"]) and st["tasks"] and (hw_active() or st.get("hw_used")):
            hwctl.run_task(hwctl.TASK_STOP)
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    st["last_restart"] = time.time()
    st["hw_used"] = hw_active()
    if sync_on() and hwctl.apply_hwinfo_interval(cfg["interval"], st["hw_exe"]):
        pass                                          # the task adjusted the interval and (re)started HWiNFO
    elif manage_on() and not hwctl.proc_running():
        hwctl.run_task(hwctl.TASK_START)
    # when opened from a shortcut Windows puts the window on the clicked screen: move it, then maximise
    root.after(200, lambda: place(final=True))
    root.after(400, tick)
    root.after(300, upd_pump)
    upd_reschedule(5000)
    root.mainloop()


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
