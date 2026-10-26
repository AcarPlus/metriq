"""Process window: which program uses the CPU / RAM / GPU / VRAM / disk, and which ones go into the CSV.

Opened from the panel by clicking the name of a CPU / RAM / GPU / VRAM / disk row (``Controller.tag_row``); the
(i) icon next to the CPU / GPU / RAM / VRAM rows opens a hover balloon with the top programs instead.
Sampling (procmon.MON) runs only while the window or a balloon is open.

What the lists add up to (so the numbers can be compared with the panel):
    RAM   every program's private working set + "Windows kernel & drivers" = RAM in use (total - available,
          the panel's own definition). The kernel row is what no program owns: kernel / driver memory, paged and
          non-paged pool, page tables, modified file cache, memory shared between programs.
    VRAM  per-program dedicated usage (the adapter's remainder is shown on the same kernel row when positive).
    Programs below the "hide below" limits are not listed one by one but summed in "Other programs (hidden)",
    so the sums of the three groups are always complete.

The list is a canvas (cards per group, inline bars, pill toggles) drawn in the panel's theme. It stays calm: the
order only changes on a header click or after the pointer has rested outside the list.
"""
import ctypes
import os
import time
import tkinter as tk
import tkinter.font as tkfont

import config
import i18n
import procgfx
import procmon
import proclog                                   # also registers the texts
import themes
import uikit
from i18n import t
from themes import mix
from tooltip import Tooltip

CATS = ("apps", "background", "windows")
COLS = ("cpu", "ram", "gpu", "vram", "disk")
HEAD = {"cpu": "CPU %", "ram": "RAM MB", "gpu": "GPU %", "vram": "VRAM MB", "disk": "Disk MB/s"}
DEC = {"cpu": 1, "ram": 0, "gpu": 1, "vram": 0, "disk": 2}
MCOL = {"cpu": "accent_clock", "ram": "accent_net", "gpu": "accent_power", "vram": "accent_fan", "disk": "accent_temp"}
CCOL = {"apps": "accent_clock", "background": "accent_net", "windows": "accent_fan"}
FLOOR = {"cpu": 5.0, "ram": 512.0, "gpu": 5.0, "vram": 256.0, "disk": 1.0}       # bar scale: at least this full
UNIT = {"cpu": "%", "ram": "MB", "gpu": "%", "vram": "MB", "disk": "MB/s"}
STILL = 3.0                                      # s the order stays put after the pointer left the list
STILL_TOGGLE = 5.0                               # s the order stays put after a log toggle
# a program is listed when ANY of its numbers reaches its limit (0 = that limit is off)
DEFAULT_MIN = {"cpu": 0.5, "ram": 100.0, "gpu": 0.5, "vram": 100.0, "disk": 0.1}
_STATE = {"min": dict(DEFAULT_MIN), "show_all": False}              # limits kept while the panel runs

# which panel rows open the window: sample reading labels of each metric (the row's regex is tested against them)
_ROW_LABELS = (
    ("cpu", ("Total CPU Usage", "CPU Total")),
    ("gpu", ("GPU Core Load", "GPU Utilization", "GPU Load")),
    ("vram", ("GPU D3D Memory Dedicated", "GPU Memory Allocated", "GPU Memory Used")),
    ("ram", ("Physical Memory Used", "Memory Used")),
)
_INFO = ("cpu", "gpu", "ram", "vram")            # rows that get the (i) icon


def metric_for_row(row):
    """'cpu' | 'ram' | 'gpu' | 'vram' | 'disk' when the panel row shows that system-wide value, else None."""
    try:
        sensor_ok = lambda s: row.sre is None or row.sre.search(s)
        for metric, samples in _ROW_LABELS:
            if any(row.lre.search(s) for s in samples):
                if metric in ("ram",) and not sensor_ok("System:"):
                    continue
                return metric
        if row.sre is not None and "Drive" in row.sre.pattern and any(row.lre.search(s) for s in
                                                                         ("Read Rate", "Write Rate")):
            return "disk"
    except Exception:
        pass
    return None


def total_ram_mb():
    return procmon.memory_mb()[0] or 16384.0


def _dec(s):
    return s.replace(".", ",") if i18n.language() == "tr" else s


