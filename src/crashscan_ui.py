"""Settings-window tab for the crash detective (logic: crashscan.py)."""
import os
import re
import threading
import time
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import config
import crashscan
import helptexts
import logview
import sensreport
import tooltip
import uikit
from i18n import t

FMT = "%Y-%m-%d %H:%M:%S"


def compact_usage(text):
    """'CPU 5 · RAM 5 · GPU 5 · VRAM 3 · Disk 1' -> 'CPU·RAM·GPU ×5 · VRAM ×3 · Disk ×1' (groups with the same count
    share one multiplier; anything that does not look like that is returned unchanged)."""
    parts = re.findall(r"([A-Za-z]+) (\d+)(?: · |$)", text or "")
    if not parts:
        return text or ""
    out = []
    for name, n in parts:
        if out and out[-1][1] == n:
            out[-1][0].append(name)
        else:
            out.append(([name], n))
    return " · ".join("·".join(names) + " ×" + n for names, n in out)


def add_tab(sw):
    """Fill the diagnostics page (the last one) of the SettingsWindow `sw`."""
    tab = sw.add_page(5)
    DiagTab(sw, tab)
    return tab


class DiagTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p = sw, sw.C, p
        self.events, self.winlog, self.result, self.busy = [], True, None, False
        self._tdata = {}
        C, F = self.C, sw.F_TXT
        # ---- normal recordings (above the error detection)
        rcard = sw.card(p, t("cr_rec_title"))
        rbg = rcard.fillc
        intro = sw.label(rcard.body, t("cr_rec_intro"), dim=True, size=9, bg=rbg)
        uikit.autowrap(intro)
        sw.full(rcard, intro, pady=(0, 8), divider=False)
        bar = tk.Frame(rcard.body, bg=rbg)
        sw.full(rcard, bar, pady=(0, 8), divider=False)
        self.btn = sw.small_btn(bar, "↻", self.start, bg=rbg)           # refresh (the scan runs on its own)
        self.btn.pack(side="left")
        helptexts.info_icon(bar, "scan", bg=rbg).pack(side="left", padx=(8, 0))
        self.status = sw.label(bar, "", dim=True, size=9, bg=rbg)
        self.status.pack(side="left", padx=(12, 0))
        self.normal = []
        self.rtree = self._tree(rcard, ("start", "dur", "rows", "data", "side"),
                                t("cr_rec_cols").split("|"), (90, 60, 40, 70, 70), 5,
                                lambda e: self._rec_sel())
        self.rtree.column("rows", anchor="e")
        self.rtree.heading("rows", anchor="e")
        rrow = tk.Frame(rcard.body, bg=rbg)
        sw.full(rcard, rrow, pady=(0, 2), divider=False, sticky="w")
        self.rec_btn = sw.button(rrow, t("lv_open_at"), self.open_recording)
        self.rec_btn.pack(side="left")
        self.rec_btn.config(state="disabled")
        sw.button(rrow, t("cr_open_dir"), self.open_dir).pack(side="left", padx=(8, 0))

        # ---- sensor report (for support on hardware the developer cannot test)
        scard = sw.card(p, t("sr_title"))
        sbg = scard.fillc
        sintro = sw.label(scard.body, t("sr_intro"), dim=True, size=9, bg=sbg)
        uikit.autowrap(sintro)
        sw.full(scard, sintro, pady=(0, 8), divider=False)
        srow = tk.Frame(scard.body, bg=sbg)
        sw.full(scard, srow, pady=(0, 2), divider=False, sticky="w")
        sw.button(srow, t("sr_btn"), self.export_report, bg=sbg).pack(side="left")
        self.sr_status = sw.label(srow, "", dim=True, size=9, bg=sbg)
        self.sr_status.pack(side="left", padx=(12, 0))

        # ---- suspicious recordings: error detection
        card = sw.card(p, t("cr_err_title"))
        bg = card.fillc
        self.v_err = tk.BooleanVar(value=bool(sw.api.cfg.get("diag_errors", True)))
        sw.row(card, t("cr_err_show"), sw.switch(card.body, self.v_err, self._toggle_err))
        n0 = len(card.body.winfo_children())
        self.tree = self._tree(card, ("date", "type", "sum"), [t("cr_col_date"), t("cr_col_type"), t("cr_col_sum")],
                               (120, 90, 200), 6, lambda e: self.show())
        box2 = uikit.Card(card.body, C, fill=C["bg"], pad=(12, 12, 10, 10), radius=8, bg=bg)
        sw.full(card, box2, pady=(0, 8), divider=False)
        self.txt = tk.Text(box2.body, height=12, wrap="word", bg=C["bg"], fg=C["fg"], relief="flat", bd=0,
                           highlightthickness=0, font=(sw.F_NUM, 9), padx=2, pady=2, insertbackground=C["fg"],
                           selectbackground=C["select"], selectforeground=C["fg"])
        self.txt.pack(fill="x", expand=True)
        self.txt.bind("<MouseWheel>", lambda e: (self.txt.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                                                 "break")[1])
        self.set_text(t("cr_select"))
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 2), divider=False, sticky="w")
        self.open_btn = sw.button(row, t("cr_open_csv"), self.open_csv)
        self.open_btn.pack(side="left")
        self.open_btn.config(state="disabled")
        self.view_btn = logview.add_event_button(sw, row, self.current, self.folder)
        self.view_btn.config(state="disabled")                       # only with a selected suspicious event
        self.err_widgets = [w for w in card.body.winfo_children()[n0:] if w.winfo_manager() == "grid"]
        self._toggle_err(save=False)
        self.start()                                                 # scan as soon as the tab opens

    def export_report(self):
        """Asks where to save, then writes the sensor report (no personal data, see sensreport.py)."""
        api = self.sw.api
        try:
            rd = api.readings()
        except Exception:
            rd = []
        if not rd:
            self.sr_status.config(text=t("sr_nodata"))
            return
        name = "metriQ-sensors-%s.txt" % time.strftime("%Y-%m-%d")
        path = filedialog.asksaveasfilename(parent=self.sw.win, title=t("sr_btn"), defaultextension=".txt",
                                            initialfile=name, filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            text = sensreport.build(rd, api.source, api.layout(), api.cfg)
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(text)
            self.sr_status.config(text=t("sr_saved", f=os.path.basename(path)))
        except Exception as ex:
            messagebox.showerror(config.APP_NAME, t("sr_err", e=ex), parent=self.sw.win)

    def _toggle_err(self, save=True):
        on = bool(self.v_err.get())
        for w in self.err_widgets:
            try:
                (w.grid if on else w.grid_remove)()
            except tk.TclError:
                pass
        if save:
            cfg = self.sw.api.cfg
            cfg["diag_errors"] = on
            config.save_keys(cfg, "diag_errors")

    def _tree(self, card, cols, heads, widths, height, on_select):
        sw, C = self.sw, self.C
        box = uikit.Card(card.body, C, fill=C["bg"], pad=(6, 6, 6, 6), radius=8, bg=card.fillc)
        sw.full(card, box, pady=(0, 8), divider=False)
        tree = ttk.Treeview(box.body, columns=cols, show="headings", height=height, style="HP.Treeview",
                            selectmode="browse")
        for col, head, w in zip(cols, heads, widths):
            tree.heading(col, text=head, anchor="w")
            tree.column(col, width=uikit.px(w), minwidth=uikit.px(w), anchor="w", stretch=False)
        sb = uikit.Scrollbar(box.body, C, tree.yview, bg=C["bg"])
        hb = uikit.HScrollbar(box.body, C, tree.xview, bg=C["bg"])
        sb.configure(height=1)            # a bare canvas asks for ~270 px: that left an empty band above the header
        hb.configure(width=1)
        tree.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
        hb.pack(side="bottom", fill="x", pady=(4, 0))
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y", padx=(4, 0))
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<MouseWheel>", lambda e: (tree.yview_scroll(-1 if e.delta > 0 else 1, "units"), "break")[1])
        tree.bind("<Shift-MouseWheel>", lambda e: (tree.xview_scroll(-3 if e.delta > 0 else 3, "units"), "break")[1])
        # content-fitted columns: the last one takes the remaining width (ellipsis + tooltip with the full text)
        self._tdata[tree] = {"cols": list(cols), "heads": dict(zip(cols, heads)), "min": dict(zip(cols, widths)),
                             "need": {}, "disp": {}, "tip": {}, "job": None, "key": None, "cell": None,
                             "font": tkfont.Font(root=tree, font=(sw.F_TXT, 10)),
                             "fontb": tkfont.Font(root=tree, font=(sw.F_TXT, 9, "bold"))}
        tree.bind("<Configure>", lambda e, tr=tree: self._later(tr), add="+")
        tip = tooltip.Tooltip(tree, lambda tr=tree: self._cell_tip(tr), _bind=False)
        tree.bind("<Enter>", tip._enter, add="+")
        tree.bind("<Leave>", tip._leave, add="+")
        tree.bind("<ButtonPress>", tip._leave, add="+")
        tree.bind("<Motion>", lambda e, tr=tree, tp=tip: self._tip_motion(tr, tp, e), add="+")
        return tree

    # ------------------------------------------------------------ list sizing / cell tooltips
    def _later(self, tree):
        st = self._tdata[tree]
        if st["job"] is None:
            st["job"] = tree.after_idle(lambda: self._fit(tree))

    def _put(self, tree, iid, vals, tips=None):
        """Insert a row; the last cell may be shortened later, tips = {column: full text} for cells shown shortened."""
        st = self._tdata[tree]
        tree.insert("", "end", iid=iid, values=vals)
        st["disp"][iid] = str(vals[-1])
        for col, txt in (tips or {}).items():
            st["tip"][(iid, col)] = txt

    def _clear(self, tree):
        st = self._tdata[tree]
        tree.delete(*tree.get_children())
        st["disp"].clear()
        st["tip"].clear()
        st["key"] = None

    def _measure(self, tree):
        """Width each column wants: its widest cell (or heading) plus padding; then fit them to the window."""
        st = self._tdata[tree]
        last = st["cols"][-1]
        pad = uikit.px(14)
        for col in st["cols"]:
            w = st["fontb"].measure(st["heads"][col]) + uikit.px(18)
            for iid in tree.get_children():
                txt = st["disp"][iid] if col == last else str(tree.set(iid, col))
                w = max(w, st["font"].measure(txt) + pad)
            if tree.column(col, "anchor") == "e":
                w += uikit.px(10)                               # right-aligned numbers: air towards the next column
            st["need"][col] = max(w, uikit.px(st["min"][col]))
        self._fit(tree)

    def _fit(self, tree):
        st = self._tdata[tree]
        st["job"] = None
        try:
            avail = tree.winfo_width() - 2
            if avail < 60 or not st["need"]:
                return
            cols, need = st["cols"], st["need"]
            last = cols[-1]
            others = sum(need[c] for c in cols[:-1])
            lw = max(min(need[last], uikit.px(170)), avail - others)     # too narrow even so: the scroll bar takes over
            for c in cols[:-1]:
                tree.column(c, width=need[c], minwidth=need[c], stretch=False)
            tree.column(last, width=lw, minwidth=lw, stretch=False)
            key = (lw, len(st["disp"]))
            if key != st["key"]:
                st["key"] = key
                for iid, txt in st["disp"].items():
                    tree.set(iid, last, logview.fit_text(txt, st["font"], lw - uikit.px(16)))
        except tk.TclError:
            pass

    @staticmethod
    def _cell_at(tree, xroot, yroot):
        row = tree.identify_row(yroot - tree.winfo_rooty())
        col = tree.identify_column(xroot - tree.winfo_rootx())
        if not row or not col:
            return None
        try:
            return row, tree["columns"][int(col[1:]) - 1]
        except (tk.TclError, ValueError, IndexError):
            return None

    def _tip_motion(self, tree, tip, e):
        cell = self._cell_at(tree, e.x_root, e.y_root)
        st = self._tdata[tree]
        if cell != st["cell"]:                              # another cell: re-arm so the balloon shows its text
            st["cell"] = cell
            tip._leave()
            tip._enter(e)

    def _cell_tip(self, tree):
        """Full text of the cell under the pointer, when the list shows it shortened."""
        st = self._tdata[tree]
        x, y = tree.winfo_pointerxy()
        cell = self._cell_at(tree, x, y)
        if not cell:
            return ""
        iid, col = cell
        full = st["tip"].get((iid, col))
        if full is None and col == st["cols"][-1]:
            full = st["disp"].get(iid)
        try:
            shown = str(tree.set(iid, col))
        except tk.TclError:
            return ""
        return full if full and full != shown else ""

    def _rec_sel(self):
        self.rec_btn.config(state="normal" if self.rtree.selection() else "disabled")

    def open_recording(self):
        sel = self.rtree.selection()
        if sel:
            logview.show(self.sw.api, self.folder(), path=self.normal[int(sel[0])]["path"])

    # ------------------------------------------------------------ helpers
    def folder(self):
        try:
            return config.log_dir({"log_dir": self.sw.v_logdir.get()})
        except (AttributeError, tk.TclError):
            return config.log_dir(self.sw.api.cfg)

    def set_text(self, s):
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", s)
        self.txt.config(state="disabled")

    def open_dir(self):
        try:
            d = self.folder()
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except OSError:
            pass

    def open_csv(self):
        ev = self.current()
        if ev:
            try:
                os.startfile(ev["path"])
            except OSError:
                pass

    def current(self):
        sel = self.tree.selection()
        return self.events[int(sel[0])] if sel else None

    # ------------------------------------------------------------ scan (worker thread, polled)
    def start(self):
        if self.busy:
            return
        self.busy, self.result = True, None
        self.btn.config(state="disabled")
        self.status.config(text=t("cr_scanning"))
        folder = self.folder()

        def work():
            try:
                self.result = crashscan.scan(folder)
            except Exception as e:
                self.result = {"error": str(e)}
        threading.Thread(target=work, daemon=True).start()
        self.sw.win.after(150, self.poll)

    def poll(self):
        try:
            if self.result is None:
                self.sw.win.after(150, self.poll)
                return
            self.busy = False
            self.btn.config(state="normal")
            r = self.result
            if "error" in r:
                self.status.config(text=t("cr_err", e=r["error"]), fg=self.C["red"])
                return
            self.events, self.winlog = r["events"], r["winlog"]
            self.normal = r.get("normal", [])
            self._clear(self.rtree)
            for i, rec in enumerate(self.normal):
                usage = rec.get("usage", "—")
                st = rec["start"]
                try:
                    dur = crashscan.fmt_dur((datetime.strptime(rec["last"], FMT) - datetime.strptime(st, FMT))
                                            .total_seconds())
                except ValueError:
                    dur = "—"
                # short cells (day.month hh:mm); the full range and the logged columns are in the start cell's tip
                self._put(self.rtree, str(i), (f"{st[8:10]}.{st[5:7]} {st[11:16]}", dur, rec["rows"],
                                               t("cr_data_" + rec.get("data", "panel")),
                                               t("cr_yes" if rec.get("side") else "cr_no")),
                          {"start": f"{st} → {rec['last']}\n{compact_usage(usage)}"})
            self._measure(self.rtree)
            self._rec_sel()
            self._clear(self.tree)
            for i, ev in enumerate(self.events):
                self._put(self.tree, str(i), (ev["last"], t("cr_type_" + ev["type"]), crashscan.short_summary(ev)))
            self._measure(self.tree)
            self.status.config(text=t("cr_found", n=len(self.events), f=r["files"]) if self.events
                               else t("cr_none"), fg=self.C["dim"])
            self.set_text(t("cr_select") if self.events else t("cr_none"))
            self.open_btn.config(state="disabled")
            self.view_btn.config(state="disabled")
            if self.events:
                self.tree.selection_set("0")
        except tk.TclError:
            pass                                    # window closed during the scan

    def show(self):
        ev = self.current()
        if not ev:
            return
        self.set_text(crashscan.details(ev, self.winlog))
        self.open_btn.config(state="normal")
        self.view_btn.config(state="normal")
