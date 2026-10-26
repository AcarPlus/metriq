"""Process window: which program uses the CPU / RAM / GPU / VRAM / disk, and which ones go into the CSV.

Opened from the panel by clicking the name of a CPU / RAM / GPU / VRAM / disk row (``Controller.tag_row``).
Sampling (procmon.MON) runs only while the window is open.
"""
import ctypes
import os
import tkinter as tk
from tkinter import ttk

import config
import procmon
import proclog                                   # also registers the texts
from i18n import t

CATS = ("apps", "background", "windows")
COLS = ("cpu", "ram", "gpu", "vram", "disk", "log")
HEAD = {"cpu": "CPU %", "ram": "RAM MB", "gpu": "GPU %", "vram": "VRAM MB", "disk": "Disk MB/s"}
ON, OFF = "☑", "☐"
ACCENT = "#58a6ff"

# which panel rows open the window: sample reading labels of each metric (the row's regex is tested against them)
_ROW_LABELS = (
    ("cpu", ("Total CPU Usage", "CPU Total")),
    ("gpu", ("GPU Core Load", "GPU Utilization", "GPU Load")),
    ("vram", ("GPU D3D Memory Dedicated", "GPU Memory Allocated", "GPU Memory Used")),
    ("ram", ("Physical Memory Used", "Memory Used")),
)
_STATE = {"min": 1.0}                            # threshold kept while the panel runs


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
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                    ("ap", ctypes.c_ulonglong), ("tf", ctypes.c_ulonglong), ("af", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
    try:
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.tp / 1048576.0
    except Exception:
        return 16384.0


class Controller:
    """Owned by the panel: creates the window on demand and wires the panel rows to it."""

    def __init__(self, api):
        self.api = api
        self.win = None

    def tag_row(self, cv, item, row):
        metric = metric_for_row(row)
        if not metric:
            return
        cv.tag_bind(item, "<Button-1>", lambda e, m=metric: self.open(m))
        cv.tag_bind(item, "<Enter>", lambda e: cv.config(cursor="hand2"))
        cv.tag_bind(item, "<Leave>", lambda e: cv.config(cursor=""))

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


class ProcWindow:
    def __init__(self, api, metric):
        self.api, self.root, self.C = api, api.root, api.C
        self.cfg = api.cfg
        self.F_TXT, self.F_HEAD, self.F_NUM = api.fonts
        self.sort = (metric, True)                       # (column or "name", descending)
        self.items = {}                                  # iid -> True
        self.last_t = None
        self.ram_total = total_ram_mb()
        filt = proclog.filter_cfg(self.cfg)
        self.all = filt == "all"
        self.picked = set() if self.all else set(filt)
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
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
        self._style()
        self._build()
        self._place()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Control-f>", lambda e: (self.e_search.focus_set(), "break")[1])
        procmon.MON.acquire("procwin", 1.0)
        self.win.deiconify()
        self._dark_title()
        self.after_id = None
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
        if getattr(self, "after_id", None):
            try:
                self.win.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _dark_title(self):
        try:
            hwnd = int(self.win.wm_frame(), 16)
            val = ctypes.c_int(1)
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
        w, h = min(940, mw - 40), min(680, mh - 60)
        self.win.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
        self.win.minsize(min(560, w), 320)

    def _style(self):
        C = self.C
        s = ttk.Style(self.win)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("PW.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["fg"], rowheight=26,
                    borderwidth=0, font=(self.F_TXT, 10), bordercolor=C["border"], lightcolor=C["card"],
                    darkcolor=C["card"])
        s.map("PW.Treeview", background=[("selected", "#1f3a5f")], foreground=[("selected", C["fg"])])
        s.configure("PW.Treeview.Heading", background=C["track"], foreground=C["name"], relief="flat",
                    font=(self.F_TXT, 10, "bold"), borderwidth=0, padding=(6, 5))
        s.map("PW.Treeview.Heading", background=[("active", C["border"])])
        s.configure("PW.Vertical.TScrollbar", background=C["track"], troughcolor=C["bg"], bordercolor=C["bg"],
                    arrowcolor=C["dim"], lightcolor=C["track"], darkcolor=C["track"], gripcount=0)
        s.map("PW.Vertical.TScrollbar", background=[("active", C["border"])])
        s.layout("PW.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])      # no border frame

    # ------------------------------------------------------------ widgets
    def _build(self):
        C, f = self.C, (self.F_TXT, 10)
        outer = tk.Frame(self.win, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=12, pady=10)
        bar = tk.Frame(outer, bg=C["bg"])
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text=t("pw_search"), bg=C["bg"], fg=C["dim"], font=f).pack(side="left")
        self.v_search = tk.StringVar()
        self.e_search = tk.Entry(bar, textvariable=self.v_search, width=22, bg=C["track"], fg=C["fg"],
                                 insertbackground=C["fg"], relief="flat", highlightthickness=1,
                                 highlightbackground=C["border"], highlightcolor=ACCENT, font=f)
        self.e_search.pack(side="left", padx=(6, 18), ipady=3)
        self.v_search.trace_add("write", lambda *a: self.refresh(force=True))
        tk.Label(bar, text=t("pw_min"), bg=C["bg"], fg=C["dim"], font=f).pack(side="left")
        self.v_min = tk.StringVar(value=f"{_STATE['min']:g}")
        sp = tk.Spinbox(bar, textvariable=self.v_min, from_=0, to=100, increment=0.5, format="%.1f", width=5,
                        bg=C["track"], fg=C["fg"], insertbackground=C["fg"], buttonbackground=C["track"],
                        relief="flat", highlightthickness=1, highlightbackground=C["border"],
                        highlightcolor=ACCENT, font=f, justify="center", command=self._min_changed)
        sp.pack(side="left", padx=(6, 2), ipady=2)
        sp.bind("<KeyRelease>", lambda e: self._min_changed())
        tk.Label(bar, text="%", bg=C["bg"], fg=C["dim"], font=f).pack(side="left", padx=(0, 18))
        self.v_all = tk.BooleanVar(value=self.all)
        self.cb_all = tk.Checkbutton(bar, text=t("pw_all"), variable=self.v_all, command=self._all_toggled,
                                     bg=C["bg"], fg=C["fg"], activebackground=C["bg"], activeforeground=C["fg"],
                                     selectcolor=C["track"], font=f, highlightthickness=0, bd=0, cursor="hand2")
        self.cb_all.pack(side="left")
        self.lbl_count = tk.Label(bar, text="", bg=C["bg"], fg=C["faint"], font=(self.F_TXT, 9))
        self.lbl_count.pack(side="right")

        body = tk.Frame(outer, bg=C["card"], highlightthickness=1, highlightbackground=C["border"])
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, style="PW.Treeview", columns=COLS, selectmode="browse")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview, style="PW.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.column("#0", width=300, minwidth=160, stretch=True)
        for c in COLS:
            self.tree.column(c, width=96 if c != "log" else 110, minwidth=70, anchor="center" if c == "log" else "e",
                             stretch=False)
        self.tree.tag_configure("cat", background=C["track"], foreground=C["fg"], font=(self.F_TXT, 10, "bold"))
        self.tree.tag_configure("child", foreground=C["dim"])
        self.tree.tag_configure("logged", foreground=C["green"])
        self.tree.tag_configure("idle", foreground=C["faint"])
        self.tree.bind("<Button-1>", self._click)
        tk.Label(outer, text=t("pw_hint"), bg=C["bg"], fg=C["faint"], font=(self.F_TXT, 9), anchor="w").pack(
            fill="x", pady=(6, 0))
        self._headings()

    def _headings(self):
        col, desc = self.sort
        arrow = " ▼" if desc else " ▲"
        self.tree.heading("#0", text=t("pw_name") + (arrow if col == "name" else ""),
                          command=lambda: self.sort_by("name"))
        for c in COLS:
            if c == "log":
                self.tree.heading(c, text=t("pw_log_hdr") + (arrow if col == "log" else ""),
                                  command=lambda: self.sort_by("log"))
            else:
                self.tree.heading(c, text=HEAD[c] + (arrow if col == c else ""), command=lambda c=c: self.sort_by(c))

    # ------------------------------------------------------------ events
    def sort_by(self, col, desc=None):
        if desc is None:
            desc = (not self.sort[1]) if self.sort[0] == col else (col != "name")
        self.sort = (col, desc)
        self._headings()
        self.refresh(force=True)

    def _min_changed(self):
        try:
            _STATE["min"] = max(0.0, float(self.v_min.get().replace(",", ".")))
        except ValueError:
            return
        self.refresh(force=True)

    def _all_toggled(self):
        self.all = bool(self.v_all.get())
        self._save()
        self.refresh(force=True)

    def _save(self):
        value = "all" if self.all else sorted(self.picked)
        try:
            self.api.apply({"proc_log_filter": value})
        except Exception:
            pass

    def _click(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return
        row, col = self.tree.identify_row(e.y), self.tree.identify_column(e.x)
        if col != "#%d" % (COLS.index("log") + 1) or not row.startswith("g:") or self.all:
            return
        key = row[2:]
        if key in self.picked:
            self.picked.discard(key)
        else:
            self.picked.add(key)
        self._save()
        self.tree.set(row, "log", ON if key in self.picked else OFF)
        self.refresh(force=True)
        return "break"

    # ------------------------------------------------------------ data
    def tick(self):
        if not self.alive():
            return
        self.refresh()
        self.after_id = self.win.after(1000, self.tick)

    @staticmethod
    def _fmt(v, dec):
        s = f"{v:.{dec}f}"
        return s

    def _visible(self, g, q, mn):
        if q and q not in g.key:
            return False
        if mn <= 0:
            return True
        ram_pct = g.ram / self.ram_total * 100.0
        vram_pct = g.vram / self.ram_total * 100.0
        return g.cpu >= mn or g.gpu >= mn or ram_pct >= mn or vram_pct >= mn or g.disk >= mn

    def _sortkey(self, g):
        col = self.sort[0]
        if col == "name":
            return g.name.lower()
        if col == "log":
            return (g.key in self.picked or self.all, g.cpu)
        return g.value(col)

    def refresh(self, force=False):
        snap = procmon.MON.snapshot()
        if snap is None:
            self.lbl_count.config(text=t("pw_wait"))
            return
        if snap.t == self.last_t and not force:
            return
        self.last_t = snap.t
        mn, q = _STATE["min"], self.v_search.get().strip().lower()
        col, desc = self.sort
        shown = 0
        plan = []                                        # (iid, parent, index, text, values, tags)
        for ci, cat in enumerate(CATS):
            cid = "c:" + cat
            groups = [g for g in snap.groups if g.cat == cat and self._visible(g, q, mn)]
            groups.sort(key=self._sortkey, reverse=desc)
            tot = [sum(g.value(m) for g in groups) for m in ("cpu", "ram", "gpu", "vram", "disk")]
            plan.append((cid, "", ci, f"{t('pw_' + cat)}  ({len(groups)})",
                         (self._fmt(tot[0], 1), self._fmt(tot[1], 0), self._fmt(min(100.0, tot[2]), 1),
                          self._fmt(tot[3], 0), self._fmt(tot[4], 2), ""), ("cat",)))
            for i, g in enumerate(groups):
                shown += 1
                gid = "g:" + g.key
                multi = len(g.procs) > 1
                logged = self.all or g.key in self.picked
                vals = (self._fmt(g.cpu, 1), self._fmt(g.ram, 0), self._fmt(g.gpu, 1), self._fmt(g.vram, 0),
                        self._fmt(g.disk, 2), ON if logged else OFF)
                tags = ("logged",) if logged else (() if (g.cpu >= 0.05 or g.gpu >= 0.05) else ("idle",))
                plan.append((gid, cid, i, f"{g.name}  ({len(g.procs)})" if multi else g.name, vals, tags))
                if multi:
                    procs = g.procs
                    if col in procmon.METRICS:
                        procs = sorted(procs, key=lambda p: getattr(p, col), reverse=desc)
                    for j, p in enumerate(procs):
                        plan.append(("p:%d" % p.pid, gid, j, f"{g.name}  ·  {t('pw_proc', pid=p.pid)}",
                                     (self._fmt(p.cpu, 1), self._fmt(p.ram, 0), self._fmt(p.gpu, 1),
                                      self._fmt(p.vram, 0), self._fmt(p.disk, 2), ""), ("child",)))
        want = {x[0] for x in plan}
        for iid in [i for i in self.items if i not in want]:
            if self.tree.exists(iid):
                self.tree.delete(iid)
        self.items = {i: True for i in self.items if i in want and self.tree.exists(i)}
        for item in plan:
            self._put(*item)
        txt = t("pw_count", n=shown)
        if not snap.gpu_ok:
            txt += " · " + t("pw_nogpu")
        self.lbl_count.config(text=txt)

    def _put(self, iid, parent, index, text, vals, tags):
        if iid in self.items and self.tree.exists(iid):
            self.tree.item(iid, text=text, values=vals, tags=tags)
            if self.tree.parent(iid) != parent or self.tree.index(iid) != index:
                self.tree.move(iid, parent, index)
        else:
            self.tree.insert(parent, index, iid=iid, text=text, values=vals, tags=tags, open=(parent == ""))
            self.items[iid] = True
