"""Settings-window tab for the crash detective (logic: crashscan.py)."""
import os
import threading
import tkinter as tk
from tkinter import ttk

import config
import crashscan
from i18n import t


def add_tab(sw):
    """Append the diagnostics tab to the SettingsWindow `sw` (after the layout tab)."""
    C = sw.C
    tab = tk.Frame(sw.nb, bg=C["bg"], padx=16, pady=14)
    sw.nb.add(tab, text=t("cr_tab"))
    DiagTab(sw, tab)
    return tab


class DiagTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p = sw, sw.C, p
        self.events, self.winlog, self.result, self.busy = [], True, None, False
        C, F = self.C, sw.F_TXT
        sw.label(p, t("cr_title"), bold=True, size=12).pack(anchor="w")
        sw.label(p, t("cr_intro"), dim=True, size=9, wrap=640).pack(anchor="w", pady=(2, 8))
        bar = tk.Frame(p, bg=C["bg"])
        bar.pack(fill="x")
        self.btn = sw.button(bar, t("cr_scan"), self.start, primary=True)
        self.btn.pack(side="left")
        sw.button(bar, t("cr_open_dir"), self.open_dir).pack(side="left", padx=(8, 0))
        self.status = sw.label(bar, "", dim=True, size=9)
        self.status.pack(side="left", padx=(12, 0))

        box = tk.Frame(p, bg=C["bg"])
        box.pack(fill="x", pady=(10, 0))
        self.tree = ttk.Treeview(box, columns=("date", "type", "sum"), show="headings", height=6,
                                 style="HP.Treeview", selectmode="browse")
        for col, key, w in (("date", "cr_col_date", 150), ("type", "cr_col_type", 120), ("sum", "cr_col_sum", 360)):
            self.tree.heading(col, text=t(key))
            self.tree.column(col, width=w, anchor="w", stretch=(col == "sum"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview, style="HP.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show())
        self.tree.bind("<MouseWheel>", lambda e: (self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                                                  "break")[1])

        self.txt = tk.Text(p, height=14, wrap="word", bg=C["card"], fg=C["fg"], relief="flat", bd=0,
                           highlightthickness=1, highlightbackground=C["border"], font=(sw.F_NUM, 9),
                           padx=10, pady=8, insertbackground=C["fg"], selectbackground="#1f3a5f")
        self.txt.pack(fill="both", expand=True, pady=(10, 0))
        self.txt.bind("<MouseWheel>", lambda e: (self.txt.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                                                 "break")[1])
        self.set_text(t("cr_select"))
        self.open_btn = sw.button(p, t("cr_open_csv"), self.open_csv)
        self.open_btn.pack(anchor="w", pady=(10, 0))
        self.open_btn.config(state="disabled")

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
            self.tree.delete(*self.tree.get_children())
            for i, ev in enumerate(self.events):
                self.tree.insert("", "end", iid=str(i), values=(
                    ev["last"], t("cr_type_" + ev["type"]), crashscan.short_summary(ev)))
            self.status.config(text=t("cr_found", n=len(self.events), f=r["files"]) if self.events
                               else t("cr_none"), fg=self.C["dim"])
            self.set_text(t("cr_select") if self.events else t("cr_none"))
            self.open_btn.config(state="disabled")
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
