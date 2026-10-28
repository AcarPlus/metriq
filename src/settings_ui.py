"""Settings window (tk.Toplevel, themed like the panel) for metriQ.

``SettingsWindow(api)`` gets a small API object from panel.py:
    root, cfg (live dict, read only here), C (colours), fonts (text, heading, mono),
    apply(changes, lay_mode, lay_cfg) -> bool, layout() -> (is_auto, sections_cfg), readings() -> [reading],
    monitor() -> dict | None, monitors() -> [dict], refresh_tasks() -> bool, detect(path_setting) -> path,
    source (backend.Manager: the active data source)

Look: a sidebar (one entry per page, accent coloured) and rounded cards on the right (uikit.py has the widgets).
Pages are added with add_page()/_page(); settings_theme.py and crashscan_ui.py add their own.
"""
import copy
import gc
import os
import re
import threading
import tkinter as tk
import traceback
from tkinter import colorchooser, filedialog, messagebox, ttk

import backend
import config
import crashscan_ui
import perf_ui
import helptexts
import hwctl
import i18n
import layout
import logview
import procset
import themes
import uikit
import version
from settings_theme import ThemeTab
from i18n import t
from uikit import Check, ScrollFrame, Switch          # (Check / ScrollFrame are also used by the other tabs)

i18n.register({
    "tr": {
        "st_card_window": "Pencere", "st_card_display": "Görünüm", "st_card_updates": "Güncellemeler",
        "st_card_rec": "Kayıt", "st_card_files": "Dosyalar", "st_card_source": "Veri kaynağı",
        "st_card_extra": "Ek kaynaklar", "st_card_hwinfo": "HWiNFO",
    },
    "en": {
        "st_card_window": "Window", "st_card_display": "Display", "st_card_updates": "Updates",
        "st_card_rec": "Recording", "st_card_files": "Files", "st_card_source": "Data source",
        "st_card_extra": "Extra sources", "st_card_hwinfo": "HWiNFO",
    },
})

# (title key, icon of the sidebar / cards, accent token, panel icon used in the card headers)
PAGES = (("st_tab_look", "look", "accent_clock", "gauge"), ("st_tab_log", "log", "accent_temp", "temp"),
         ("st_tab_hw", "hw", "accent_power", "bolt"), ("st_tab_layout", "layout", "accent_net", "net"),
         ("st_tab_theme", "theme", "accent_fan", "fan"), ("cr_tab", "diag", "accent_fps", "fps"),
         ("pf_tab", "perf", "accent_power", "bolt"))

