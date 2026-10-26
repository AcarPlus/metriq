"""HWiNFO Panel: a tkinter sensor dashboard (HWiNFO64, LibreHardwareMonitor or built-in Windows sensors, see README)."""
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
import hwctl
import i18n
import layout
import settings_ui
import updater
from i18n import t

HERE = config.APP_DIR        # source folder, or the bundled resources when frozen (PyInstaller)
ICO = os.path.join(HERE, "panel.ico")


# ---------------------------------------------------------------- CSV logger
class Logger:
    """Every recording session (on -> off) goes to its own CSV file."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.f = self.w = self.path = None
        self.last = 0
        self.delim, self.dec = config.csv_format(cfg)

    def num(self, v, dec):
        """Locale friendly number, e.g. 64.555 -> '64,6' when the decimal symbol is a comma."""
        return f"{v:.{dec}f}".replace(".", self.dec)

    def close(self):
        if self.f:
            self.f.close()
        self.f = self.w = self.path = None

    def write(self, stamp, header, values):
        if self.f is None:
            d = config.log_dir(self.cfg)
            os.makedirs(d, exist_ok=True)
            self.path = os.path.join(d, stamp.replace(" ", "_").replace(":", "-") + ".csv")
            self.f = open(self.path, "a", newline="", encoding="utf-8-sig")
            self.w = csv.writer(self.f, delimiter=self.delim)
            self.w.writerow([t("csv_time")] + header)
        self.w.writerow([stamp] + values)
        self.f.flush()


# ---------------------------------------------------------------- design
C = dict(bg="#0b0f14", card="#121820", stripe="#151c25", border="#1f2833", track="#1e2630",
         fg="#f0f6fc", name="#c9d1d9", dim="#9198a1", faint="#6e7681",
         green="#3fb950", yellow="#e3b341", orange="#f0883e", red="#f85149")


def mix(c1, c2, tt):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * tt) for x, y in zip(a, b))


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


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

    F_TXT = pick_font("Segoe UI Variable Text", "Segoe UI")
    F_HEAD = pick_font("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI")
    F_NUM = pick_font("Cascadia Mono", "Consolas")
    cv = tk.Canvas(root, bg=C["bg"], highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)

    logger = Logger(cfg)
    stats = {}          # row.key -> [min, max, sum, count]; reset at panel start and when recording starts
    st = {"items": {}, "last": {}, "status": {}, "size": None, "ok": False, "sections": [], "sections_cfg": [],
          "auto": True, "auto_n": -1, "checked": False, "empty": None, "note": note, "settings": None,
          "hw_exe": hwctl.detect_hwinfo(cfg["hwinfo_path"]), "tasks": hwctl.tasks_installed(),
          "mon": config.pick_monitor(cfg["monitor"]), "mode": str(cfg["window_mode"]).lower(),
          "upd": None, "upd_busy": False, "upd_prog": 0, "upd_err": 0, "upd_gen": 0, "upd_after": None,
          "closing": False}
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

    def current_hint():
        """Why there is no data (empty when everything is fine)."""
        if st["ok"]:
            return ""
        if src.active == "lhm":
            return t("hint_lhm")
        if not hw_active():
            return ""
        h = t("hint_no_shm") if hwctl.proc_running() else t("hint_not_running")
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
        st["items"].clear()
        st["last"].clear()
        st["empty"] = None
        sections = st["sections"]
        HEAD, FOOT, STATUS = layout.HEAD, layout.FOOT, layout.STATUS    # in row heights
        p = layout.plan(sections, W, H, cfg["columns"], cfg["ui_scale"])
        rh, m, g = p.rh, p.m, p.g
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
                accent = sec.color
                hh, pb = rh * HEAD, rh * FOOT
                ch = hh + len(sec.rows) * rh + pb
                round_rect(cv, cx0, yy, cx1, yy + ch, 16, fill=C["card"], outline=C["border"])
                cv.create_line(cx0 + 1.5, yy + 16, cx0 + 1.5, yy + ch - 16, fill=accent, width=3)
                hy = yy + hh * 0.5
                bubble = mix(C["card"], accent, 0.16)
                cv.create_oval(col_name, hy - isz * 1.6, col_name + isz * 3.2, hy + isz * 1.6, fill=bubble, outline="")
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
                for lbl, xx in ((t("col_now"), col_now + 6 + unit_w), (t("col_min"), col_min),
                                (t("col_max"), col_max), (t("col_avg"), col_avg)):
                    cv.create_text(xx, hy, text=lbl, anchor="e", font=f_col, fill=mix(C["dim"], accent, 0.45))
                cv.create_line(cx0 + pad * 0.6, yy + hh - 1, cx1 - pad * 0.6, yy + hh - 1, fill=C["border"])
                ry = yy + hh
                for i, row in enumerate(sec.rows):
                    if i % 2:
                        cv.create_rectangle(cx0 + 6, ry, cx1 - 6, ry + rh, fill=C["stripe"], outline="")
                    my = ry + rh / 2
                    cv.create_text(col_name, my, text=fit_text(fnt_name, row.name, name_avail), anchor="w",
                                   font=f_name, fill=C["name"])
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
                    ry += rh
                yy += ch + g
        # ----- status bar (full width, 2 lines: controls + info)
        sx0, sx1 = m, W - m
        sw_span = sx1 - sx0
        rs = min(rh, max(layout.MIN_RH, sw_span * 0.042))      # its text must not grow with a huge row height
        spad = min(sw_span * 0.035, rs * 1.0)
        col_name = sx0 + spad
        sb_h = rs * (STATUS - 0.2)
        sy = H - m - sb_h
        round_rect(cv, sx0, sy, sx1, sy + sb_h, 16, fill=C["card"], outline=C["border"])
        my = sy + sb_h * 0.36
        iy = sy + sb_h * 0.76
        pw = min(sw_span * 0.25, max(rs * 6.8, 130))
        s = st["status"]
        s["pill"] = round_rect(cv, col_name - 6, my - rs * 0.55, col_name - 6 + pw, my + rs * 0.55, rs * 0.52,
                               fill=C["track"], outline="", tags="logbtn")
        s["dot"] = cv.create_oval(col_name + rs * 0.2, my - rs * 0.18, col_name + rs * 0.56, my + rs * 0.18,
                                  fill=C["red"], outline="", tags="logbtn")
        s["txt"] = cv.create_text(col_name + rs * 0.8, my, text="", anchor="w",
                                  font=(F_TXT, -max(10, int(rs * 0.50)), "bold"), fill=C["fg"], tags="logbtn")
        # recording mode switch: required rows <-> all sensors
        fs_sw = -max(9, int(rs * 0.38))
        f_sw = (F_TXT, fs_sw, "bold")
        sw_w, sw_h = rs * 1.7, rs * 0.8
        lx = col_name - 6 + pw + min(sw_span * 0.05, rs * 1.2)
        s["sw_l"] = cv.create_text(lx, my, text=t("sw_required"), anchor="w", font=f_sw, fill=C["fg"],
                                   tags="modebtn")
        tx0 = lx + tkfont.Font(family=F_TXT, size=fs_sw, weight="bold").measure(t("sw_required")) + rs * 0.35
        s["sw_track"] = round_rect(cv, tx0, my - sw_h / 2, tx0 + sw_w, my + sw_h / 2, sw_h / 2,
                                   fill=C["track"], outline=C["border"], tags="modebtn")
        kr = sw_h / 2 - 3
        s["sw_geo"] = (tx0 + 3 + kr, tx0 + sw_w - 3 - kr, my, kr)
        kx = s["sw_geo"][1] if cfg.get("log_all") else s["sw_geo"][0]
        s["sw_knob"] = cv.create_oval(kx - kr, my - kr, kx + kr, my + kr, fill=C["fg"], outline="", tags="modebtn")
        s["sw_r"] = cv.create_text(tx0 + sw_w + rs * 0.35, my, text=t("sw_all"), anchor="w", font=f_sw,
                                   fill=C["faint"], tags="modebtn")
        # right end: [interval label] [spinbox] [gear button]
        gs = max(rs * 0.42, 8.0)
        hit = gs * 1.45
        gx = sx1 - spad - hit
        s["gear_bg"] = cv.create_oval(gx - hit, my - hit, gx + hit, my + hit, fill=C["card"], outline="",
                                      tags="gearbtn")
        draw_icon(cv, "gear", gx, my, gs, C["dim"], C["card"], tags=("gearbtn",))
        # reset MIN / MAX / AVG button, left of the gear
        rx = gx - hit * 2 - 4
        s["reset_bg"] = cv.create_oval(rx - hit, my - hit, rx + hit, my + hit, fill=C["card"], outline="",
                                       tags="resetbtn")
        draw_icon(cv, "reset", rx, my, gs, C["dim"], C["card"], tags=("resetbtn",))
        spin_w = max(44.0, min(sw_span * 0.1, rs * 3.2))
        spin_r = rx - hit - 6
        cv.create_text(spin_r - spin_w - 8, my, text=t("interval_lbl"), anchor="e", font=(F_TXT, fs_sw),
                       fill=C["dim"])
        spin.config(font=(F_NUM, -max(10, int(rs * 0.52))))
        cv.create_window(spin_r, my, window=spin, anchor="e", width=spin_w, height=rs * 1.0)
        s["info"] = cv.create_text(col_name, iy, text="", anchor="w", font=(F_TXT, fs_sw), fill=C["dim"])
        # update notice (right end of the info line): clickable when a newer release was found
        s["upd"] = cv.create_text(sx1 - spad, iy, text="", anchor="e", font=(F_TXT, fs_sw, "bold"),
                                  fill=layout.ICON_COLORS["gauge"], state="hidden", tags="updbtn")
        s["f_info"] = meas_font(F_TXT, fs_sw)
        s["f_upd"] = meas_font(F_TXT, fs_sw, "bold")
        s["info_w"] = (sx1 - spad) - col_name
        for tag, fn in (("logbtn", toggle), ("modebtn", toggle_mode), ("gearbtn", open_settings),
                        ("resetbtn", reset_stats), ("updbtn", upd_click)):
            cv.tag_bind(tag, "<Button-1>", fn)
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor=""))

        def gear_hover(on):
            acc = layout.ICON_COLORS["gauge"]
            bgc = mix(C["card"], acc, 0.16) if on else C["card"]
            cv.itemconfig("gear_fill", fill=acc if on else C["dim"])
            cv.itemconfig("gear_hole", fill=bgc)
            cv.itemconfig(s["gear_bg"], fill=bgc)
        def reset_hover(on):
            acc = layout.ICON_COLORS["gauge"]
            cv.itemconfig("reset_fill", fill=acc if on else C["dim"], outline=acc if on else C["dim"])
            cv.itemconfig(s["reset_bg"], fill=mix(C["card"], acc, 0.16) if on else C["card"])
        cv.tag_bind("resetbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), reset_hover(True)))
        cv.tag_bind("resetbtn", "<Leave>", lambda e: (cv.config(cursor=""), reset_hover(False)))
        cv.tag_bind("gearbtn", "<Enter>", lambda e: (cv.config(cursor="hand2"), gear_hover(True)))
        cv.tag_bind("gearbtn", "<Leave>", lambda e: (cv.config(cursor=""), gear_hover(False)))
        refresh_status()

    def set_text(iid, txt, color=None):
        key = (txt, color)
        if st["last"].get(iid) == key:
            return
        st["last"][iid] = key
        if color:
            cv.itemconfig(iid, text=txt, fill=color)
        else:
            cv.itemconfig(iid, text=txt)

    def refresh_status():
        s = st["status"]
        if not s:
            return
        on = cfg["logging"]
        all_ = cfg.get("log_all", False)
        cv.itemconfig(s["pill"], fill=mix(C["card"], C["green"] if on else C["red"], 0.18))
        cv.itemconfig(s["dot"], fill=C["green"] if on else C["red"])
        cv.itemconfig(s["txt"], text=t("log_on") if on else t("log_off"))
        cv.itemconfig(s["sw_track"], fill=mix(C["track"], "#58a6ff", 0.55) if all_ else C["track"])
        cv.itemconfig(s["sw_l"], fill=C["faint"] if all_ else C["fg"])
        cv.itemconfig(s["sw_r"], fill=C["fg"] if all_ else C["faint"])
        hint = current_hint()
        conn = conn_text() if st["ok"] else (hint or wait_text())
        if st.get("frozen") and st["items"]:
            conn = t("frozen", src=backend.display_name(src.active)) + (" · " + hint if hint else "")
        ap = st.get("applying")
        if ap and time.time() - ap[0] < 12:
            conn = t("applying", ms=ap[1])
        if on:
            f = os.path.basename(logger.path) if logger.path else t("file_wait")
        else:
            f = t("file_hint")
        mode_txt = t("mode_all", n=st.get("nall", 0)) if all_ else t("mode_req", n=st.get("nreq", 0))
        info = f"{time.strftime('%H:%M:%S')}   ·   {conn}   ·   {mode_txt}   ·   {f}"
        if st.get("note"):
            info = st["note"] + "   ·   " + info
        if time.time() - st.get("reset_note", 0) < 3:
            info = "✓ " + t("reset_done") + "   ·   " + info
        utxt, ucol, uclick = upd_state()
        if utxt:
            info = fit_text(s["f_info"], info, s["info_w"] - s["f_upd"].measure(utxt) - 24)
            cv.itemconfig(s["upd"], text=utxt, fill=ucol, state="normal")
            cv.itemconfig(s["upd"], tags=("updbtn",) if uclick else ())
        else:
            cv.itemconfig(s["upd"], state="hidden")
        cv.itemconfig(s["info"], text=info, fill=C["dim"] if st["ok"] else C["red"])
        if st["empty"]:
            cv.itemconfig(st["empty"], text=t("hint_empty") + ("\n\n" + hint if hint else ""))

    def animate_knob(target_x, steps=8):
        s = st["status"]
        x0, x1, my, kr = s["sw_geo"]
        cur = cv.coords(s["sw_knob"])
        if not cur:
            return
        cx = (cur[0] + cur[2]) / 2
        if steps <= 1 or abs(target_x - cx) < 0.5:
            cv.coords(s["sw_knob"], target_x - kr, my - kr, target_x + kr, my + kr)
            return
        nx = cx + (target_x - cx) * 0.45
        cv.coords(s["sw_knob"], nx - kr, my - kr, nx + kr, my + kr)
        root.after(16, lambda: animate_knob(target_x, steps - 1))

    def toggle_mode(e=None):
        cfg["log_all"] = not cfg.get("log_all", False)
        logger.close()          # new file when the mode changes
        logger.last = 0
        config.save_keys(cfg, "log_all")
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
        config.save_keys(cfg, "logging")
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
        config.save_keys(cfg, "interval")
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

    spin.config(command=set_interval)
    spin.bind("<Return>", lambda e: (set_interval(), cv.focus_set()))
    spin.bind("<FocusOut>", set_interval)
    root.bind("<KeyPress-l>", key_toggle)
    root.bind("<KeyPress-L>", key_toggle)
    root.bind("<KeyPress-r>", key_reset)
    root.bind("<KeyPress-R>", key_reset)
    root.bind("<KeyPress-s>", key_settings)
    root.bind("<KeyPress-S>", key_settings)
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
        if st["auto"] and len(rd) != st["auto_n"]:
            st["auto_n"] = len(rd)
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
        if {"log_dir", "csv_delimiter", "csv_decimal", "log_all", "logging"} & set(real):
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

    api = types.SimpleNamespace(root=root, cfg=cfg, C=C, fonts=(F_TXT, F_HEAD, F_NUM), apply=apply_settings, layout=layout_snapshot, readings=read_now,
                                monitor=panel_monitor, refresh_tasks=refresh_tasks,
                                detect=hwctl.detect_hwinfo, monitors=config.monitors, source=src)

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
            return t("upd_downloading", p=st["upd_prog"]), layout.ICON_COLORS["gauge"], False
        if time.time() - st["upd_err"] < 5:
            return t("upd_failed"), C["red"], False
        if info and cfg.get("check_updates", True):
            return t("upd_available", v=info["version"]), layout.ICON_COLORS["gauge"], True
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
        if not frozen and ok and cfg["logging"] and rows and now - logger.last >= cfg["interval"] - 0.05:
            logger.last = now
            try:
                if cfg.get("log_all"):
                    hdr = [f"{r['sensor']} | {r['label']} [{r['unit']}]" for r in rd]
                    vals = [logger.num(r["value"], 3) for r in rd]
                else:
                    hdr = [f"{r.name} ({r.unit_eff})" for r in rows]
                    vals = logvals
                logger.write(time.strftime("%Y-%m-%d %H:%M:%S"), hdr, vals)
            except Exception:
                pass
        refresh_status()
        root.after(500, tick)

    def on_close():
        st["closing"] = True
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