def fmt_mem(mb):
    return _dec(f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB")


def fmt_val(metric, v):
    if metric in ("cpu", "gpu"):
        return _dec(f"{v:.1f} %")
    if metric == "disk":
        return _dec(f"{v:.2f} MB/s")
    return fmt_mem(v)


def _num(metric, v):
    s = f"{v:,.{DEC[metric]}f}".replace(",", " ")
    return _dec(s)


def popup_top(cfg):
    try:
        return max(1, min(10, int(float(cfg.get("usage_popup_top", 1)))))
    except (TypeError, ValueError):
        return 1


# ============================================================== panel side: balloons + row hook
class UsageTip(Tooltip):
    """Hover balloon with the top programs of one metric. Sampling runs only while the pointer is on the icon."""

    def __init__(self, cv, tag, metric, ctl):
        super().__init__(cv, self._rows, live_ms=1000, _bind=False)
        self.metric, self.ctl, self.tag = metric, ctl, tag
        self.owner = "tip:" + metric
        self.held = False
        self._wd = None
        cv.tag_bind(tag, "<Enter>", self._enter, add="+")
        cv.tag_bind(tag, "<Leave>", self._leave, add="+")
        cv.tag_bind(tag, "<Motion>", self._motion, add="+")

    def _enter(self, e):
        if not self.held:
            self.held = True
            procmon.MON.acquire(self.owner, 1.0)
        self._watch_start()
        super()._enter(e)

    def _leave(self, e=None):
        super()._leave(e)
        self._release()

    def _release(self):
        if self.held:
            self.held = False
            procmon.MON.release(self.owner)
        if self._wd:
            try:
                self.w.after_cancel(self._wd)
            except tk.TclError:
                pass
            self._wd = None

    def _watch_start(self):
        if self._wd is None:
            self._wd = self.w.after(1000, self._watch)

    def _watch(self):
        """The panel redraws its canvas now and then: the item under the pointer may be gone without a <Leave>."""
        self._wd = None
        try:
            gone = self.w.type(self.tag) is None
        except tk.TclError:
            gone = True
        if gone:
            self.hide()
            self._release()
        elif self.held:
            self._watch_start()

    def _rows(self):
        snap = procmon.MON.snapshot()
        m = self.metric
        head = (t("pw_top_" + m), "", "head")
        if snap is None or (m == "cpu" and not snap.warm):
            return [head, (t("pw_wait"), "")]
        if m in ("gpu", "vram") and not snap.gpu_ok:
            return [head, (t("pw_nogpu"), "")]
        top = snap.top(m, popup_top(self.ctl.api.cfg))
        if not top:
            return [head, (t("pw_none"), "")]
        out = [head]
        for i, g in enumerate(top):
            name = g.name + (f"  ×{len(g.procs)}" if len(g.procs) > 1 else "")
            out.append(((f"{i + 1}.  " if len(top) > 1 else "") + name, fmt_val(m, g.value(m))))
        return out


class Controller:
    """Owned by the panel: creates the window on demand and wires the panel rows to it."""

    def __init__(self, api):
        self.api = api
        self.win = None
        self.tips = []

    def tag_row(self, cv, item, row, limit=None):
        """Panel hook (called for every row name): makes CPU / RAM / GPU / VRAM / disk rows open the window and
        adds the (i) balloon icon right behind the name when there is room (limit = right edge in canvas px)."""
        metric = metric_for_row(row)
        if not metric:
            return
        cv.tag_bind(item, "<Button-1>", lambda e, m=metric: self.open(m))
        cv.tag_bind(item, "<Enter>", lambda e: cv.config(cursor="hand2"))
        cv.tag_bind(item, "<Leave>", lambda e: cv.config(cursor=""))
        if metric in _INFO and procgfx.AVAILABLE:
            try:
                self._info_icon(cv, item, metric, limit)
            except Exception:
                pass

    def _info_icon(self, cv, item, metric, limit):
        x0, y0, x1, y1 = cv.bbox(item)
        d = max(12, int(round((y1 - y0) * 0.74)))
        cx = x1 + 6 + d / 2
        if limit is not None and cx + d / 2 > limit:
            return
        C = self.api.C
        norm = procgfx.info(d, mix(C["card"], C["green"], 0.22), C["green"], mix(C["card"], C["green"], 0.55))
        hot = procgfx.info(d, C["green"], C["card"])
        icon = cv.create_image(cx, (y0 + y1) / 2, image=norm, tags="uinfo")
        cv.tag_bind(icon, "<Enter>", lambda e: (cv.itemconfig(icon, image=hot), cv.config(cursor="hand2")), add="+")
        cv.tag_bind(icon, "<Leave>", lambda e: (cv.itemconfig(icon, image=norm), cv.config(cursor="")), add="+")
        cv.tag_bind(icon, "<Button-1>", lambda e, m=metric: self.open(m), add="+")
        cv._uinfo_imgs = getattr(cv, "_uinfo_imgs", ())[-8:] + (norm, hot)          # keep the pictures alive
        self.tips.append(UsageTip(cv, icon, metric, self))
        self.tips = self.tips[-24:]

    def open(self, metric=None):
        w = self.win
        try:
            if w is not None and w.alive():
                if metric:
                    w.sort_by(metric, True)
                w.show()
                return w
        except tk.TclError:
            pass
        self.win = ProcWindow(self.api, metric or "cpu")
        return self.win

    def close(self):
        if self.win is not None:
            self.win.close()
            self.win = None


class _Ghost(procmon.Group):
    """A program that disappeared while the list was held still: stays as an empty row until the list moves."""


# ============================================================== the window
class ProcWindow:
    def __init__(self, api, metric):
        self.api, self.root, self.C = api, api.root, api.C
        self.cfg = api.cfg
        self.sort = (metric, True)                       # (column or "name" or "log", descending)
        self.order = {}                                  # cat -> [group keys] as listed (kept while the list rests)
        self.child_order = {}                            # group key -> [pids]
        self.names = {}                                  # group key -> display name (for ghosts)
        self.expanded, self.collapsed = set(), set()
        self.selected = None
        self.model = []                                  # per category: dict(cat, rows, ...)
        self.rows, self.blocks = [], []                  # laid out rows / card blocks (content coordinates)
        self.content_h = 0
        self.top = 0
        self.hover = None                                # row index under the pointer
        self.hover_part = ""
        self.inside = False
        self.left_at = self.toggled_at = -1e9
        self.was_frozen = False
        self.resort = True
        self.snap = None
        self.last_t = None
        self.scale = dict(FLOOR)
        self.disp, self.tgt = {}, {}
        self.anim_id = None
        self.drag = None
        self.tip_id = None
        self.tip_text = None
        filt = proclog.filter_cfg(self.cfg)
        self.all = filt == "all"
        self.picked = set() if self.all else set(filt)
        self.after_id = None
        self.period = 1.0
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        uikit.set_scale(self.win)
        self.win.title(f"{config.APP_NAME} – {t('pw_title')}")
        self.win.configure(bg=self.C["bg"])
        try:
            self.win.iconbitmap(default=os.path.join(config.APP_DIR, "metriq.ico"))
        except Exception:
            pass
        try:
            self.win.attributes("-topmost", bool(self.root.attributes("-topmost")))
        except tk.TclError:
            pass
        self._fonts()
        self._build()
        self._place()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Control-f>", lambda e: (self.e_search.focus_set(), "break")[1])
        self.win.bind("<MouseWheel>", self._wheel)
        procmon.MON.acquire("procwin", self.period)
        self.win.deiconify()
        self._dark_title()
        self.tick()

    # ------------------------------------------------------------ plumbing
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def show(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def close(self):
        procmon.MON.release("procwin")
        for attr in ("after_id", "anim_id"):
            i = getattr(self, attr, None)
            if i:
                try:
                    self.win.after_cancel(i)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        try:
            self.tt.hide()
        except Exception:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _dark_title(self):
        try:
            hwnd = int(self.win.wm_frame(), 16)
            val = ctypes.c_int(1 if themes.luminance(self.C["bg"]) < 0.3 else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        except Exception:
            pass

    def _place(self):
        mon = None
        try:
            mon = self.api.monitor()
        except Exception:
            pass
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        mx, my, mw, mh = (mon["wx"], mon["wy"], mon["ww"], mon["wh"]) if mon else (0, 0, sw, sh)
        w, h = min(1080, mw - 40), min(720, mh - 60)
        self.win.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
        self.win.minsize(min(1040, w), min(380, h))

    def _theme_sig(self):
        C = self.C
        return tuple(C[k] for k in ("bg", "card", "text", "ui", "green", "border", "track", "accent_clock"))

    def _fonts(self):
        try:
            self.F_TXT, self.F_HEAD, self.F_NUM = self.api.fonts_now()
        except Exception:
            self.F_TXT, self.F_HEAD, self.F_NUM = self.api.fonts
        w = self.win
        mk = lambda fam, size, weight="normal": tkfont.Font(root=w, family=fam, size=size, weight=weight)
        self.f_txt, self.f_bold = mk(self.F_TXT, 10), mk(self.F_TXT, 10, "bold")
        self.f_small, self.f_smallb = mk(self.F_TXT, 9), mk(self.F_TXT, 9, "bold")
        self.f_num, self.f_numb = mk(self.F_NUM, 10), mk(self.F_NUM, 10, "bold")
        self.f_head = mk(self.F_HEAD, 11, "bold")
        lh = self.f_txt.metrics("linespace")
        self.rh = max(34, lh + 18)
        self.cat_h = max(42, lh + 26)
        self.hdr_h = max(32, lh + 14)
        self.mcw = max(88, self.f_num.measure("88 888.88") + 28)
        self.logw = 86
        self.pad = 6

    # ------------------------------------------------------------ widgets
    def _boxed(self, parent, var, width, justify="left", icon=None, hint=None, size=10, height=None):
        """Rounded input field (same look as the settings window); returns (field, entry)."""
        C = self.C
        f = uikit.entry(parent, C, var, width, (self.F_TXT, size), justify=justify, icon=icon, height=height)
        e = f.entry
        if hint:                                          # placeholder text while empty
            ph = tk.Label(f, text=hint, bg=f.fill, fg=C["faint"], font=(self.F_TXT, size), bd=0, cursor="xterm")
            ph.place(in_=e, x=1, rely=0.5, anchor="w")
            ph.bind("<Button-1>", lambda ev: e.focus_set())
            var.trace_add("write", lambda *a: ph.place_forget() if var.get() else
                          ph.place(in_=e, x=1, rely=0.5, anchor="w"))
        return f, e

    def _build(self):
        C = self.C
        self.sig = self._theme_sig()
        self.outer = tk.Frame(self.win, bg=C["bg"])
        self.outer.pack(fill="both", expand=True, padx=16, pady=(14, 8))
        bar = tk.Frame(self.outer, bg=C["bg"])
        bar.pack(fill="x")
        # --- search
        self.v_search = tk.StringVar()
        box, self.e_search = self._boxed(bar, self.v_search, 26, icon=procgfx.magnifier(15, C["dim"]),
                                         hint=t("pw_search_ph"), height=uikit.px(34))
        box.pack(side="left")
        self.v_search.trace_add("write", lambda *a: self.refresh(force=True, resort=True))
        # --- log everything
        self.v_all = tk.BooleanVar(value=self.all)
        self.sw_all = tk.Canvas(bar, width=40, height=22, bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.sw_all.pack(side="left", padx=(22, 0))
        self.sw_all.bind("<Button-1>", lambda e: self._all_toggled())
        self.lbl_all = tk.Label(bar, text=t("pw_all"), bg=C["bg"], fg=C["fg"], font=self.f_txt, cursor="hand2")
        self.lbl_all.pack(side="left", padx=(8, 0))
        self.lbl_all.bind("<Button-1>", lambda e: self._all_toggled())
        Tooltip(self.sw_all, t("pw_all_tip"))
        Tooltip(self.lbl_all, t("pw_all_tip"))
        self._draw_all_switch()
        self.lbl_count = tk.Label(bar, text="", bg=C["bg"], fg=C["dim"], font=self.f_small)
        self.lbl_count.pack(side="right")
        # --- limits
        fl = tk.Frame(self.outer, bg=C["bg"])
        fl.pack(fill="x", pady=(10, 12))
        tk.Label(fl, text=t("pw_min"), bg=C["bg"], fg=C["dim"], font=self.f_small).pack(side="left", padx=(0, 10))
        self.v_min = {}
        for m in COLS:
            v = tk.StringVar(value=f"{_STATE['min'][m]:g}")
            self.v_min[m] = v
            grp = tk.Frame(fl, bg=C["bg"])
            grp.pack(side="left", padx=(0, 14))
            ic = procgfx.metric_icon(m, 16, C[MCOL[m]])
            if ic is not None:
                lb = tk.Label(grp, image=ic, bg=C["bg"], bd=0)
                lb.image = ic
                lb.pack(side="left", padx=(0, 5))
            tk.Label(grp, text=m.upper() if m != "disk" else "Disk", bg=C["bg"], fg=C["dim"],
                     font=self.f_small).pack(side="left", padx=(0, 7))
            fr, e = self._boxed(grp, v, 5, "center", size=9, height=uikit.px(28))
            fr.pack(side="left")
            tk.Label(grp, text=UNIT[m], bg=C["bg"], fg=C["faint"], font=self.f_small).pack(side="left", padx=(5, 0))
            v.trace_add("write", lambda *a, m=m: self._min_changed(m))
        # "show everything": ignore the limits above
        self.v_showall = tk.BooleanVar(value=_STATE["show_all"])
        cb = uikit.Check(fl, t("pw_show_all"), self.v_showall, C, (self.F_TXT, 9), bg=C["bg"],
                         command=self._showall_toggled, small=True)
        cb.pack(side="left", padx=(8, 0))
        for w_ in (cb.sw, cb.lbl):
            Tooltip(w_, t("pw_show_all_tip"))
        # --- list
        body = tk.Frame(self.outer, bg=C["bg"])
        body.pack(fill="both", expand=True)
        self.hdr = tk.Canvas(body, height=self.hdr_h, bg=C["bg"], highlightthickness=0, bd=0)
        self.hdr.pack(fill="x")
        self.cv = tk.Canvas(body, bg=C["bg"], highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.hdr.bind("<Configure>", lambda e: self._draw_header())
        self.hdr.bind("<Button-1>", self._hdr_click)
        self.hdr.bind("<Motion>", self._hdr_motion)
        self.hdr.bind("<Leave>", lambda e: self._hdr_motion(None))
        self.cv.bind("<Configure>", lambda e: self._layout_draw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Enter>", self._enter)
        self.cv.bind("<Leave>", self._leave)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.hdr_hover = None
        self.tt = Tooltip(self.cv, self._tip_content, _bind=False)
        # --- footer
        ft = tk.Frame(self.outer, bg=C["bg"])
        ft.pack(fill="x", pady=(8, 0))
        self.lbl_note = tk.Label(ft, text="", bg=C["bg"], fg=C["faint"], font=self.f_small)
        self.lbl_note.pack(side="right", anchor="n", padx=(12, 0))
        self.lbl_sum = tk.Label(ft, text="", bg=C["bg"], fg=C["dim"], font=self.f_small, anchor="w", justify="left")
        self.lbl_sum.pack(side="left", fill="x", expand=True)
        ft.bind("<Configure>", lambda e: self.lbl_sum.config(wraplength=max(200, e.width - 190)))

    def _rebuild(self):
        """The panel's theme / fonts changed: build the window contents again."""
        q = self.v_search.get() if hasattr(self, "v_search") else ""
        self.tt.hide()
        self.outer.destroy()
        self._fonts()
        self.win.configure(bg=self.C["bg"])
        self._build()
        self.v_search.set(q)
        self._dark_title()
        self.refresh(force=True, resort=False)

    def _draw_all_switch(self):
        C, cv = self.C, self.sw_all
        cv.delete("all")
        on = self.all
        im = procgfx.switch(on, 36, 20, C["green"], C["track"], C["border"], "#ffffff", C["dim"])
        if im is not None:
            cv.image = im
            cv.create_image(2, 1, image=im, anchor="nw")
        else:
            cv.create_rectangle(2, 3, 38, 19, fill=C["green"] if on else C["track"], outline=C["border"])
            cv.create_oval((22 if on else 6), 5, (36 if on else 20), 17, fill="#ffffff" if on else C["dim"], outline="")

    # ------------------------------------------------------------ events
    def sort_by(self, col, desc=None):
        if desc is None:
            desc = (not self.sort[1]) if self.sort[0] == col else (col not in ("name",))
        self.sort = (col, desc)
        self._draw_header()
        self.refresh(force=True, resort=True)

    def _min_changed(self, m):
        try:
            _STATE["min"][m] = max(0.0, float(self.v_min[m].get().replace(",", ".")))
        except ValueError:
            return
        self.refresh(force=True, resort=True)

    def _showall_toggled(self):
        _STATE["show_all"] = bool(self.v_showall.get())
        self.refresh(force=True, resort=True)

    def _all_toggled(self):
        self.all = not self.all
        self.v_all.set(self.all)
        self._draw_all_switch()
        self._save()
        self.toggled_at = time.monotonic()
        self._layout_draw()

    def _save(self):
        value = "all" if self.all else sorted(self.picked)
        try:
            self.api.apply({"proc_log_filter": value})
        except Exception:
            pass

    def _toggle_log(self, key):
        if self.all:
            return
        if key in self.picked:
            self.picked.discard(key)
        else:
            self.picked.add(key)
        self._save()
        self.toggled_at = time.monotonic()
        self._draw()
        self._footer()

    def _wheel(self, e):
        self._scroll(-int(e.delta / 120) * self.rh * 1.5)

    def _scroll(self, dy):
        mx = max(0, self.content_h - self.cv.winfo_height())
        new = max(0, min(mx, self.top + dy))
        if new != self.top:
            self.top = new
            self._motion_refresh()
            self._draw()

    def _enter(self, e):
        self.inside = True

    def _leave(self, e):
        self.inside = False
        self.left_at = time.monotonic()
        self.hover, self.hover_part = None, ""
        self._tip_update(None)
        self._draw()

    def _motion_refresh(self):
        self.hover = None

    def _row_at(self, y):
        yy = y + self.top
        for i, r in enumerate(self.rows):
            if r["y"] <= yy < r["y"] + r["h"]:
                return i
        return None

    def _part(self, r, x):
        """Which part of a row the pointer is on: pill | chev | scroll | name."""
        g = self.geom
        if x >= g["x1"] + 2:
            return "scroll"
        if r["kind"] == "grp" and g["log0"] <= x <= g["x1"]:
            return "pill"
        if r["kind"] in ("cat", "grp") and x < g["x0"] + 40 and r.get("chev"):
            return "chev"
        return "name"

    def _motion(self, e):
        if self.drag:
            return
        self.inside = True
        i = self._row_at(e.y)
        part = self._part(self.rows[i], e.x) if i is not None else ""
        if i != self.hover or part != self.hover_part:
            self.hover, self.hover_part = i, part
            self._draw()
        r = self.rows[i] if i is not None else None
        hand = bool(r) and (part in ("pill", "chev") or r["kind"] in ("cat", "grp"))
        if part == "pill" and self.all:
            hand = False
        self.cv.config(cursor="hand2" if hand else "")
        self._tip_update(e)

    def _press(self, e):
        self.inside = True
        g = self.geom
        if e.x >= g["x1"] + 2 and self.content_h > self.cv.winfo_height():
            self.drag = (e.y, self.top)
            if not self._on_thumb(e.y):                       # click in the track: jump there
                self._scroll_to_y(e.y)
                self.drag = (e.y, self.top)
            return
        i = self._row_at(e.y)
        if i is None:
            return
        r = self.rows[i]
        part = self._part(r, e.x)
        if r["kind"] == "cat":
            (self.collapsed.discard if r["cat"] in self.collapsed else self.collapsed.add)(r["cat"])
            self._layout_draw()
        elif r["kind"] == "grp":
            if part == "pill":
                self._toggle_log(r["key"])
            elif r.get("chev") and (part == "chev" or e.x < g["name0"] + 220):
                (self.expanded.discard if r["key"] in self.expanded else self.expanded.add)(r["key"])
                self.selected = r["rid"]
                self._layout_draw()
            else:
                self.selected = r["rid"]
                self._draw()
        elif r["kind"] in ("other", "kernel", "child"):
            self.selected = None
            self._draw()

    def _drag(self, e):
        if not self.drag:
            return
        y0, top0 = self.drag
        H = self.cv.winfo_height()
        mx = max(1, self.content_h - H)
        th = self._thumb()[1] - self._thumb()[0]
        span = max(1.0, H - th - 8)
        new = max(0, min(mx, top0 + (e.y - y0) * mx / span))
        if new != self.top:
            self.top = new
            self._draw()

    def _scroll_to_y(self, y):
        H = self.cv.winfo_height()
        mx = max(1, self.content_h - H)
        th = self._thumb()[1] - self._thumb()[0]
        frac = max(0.0, min(1.0, (y - th / 2 - 4) / max(1.0, H - th - 8)))
        self.top = frac * mx
        self._draw()

    def _thumb(self):
        H = self.cv.winfo_height()
        ch = max(1, self.content_h)
        th = max(30.0, (H - 8) * H / ch)
        mx = max(1, ch - H)
        y0 = 4 + (H - 8 - th) * (self.top / mx)
        return y0, y0 + th

    def _on_thumb(self, y):
        a, b = self._thumb()
        return a <= y <= b

    def _hdr_click(self, e):
        c = self._hdr_col(e.x)
        if c:
            self.sort_by(c)

    def _hdr_col(self, x):
        g = self.geom
        if x < g["cols"]["cpu"][0]:
            return "name"
        if x >= g["log0"]:
            return "log"
        for m in COLS:
            a, b = g["cols"][m]
            if a <= x < b:
                return m
        return None

    def _hdr_motion(self, e):
        c = self._hdr_col(e.x) if e else None
        if c != self.hdr_hover:
            self.hdr_hover = c
            self.hdr.config(cursor="hand2" if c else "")
            self._draw_header()

    # ------------------------------------------------------------ tooltips of special rows
    def _tip_for(self):
        if self.hover is None or self.hover >= len(self.rows):
            return None, None
        r = self.rows[self.hover]
        if r["kind"] == "kernel":
            return ("k",), t("pw_kernel_tip")
        if r["kind"] == "other":
            return ("o", r["cat"]), t("pw_other_tip", n=r["n"])
        if r["kind"] == "grp" and self.hover_part == "pill" and self.all:
            return ("lock",), t("pw_locked_tip")
        if r["kind"] == "grp" and self.hover_part == "pill":
            return ("pill", r["key"]), t("pw_pill_tip")
        return None, None

    def _tip_update(self, e):
        tid, text = self._tip_for() if e is not None else (None, None)
        if tid != self.tip_id:
            self.tip_id, self.tip_text = tid, text
            self.tt._leave()
            if tid is not None and e is not None:
                self.tt._enter(e)
        elif tid is not None and e is not None:
            self.tt._motion(e)

    def _tip_content(self):
        return self.tip_text

    # ------------------------------------------------------------ data
    def tick(self):
        if not self.alive():
            return
        if self._theme_sig() != self.sig:
            self._rebuild()
        self.refresh()
        snap = self.snap
        if self.period < 2.0 and snap is not None and snap.warm:
            self.period = 2.0                                 # first rates are in: the list refreshes every 2 s
            procmon.MON.acquire("procwin", self.period)
        self.after_id = self.win.after(500, self.tick)

    def _frozen(self):
        now = time.monotonic()
        return self.inside or now - self.left_at < STILL or now - self.toggled_at < STILL_TOGGLE

    def _visible(self, g, q, mins):
        if q and q not in g.key and q not in g.name.lower():
            return False
        if not self.all and g.key in self.picked:
            return True
        if not mins:
            return True
        return any(g.value(m) >= v for m, v in mins.items())

    def _sortkey(self, g):
        col = self.sort[0]
        if col == "name":
            return g.name.lower()
        if col == "log":
            return (self.all or g.key in self.picked, g.cpu)
        return g.value(col)

    def _sorted(self, groups):
        desc = self.sort[1]
        return sorted(groups, key=lambda g: (self._sortkey(g), g.name.lower()), reverse=desc)

    def refresh(self, force=False, resort=False):
        snap = procmon.MON.snapshot()
        if snap is None:
            self.lbl_count.config(text=t("pw_wait"))
            return
        if resort:
            self.resort = True
        frozen = self._frozen() and not self.resort and bool(self.order)
        released = self.was_frozen and not frozen
        if not force and snap.t == self.last_t and not released:
            return
        self.was_frozen = frozen
        self.last_t = snap.t
        self.snap = snap
        self.resort = False
        mins = {} if _STATE["show_all"] else {m: v for m, v in _STATE["min"].items() if v > 0}
        q = self.v_search.get().strip().lower()
        by_key = snap.by_key
        for g in snap.groups:
            self.names[g.key] = g.name
        # ---- membership: held still while the list is in use, otherwise recomputed and re-sorted
        listed, pending = {}, False
        if frozen:
            shown = {k for ks in self.order.values() for k in ks}
            for cat in CATS:
                row = []
                for k in self.order.get(cat, []):
                    g = by_key.get(k)
                    if g is None:
                        g = _Ghost(k, self.names.get(k, k))
                        g.cat = cat
                    row.append(g)
                listed[cat] = row
            for cat in CATS:                                  # would a re-sort / new programs change the list?
                ideal = [g.key for g in self._sorted([g for g in snap.groups if g.cat == cat
                                                      and self._visible(g, q, mins)])]
                if ideal != [g.key for g in listed[cat] if not isinstance(g, _Ghost)]:
                    pending = True
        else:
            for cat in CATS:
                listed[cat] = self._sorted([g for g in snap.groups if g.cat == cat and self._visible(g, q, mins)])
            shown = {g.key for ks in listed.values() for g in ks}
            self.order = {cat: [g.key for g in gs] for cat, gs in listed.items()}
        # ---- model: per category the listed groups, the hidden rest and the totals
        model = []
        n_hidden = 0
        searching = bool(q)
        for cat in CATS:
            groups = listed[cat]
            rest = [g for g in snap.groups if g.cat == cat and g.key not in shown]
            n_hidden += len(rest)
            hid = {m: sum(g.value(m) for g in rest) for m in COLS}
            tot = {m: sum(g.value(m) for g in groups) + (0 if searching else hid[m]) for m in COLS}
            kern = cat == "windows" and not searching
            if kern:
                tot["ram"] += snap.ram_kernel
                tot["vram"] += snap.vram_kernel
            tot["gpu"] = min(100.0, tot["gpu"])
            hid["gpu"] = min(100.0, hid["gpu"])
            model.append(dict(cat=cat, groups=groups, rest=len(rest) if not searching else 0, hid=hid, tot=tot,
                              kernel=kern))
        # ---- bar scales: relative to the busiest listed program, but never fuller than a sensible floor
        for m in COLS:
            top = max((g.value(m) for md in model for g in md["groups"]), default=0.0)
            self.scale[m] = max(FLOOR[m], top)
        self.model = model
        n_listed = sum(len(md["groups"]) for md in model)
        txt = t("pw_count", n=n_listed) + (" · " + t("pw_hidden", n=n_hidden) if n_hidden and not searching else "")
        txt += " · " + (t("pw_logging_all") if self.all else t("pw_logging_n", n=len(self.picked)))
        if not snap.gpu_ok:
            txt += " · " + t("pw_nogpu")
        self.lbl_count.config(text=txt)
        self.lbl_note.config(text=t("pw_paused") if (frozen and pending) else "")
        self._footer()
        self._layout_draw()

    def _footer(self):
        snap = self.snap
        if snap is None:
            return
        prog = sum(g.ram for g in snap.groups)
        s = t("pw_sum_ram", used=fmt_mem(snap.ram_used), prog=fmt_mem(prog), kern=fmt_mem(snap.ram_kernel))
        if snap.vram_used is not None:
            s += "    " + t("pw_sum_vram", used=fmt_mem(snap.vram_used),
                            prog=fmt_mem(sum(g.vram for g in snap.groups)))
        self.lbl_sum.config(text=s)

    # ------------------------------------------------------------ layout
    def _geometry(self, W):
        x0, x1 = 1.0, float(W) - 14.0
        g = {"x0": x0, "x1": x1, "log0": x1 - self.logw, "name0": x0 + 22 + 20}
        right = x1 - self.logw
        cols = {}
        for m in reversed(COLS):
            cols[m] = (right - self.mcw, right)
            right -= self.mcw
        g["cols"] = cols
        g["name1"] = right - 10
        self.geom = g
        return g

    def _layout(self):
        """Rows with their y positions (content coordinates) and the card blocks around them."""
        W = max(self.cv.winfo_width(), 200)
        self._geometry(W)
        rows, blocks = [], []
        y = 4.0
        rh, ch = self.rh, self.cat_h
        for md in self.model:
            cat = md["cat"]
            y0 = y
            sub = []
            open_ = cat not in self.collapsed
            nl = len(md["groups"])
            sub.append(dict(kind="cat", cat=cat, h=ch, rid=("c", cat), vals=md["tot"], n=nl, chev=True))
            if open_:
                for g in md["groups"]:
                    multi = len(g.procs) > 1
                    sub.append(dict(kind="grp", key=g.key, g=g, h=rh, rid=("g", g.key), chev=multi))
                    if multi and g.key in self.expanded:
                        for p in self._children(g):
                            sub.append(dict(kind="child", p=p, g=g, h=rh * 0.86, rid=("p", p.pid)))
                if md["rest"]:
                    sub.append(dict(kind="other", cat=cat, n=md["rest"], vals=md["hid"], h=rh, rid=("o", cat)))
                if md["kernel"]:
                    sub.append(dict(kind="kernel", h=rh, rid=("k",),
                                    vals={"ram": self.snap.ram_kernel, "vram": self.snap.vram_kernel}))
            for r in sub:
                r["y"], r["blk"] = y, len(blocks)
                y += r["h"]
                rows.append(r)
            y += self.pad
            blocks.append(dict(y0=y0, y1=y, cat=cat))
            y += 14
        self.rows, self.blocks = rows, blocks
        self.content_h = y - 6
        H = self.cv.winfo_height()
        self.top = max(0, min(self.top, max(0, self.content_h - H)))

    def _children(self, g):
        key = g.key
        col = self.sort[0]
        procs = list(g.procs)
        prev = self.child_order.get(key)
        if prev and self._frozen():
            pos = {pid: i for i, pid in enumerate(prev)}
            procs.sort(key=lambda p: pos.get(p.pid, 10 ** 6))
        else:
            if col in procmon.METRICS:
                procs.sort(key=lambda p: getattr(p, col), reverse=self.sort[1])
            else:
                procs.sort(key=lambda p: -p.cpu)
            self.child_order[key] = [p.pid for p in procs]
        return procs

    def _layout_draw(self):
        if not self.model:
            return
        self._layout()
        self._draw()
        self._draw_header()

    # ------------------------------------------------------------ drawing
    def _fit(self, font, text, avail):
        if avail <= 0:
            return ""
        if font.measure(text) <= avail:
            return text
        while len(text) > 1 and font.measure(text + "…") > avail:
            text = text[:-1]
        return text + "…"

    def _card_block(self, cv, y0, y1, accent, H):
        """Card background of one category: clipped to the visible part so tall cards stay cheap."""
        C, g = self.C, self.geom
        x0, x1 = g["x0"], g["x1"]
        R = max(6, min(18, int(themes.current().radius * 0.75)))
        sy0, sy1 = y0 - self.top, y1 - self.top
        if sy1 < -2 or sy0 > H + 2:
            return
        gfxok = procgfx.AVAILABLE
        ya, yb = sy0 + R, sy1 - R
        lo, hi = max(ya, -2), min(yb, H + 2)
        if hi > lo:
            cv.create_rectangle(x0, lo, x1, hi, fill=C["card"], outline="")
            cv.create_line(x0, lo, x0, hi, fill=C["border"])
            cv.create_line(x1, lo, x1, hi, fill=C["border"])
            a0 = max(ya, -2)
            if accent:
                cv.create_line(x0 + 2, a0, x0 + 2, hi, fill=accent, width=3)
        for top_edge, ye in ((True, sy0), (False, sy1)):
            if ye < -R - 2 or ye > H + R + 2:
                continue
            yy = ye if top_edge else ye - R
            cv.create_rectangle(x0 + R, yy, x1 - R, yy + R, fill=C["card"], outline="")
            cv.create_line(x0 + R, ye, x1 - R, ye, fill=C["border"])
            if gfxok:
                for side in ("l", "r"):
                    im = procgfx.corner(R, ("t" if top_edge else "b") + side, C["card"], C["border"])
                    cv.create_image(x0 if side == "l" else x1 - R + 1, yy, image=im, anchor="nw")
            else:
                cv.create_rectangle(x0, yy, x1, yy + R, fill=C["card"], outline=C["border"])

    def _draw(self):
        cv, C = self.cv, self.C
        if not self.model or not hasattr(self, "geom"):
            return
        cv.delete("all")
        procgfx.begin()
        W, H = cv.winfo_width(), cv.winfo_height()
        g = self.geom
        self.tgt = {}
        acc = {md["cat"]: C[CCOL[md["cat"]]] for md in self.model}
        for b in self.blocks:
            self._card_block(cv, b["y0"], b["y1"], acc[b["cat"]], H)
        for i, r in enumerate(self.rows):
            sy = r["y"] - self.top
            if sy + r["h"] < 0 or sy > H:
                continue
            self._draw_row(cv, i, r, sy, acc)
        self._draw_scroll(cv, W, H)
        self._anim_check()

    def _cell(self, cv, rid, m, right, sy, h, v, strong=False, dim=False, bar=True):
        C = self.C
        x = right - 14
        zero = v < (0.05 if DEC[m] else 0.5)
        font = self.f_numb if strong else self.f_num
        col = C["faint"] if zero else (C["dim"] if dim else C["fg"])
        cv.create_text(x, sy + h * (0.38 if bar else 0.5), text="–" if zero else _num(m, v), anchor="e", font=font,
                       fill=col)
        if not bar:
            return
        bw = self.mcw - 30
        bx1, by = x, sy + h * 0.80
        bx0 = bx1 - bw
        cv.create_line(bx0, by, bx1, by, width=3, fill=C["track"], capstyle="round")
        frac = 0.0 if zero else min(1.0, v / self.scale[m])
        key = (rid, m)
        self.tgt[key] = frac
        d = self.disp.get(key, frac)
        self.disp[key] = d
        if d > 0.004:
            cv.create_line(bx0, by, bx0 + max(0.0, bw * d), by, width=3, fill=C[MCOL[m]], capstyle="round")

    def _draw_row(self, cv, i, r, sy, acc):
        C, g = self.C, self.geom
        h = r["h"]
        kind = r["kind"]
        x0, x1 = g["x0"], g["x1"]
        ymid = sy + h / 2
        hot = (i == self.hover) and kind in ("grp", "child", "cat", "other", "kernel")
        sel = r.get("rid") == self.selected and kind == "grp"
        # separator under every row but the last of a block, highlight under the pointer / selection
        if kind != "cat" and (hot or sel):
            fill = mix(C["card"], C["ui"], 0.20 if sel else 0.09)
            im = procgfx.rrect(x1 - x0 - 12, h - 4, 8, fill)
            if im is not None:
                cv.create_image(x0 + 8, sy + 2, image=im, anchor="nw")
            else:
                cv.create_rectangle(x0 + 8, sy + 2, x1 - 4, sy + h - 2, fill=fill, outline="")
        elif kind == "cat" and hot:
            cv.create_rectangle(x0 + 5, sy + 3, x1 - 3, sy + h - 3, fill=mix(C["card"], acc[r["cat"]], 0.07),
                                outline="")
        nxt = self.rows[i + 1] if i + 1 < len(self.rows) else None
        if kind == "cat" or (nxt and nxt["blk"] == r["blk"] and not hot and not sel):
            ln = mix(C["card"], C["border"], 0.9 if kind == "cat" else 0.45)
            cv.create_line(x0 + 18, sy + h - 0.5, x1 - 12, sy + h - 0.5, fill=ln)
        name_avail = g["name1"] - g["name0"]
        if kind == "cat":
            col = acc[r["cat"]]
            s = 9
            im = procgfx.cat_icon({"apps": "apps", "background": "background", "windows": "windows"}[r["cat"]], s, col,
                                  mix(C["card"], col, 0.16))
            cx = x0 + 22 + 10
            if im is not None:
                cv.create_image(cx + 8, ymid, image=im)
            else:
                cv.create_oval(cx - 6, ymid - 14, cx + 22, ymid + 14, fill=mix(C["card"], col, 0.16), outline="")
            tx = cx + 8 + 22
            title = t("pw_" + r["cat"])
            cv.create_text(tx, ymid, text=title, anchor="w", font=self.f_head, fill=C["fg"])
            tw = self.f_head.measure(title)
            cv.create_text(tx + tw + 10, ymid + 1, text=str(r["n"]), anchor="w", font=self.f_small, fill=C["dim"])
            ch = procgfx.chevron(r["cat"] not in self.collapsed, 14, C["dim"])
            if ch is not None:
                cv.create_image(x0 + 22 - 10, ymid, image=ch)
            for m in COLS:
                self._cell(cv, r["rid"], m, g["cols"][m][1], sy, h, r["vals"][m], strong=True, bar=False)
            return
        if kind == "grp":
            gr = r["g"]
            ghost = isinstance(gr, _Ghost)
            logged = self.all or r["key"] in self.picked
            nm = gr.name
            cnt = f"×{len(gr.procs)}" if len(gr.procs) > 1 else ""
            cw = self.f_small.measure("  " + cnt) if cnt else 0
            txt = self._fit(self.f_txt, nm, name_avail - cw)
            cv.create_text(g["name0"], ymid, text=txt, anchor="w", font=self.f_txt,
                           fill=C["faint"] if ghost else C["fg"])
            if cnt:
                cv.create_text(g["name0"] + self.f_txt.measure(txt) + 8, ymid + 1, text=cnt, anchor="w",
                               font=self.f_small, fill=C["dim"])
            if r["chev"]:
                ch = procgfx.chevron(r["key"] in self.expanded, 12, C["dim"])
                if ch is not None:
                    cv.create_image(x0 + 22 + 9, ymid, image=ch)
            for m in COLS:
                self._cell(cv, r["rid"], m, g["cols"][m][1], sy, h, gr.value(m), dim=ghost)
            self._pill(cv, g["log0"] + self.logw / 2, ymid, logged, locked=self.all, hot=(hot and self.hover_part == "pill"))
        elif kind == "child":
            p = r["p"]
            cv.create_oval(g["name0"] + 3, ymid - 2, g["name0"] + 7, ymid + 2, fill=C["faint"], outline="")
            cv.create_text(g["name0"] + 16, ymid, text=f"{r['g'].name}  ·  {t('pw_proc', pid=p.pid)}", anchor="w",
                           font=self.f_small, fill=C["dim"])
            for m in COLS:
                self._cell(cv, r["rid"], m, g["cols"][m][1], sy, h, getattr(p, m), dim=True, bar=False)
        elif kind == "other":
            cv.create_text(g["name0"], ymid, text=self._fit(self.f_txt, t("pw_other", n=r["n"]), name_avail),
                           anchor="w", font=self.f_txt, fill=C["dim"])
            for m in COLS:
                self._cell(cv, r["rid"], m, g["cols"][m][1], sy, h, r["vals"].get(m, 0.0), dim=True, bar=False)
        elif kind == "kernel":
            cv.create_text(g["name0"], ymid, text=self._fit(self.f_txt, t("pw_kernel"), name_avail - 20),
                           anchor="w", font=self.f_txt, fill=C["name"])
            im = procgfx.info(14, mix(C["card"], C["dim"], 0.2), C["dim"], None)
            if im is not None:
                cv.create_image(g["name0"] + min(self.f_txt.measure(t("pw_kernel")), name_avail - 20) + 16, ymid,
                                image=im)
            for m in COLS:
                self._cell(cv, r["rid"], m, g["cols"][m][1], sy, h, r["vals"].get(m, 0.0), dim=True, bar=False)

    def _pill(self, cv, cx, cy, on, locked, hot):
        C = self.C
        w, h = 34, 18
        if locked:
            tr_on, kn_on = mix(C["card"], C["green"], 0.55), mix(C["card"], "#ffffff", 0.75)
        else:
            tr_on, kn_on = C["green"], "#ffffff"
        outline = C["dim"] if hot else C["border"]
        im = procgfx.switch(on, w, h, tr_on, C["track"], outline, kn_on, C["dim"] if not hot else C["fg"])
        if im is not None:
            cv.create_image(cx - w / 2, cy - h / 2, image=im, anchor="nw")
        else:
            cv.create_rectangle(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, fill=tr_on if on else C["track"],
                                outline=outline)
            kx = cx + w / 2 - h / 2 if on else cx - w / 2 + h / 2
            cv.create_oval(kx - 6, cy - 6, kx + 6, cy + 6, fill=kn_on if on else C["dim"], outline="")

    def _draw_scroll(self, cv, W, H):
        if self.content_h <= H:
            return
        C = self.C
        x = W - 6
        cv.create_line(x, 4, x, H - 4, width=4, fill=mix(C["bg"], C["track"], 0.6), capstyle="round")
        a, b = self._thumb()
        active = self.drag is not None or (self.hover_part == "scroll")
        cv.create_line(x, a + 2, x, b - 2, width=4, fill=C["dim"] if active else C["faint"], capstyle="round")

    def _draw_header(self):
        h, C = self.hdr, self.C
        if not hasattr(self, "geom"):
            self._geometry(max(self.cv.winfo_width(), 200))
        h.delete("all")
        g = self.geom
        H = self.hdr_h
        col, desc = self.sort
        y = H / 2 + 2
        labels = [("name", t("pw_name"), g["name0"], "w")]
        for m in COLS:
            labels.append((m, HEAD[m], g["cols"][m][1] - 14, "e"))
        labels.append(("log", t("pw_log_hdr"), g["log0"] + self.logw / 2, "c"))
        for key, text, x, anchor in labels:
            active = key == col
            fg = C["fg"] if active else (C["name"] if key == self.hdr_hover else C["dim"])
            font = self.f_smallb
            if anchor == "c":
                w = font.measure(text)
                tx, anc = x - w / 2, "w"
            else:
                tx, anc = x, anchor
            h.create_text(tx, y, text=text, anchor=anc, font=font, fill=fg)
            lead = 0
            if key in COLS:                                  # the metric's icon in front of its title
                ic = procgfx.metric_icon(key, 14, C[MCOL[key]])
                if ic is not None:
                    lead = 19
                    h.create_image(tx - font.measure(text) - 4 - 7, y, image=ic)
            if active:
                w = font.measure(text)
                if anchor == "e":
                    ax = tx - w - 9 - lead
                elif anchor == "w":
                    ax = tx + w + 9
                else:
                    ax = tx + w + 9
                im = procgfx.arrow(desc, 9, C["ui"])
                if im is not None:
                    h.create_image(ax, y, image=im)
                else:
                    h.create_text(ax, y, text="▼" if desc else "▲", font=self.f_small, fill=C["ui"])
        h.create_line(g["x0"] + 4, H - 1, g["x1"] - 4, H - 1, fill=C["border"])
        if self.content_h > self.cv.winfo_height() and self.top > 0:
            pass

    # ------------------------------------------------------------ bar animation
    def _anim_check(self):
        moving = any(abs(self.tgt[k] - self.disp.get(k, self.tgt[k])) > 0.004 for k in self.tgt)
        if moving and self.anim_id is None:
            self.anim_id = self.win.after(40, self._anim_step)
        if len(self.disp) > 4000:
            self.disp = {k: v for k, v in self.disp.items() if k in self.tgt}

    def _anim_step(self):
        self.anim_id = None
        if not self.alive():
            return
        for k, tv in self.tgt.items():
            d = self.disp.get(k, tv)
            self.disp[k] = tv if abs(tv - d) < 0.01 else d + (tv - d) * 0.35
        self._draw()


# ------------------------------------------------------------------ texts
i18n.register({
    "en": {
        "pw_search": "Search",
        "pw_search_ph": "Search programs",
        "pw_min": "Hide below",
        "pw_all": "Log all programs",
        "pw_all_tip": "On: every program goes into the process CSV. Off: only the ones you switch on below.",
        "pw_other": "Other programs (hidden)  ×{n}",
        "pw_other_tip": "{n} programs below the limits above, added up. Set a limit to 0 to list them one by one.",
        "pw_kernel": "Windows kernel & drivers",
        "pw_kernel_tip": ("Memory in use that belongs to no program: kernel and driver memory (paged / non-paged "
                          "pool), page tables, modified file cache and memory shared between programs.\n"
                          "= RAM in use (total - available, like the panel) - the programs' private working sets."),
        "pw_locked_tip": "Every program is logged. Turn off \"Log all programs\" to choose.",
        "pw_pill_tip": "Add this program to the process CSV",
        "pw_hidden": "{n} hidden",
        "pw_count": "{n} listed",
        "pw_none": "no activity",
        "pw_paused": "⏸ order held",
        "pw_sum_ram": "RAM in use {used} = programs {prog} + Windows kernel & drivers {kern}",
        "pw_sum_vram": "VRAM in use {used} (programs {prog})",
        "pw_logging_all": "all in log",
        "pw_logging_n": "{n} in log",
        "pw_top_cpu": "Top CPU users", "pw_top_gpu": "Top GPU users",
        "pw_top_ram": "Top RAM users", "pw_top_vram": "Top VRAM users",
        "ps_popup": "Programs in the (i) balloon",
        "ps_popup_hint": "1–10 programs per balloon",
    },
    "tr": {
        "pw_search": "Ara",
        "pw_search_ph": "Program ara",
        "pw_min": "Altını gizle",
        "pw_all": "Tüm programları kaydet",
        "pw_all_tip": "Açık: her program işlem CSV'sine yazılır. Kapalı: yalnızca aşağıda açtıklarınız.",
        "pw_other": "Diğer programlar (gizli)  ×{n}",
        "pw_other_tip": "Yukarıdaki sınırların altında kalan {n} programın toplamı. Dökümünü görmek için bir sınırı 0 yapın.",
        "pw_kernel": "Sistem çekirdeği ve sürücüler",
        "pw_kernel_tip": ("Hiçbir programa ait olmayan kullanılan bellek: çekirdek ve sürücü belleği (paged / "
                          "non-paged havuz), sayfa tabloları, değiştirilmiş dosya önbelleği ve programlar arası "
                          "paylaşılan bellek.\n"
                          "= kullanılan RAM (toplam - boş, panelle aynı) - programların özel çalışma kümeleri."),
        "pw_locked_tip": "Tüm programlar kaydediliyor. Seçmek için \"Tüm programları kaydet\" ayarını kapatın.",
        "pw_pill_tip": "Bu programı işlem CSV'sine ekle",
        "pw_hidden": "{n} gizli",
        "pw_count": "{n} program listeli",
        "pw_none": "etkinlik yok",
        "pw_paused": "⏸ sıra sabit",
        "pw_sum_ram": "Kullanılan RAM {used} = programlar {prog} + sistem çekirdeği ve sürücüler {kern}",
        "pw_sum_vram": "Kullanılan VRAM {used} (programlar {prog})",
        "pw_logging_all": "tümü kayıtta",
        "pw_logging_n": "{n} kayıtta",
        "pw_top_cpu": "CPU'yu en çok kullananlar", "pw_top_gpu": "GPU'yu en çok kullananlar",
        "pw_top_ram": "RAM'i en çok kullananlar", "pw_top_vram": "VRAM'i en çok kullananlar",
        "ps_popup": "(i) balonundaki program sayısı",
        "ps_popup_hint": "balon başına 1–10 program",
    },
})


i18n.register({
    "tr": {"pw_show_all": "Hepsini göster",
           "pw_show_all_tip": "Sınırları yok sayar ve çalışan tüm programları listeler."},
    "en": {"pw_show_all": "Show everything",
           "pw_show_all_tip": "Ignores the limits and lists every running program."},
})
