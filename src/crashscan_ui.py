"""Settings-window tab for the crash detective (logic: crashscan.py)."""
import os
import threading
import tkinter as tk
from tkinter import ttk

import config
import crashscan
import helptexts
import logview
import tooltip
import uikit
from i18n import t


def add_tab(sw):
    """Fill the diagnostics page (the last one) of the SettingsWindow `sw`."""
    tab = sw.add_page(5)
    DiagTab(sw, tab)
    return tab


class DiagTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p = sw, sw.C, p
        self.events, self.winlog, self.result, self.busy = [], True, None, False
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
        self.rtree = self._tree(rcard, ("start", "last", "rows", "data", "side", "usage"),
                                t("cr_rec_cols").split("|"), (128, 128, 45, 90, 105, 120), 5,
                                lambda e: self._rec_sel())
        self.rtree.column("rows", anchor="e")
        self.rtip = tooltip.Tooltip(self.rtree, self._rec_tip)
        rrow = tk.Frame(rcard.body, bg=rbg)
        sw.full(rcard, rrow, pady=(0, 2), divider=False, sticky="w")
        self.rec_btn = sw.button(rrow, t("lv_open_at"), self.open_recording)
        self.rec_btn.pack(side="left")
        self.rec_btn.config(state="disabled")
        sw.button(rrow, t("cr_open_dir"), self.open_dir).pack(side="left", padx=(8, 0))

        # ---- suspicious recordings: error detection
        card = sw.card(p, t("cr_err_title"))
        bg = card.fillc
        self.v_err = tk.BooleanVar(value=bool(sw.api.cfg.get("diag_errors", True)))
        sw.row(card, t("cr_err_show"), sw.switch(card.body, self.v_err, self._toggle_err))
        n0 = len(card.body.winfo_children())
        self.tree = self._tree(card, ("date", "type", "sum"), [t("cr_col_date"), t("cr_col_type"), t("cr_col_sum")],
                               (150, 120, 360), 6, lambda e: self.show())
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
            tree.heading(col, text=head)
            tree.column(col, width=w, anchor="w", stretch=(col == cols[-1]))
        sb = uikit.Scrollbar(box.body, C, tree.yview, bg=C["bg"])
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y", padx=(4, 0))
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<MouseWheel>", lambda e: (tree.yview_scroll(-1 if e.delta > 0 else 1, "units"), "break")[1])
        return tree

    def _rec_tip(self):
        """Full text of the cell under the mouse (cells can be truncated)."""
        tree = self.rtree
        x, y = self.rtip._xy
        row = tree.identify_row(y - tree.winfo_rooty())
        col = tree.identify_column(x - tree.winfo_rootx())
        if not row or not col:
            return ""
        try:
            return str(tree.set(row, tree["columns"][int(col[1:]) - 1]))
        except (tk.TclError, ValueError, IndexError):
            return ""

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
            self.rtree.delete(*self.rtree.get_children())
            for i, rec in enumerate(self.normal):
                self.rtree.insert("", "end", iid=str(i), values=(
                    rec["start"], rec["last"], rec["rows"],
                    t("cr_data_" + rec.get("data", "panel")), t("cr_yes" if rec.get("side") else "cr_no"),
                    rec.get("usage", "—")))
            self._rec_sel()
            self.tree.delete(*self.tree.get_children())
            for i, ev in enumerate(self.events):
                self.tree.insert("", "end", iid=str(i), values=(
                    ev["last"], t("cr_type_" + ev["type"]), crashscan.short_summary(ev)))
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