_bg = uikit._bg_of
themes_keys = {"theme", "theme_overrides", "value_color_mode", "value_steps"}


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
        self.F_TXT, self.F_HEAD, self.F_NUM = api.fonts_now()
        self.win = tk.Toplevel(self.root)
        self.win.title(t("st_title"))
        self.win.configure(bg=self.C["bg"])
        uikit.set_window_icon(self.win)
        self.win.transient(self.root)
        self.win.withdraw()
        self._scaling0 = None
        self._apply_ui_scale()
        self._style()
        self.tab_index = 0
        self.initial = {}
        self.pages = []
        self._closing = False            # Save / Cancel in progress: nothing may be rebuilt any more
        self._rebuild_job = None
        self._src_gen = 0                # update_sources: only the newest background probe may write its result
        self._built = False
        self.build_ui(first=True)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<MouseWheel>", self._wheel)

    def _apply_ui_scale(self):
        """The panel's "UI scale" (ui_scale, %) also scales this window: Tk's point-to-pixel factor drives every
        point-sized font and uikit.px (controls). The factor is restored when the window closes."""
        tk_ = self.root.tk
        try:
            if self._scaling0 is None:
                self._scaling0 = float(tk_.call("tk", "scaling"))
            f = config.clamp_scale(self.api.cfg.get("ui_scale")) / 100.0
            new = self._scaling0 * f
            changed = abs(float(tk_.call("tk", "scaling")) - new) > 1e-6
            if changed:
                tk_.call("tk", "scaling", new)
                uikit.forget_fonts(self.win)
        except tk.TclError:
            pass
        uikit.set_scale(self.win)

    def winfo_exists(self):
        if self._closing:
            return False
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
        """ttk is only left for the lists (Treeview) of the add-sensor dialog and the crash detective."""
        C = self.C
        s = ttk.Style(self.win)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        f = (self.F_TXT, 10)
        s.configure("HP.Treeview", background=C["bg"], fieldbackground=C["bg"], foreground=C["fg"],
                    rowheight=int(28 * config.clamp_scale(self.api.cfg.get("ui_scale")) / 100), borderwidth=0, font=f, bordercolor=C["bg"], lightcolor=C["bg"],
                    darkcolor=C["bg"], relief="flat")
        s.layout("HP.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.map("HP.Treeview", background=[("selected", C["select"])], foreground=[("selected", C["fg"])])
        s.configure("HP.Treeview.Heading", background=C["card"], foreground=C["dim"], relief="flat",
                    font=(self.F_TXT, 9, "bold"), padding=(6, 6), borderwidth=0, bordercolor=C["card"],
                    lightcolor=C["card"], darkcolor=C["card"])
        s.map("HP.Treeview.Heading", background=[("active", C["track"])], foreground=[("active", C["fg"])])

    def _dark_title(self):
        try:
            import ctypes
            hwnd = int(self.win.wm_frame(), 16)
            val = ctypes.c_int(1 if themes.current().is_dark() else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        except Exception:
            pass

    # ------------------------------------------------------------ widget factories
    def label(self, parent, text, dim=False, bold=False, bg=None, wrap=0, size=10):
        return tk.Label(parent, text=text, bg=bg or _bg(parent), fg=self.C["dim"] if dim else self.C["fg"],
                        font=(self.F_TXT, size, "bold" if bold else "normal"), anchor="w", justify="left",
                        wraplength=wrap)

    def entry(self, parent, var, width=12, bg=None, height=None, justify="left"):
        return uikit.entry(parent, self.C, var, width, (self.F_TXT, 10), justify=justify, bg=bg, height=height)

    def spinbox(self, parent, var, lo, hi, inc, fmt, width=7):
        return uikit.spin(parent, self.C, var, lo, hi, inc, fmt, width, (self.F_TXT, 10))

    def button(self, parent, text, cmd, primary=False, bg=None, width=None, font_size=10):
        return uikit.Button(parent, self.C, text, cmd, (self.F_TXT, font_size, "bold" if primary else "normal"),
                            primary=primary, bg=bg)

    def small_btn(self, parent, text, cmd, bg=None):
        icon = {"▲": "up", "▼": "down", "✕": "x"}.get(text)
        if icon:
            return uikit.Button(parent, self.C, "", cmd, (self.F_TXT, 9), small=True, icon=icon, bg=bg)
        return uikit.Button(parent, self.C, text, cmd, (self.F_TXT, 9), small=True, bg=bg)

    def combo(self, parent, values, index, width=24):
        return uikit.Combo(parent, self.C, values, index, width, (self.F_TXT, 10))

    def switch(self, parent, var, command=None, small=False):
        return uikit.Switch(parent, self.C, var, command, w=34 if small else 40, h=18 if small else 22)

    def mark(self, w, ok):
        try:
            w.set_error(not ok)
        except AttributeError:
            pass

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

    # ------------------------------------------------------------ cards and rows
    def card(self, parent, title=None, kind=None, acc=None, pady=(0, 12)):
        c = uikit.Card(parent, self.C, title, acc or self.page_acc, font_head=(self.F_HEAD, 11, "bold"),
                       bg=_bg(parent), icon_kind=kind or self.page_kind)
        c.pack(fill="x", pady=pady)
        return c

    def label_help(self, parent, text, help=None, bg=None, size=10, bold=False):
        """The label of a settings row, followed by its (i) help marker when `help` names a help text."""
        if not help:
            return self.label(parent, text, bg=bg, size=size, bold=bold)
        fr = tk.Frame(parent, bg=bg or _bg(parent))
        self.label(fr, text, bg=bg, size=size, bold=bold).pack(side="left")
        helptexts.info_icon(fr, help, bg=bg or _bg(parent)).pack(side="left", padx=(6, 0))
        return fr

    def row(self, card, label, widget=None, hint=None, help=None, stack=False):
        """One settings row of a card: label (+ help icon, + hint below) on the left, the control on the right
        (`stack`: the control goes below the label and takes the full width)."""
        bg = card.fillc
        r = card.slot()
        left = tk.Frame(card.body, bg=bg)
        self.label_help(left, label, help, bg=bg).pack(anchor="w")
        if hint:
            h = self.label(left, hint, dim=True, size=9, bg=bg)
            uikit.autowrap(h)
            h.pack(anchor="w", fill="x", pady=(2, 0))
        if widget is None:
            left.grid(row=r, column=0, columnspan=2, sticky="ew", pady=8)
        elif stack:
            left.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(10, 4))
            widget.grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        else:
            left.grid(row=r, column=0, sticky="ew", pady=8)
            widget.grid(row=r, column=1, sticky="e", padx=(18, 0), pady=8)
        return left

    def full(self, card, widget, pady=(8, 8), divider=False, sticky="ew"):
        """A widget over the full width of the card (status text, lists, ...)."""
        r = card.slot(divider)
        widget.grid(row=r, column=0, columnspan=2, sticky=sticky, pady=pady)
        return widget

    # ------------------------------------------------------------ build
    def build_ui(self, first=False):
        C = self.C
        cfg = self.api.cfg
        self._release_vars()
        self._apply_ui_scale()
        self._vars_before = {id(o) for o in gc.get_objects() if isinstance(o, tk.Variable)}
        old = list(self.win.winfo_children())
        for w in old:                    # hide now, destroy once the new content is up (no empty window in between)
            w.pack_forget()
        self.win.title(t("st_title"))
        self.checks = []
        self.lay_checks = []
        self.scrolls = []
        self.pages = []

        outer = tk.Frame(self.win, bg=C["bg"])
        outer.pack(fill="both", expand=True)
        bar = tk.Frame(outer, bg=C["bg"])
        bar.pack(side="bottom", fill="x", padx=18, pady=12)
        tk.Frame(outer, bg=C["border"], height=1).pack(side="bottom", fill="x")
        main = tk.Frame(outer, bg=C["bg"])
        main.pack(side="top", fill="both", expand=True)
        items = [(t(k), icon, C[tok]) for k, icon, tok, _ in PAGES]
        self.nav = uikit.Nav(main, C, items, self.select, (self.F_TXT, 10), (self.F_HEAD, 12, "bold"), t("st_title"),
                             bg=C["bg"])
        self.nav.pack(side="left", fill="y")
        tk.Frame(main, bg=C["border"], width=1).pack(side="left", fill="y")
        content = tk.Frame(main, bg=C["bg"])
        content.pack(side="left", fill="both", expand=True)
        self.title_lbl = tk.Label(content, text="", bg=C["bg"], fg=C["fg"], font=(self.F_HEAD, 16, "bold"), anchor="w")
        self.title_lbl.pack(fill="x", padx=(26, 20), pady=(18, 8))
        self.host = tk.Frame(content, bg=C["bg"])
        self.host.pack(fill="both", expand=True, padx=(26, 8), pady=(0, 6))

        # the first three pages hold the plain settings (cheap); layout, theme, diagnostics and performance are built
        # on the first visit (the layout page alone takes ~0.6 s)
        self.lay_sf = self.layout_tool = self.lbl_lay = self.theme_tab = None
        self.lay, self.lay_mode, self.lay_is_auto = [], "keep", None
        self._lazy = {}
        self._theme0 = ThemeTab.initial_values(cfg)
        self._tab_look(self._page(0).inner, cfg)
        self._tab_log(self._page(1).inner, cfg)
        self._tab_hw(self._page(2).inner, cfg)
        self._page(3, scroll=False)
        self._lazy[3] = self._tab_layout
        self._page(4, scroll=False)
        self._lazy[4] = lambda: self._tab_theme(cfg)
        self._page(5)
        self._lazy[5] = lambda: crashscan_ui.add_tab(self)
        self._page(6)
        self._lazy[6] = lambda: perf_ui.add_tab(self)

        tk.Label(bar, text=f"{config.APP_NAME} v{version.__version__}", bg=C["bg"], fg=C["faint"],
                 font=(self.F_TXT, 9)).pack(side="left")
        self.msg = tk.Label(bar, text="", bg=C["bg"], fg=C["red"], font=(self.F_TXT, 10), anchor="w", justify="left")
        self.msg.pack(side="left", padx=(18, 0))
        self.button(bar, t("st_save"), self.save, primary=True).pack(side="right")
        self.button(bar, t("st_apply"), self.apply).pack(side="right", padx=(0, 8))
        self.button(bar, t("st_cancel"), self.cancel).pack(side="right", padx=(0, 8))

        self.initial = self.read_ui()
        self.select(min(self.tab_index, len(self.pages) - 1))
        self._fit(first)
        self._built = True
        if old:
            self.win.after_idle(lambda: self._destroy_all(old))

    def _release_vars(self):
        """Remove the traces of every Tk variable made by the last build. A trace is a Tcl command that holds a Python
        closure, which holds the widgets: without this each build (and every open / close) leaked its whole widget tree,
        its images and their GDI bitmaps until the process hit the 10 000 handle limit ("Fail to allocate bitmap")."""
        before = getattr(self, "_vars_before", None)
        if before is None:
            return
        for o in gc.get_objects():
            if isinstance(o, tk.Variable) and id(o) not in before:
                try:
                    for mode, cb in o.trace_info():
                        o.trace_remove(mode, cb)
                except (tk.TclError, RuntimeError, AttributeError):
                    pass

    @staticmethod
    def _destroy_all(widgets):
        for w in widgets:
            try:
                w.destroy()
            except tk.TclError:
                pass

    def rebuild_later(self):
        """Language / theme changed: rebuild the pages once the current event is finished (never inside the click
        handler of a widget that is about to be destroyed)."""
        if self._rebuild_job is None and not self._closing:
            self._rebuild_job = self.win.after_idle(self._rebuild)

    def _rebuild(self):
        self._rebuild_job = None
        if self._closing or not self.winfo_exists():
            return
        try:
            self.build_ui()
        except tk.TclError:
            traceback.print_exc()

    # ---------------- pages
    def _page(self, i, scroll=True):
        """Start page i of PAGES; returns its ScrollFrame (scroll=True) or the plain frame the caller fills."""
        key, icon, tok, kind = PAGES[i]
        C = self.C
        self.page_acc, self.page_kind = C[tok], kind
        fr = ScrollFrame(self.host, C["bg"], C) if scroll else tk.Frame(self.host, bg=C["bg"])
        if scroll:
            fr.inner.configure(padx=0, pady=2)
            self.scrolls.append(fr)
        self.pages.append({"frame": fr, "title": t(key), "sf": fr if scroll else None})
        return fr

    def add_page(self, i):
        """The frame prepared for page i by build_ui (layout, theme and diagnostics tabs fill it themselves)."""
        self.page_acc, self.page_kind = self.C[PAGES[i][2]], PAGES[i][3]
        fr = self.pages[i]["frame"]
        return getattr(fr, "inner", fr)

    def page_count(self):
        return len(self.pages)

    def n_pages(self):
        return len(self.pages)

    def select(self, i):
        i = max(0, min(int(i), len(self.pages) - 1))
        self._ensure(i)
        for pg in self.pages:
            pg["frame"].pack_forget()
        pg = self.pages[i]
        pg["frame"].pack(fill="both", expand=True)
        self.tab_index = i
        self.nav.set_active(i)
        self.title_lbl.config(text=pg["title"])

    def _ensure(self, i):
        """Build page i if it has not been built yet (first visit)."""
        fn = self._lazy.pop(i, None)
        if fn is None:
            return
        self.win.configure(cursor="watch")
        try:
            fn()
            self._size_scrolls()
        finally:
            self.win.configure(cursor="")

    # ---------------- theme (see settings_theme.py)
    def _tab_theme(self, cfg):
        self.theme_tab = ThemeTab(self, cfg)

    # ---------------- appearance
    def _tab_look(self, p, cfg):
        C = self.C
        card = self.card(p, t("st_card_window"), "fps")
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
        self.cb_mon = self.combo(card.body, names, idx, 34)
        self.row(card, t("st_monitor"), self.cb_mon, help="monitor")

        self.wm_vals = ["maximized", "fullscreen", "window"]
        cur = str(cfg.get("window_mode", "maximized")).lower()
        self.cb_wm = self.combo(card.body, [t("st_wm_maximized"), t("st_wm_fullscreen"), t("st_wm_window")],
                                self.wm_vals.index(cur) if cur in self.wm_vals else 0, 20)
        self.row(card, t("st_window_mode"), self.cb_wm, help="window_mode")

        self.v_top = tk.BooleanVar(value=bool(cfg.get("topmost", True)))
        self.row(card, t("st_topmost"), self.switch(card.body, self.v_top), help="topmost")

        card = self.card(p, t("st_card_display"), "gauge")
        langs = [("auto", t("st_auto")), ("tr", "Türkçe"), ("en", "English")]
        self.lang_vals = [v for v, _ in langs]
        cur = str(cfg.get("language", "auto")).lower()
        self.cb_lang = self.combo(card.body, [n for _, n in langs],
                                  self.lang_vals.index(cur) if cur in self.lang_vals else 0, 20)
        self.row(card, t("st_language"), self.cb_lang)

        self.col_vals = ["auto", 1, 2, 3, 4]
        cur = config.norm_columns(cfg.get("columns"))
        self.cb_cols = self.combo(card.body, [t("st_auto"), "1", "2", "3", "4"], self.col_vals.index(cur), 20)
        self.row(card, t("st_columns"), self.cb_cols, help="columns")

        self.v_scale = tk.StringVar(value=str(config.clamp_scale(cfg.get("ui_scale"))))
        self.e_scale = self.spinbox(card.body, self.v_scale, config.SCALE_MIN, config.SCALE_MAX, 5, "%.0f", 6)
        self.row(card, t("st_scale"), self.e_scale, t("st_scale_hint"), help="scale")

        card = self.card(p, t("st_card_updates"), "net")
        self.v_upd = tk.BooleanVar(value=bool(cfg.get("check_updates", True)))
        self.row(card, t("st_check_updates"), self.switch(card.body, self.v_upd), help="updates")

        def ok_scale():
            v = _to_float(self.v_scale.get())
            return config.SCALE_MIN <= v <= config.SCALE_MAX and v == int(v)
        rc = self.add_check(self.checks, 0, [self.e_scale], ok_scale)
        self.v_scale.trace_add("write", lambda *a: rc())

    # ---------------- logging
    def _tab_log(self, p, cfg):
        C = self.C
        card = self.card(p, t("st_card_rec"))
        self.v_logging = tk.BooleanVar(value=bool(cfg.get("logging")))
        self.row(card, t("st_logging"), self.switch(card.body, self.v_logging), help="logging")

        self.v_interval = tk.StringVar(value=f"{config.clamp_interval(cfg.get('interval')):.1f}")
        self.e_interval = self.spinbox(card.body, self.v_interval, config.INTERVAL_MIN, config.INTERVAL_MAX, 0.5,
                                       "%.1f", 7)
        self.row(card, t("st_interval"), self.e_interval, t("st_interval_hint"), help="interval")

        def ok_interval():
            return config.INTERVAL_MIN <= _to_float(self.v_interval.get()) <= config.INTERVAL_MAX
        rc = self.add_check(self.checks, 1, [self.e_interval], ok_interval)
        self.v_interval.trace_add("write", lambda *a: rc())

        self.v_logall = tk.BooleanVar(value=bool(cfg.get("log_all")))
        seg = uikit.Segmented(card.body, C, [t("sw_required"), t("sw_all")], 1 if self.v_logall.get() else 0,
                              lambda i: self.v_logall.set(bool(i)), (self.F_TXT, 10))
        self.v_logall.trace_add("write", lambda *a: seg.set(1 if self.v_logall.get() else 0))
        self.row(card, t("st_data"), seg, help="logall")

        card = self.card(p, t("st_card_files"))
        self.v_logdir = tk.StringVar(value=str(cfg.get("log_dir") or ""))
        row = tk.Frame(card.body, bg=card.fillc)
        self.e_logdir = self.entry(row, self.v_logdir, 20)
        self.e_logdir.pack(side="left", fill="x", expand=True)
        self.button(row, t("st_browse"), self.browse_logdir).pack(side="left", padx=(8, 0))
        self.button(row, t("st_openfolder"), self.open_logdir).pack(side="left", padx=(8, 0))
        logview.add_button(self, row, lambda: config.log_dir({"log_dir": self.v_logdir.get()}))
        self.row(card, t("st_logdir"), row, t("st_logdir_hint"), help="logdir", stack=True)

        delims = ["auto", ";", ",", "\t"]
        cur = str(cfg.get("csv_delimiter", "auto"))
        if cur not in delims:
            delims.append(cur)
        self.delim_vals = delims
        self.cb_delim = self.combo(card.body, [t("st_tab") if d == "\t" else (t("st_auto") if d == "auto" else d)
                                               for d in delims], delims.index(cur), 14)
        self.row(card, t("st_delim"), self.cb_delim, help="delim")

        decs = ["auto", ",", "."]
        cur = str(cfg.get("csv_decimal", "auto"))
        if cur not in decs:
            decs.append(cur)
        self.dec_vals = decs
        self.cb_dec = self.combo(card.body, [t("st_auto") if d == "auto" else d for d in decs], decs.index(cur), 14)
        self.row(card, t("st_decimal"), self.cb_dec, help="decimal")
        self.procs = procset.ProcSection(self, p, cfg)         # per-process CSV options (procset.py)

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
        card = self.card(p, t("st_card_source"))
        bg = card.fillc
        self.be_vals = list(backend.BACKENDS)
        cur = config.norm_backend(cfg.get("backend"))
        self.cb_backend = self.combo(card.body, [t("st_be_" + v) for v in self.be_vals], self.be_vals.index(cur), 46)
        self.row(card, t("st_backend"), self.cb_backend, help="backend", stack=True)
        self.lbl_src = tk.Label(card.body, text="", bg=bg, fg=C["fg"], font=(self.F_TXT, 10, "bold"), anchor="w",
                                justify="left")
        uikit.autowrap(self.lbl_src)
        self.full(card, self.lbl_src, pady=(10, 2), divider=False)
        self.lbl_avail = self.label(card.body, "", dim=True, size=9, bg=bg)
        uikit.autowrap(self.lbl_avail)
        self.full(card, self.lbl_avail, pady=(0, 2), divider=False)
        self.lbl_notes = tk.Label(card.body, text="", bg=bg, fg=C["yellow"], font=(self.F_TXT, 9), anchor="w",
                                  justify="left")
        uikit.autowrap(self.lbl_notes)
        self.full(card, self.lbl_notes, pady=(4, 2), divider=False)
        self.full(card, self.small_btn(card.body, t("st_refresh"), self.update_sources, bg=bg), pady=(8, 2),
                  divider=False, sticky="w")

        card = self.card(p, t("st_card_extra"))
        self.v_lhm = tk.StringVar(value=str(cfg.get("lhm_url") or "auto"))
        self.e_lhm = self.entry(card.body, self.v_lhm, 20)
        self.row(card, t("st_lhm_url"), self.e_lhm, t("st_lhm_hint"), help="lhm", stack=True)

        def ok_lhm():
            v = self.v_lhm.get().strip().lower()
            return v in ("", "auto") or re.match(r"^https?://[^\s/]+(/\S*)?$", v) is not None
        rc = self.add_check(self.checks, 2, [self.e_lhm], ok_lhm)
        self.v_lhm.trace_add("write", lambda *a: rc())

        self.v_pm = tk.StringVar(value=str(cfg.get("presentmon_path") or ""))
        row = tk.Frame(card.body, bg=bg)
        self.e_pm = self.entry(row, self.v_pm, 20)
        self.e_pm.pack(side="left", fill="x", expand=True)
        self.button(row, t("st_browse"), self.browse_pm).pack(side="left", padx=(8, 0))
        self.row(card, t("st_pm_path"), row, t("st_pm_hint"), help="pm", stack=True)

        def ok_pm():
            v = self.v_pm.get().strip()
            return v == "" or os.path.isfile(os.path.expandvars(v))
        rc2 = self.add_check(self.checks, 2, [self.e_pm], ok_pm)
        self.v_pm.trace_add("write", lambda *a: rc2())

        card = self.card(p, t("st_card_hwinfo"))
        hint = self.label(card.body, t("st_hw_only"), dim=True, size=9, bg=bg)
        uikit.autowrap(hint)
        self.full(card, hint, pady=(0, 2), divider=False)
        self.v_hwpath = tk.StringVar(value=str(cfg.get("hwinfo_path") or "auto"))
        row = tk.Frame(card.body, bg=bg)
        self.e_hwpath = self.entry(row, self.v_hwpath, 20)
        self.e_hwpath.pack(side="left", fill="x", expand=True)
        self.button(row, t("st_browse"), self.browse_hw).pack(side="left", padx=(8, 0))
        self.lbl_detected = self.label(card.body, "", dim=True, size=9, bg=bg)
        uikit.autowrap(self.lbl_detected)
        self.row(card, t("st_hwpath"), row, help="hwpath", stack=True)
        self.full(card, self.lbl_detected, pady=(0, 6), divider=False)
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
        self.row(card, t("st_manage"), self.switch(card.body, self.v_manage), help="manage")
        self.row(card, t("st_sync"), self.switch(card.body, self.v_sync), help="sync")

        tasks = self.api.tasks()                          # cached; the schtasks query runs in the background below
        self.lbl_tasks = tk.Label(card.body, text=t("st_tasks_ok") if tasks else t("st_tasks_no"), bg=bg,
                                  fg=C["green"] if tasks else C["yellow"], font=(self.F_TXT, 10), anchor="w",
                                  justify="left")
        uikit.autowrap(self.lbl_tasks)
        self.full(card, self.lbl_tasks, pady=(8, 2))
        self.update_detected()
        self.update_sources()
        if not self._built:
            self._refresh_tasks_bg()

    def _refresh_tasks_bg(self):
        res = {}

        def work():
            try:
                res["v"] = bool(self.api.refresh_tasks())
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if self._closing or not self.winfo_exists():
                return
            if "v" not in res:
                self.win.after(100, poll)
                return
            try:
                ok = res["v"]
                self.lbl_tasks.config(text=t("st_tasks_ok") if ok else t("st_tasks_no"),
                                      fg=self.C["green"] if ok else self.C["yellow"])
            except (tk.TclError, AttributeError):
                pass
        self.win.after(100, poll)

    def update_sources(self):
        """Active source, what is available on this PC, and which data the active source cannot provide."""
        self._src_gen += 1
        gen = self._src_gen
        url = self.v_lhm.get().strip() or "auto"
        res = {}

        def probe():                     # an unreachable LHM can take seconds: never on the UI thread
            try:
                res["v"] = (backend.hwinfo_available(), backend.lhm_available({"lhm_url": url}))
            except Exception:
                res["v"] = (False, False)

        def poll():
            if gen != self._src_gen or self._closing or not self.winfo_exists():
                return
            if "v" in res:
                self._show_sources(*res["v"])
            else:
                self.win.after(60, poll)
        self._show_sources(None, None)
        threading.Thread(target=probe, daemon=True).start()
        self.win.after(60, poll)

    def _show_sources(self, hw, lhm):
        src = self.api.source
        via = src.via()
        yes, no = t("st_av_yes"), t("st_av_no")
        q = lambda v: "…" if v is None else (yes if v else no)
        avail = (f"{t('st_avail')}:   {t('st_av_hw')}: {q(hw)}   ·   {t('st_av_lhm')}: "
                 f"{q(lhm)}   ·   {t('st_av_builtin')}: {t('st_av_always')}")
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
        tab = self.add_page(3)
        self.layout_tab = tab
        tool = tk.Frame(tab, bg=C["bg"])
        tool.pack(fill="x", pady=(0, 6))
        self.button(tool, t("st_lay_reset"), self.reset_auto).pack(side="left")
        self.button(tool, t("st_lay_add"), self.add_sensor_dialog).pack(side="left", padx=8)
        self.button(tool, t("st_lay_open"), self.open_config).pack(side="left")
        helptexts.info_icon(tool, "layout", bg=C["bg"]).pack(side="left", padx=(12, 0))
        self.lbl_lay = tk.Label(tab, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9), anchor="w",
                                justify="left")
        self.lbl_lay.pack(fill="x")
        self.lay_sf = ScrollFrame(tab, C["bg"], C)
        self.lay_sf.pack(fill="both", expand=True, pady=(6, 4))
        self.lay_sf.inner.configure(padx=0, pady=2)
        self.pages[3]["sf"] = self.lay_sf
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
            if self.lbl_lay is not None:
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
        px = uikit.px
        for si, sec in enumerate(self.lay):
            colv = themes.accent_for(sec.get("icon"), sec.get("color"))
            card = uikit.Card(sf.inner, C, None, colv, bg=C["bg"], pad=(px(22), px(14), px(12), px(12)))
            card.pack(fill="x", padx=(0, 6), pady=(0, 12))
            cbg = card.fillc
            hdr = tk.Frame(card.body, bg=cbg)
            hdr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            tv = tk.StringVar(value=str(sec.get("title", "")))
            self.label(hdr, t("st_lay_title"), dim=True, bg=cbg, size=9).pack(side="left", padx=(0, 8))
            te = self.entry(hdr, tv, 26)
            te.pack(side="left")
            tv.trace_add("write", lambda *a: self._dirty())
            self.label(hdr, t("st_lay_color"), dim=True, bg=cbg, size=9).pack(side="left", padx=(16, 8))
            sw = uikit.Swatch(hdr, C, colv, bg=cbg)
            sw.cmd = lambda s=sec, b=sw: self.pick_color(s, b)
            sw.pack(side="left")
            self.small_btn(hdr, "▼", lambda i=si: self.move_section(i, 1), bg=cbg).pack(side="right")
            self.small_btn(hdr, "▲", lambda i=si: self.move_section(i, -1), bg=cbg).pack(side="right", padx=(0, 4))
            self.sec_ui.append((sec, tv, colv))
            body = tk.Frame(card.body, bg=cbg)
            body.grid(row=1, column=0, sticky="ew")
            for ci, (txt, hk) in enumerate(((t("st_lay_show"), "lay_show"), (t("st_lay_name"), None),
                                            (t("st_lay_green"), "lay_limits"), (t("st_lay_red"), "lay_limits"),
                                            (t("st_lay_bar"), "lay_bar"))):
                hf = tk.Frame(body, bg=cbg)
                hf.grid(row=0, column=ci, sticky="w", padx=(4, 8), pady=(2, 2))
                tk.Label(hf, text=txt, bg=cbg, fg=C["faint"], font=(self.F_TXT, 8), anchor="w").pack(side="left")
                if hk:
                    helptexts.info_icon(hf, hk, bg=cbg, size=11).pack(side="left", padx=(3, 0))
            body.grid_columnconfigure(1, weight=1)
            for ri, row in enumerate(sec["rows"]):
                gr = ri + 1
                vv = tk.BooleanVar(value=row.get("visible", True) is not False)
                self.switch(body, vv, self._dirty, small=True).grid(row=gr, column=0, padx=(4, 8), pady=3, sticky="w")
                nv = tk.StringVar(value=str(row.get("name") or row.get("label") or ""))
                ne = self.entry(body, nv, 24, height=px(30))
                ne.grid(row=gr, column=1, padx=(0, 8), pady=3, sticky="we")
                lim = row.get("limits")
                has_lim = isinstance(lim, (list, tuple)) and len(lim) == 2
                gv = tk.StringVar(value=_fmt_num(lim[0]) if has_lim else "")
                rv = tk.StringVar(value=_fmt_num(lim[1]) if has_lim else "")
                ge = self.entry(body, gv, 6, height=px(30))
                ge.grid(row=gr, column=2, padx=(0, 8), pady=3)
                re_ = self.entry(body, rv, 6, height=px(30))
                re_.grid(row=gr, column=3, padx=(0, 8), pady=3)
                bar = row.get("bar")
                bv = tk.BooleanVar(value=(has_lim and lim[0] != lim[1]) if bar is None else bool(bar))
                self.switch(body, bv, self._dirty, small=True).grid(row=gr, column=4, padx=(4, 8), pady=3, sticky="w")
                bf = tk.Frame(body, bg=cbg)
                bf.grid(row=gr, column=5, padx=(4, 0), pady=3, sticky="e")
                self.small_btn(bf, "▲", lambda i=si, j=ri: self.move_row(i, j, -1), bg=cbg).pack(side="left", padx=(0, 2))
                self.small_btn(bf, "▼", lambda i=si, j=ri: self.move_row(i, j, 1), bg=cbg).pack(side="left", padx=(0, 2))
                self.small_btn(bf, "✕", lambda i=si, j=ri: self.delete_row(i, j), bg=cbg).pack(side="left")
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
        cur = themes.accent_for(sec.get("icon"), sec.get("color"))
        res = colorchooser.askcolor(color=cur, parent=self.win, title=t("st_lay_color"))
        if res and res[1]:
            self.sync_layout()
            sec["color"] = res[1].lower()
            self._dirty()
            self.render_layout()

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
        qe.pack(side="left", fill="x", expand=True)
        tf = tk.Frame(body, bg=C["bg"])
        tf.pack(fill="both", expand=True, pady=(10, 8))
        cols = ("sensor", "label", "unit", "value")
        tree = ttk.Treeview(tf, columns=cols, show="headings", style="HP.Treeview", selectmode="extended", height=16)
        for c, txt, wd in (("sensor", t("st_col_sensor"), 280), ("label", t("st_col_label"), 280),
                           ("unit", t("st_col_unit"), 60), ("value", t("st_col_value"), 90)):
            tree.heading(c, text=txt, anchor="w")
            tree.column(c, width=wd, anchor="e" if c == "value" else "w", stretch=c in ("sensor", "label"))
        sb = uikit.Scrollbar(tf, C, tree.yview, bg=C["bg"])
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(4, 0))
        tree.pack(side="left", fill="both", expand=True)
        info = tk.Label(body, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9), anchor="w")
        info.pack(fill="x")
        bot = tk.Frame(body, bg=C["bg"])
        bot.pack(fill="x", pady=(8, 0))
        self.label(bot, t("st_add_to")).pack(side="left", padx=(0, 8))
        sec_cb = self.combo(bot, [], 0, 34)
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
        px = uikit.px
        chrome = px(170)                                   # page title + bottom bar + paddings
        avail_h = int(wh * 0.92) - chrome
        simple = [min(sf.req_height(), sf.cap or 10 ** 6) + sf.reserve for sf in self.scrolls]
        want = max(simple + [px(640)]) + 10                # (pages not built yet scroll inside the same size)
        self._content_h = max(px(360), min(want, avail_h, px(700)))
        side_w = self.nav.winfo_reqwidth()
        sc_w = max([sf.req_width() for sf in self.scrolls] + [px(720)])
        self._sc_w = min(sc_w, int(ww * 0.95) - side_w - px(60))
        self._size_scrolls()
        win.update_idletasks()
        w = min(win.winfo_reqwidth(), int(ww * 0.97))
        h = min(win.winfo_reqheight(), int(wh * 0.95))
        win.minsize(min(w, px(760)), min(h, px(480)))
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

    def _size_scrolls(self):
        """Give every built scroll area the common size (the layout page also holds a toolbar above its list)."""
        ch, sw_ = getattr(self, "_content_h", None), getattr(self, "_sc_w", None)
        if ch is None:
            return
        for sf in self.scrolls:
            if sf is self.lay_sf:
                tool_h = self.layout_tool.winfo_reqheight() + self.lbl_lay.winfo_reqheight() + 14
                sf.cv.configure(height=max(150, ch - tool_h), width=sw_)
            else:
                sf.cv.configure(height=ch - sf.reserve, width=sw_)

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
            sf = self.pages[self.tab_index]["sf"]
            if sf is not None:
                sf.scroll(e.delta)
        except (tk.TclError, IndexError):
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
            **self.procs.values(),
            "backend": self.be_vals[max(0, self.cb_backend.current())],
            "lhm_url": self.v_lhm.get().strip() or "auto",
            "presentmon_path": self.v_pm.get().strip(),
            "hwinfo_path": self.v_hwpath.get().strip() or "auto",
            "manage_hwinfo": bool(self.v_manage.get()),
            "sync_hwinfo_interval": bool(self.v_sync.get()),
        }
        if self.theme_tab is not None:
            d.update(self.theme_tab.read())
        else:                                    # theme page not visited yet: unchanged
            cfg = self.api.cfg
            d.update({"theme": self._theme0["theme"], "theme_overrides": dict(self._theme0["theme_overrides"]),
                      "value_color_mode": self._theme0["value_color_mode"],
                      "value_steps": list(self._theme0["value_steps"])})
        return d

    def validate(self):
        bad = None
        for tab, rc in self.checks + self.lay_checks:
            if not rc() and bad is None:
                bad = tab
        return bad

    def say(self, text, error=False):
        """Short notice under the tabs: ~2 s for "applied", ~4 s for errors, then a quick fade-out."""
        col = self.C["red"] if error else self.C["green"]
        try:
            if getattr(self, "_say_job", None):
                self.msg.after_cancel(self._say_job)
            self.msg.config(text=text, fg=col)
            self._say_job = self.msg.after(4000 if error else 2000, lambda: self._say_fade(col, 0))
        except tk.TclError:
            pass

    def _say_fade(self, col, n):
        try:
            if n >= 5:
                self.msg.config(text="")
                self._say_job = None
                return
            self.msg.config(fg=themes.mix(col, self.C["bg"], (n + 1) / 5))
            self._say_job = self.msg.after(40, lambda: self._say_fade(col, n + 1))
        except tk.TclError:
            pass

    def apply(self, closing=False):
        """Validate, apply live, write config.json. Returns True when everything went through.
        closing: Save is about to close the window, so it is not rebuilt for a new language / theme."""
        if self._closing:
            return False
        bad = self.validate()
        if bad is not None:
            self.say(t("st_invalid"), error=True)
            self.select(bad)
            return False
        new = self.read_ui()
        changes = {k: v for k, v in new.items() if v != self.initial.get(k)}
        lay_mode, lay_cfg = self.lay_mode, None
        if lay_mode == "explicit":
            self.sync_layout()
            lay_cfg = copy.deepcopy(self.lay)
        lang_changed = "language" in changes
        theme_changed = bool(themes_keys & set(changes))
        ok = self.api.apply(changes, lay_mode, lay_cfg)
        self.initial = new
        if lay_mode == "explicit":
            self.lay_is_auto = False
        elif lay_mode == "auto":
            self.lay_is_auto = True
        self.lay_mode = "keep"
        if closing:
            pass
        elif lang_changed or theme_changed:
            if theme_changed:          # the settings window follows the theme
                self.C, (self.F_TXT, self.F_HEAD, self.F_NUM) = self.api.C, self.api.fonts_now()
                self.win.configure(bg=self.C["bg"])
                self._style()
                self._dark_title()
            self.rebuild_later()
        else:
            try:
                self.win.attributes("-topmost", bool(self.root.attributes("-topmost")))
            except tk.TclError:
                pass
            self._lay_note()
            self.update_sources()
        if not closing:
            self.say(t("st_applied") if ok else t("st_write_err"), error=not ok)
        return ok

    def save(self):
        if self._closing:
            return
        if self.apply(closing=True):
            self.close()

    def cancel(self):
        self.close()

    def close(self):
        """Hide at once, destroy after the running event: the window is never torn down inside its own callback."""
        if self._closing:
            return
        self._closing = True
        self._src_gen += 1
        try:
            self.win.withdraw()
            self.win.after_idle(self._destroy_win)
        except tk.TclError:
            pass

    def _destroy_win(self):
        try:
            self._release_vars()
            uikit.forget_fonts(self.win)
            self.win.destroy()
        except tk.TclError:
            pass
        try:
            if self._scaling0 is not None:
                self.root.tk.call("tk", "scaling", self._scaling0)
                uikit.set_scale(self.root)
        except tk.TclError:
            pass
        self._vars_before = None
        gc.collect()
