"""Settings window (tk.Toplevel, dark theme) for HWiNFO Panel.

``SettingsWindow(api)`` gets a small API object from panel.py:
    root, cfg (live dict, read only here), C (colours), fonts (text, heading, mono),
    apply(changes, lay_mode, lay_cfg) -> bool, layout() -> (is_auto, sections_cfg), readings() -> [reading],
    monitor() -> dict | None, monitors() -> [dict], refresh_tasks() -> bool, detect(path_setting) -> path,
    source (backend.Manager: the active data source)
"""
import copy
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

import backend
import config
import crashscan_ui
import hwctl
import i18n
import layout
import version
from i18n import t

ACCENT = "#58a6ff"


# ====================================================================== small themed widgets
class Check(tk.Frame):
    """Dark check box (the native one cannot be recoloured)."""

    def __init__(self, parent, text, var, C, font, bg=None, command=None):
        bg = bg or C["bg"]
        super().__init__(parent, bg=bg)
        self.var, self.command, self.C, self.bg = var, command, C, bg
        self.box = tk.Canvas(self, width=18, height=18, bg=bg, highlightthickness=0, bd=0, takefocus=1,
                             cursor="hand2")
        self.box.pack(side="left")
        self.lbl = None
        if text:
            self.lbl = tk.Label(self, text=text, bg=bg, fg=C["fg"], font=font, cursor="hand2", anchor="w",
                                justify="left")
            self.lbl.pack(side="left", padx=(6, 0))
            self.lbl.bind("<Button-1>", self.toggle)
        self.box.bind("<Button-1>", lambda e: (self.box.focus_set(), self.toggle()))
        self.box.bind("<space>", self.toggle)
        self.box.bind("<FocusIn>", lambda e: self.draw())
        self.box.bind("<FocusOut>", lambda e: self.draw())
        var.trace_add("write", lambda *a: self.draw())
        self.draw()

    def draw(self):
        try:
            c, on = self.box, bool(self.var.get())
            c.delete("all")
            focus = self.focus_displayof() is c
            edge = ACCENT if (on or focus) else self.C["faint"]
            c.create_rectangle(2, 2, 16, 16, outline=edge, width=1, fill=ACCENT if on else self.C["track"])
            if on:
                c.create_line(5, 9.5, 8, 12.5, 13, 5.5, fill=self.C["bg"], width=2, capstyle="round",
                              joinstyle="round")
        except tk.TclError:
            pass

    def toggle(self, e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()


class ScrollFrame(tk.Frame):
    """Vertically scrolling container; put widgets into .inner."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview, style="HP.Vertical.TScrollbar")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.wid = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self.wid, width=e.width))
        self.sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)

    def req_height(self):
        self.inner.update_idletasks()
        return self.inner.winfo_reqheight()

    def req_width(self):
        self.inner.update_idletasks()
        return self.inner.winfo_reqwidth()

    def scroll(self, delta):
        lo, hi = self.cv.yview()
        if lo <= 0.0 and hi >= 1.0:
            return
        self.cv.yview_scroll(int(-delta / 120 * 3) or (-1 if delta > 0 else 1), "units")


def _to_float(s):
    return float(str(s).strip().replace(",", "."))


def _fmt_num(v):
    return f"{v:g}"


# ====================================================================== the window
class SettingsWindow:
    def __init__(self, api):
        self.api = api
        self.root = api.root
        self.C = api.C
        self.F_TXT, self.F_HEAD, self.F_NUM = api.fonts
        self.win = tk.Toplevel(self.root)
        self.win.title(t("st_title"))
        self.win.configure(bg=self.C["bg"])
        try:
            self.win.iconbitmap(default=os.path.join(config.APP_DIR, "panel.ico"))
        except Exception:
            pass
        self.win.transient(self.root)
        self.win.withdraw()
        self._style()
        self.tab_index = 0
        self.initial = {}
        self.build_ui(first=True)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<MouseWheel>", self._wheel)
        self.win.bind_class("TCombobox", "<MouseWheel>", lambda e: (self._wheel(e), "break")[1])

    def winfo_exists(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def deiconify(self):
        self.win.deiconify()

    def lift(self):
        self.win.lift()

    def focus_force(self):
        self.win.focus_force()

    # ------------------------------------------------------------ theme
    def _style(self):
        C = self.C
        s = ttk.Style(self.win)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        f = (self.F_TXT, 10)
        s.configure("HP.TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0), bordercolor=C["border"],
                    lightcolor=C["bg"], darkcolor=C["bg"])
        s.configure("HP.TNotebook.Tab", background=C["card"], foreground=C["dim"], padding=(18, 7),
                    borderwidth=0, bordercolor=C["border"], lightcolor=C["card"], darkcolor=C["card"], font=(self.F_TXT, 10, "bold"))
        s.map("HP.TNotebook.Tab", background=[("selected", C["track"]), ("active", C["stripe"])],
              foreground=[("selected", C["fg"]), ("active", C["fg"])])
        s.configure("HP.TCombobox", fieldbackground=C["track"], background=C["track"], foreground=C["fg"],
                    arrowcolor=C["dim"], bordercolor=C["border"], lightcolor=C["track"], darkcolor=C["track"],
                    selectbackground=C["track"], selectforeground=C["fg"], padding=3)
        s.map("HP.TCombobox", fieldbackground=[("readonly", C["track"])], foreground=[("readonly", C["fg"])],
              selectbackground=[("readonly", C["track"])], selectforeground=[("readonly", C["fg"])],
              bordercolor=[("focus", ACCENT)], arrowcolor=[("active", C["fg"])])
        s.configure("HP.Vertical.TScrollbar", background=C["track"], troughcolor=C["bg"], bordercolor=C["bg"],
                    arrowcolor=C["dim"], lightcolor=C["track"], darkcolor=C["track"], gripcount=0)
        s.map("HP.Vertical.TScrollbar", background=[("active", C["border"])])
        s.configure("HP.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["fg"],
                    rowheight=24, borderwidth=0, font=f, bordercolor=C["border"], lightcolor=C["border"],
                    darkcolor=C["border"])
        s.map("HP.Treeview", background=[("selected", "#1f3a5f")], foreground=[("selected", C["fg"])])
        s.configure("HP.Treeview.Heading", background=C["track"], foreground=C["name"], relief="flat",
                    font=(self.F_TXT, 10, "bold"))
        s.map("HP.Treeview.Heading", background=[("active", C["border"])])
        self.win.option_add("*TCombobox*Listbox.background", C["track"])
        self.win.option_add("*TCombobox*Listbox.foreground", C["fg"])
        self.win.option_add("*TCombobox*Listbox.selectBackground", "#1f3a5f")
        self.win.option_add("*TCombobox*Listbox.selectForeground", C["fg"])
        self.win.option_add("*TCombobox*Listbox.font", f)

    def _dark_title(self):
        try:
            import ctypes
            hwnd = int(self.win.wm_frame(), 16)
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        except Exception:
            pass

    # ------------------------------------------------------------ widget factories
    def label(self, parent, text, dim=False, bold=False, bg=None, wrap=0, size=10):
        return tk.Label(parent, text=text, bg=bg or self.C["bg"], fg=self.C["dim"] if dim else self.C["fg"],
                        font=(self.F_TXT, size, "bold" if bold else "normal"), anchor="w", justify="left",
                        wraplength=wrap)

    def entry(self, parent, var, width=12, bg=None):
        C = self.C
        return tk.Entry(parent, textvariable=var, width=width, bg=C["track"], fg=C["fg"], insertbackground=C["fg"],
                        relief="flat", highlightthickness=1, highlightbackground=C["border"],
                        highlightcolor=ACCENT, font=(self.F_TXT, 10), disabledbackground=C["track"],
                        selectbackground="#1f3a5f", selectforeground=C["fg"])

    def spinbox(self, parent, var, lo, hi, inc, fmt, width=7):
        C = self.C
        return tk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=inc, format=fmt, width=width,
                          bg=C["track"], fg=C["fg"], insertbackground=C["fg"], buttonbackground=C["track"],
                          relief="flat", highlightthickness=1, highlightbackground=C["border"],
                          highlightcolor=ACCENT, font=(self.F_TXT, 10), justify="left",
                          selectbackground="#1f3a5f", selectforeground=C["fg"])

    def button(self, parent, text, cmd, primary=False, bg=None, width=None, font_size=10):
        C = self.C
        base = ACCENT if primary else C["track"]
        hover = "#79b8ff" if primary else C["border"]
        b = tk.Button(parent, text=text, command=cmd, bg=base, fg=C["bg"] if primary else C["fg"],
                      activebackground=hover, activeforeground=C["bg"] if primary else C["fg"], relief="flat", bd=0,
                      padx=14, pady=5, cursor="hand2", font=(self.F_TXT, font_size, "bold" if primary else "normal"),
                      highlightthickness=0, takefocus=1)
        if width:
            b.config(width=width)
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=base))
        return b

    def small_btn(self, parent, text, cmd, bg=None):
        C = self.C
        base = bg or C["card"]
        b = tk.Button(parent, text=text, command=cmd, bg=base, fg=C["dim"], activebackground=C["border"],
                      activeforeground=C["fg"], relief="flat", bd=0, padx=6, pady=1, cursor="hand2",
                      font=(self.F_TXT, 9), highlightthickness=0, takefocus=0)
        b.bind("<Enter>", lambda e: b.config(fg=C["fg"], bg=C["border"]))
        b.bind("<Leave>", lambda e: b.config(fg=C["dim"], bg=base))
        return b

    def combo(self, parent, values, index, width=24):
        cb = ttk.Combobox(parent, values=values, state="readonly", width=width, style="HP.TCombobox",
                          font=(self.F_TXT, 10))
        cb.current(max(0, min(index, len(values) - 1)))
        return cb

    def mark(self, w, ok):
        C = self.C
        w.config(highlightbackground=C["border"] if ok else C["red"], highlightcolor=ACCENT if ok else C["red"])

    def add_check(self, store, tab, widgets, fn):
        """Register a validator. Returns recheck() which re-marks only these widgets."""
        def recheck():
            try:
                ok = bool(fn())
            except Exception:
                ok = False
            for w in widgets:
                try:
                    self.mark(w, ok)
                except tk.TclError:
                    pass
            return ok
        store.append((tab, recheck))
        return recheck

    # ------------------------------------------------------------ build
    def build_ui(self, first=False):
        C = self.C
        cfg = self.api.cfg
        for w in self.win.winfo_children():
            w.destroy()
        self.win.title(t("st_title"))
        self.checks = []
        self.lay_checks = []
        self.scrolls = []
        self.tab_frames = []

        outer = tk.Frame(self.win, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=14, pady=(12, 12))
        self.nb = ttk.Notebook(outer, style="HP.TNotebook")
        self.nb.pack(fill="both", expand=True)

        look = self._new_tab(t("st_tab_look"))
        log = self._new_tab(t("st_tab_log"))
        hw = self._new_tab(t("st_tab_hw"))
        self._tab_look(look.inner, cfg)
        self._tab_log(log.inner, cfg)
        self._tab_hw(hw.inner, cfg)
        self._tab_layout()
        crashscan_ui.add_tab(self)

        self.msg = tk.Label(outer, text="", bg=C["bg"], fg=C["red"], font=(self.F_TXT, 10), anchor="w",
                            justify="left")
        self.msg.pack(fill="x", pady=(8, 0))
        bar = tk.Frame(outer, bg=C["bg"])
        bar.pack(fill="x", pady=(6, 0))
        self.button(bar, t("st_apply"), self.apply).pack(side="right")
        self.button(bar, t("st_cancel"), self.cancel).pack(side="right", padx=8)
        self.button(bar, t("st_save"), self.save, primary=True).pack(side="right")
        tk.Label(bar, text=f"{config.APP_NAME} v{version.__version__}", bg=C["bg"], fg=C["faint"],
                 font=(self.F_TXT, 8)).pack(side="left")

        self.initial = self.read_ui()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: setattr(self, "tab_index", self.nb.index("current")))
        self._fit(first)
        try:
            self.nb.select(min(self.tab_index, self.nb.index("end") - 1))
        except tk.TclError:
            pass

    def _new_tab(self, title):
        sf = ScrollFrame(self.nb, self.C["bg"])
        self.nb.add(sf, text=title)
        self.scrolls.append(sf)
        sf.inner.configure(padx=16, pady=14)
        return sf

    def _grid_row(self, parent, r, label, widget, hint=None):
        self.label(parent, label).grid(row=r, column=0, sticky="w", padx=(0, 16), pady=6)
        widget.grid(row=r, column=1, sticky="w", pady=6)
        if hint:
            self.label(parent, hint, dim=True, size=9).grid(row=r, column=2, sticky="w", padx=(10, 0))

    # ---------------- appearance
    def _tab_look(self, p, cfg):
        langs = [("auto", t("st_auto")), ("tr", "Türkçe"), ("en", "English")]
        self.lang_vals = [v for v, _ in langs]
        cur = str(cfg.get("language", "auto")).lower()
        self.cb_lang = self.combo(p, [n for _, n in langs], self.lang_vals.index(cur) if cur in self.lang_vals else 0)
        self._grid_row(p, 0, t("st_language"), self.cb_lang)

        self.col_vals = ["auto", 1, 2, 3]
        cur = config.norm_columns(cfg.get("columns"))
        self.cb_cols = self.combo(p, [t("st_auto"), "1", "2", "3"], self.col_vals.index(cur), 12)
        self._grid_row(p, 1, t("st_columns"), self.cb_cols)

        mons = self.api.monitors() or []
        self.mon_vals = ["auto"] + [m["index"] for m in mons]
        names = [t("st_auto")]
        for m in mons:
            extra = [t("st_mon_primary")] if m["primary"] else []
            if m["h"] > m["w"]:
                extra.append(t("st_mon_portrait"))
            names.append(f"{m['index']}:  {m['w']} × {m['h']}" + ("   ·   " + ", ".join(extra) if extra else ""))
        cur = cfg.get("monitor", "auto")
        idx = 0
        if str(cur).strip().lower() not in ("", "auto"):
            try:
                idx = self.mon_vals.index(int(cur)) if int(cur) in self.mon_vals else 0
            except (TypeError, ValueError):
                idx = 0
        self.cb_mon = self.combo(p, names, idx, 36)
        self._grid_row(p, 2, t("st_monitor"), self.cb_mon)

        self.wm_vals = ["maximized", "fullscreen", "window"]
        cur = str(cfg.get("window_mode", "maximized")).lower()
        self.cb_wm = self.combo(p, [t("st_wm_maximized"), t("st_wm_fullscreen"), t("st_wm_window")],
                                self.wm_vals.index(cur) if cur in self.wm_vals else 0, 24)
        self._grid_row(p, 3, t("st_window_mode"), self.cb_wm)

        self.v_top = tk.BooleanVar(value=bool(cfg.get("topmost", True)))
        Check(p, t("st_topmost"), self.v_top, self.C, (self.F_TXT, 10)).grid(row=4, column=1, sticky="w", pady=6)

        self.v_scale = tk.StringVar(value=str(config.clamp_scale(cfg.get("ui_scale"))))
        self.e_scale = self.spinbox(p, self.v_scale, config.SCALE_MIN, config.SCALE_MAX, 5, "%.0f", 6)
        self._grid_row(p, 5, t("st_scale"), self.e_scale, t("st_scale_hint"))

        self.v_upd = tk.BooleanVar(value=bool(cfg.get("check_updates", True)))
        Check(p, t("st_check_updates"), self.v_upd, self.C, (self.F_TXT, 10)).grid(row=6, column=1, sticky="w", pady=6)

        def ok_scale():
            v = _to_float(self.v_scale.get())
            return config.SCALE_MIN <= v <= config.SCALE_MAX and v == int(v)
        rc = self.add_check(self.checks, 0, [self.e_scale], ok_scale)
        self.v_scale.trace_add("write", lambda *a: rc())

    # ---------------- logging
    def _tab_log(self, p, cfg):
        self.v_interval = tk.StringVar(value=f"{config.clamp_interval(cfg.get('interval')):.1f}")
        self.e_interval = self.spinbox(p, self.v_interval, config.INTERVAL_MIN, config.INTERVAL_MAX, 0.5, "%.1f", 7)
        self._grid_row(p, 0, t("st_interval"), self.e_interval, t("st_interval_hint"))

        def ok_interval():
            return config.INTERVAL_MIN <= _to_float(self.v_interval.get()) <= config.INTERVAL_MAX
        rc = self.add_check(self.checks, 1, [self.e_interval], ok_interval)
        self.v_interval.trace_add("write", lambda *a: rc())

        self.v_logging = tk.BooleanVar(value=bool(cfg.get("logging")))
        Check(p, t("st_logging"), self.v_logging, self.C, (self.F_TXT, 10)).grid(row=1, column=1, sticky="w", pady=6)

        self.v_logall = tk.BooleanVar(value=bool(cfg.get("log_all")))
        self.label(p, t("st_data")).grid(row=2, column=0, sticky="w", padx=(0, 16), pady=6)
        fr = tk.Frame(p, bg=self.C["bg"])
        fr.grid(row=2, column=1, sticky="w", pady=6)
        self.v_logall_req = tk.BooleanVar(value=not self.v_logall.get())
        self.v_logall_all = tk.BooleanVar(value=self.v_logall.get())

        def pick(all_):
            self.v_logall.set(all_)
            self.v_logall_req.set(not all_)
            self.v_logall_all.set(all_)
        Check(fr, t("sw_required"), self.v_logall_req, self.C, (self.F_TXT, 10), command=lambda: pick(False)
              ).pack(side="left")
        Check(fr, t("sw_all"), self.v_logall_all, self.C, (self.F_TXT, 10), command=lambda: pick(True)
              ).pack(side="left", padx=(18, 0))

        self.v_logdir = tk.StringVar(value=str(cfg.get("log_dir") or ""))
        self.label(p, t("st_logdir")).grid(row=3, column=0, sticky="w", padx=(0, 16), pady=6)
        row = tk.Frame(p, bg=self.C["bg"])
        row.grid(row=3, column=1, columnspan=2, sticky="w", pady=6)
        self.e_logdir = self.entry(row, self.v_logdir, 46)
        self.e_logdir.pack(side="left", ipady=3)
        self.button(row, t("st_browse"), self.browse_logdir).pack(side="left", padx=(8, 0))
        self.button(row, t("st_openfolder"), self.open_logdir).pack(side="left", padx=(8, 0))
        self.label(p, t("st_logdir_hint"), dim=True, size=9).grid(row=4, column=1, sticky="w", pady=(0, 6))

        delims = ["auto", ";", ",", "\t"]
        cur = str(cfg.get("csv_delimiter", "auto"))
        if cur not in delims:
            delims.append(cur)
        self.delim_vals = delims
        self.cb_delim = self.combo(p, [t("st_tab") if d == "\t" else (t("st_auto") if d == "auto" else d)
                                       for d in delims], delims.index(cur), 14)
        self._grid_row(p, 5, t("st_delim"), self.cb_delim)

        decs = ["auto", ",", "."]
        cur = str(cfg.get("csv_decimal", "auto"))
        if cur not in decs:
            decs.append(cur)
        self.dec_vals = decs
        self.cb_dec = self.combo(p, [t("st_auto") if d == "auto" else d for d in decs], decs.index(cur), 14)
        self._grid_row(p, 6, t("st_decimal"), self.cb_dec)

    def browse_logdir(self):
        d = filedialog.askdirectory(parent=self.win, initialdir=config.log_dir({"log_dir": self.v_logdir.get()})
                                    if os.path.isdir(config.log_dir({"log_dir": self.v_logdir.get()})) else None)
        if d:
            self.v_logdir.set(os.path.normpath(d))

    def open_logdir(self):
        d = config.log_dir({"log_dir": self.v_logdir.get()})
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except OSError:
            self.say(t("st_open_err"), error=True)

    # ---------------- data source (+ HWiNFO options)
    def _tab_hw(self, p, cfg):
        C = self.C
        self.be_vals = list(backend.BACKENDS)
        cur = config.norm_backend(cfg.get("backend"))
        self.cb_backend = self.combo(p, [t("st_be_" + v) for v in self.be_vals], self.be_vals.index(cur), 46)
        self._grid_row(p, 0, t("st_backend"), self.cb_backend)
        self.lbl_src = tk.Label(p, text="", bg=C["bg"], fg=C["fg"], font=(self.F_TXT, 10), anchor="w",
                                justify="left", wraplength=600)
        self.lbl_src.grid(row=1, column=1, columnspan=2, sticky="w", pady=(2, 2))
        self.lbl_avail = self.label(p, "", dim=True, size=9, wrap=600)
        self.lbl_avail.grid(row=2, column=1, columnspan=2, sticky="w")
        self.lbl_notes = tk.Label(p, text="", bg=C["bg"], fg=C["yellow"], font=(self.F_TXT, 9), anchor="w",
                                  justify="left", wraplength=600)
        self.lbl_notes.grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.small_btn(p, t("st_refresh"), self.update_sources, bg=C["bg"]).grid(row=4, column=1, sticky="w",
                                                                                   pady=(4, 8))

        self.v_lhm = tk.StringVar(value=str(cfg.get("lhm_url") or "auto"))
        self.e_lhm = self.entry(p, self.v_lhm, 52)
        self.label(p, t("st_lhm_url")).grid(row=5, column=0, sticky="w", padx=(0, 16), pady=(6, 0))
        self.e_lhm.grid(row=5, column=1, columnspan=2, sticky="w", pady=(6, 0), ipady=3)
        self.label(p, t("st_lhm_hint"), dim=True, size=9, wrap=600).grid(row=6, column=1, columnspan=2,
                                                                          sticky="w", pady=(0, 6))

        def ok_lhm():
            v = self.v_lhm.get().strip().lower()
            return v in ("", "auto") or re.match(r"^https?://[^\s/]+(/\S*)?$", v) is not None
        rc = self.add_check(self.checks, 2, [self.e_lhm], ok_lhm)
        self.v_lhm.trace_add("write", lambda *a: rc())

        self.v_pm = tk.StringVar(value=str(cfg.get("presentmon_path") or ""))
        self.label(p, t("st_pm_path")).grid(row=7, column=0, sticky="w", padx=(0, 16), pady=(6, 0))
        row = tk.Frame(p, bg=C["bg"])
        row.grid(row=7, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.e_pm = self.entry(row, self.v_pm, 52)
        self.e_pm.pack(side="left", ipady=3)
        self.button(row, t("st_browse"), self.browse_pm).pack(side="left", padx=(8, 0))
        self.label(p, t("st_pm_hint"), dim=True, size=9, wrap=600).grid(row=8, column=1, columnspan=2,
                                                                         sticky="w", pady=(0, 6))

        def ok_pm():
            v = self.v_pm.get().strip()
            return v == "" or os.path.isfile(os.path.expandvars(v))
        rc2 = self.add_check(self.checks, 2, [self.e_pm], ok_pm)
        self.v_pm.trace_add("write", lambda *a: rc2())

        tk.Frame(p, bg=C["border"], height=1).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(14, 10))
        self.label(p, "HWiNFO", bold=True).grid(row=10, column=0, sticky="w")
        self.label(p, t("st_hw_only"), dim=True, size=9, wrap=600).grid(row=10, column=1, columnspan=2, sticky="w")

        self.v_hwpath = tk.StringVar(value=str(cfg.get("hwinfo_path") or "auto"))
        self.label(p, t("st_hwpath")).grid(row=11, column=0, sticky="w", padx=(0, 16), pady=6)
        row = tk.Frame(p, bg=C["bg"])
        row.grid(row=11, column=1, columnspan=2, sticky="w", pady=6)
        self.e_hwpath = self.entry(row, self.v_hwpath, 52)
        self.e_hwpath.pack(side="left", ipady=3)
        self.button(row, t("st_browse"), self.browse_hw).pack(side="left", padx=(8, 0))
        self.lbl_detected = self.label(p, "", dim=True, size=9, wrap=560)
        self.lbl_detected.grid(row=12, column=1, columnspan=2, sticky="w", pady=(0, 10))
        self._detect_job = None

        def ok_path():
            v = self.v_hwpath.get().strip()
            return v == "" or v.lower() == "auto" or os.path.isfile(os.path.expandvars(v))
        rc3 = self.add_check(self.checks, 2, [self.e_hwpath], ok_path)

        def changed(*a):
            rc3()
            if self._detect_job:
                self.win.after_cancel(self._detect_job)
            self._detect_job = self.win.after(250, self.update_detected)
        self.v_hwpath.trace_add("write", changed)

        self.v_manage = tk.BooleanVar(value=bool(cfg.get("manage_hwinfo")))
        self.v_sync = tk.BooleanVar(value=bool(cfg.get("sync_hwinfo_interval")))
        Check(p, t("st_manage"), self.v_manage, C, (self.F_TXT, 10)).grid(row=13, column=1, columnspan=2,
                                                                         sticky="w", pady=6)
        Check(p, t("st_sync"), self.v_sync, C, (self.F_TXT, 10)).grid(row=14, column=1, columnspan=2,
                                                                     sticky="w", pady=6)

        tasks = self.api.refresh_tasks()
        self.lbl_tasks = tk.Label(p, text=t("st_tasks_ok") if tasks else t("st_tasks_no"), bg=C["bg"],
                                  fg=C["green"] if tasks else C["yellow"], font=(self.F_TXT, 10),
                                  anchor="w", justify="left", wraplength=560)
        self.lbl_tasks.grid(row=15, column=1, columnspan=2, sticky="w", pady=(14, 6))
        self.update_detected()
        self.update_sources()

    def update_sources(self):
        """Active source, what is available on this PC, and which data the active source cannot provide."""
        src = self.api.source
        try:
            hw = backend.hwinfo_available()
            lhm = backend.lhm_available({"lhm_url": self.v_lhm.get().strip() or "auto"})
        except Exception:
            hw = lhm = False
        via = src.via()
        yes, no = t("st_av_yes"), t("st_av_no")
        avail = (f"{t('st_avail')}:   {t('st_av_hw')}: {yes if hw else no}   ·   {t('st_av_lhm')}: "
                 f"{yes if lhm else no}   ·   {t('st_av_builtin')}: {t('st_av_always')}")
        notes = src.notes()
        try:
            self.lbl_src.config(text=t("st_active", name=backend.display_name(src.active) +
                                       (f" ({via.upper()})" if via else "")))
            self.lbl_avail.config(text=avail)
            self.lbl_notes.config(text=(t("st_missing") + ":\n• " + "\n• ".join(notes)) if notes else "")
        except tk.TclError:
            pass

    def browse_pm(self):
        f = filedialog.askopenfilename(parent=self.win, title="PresentMon", filetypes=[("*.exe", "*.exe")])
        if f:
            self.v_pm.set(os.path.normpath(f))

    def update_detected(self):
        self._detect_job = None
        v = self.v_hwpath.get().strip() or "auto"
        try:
            path = self.api.detect(v)
        except Exception:
            path = ""
        found = bool(path) and os.path.isfile(path)
        txt = f"{t('st_detected')}: {path}" + ("" if found else f"  ({t('st_notfound')})")
        try:
            self.lbl_detected.config(text=txt, fg=self.C["dim"] if found else self.C["yellow"])
        except tk.TclError:
            pass

    def browse_hw(self):
        cur = self.api.detect(self.v_hwpath.get().strip() or "auto")
        f = filedialog.askopenfilename(parent=self.win, title="HWiNFO64.EXE",
                                       initialdir=os.path.dirname(cur) if os.path.isdir(os.path.dirname(cur)) else None,
                                       filetypes=[("HWiNFO64.EXE", "HWiNFO64.EXE"), ("*.exe", "*.exe")])
        if f:
            self.v_hwpath.set(os.path.normpath(f))

    # ---------------- layout
    def _tab_layout(self):
        C = self.C
        is_auto, lay = self.api.layout()
        self.lay = lay
        self.lay_mode = "keep"            # 'keep' | 'explicit' | 'auto'
        self.lay_is_auto = is_auto
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text=t("st_tab_layout"))
        self.layout_tab = tab
        tool = tk.Frame(tab, bg=C["bg"])
        tool.pack(fill="x", padx=16, pady=(14, 6))
        self.button(tool, t("st_lay_reset"), self.reset_auto).pack(side="left")
        self.button(tool, t("st_lay_add"), self.add_sensor_dialog).pack(side="left", padx=8)
        self.button(tool, t("st_lay_open"), self.open_config).pack(side="left")
        self.lbl_lay = tk.Label(tab, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9), anchor="w",
                                justify="left")
        self.lbl_lay.pack(fill="x", padx=16)
        self.lay_sf = ScrollFrame(tab, C["bg"])
        self.lay_sf.pack(fill="both", expand=True, padx=(16, 4), pady=(6, 10))
        self.lay_sf.inner.configure(padx=0, pady=0)
        self.scrolls.append(self.lay_sf)
        self.layout_tool = tool
        self.render_layout()

    def _lay_note(self):
        if not self.lay:
            txt = t("st_lay_empty")
        elif self.lay_mode == "auto" or (self.lay_mode == "keep" and self.lay_is_auto):
            txt = t("st_lay_auto_note")
        else:
            txt = ""
        try:
            self.lbl_lay.config(text=txt)
        except tk.TclError:
            pass

    def _dirty(self):
        if self.lay_mode != "explicit":
            self.lay_mode = "explicit"
            self._lay_note()

    def sync_layout(self):
        """Copy the edited field values back into the working layout dicts."""
        for sec, tv, cv_ in self.sec_ui:
            sec["title"] = tv.get()
        for row, nv, gv, rv, bv, vv in self.row_ui:
            name = nv.get().strip()
            if name:
                row["name"] = name
            g, r = gv.get().strip(), rv.get().strip()
            if not g and not r:
                row.pop("limits", None)
                row.pop("bar", None)
            else:
                try:
                    gf, rf = _to_float(g), _to_float(r)
                    if row.get("limits") != [gf, rf]:
                        row["limits"] = [gf, rf]
                    if "bar" in row or not bv.get():
                        row["bar"] = bool(bv.get())
                except ValueError:
                    pass
            if not vv.get():
                row["visible"] = False
            elif "visible" in row:
                row["visible"] = True

    def render_layout(self):
        C = self.C
        sf = self.lay_sf
        y0 = sf.cv.yview()[0]
        for w in sf.inner.winfo_children():
            w.destroy()
        self.lay_checks.clear()
        self.row_ui, self.sec_ui = [], []
        font = (self.F_TXT, 10)
        for si, sec in enumerate(self.lay):
            card = tk.Frame(sf.inner, bg=C["card"], highlightthickness=1, highlightbackground=C["border"])
            card.pack(fill="x", padx=(0, 6), pady=(0, 10))
            hdr = tk.Frame(card, bg=C["card"])
            hdr.pack(fill="x", padx=10, pady=(10, 4))
            tv = tk.StringVar(value=str(sec.get("title", "")))
            self.label(hdr, t("st_lay_title"), dim=True, bg=C["card"], size=9).pack(side="left", padx=(0, 6))
            te = self.entry(hdr, tv, 26)
            te.pack(side="left", ipady=2)
            tv.trace_add("write", lambda *a: self._dirty())
            colv = sec.get("color") or layout.ICON_COLORS.get(sec.get("icon"), ACCENT)
            sw = tk.Button(hdr, text="  ", bg=colv, activebackground=colv, relief="flat", bd=0, width=3, cursor="hand2",
                           highlightthickness=1, highlightbackground=C["border"])
            sw.config(command=lambda s=sec, b=sw: self.pick_color(s, b))
            self.label(hdr, t("st_lay_color"), dim=True, bg=C["card"], size=9).pack(side="left", padx=(14, 6))
            sw.pack(side="left", ipady=1)
            self.small_btn(hdr, "▼", lambda i=si: self.move_section(i, 1)).pack(side="right")
            self.small_btn(hdr, "▲", lambda i=si: self.move_section(i, -1)).pack(side="right", padx=(0, 4))
            self.sec_ui.append((sec, tv, colv))
            body = tk.Frame(card, bg=C["card"])
            body.pack(fill="x", padx=10, pady=(0, 10))
            for ci, txt in enumerate((t("st_lay_show"), t("st_lay_name"), t("st_lay_green"), t("st_lay_red"),
                                      t("st_lay_bar"))):
                tk.Label(body, text=txt, bg=C["card"], fg=C["faint"], font=(self.F_TXT, 8), anchor="w"
                         ).grid(row=0, column=ci, sticky="w", padx=(4, 6))
            body.grid_columnconfigure(1, weight=1)
            for ri, row in enumerate(sec["rows"]):
                gr = ri + 1
                vv = tk.BooleanVar(value=row.get("visible", True) is not False)
                Check(body, "", vv, C, font, bg=C["card"], command=self._dirty).grid(row=gr, column=0, padx=(8, 6),
                                                                                    pady=2, sticky="w")
                nv = tk.StringVar(value=str(row.get("name") or row.get("label") or ""))
                ne = self.entry(body, nv, 30)
                ne.grid(row=gr, column=1, ipady=2, padx=(0, 6), pady=2, sticky="we")
                lim = row.get("limits")
                has_lim = isinstance(lim, (list, tuple)) and len(lim) == 2
                gv = tk.StringVar(value=_fmt_num(lim[0]) if has_lim else "")
                rv = tk.StringVar(value=_fmt_num(lim[1]) if has_lim else "")
                ge = self.entry(body, gv, 8)
                ge.grid(row=gr, column=2, ipady=2, padx=(0, 6), pady=2)
                re_ = self.entry(body, rv, 8)
                re_.grid(row=gr, column=3, ipady=2, padx=(0, 6), pady=2)
                bar = row.get("bar")
                bv = tk.BooleanVar(value=(has_lim and lim[0] != lim[1]) if bar is None else bool(bar))
                Check(body, "", bv, C, font, bg=C["card"], command=self._dirty).grid(row=gr, column=4, padx=(8, 6),
                                                                                    pady=2, sticky="w")
                bf = tk.Frame(body, bg=C["card"])
                bf.grid(row=gr, column=5, padx=(4, 0), pady=2, sticky="e")
                self.small_btn(bf, "▲", lambda i=si, j=ri: self.move_row(i, j, -1)).pack(side="left", padx=(0, 2))
                self.small_btn(bf, "▼", lambda i=si, j=ri: self.move_row(i, j, 1)).pack(side="left", padx=(0, 2))
                self.small_btn(bf, "✕", lambda i=si, j=ri: self.delete_row(i, j)).pack(side="left")
                nv.trace_add("write", lambda *a: self._dirty())
                self.row_ui.append((row, nv, gv, rv, bv, vv))

                def lim_ok(gv=gv, rv=rv):
                    g, r = gv.get().strip(), rv.get().strip()
                    if not g and not r:
                        return True
                    return _to_float(g) != _to_float(r)
                rc = self.add_check(self.lay_checks, 3, [ge, re_], lim_ok)
                gv.trace_add("write", lambda *a, rc=rc: (rc(), self._dirty()))
                rv.trace_add("write", lambda *a, rc=rc: (rc(), self._dirty()))
        self._lay_note()
        sf.inner.update_idletasks()
        sf.cv.configure(scrollregion=sf.cv.bbox("all"))
        sf.cv.yview_moveto(y0)

    def pick_color(self, sec, btn):
        cur = sec.get("color") or layout.ICON_COLORS.get(sec.get("icon"), ACCENT)
        res = colorchooser.askcolor(color=cur, parent=self.win, title=t("st_lay_color"))
        if res and res[1]:
            sec["color"] = res[1].lower()
            btn.config(bg=sec["color"], activebackground=sec["color"])
            self._dirty()

    def move_section(self, i, d):
        j = i + d
        if 0 <= j < len(self.lay):
            self.sync_layout()
            self.lay[i], self.lay[j] = self.lay[j], self.lay[i]
            self._dirty()
            self.render_layout()

    def move_row(self, si, ri, d):
        rows = self.lay[si]["rows"]
        j = ri + d
        if 0 <= j < len(rows):
            self.sync_layout()
            rows[ri], rows[j] = rows[j], rows[ri]
            self._dirty()
            self.render_layout()

    def delete_row(self, si, ri):
        self.sync_layout()
        rows = self.lay[si]["rows"]
        del rows[ri]
        if not rows:
            del self.lay[si]
        self._dirty()
        self.render_layout()

    def reset_auto(self):
        if not messagebox.askyesno(t("st_title"), t("st_lay_reset_q"), parent=self.win):
            return
        rd = self.api.readings()
        self.lay = layout.autodetect(rd) if rd else []
        self.lay_mode = "auto"
        self.render_layout()

    def open_config(self):
        try:
            os.startfile(config.config_path())
        except OSError:
            self.say(t("st_open_err"), error=True)

    # ---------------- add sensor dialog
    def add_sensor_dialog(self):
        C = self.C
        rd = self.api.readings()
        if not rd:
            messagebox.showinfo(t("st_add_title"), t("st_add_none"), parent=self.win)
            return
        dlg = tk.Toplevel(self.win)
        dlg.title(t("st_add_title"))
        dlg.configure(bg=C["bg"])
        dlg.transient(self.win)
        try:
            dlg.attributes("-topmost", bool(self.root.attributes("-topmost")))
        except tk.TclError:
            pass
        body = tk.Frame(dlg, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=12)
        top = tk.Frame(body, bg=C["bg"])
        top.pack(fill="x")
        self.label(top, t("st_search")).pack(side="left", padx=(0, 8))
        qv = tk.StringVar()
        qe = self.entry(top, qv, 40)
        qe.pack(side="left", fill="x", expand=True, ipady=3)
        tf = tk.Frame(body, bg=C["bg"])
        tf.pack(fill="both", expand=True, pady=(10, 8))
        cols = ("sensor", "label", "unit", "value")
        tree = ttk.Treeview(tf, columns=cols, show="headings", style="HP.Treeview", selectmode="extended", height=16)
        for c, txt, wd in (("sensor", t("st_col_sensor"), 280), ("label", t("st_col_label"), 280),
                           ("unit", t("st_col_unit"), 60), ("value", t("st_col_value"), 90)):
            tree.heading(c, text=txt, anchor="w")
            tree.column(c, width=wd, anchor="e" if c == "value" else "w", stretch=c in ("sensor", "label"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview, style="HP.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        info = tk.Label(body, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9), anchor="w")
        info.pack(fill="x")
        bot = tk.Frame(body, bg=C["bg"])
        bot.pack(fill="x", pady=(8, 0))
        self.label(bot, t("st_add_to")).pack(side="left", padx=(0, 8))
        sec_cb = ttk.Combobox(bot, state="readonly", width=34, style="HP.TCombobox", font=(self.F_TXT, 10))
        sec_cb.pack(side="left")

        def fill_sections():
            self.sync_layout()
            names = [s.get("title") or "-" for s in self.lay] + [t("st_custom_section") + " +"]
            sec_cb.config(values=names)
            if sec_cb.current() < 0 or sec_cb.current() >= len(names):
                sec_cb.current(len(names) - 2 if len(names) > 1 else 0)

        fill_sections()
        shown = []

        def refill(*a):
            words = qv.get().lower().split()
            tree.delete(*tree.get_children())
            shown.clear()
            for r in rd:
                hay = f"{r['sensor']} {r['label']} {r['unit']}".lower()
                if all(w in hay for w in words):
                    shown.append(r)
                    tree.insert("", "end", iid=str(len(shown) - 1),
                                values=(r["sensor"], r["label"], r["unit"], f"{r['value']:.2f}"))
            info.config(text=f"{len(shown)} / {len(rd)}")
        qv.trace_add("write", refill)
        refill()

        def do_add(e=None):
            sel = [shown[int(i)] for i in tree.selection()]
            if not sel:
                return
            self.sync_layout()
            idx = sec_cb.current()
            if idx < 0 or idx >= len(self.lay):
                self.lay.append({"title": t("st_custom_section"), "color": layout.ICON_COLORS["gauge"],
                                 "icon": "gauge", "rows": []})
                idx = len(self.lay) - 1
            for r in sel:
                d = {"name": r["label"]}
                if r["sensor"]:
                    d["sensor"] = "^" + re.escape(r["sensor"]) + "$"
                d["label"] = "^" + re.escape(r["label"]) + "$"
                d["type"] = r["type"]
                self.lay[idx]["rows"].append(d)
            self._dirty()
            self.render_layout()
            fill_sections()
            sec_cb.current(idx)
            info.config(text=f"+{len(sel)}  ({self.lay[idx].get('title')})")

        tree.bind("<Double-1>", do_add)
        tree.bind("<Return>", do_add)
        self.button(bot, t("st_add_btn"), do_add, primary=True).pack(side="left", padx=(10, 0))
        self.button(bot, t("st_close"), dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        w, h = 1000, 620
        self._center(dlg, w, h)
        dlg.after(50, qe.focus_set)

    # ------------------------------------------------------------ sizing / placement
    def _fit(self, first):
        win = self.win
        win.update_idletasks()
        mon = self.api.monitor()
        if mon:
            wx, wy, ww, wh = mon["wx"], mon["wy"], mon["ww"], mon["wh"]
        else:
            wx, wy, ww, wh = 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()
        chrome = 190
        avail_h = int(wh * 0.92) - chrome
        simple = [sf.req_height() for sf in self.scrolls if sf is not self.lay_sf]
        lay_h = self.lay_sf.req_height() + self.layout_tool.winfo_reqheight() + 70
        want = max(simple + [min(lay_h, 560)]) + 10
        content_h = max(300, min(want, avail_h))
        width = max([sf.req_width() for sf in self.scrolls] + [700]) + 56
        width = min(width, int(ww * 0.95))
        for sf in self.scrolls:
            if sf is self.lay_sf:
                continue
            sf.cv.configure(height=content_h, width=width - 70)
        lay_list_h = content_h - self.layout_tool.winfo_reqheight() - 40
        self.lay_sf.cv.configure(height=max(150, lay_list_h), width=width - 70)
        win.update_idletasks()
        w = max(width, win.winfo_reqwidth())
        h = min(win.winfo_reqheight(), int(wh * 0.95))
        if first:
            self._center(win, w, h)
        else:
            win.geometry(f"{w}x{h}")
        try:
            win.attributes("-topmost", bool(self.root.attributes("-topmost")))
        except tk.TclError:
            pass
        if first:
            win.deiconify()
            self._dark_title()
            win.lift()
            win.focus_force()

    def _center(self, win, w, h):
        mon = self.api.monitor()
        if mon:
            x = mon["wx"] + (mon["ww"] - w) // 2
            y = mon["wy"] + (mon["wh"] - h) // 2
            x = max(mon["wx"], min(x, mon["wx"] + mon["ww"] - w))      # monitors can have negative coordinates
            y = max(mon["wy"], min(y, mon["wy"] + mon["wh"] - h))
        else:
            x = max(0, (win.winfo_screenwidth() - w) // 2)
            y = max(0, (win.winfo_screenheight() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _wheel(self, e):
        try:
            idx = self.nb.index("current")
            if idx < len(self.scrolls):
                self.scrolls[idx].scroll(e.delta)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ collect / apply
    def read_ui(self):
        """Current value of every setting the dialog manages (parsed; unvalid fields left as typed)."""
        def num(s, fn, default):
            try:
                return fn(_to_float(s))
            except ValueError:
                return default
        d = {
            "language": self.lang_vals[max(0, self.cb_lang.current())],
            "columns": self.col_vals[max(0, self.cb_cols.current())],
            "monitor": self.mon_vals[max(0, self.cb_mon.current())],
            "window_mode": self.wm_vals[max(0, self.cb_wm.current())],
            "topmost": bool(self.v_top.get()),
            "check_updates": bool(self.v_upd.get()),
            "ui_scale": num(self.v_scale.get(), int, self.v_scale.get()),
            "interval": num(self.v_interval.get(), lambda v: round(v, 1), self.v_interval.get()),
            "logging": bool(self.v_logging.get()),
            "log_all": bool(self.v_logall.get()),
            "log_dir": self.v_logdir.get().strip(),
            "csv_delimiter": self.delim_vals[max(0, self.cb_delim.current())],
            "csv_decimal": self.dec_vals[max(0, self.cb_dec.current())],
            "backend": self.be_vals[max(0, self.cb_backend.current())],
            "lhm_url": self.v_lhm.get().strip() or "auto",
            "presentmon_path": self.v_pm.get().strip(),
            "hwinfo_path": self.v_hwpath.get().strip() or "auto",
            "manage_hwinfo": bool(self.v_manage.get()),
            "sync_hwinfo_interval": bool(self.v_sync.get()),
        }
        return d

    def validate(self):
        bad = None
        for tab, rc in self.checks + self.lay_checks:
            if not rc() and bad is None:
                bad = tab
        return bad

    def say(self, text, error=False):
        try:
            self.msg.config(text=text, fg=self.C["red"] if error else self.C["green"])
        except tk.TclError:
            pass

    def apply(self):
        """Validate, apply live, write config.json. Returns True when everything went through."""
        bad = self.validate()
        if bad is not None:
            self.say(t("st_invalid"), error=True)
            try:
                self.nb.select(bad)
            except tk.TclError:
                pass
            return False
        new = self.read_ui()
        changes = {k: v for k, v in new.items() if v != self.initial.get(k)}
        lay_mode, lay_cfg = self.lay_mode, None
        if lay_mode == "explicit":
            self.sync_layout()
            lay_cfg = copy.deepcopy(self.lay)
        lang_changed = "language" in changes
        ok = self.api.apply(changes, lay_mode, lay_cfg)
        self.initial = new
        if lay_mode == "explicit":
            self.lay_is_auto = False
        elif lay_mode == "auto":
            self.lay_is_auto = True
        self.lay_mode = "keep"
        if lang_changed:
            self.build_ui()
        else:
            try:
                self.win.attributes("-topmost", bool(self.root.attributes("-topmost")))
            except tk.TclError:
                pass
            self._lay_note()
            self.update_sources()
        self.say(t("st_applied") if ok else t("st_write_err"), error=not ok)
        return ok

    def save(self):
        if self.apply():
            self.close()

    def cancel(self):
        self.close()

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
