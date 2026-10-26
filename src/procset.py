"""Settings > Logging: the per-process CSV options (kept out of settings_ui.py).

``ProcSection(settings, parent, cfg, row)`` adds its widgets to the Logging tab starting at grid row ``row``;
``values()`` returns the config keys it manages (for SettingsWindow.read_ui).
"""
import tkinter as tk

import helptexts
import layout
import proclog
from i18n import t

NAMES = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "vram": "VRAM", "disk": "Disk"}
KEYS = ("proc_top", "proc_detail", "log_max_mb", "log_keep_days")


def _int(s, default=0):
    try:
        return int(float(str(s).replace(",", ".")))
    except ValueError:
        return default


class ProcSection:
    def __init__(self, st, p, cfg, row):
        """st: the SettingsWindow (its widget factories, Check, validators); p: the tab's frame."""
        from settings_ui import Check
        self.st, self.cfg = st, cfg
        C = st.C
        top = proclog.top_cfg(cfg)
        # --- top programs: one spinbox per metric
        st.label_help(p, t("ps_top"), "top").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
        fr = tk.Frame(p, bg=C["bg"])
        fr.grid(row=row, column=1, columnspan=2, sticky="w", pady=6)
        self.v_top, self.e_top = {}, {}
        for m in proclog.ORDER:
            tk.Label(fr, text=NAMES[m], bg=C["bg"], fg=C["dim"],
                     font=(st.F_TXT, 9)).pack(side="left", padx=(0, 4))
            v = tk.StringVar(value=str(top[m]))
            e = st.spinbox(fr, v, 0, 99, 1, "%.0f", 3)
            e.pack(side="left", padx=(0, 12), ipady=2)
            self.v_top[m], self.e_top[m] = v, e
            v.trace_add("write", lambda *a: self.refresh())
        st.label(p, t("ps_top_hint"), dim=True, size=9).grid(row=row + 1, column=1, columnspan=2, sticky="w",
                                                            pady=(0, 6))
        rc = st.add_check(st.checks, 1, list(self.e_top.values()),
                          lambda: all(0 <= _int(v.get(), -1) <= 99 and v.get().strip().isdigit()
                                      for v in self.v_top.values()))
        for v in self.v_top.values():
            v.trace_add("write", lambda *a, rc=rc: rc())
        # --- side file
        self.v_detail = tk.BooleanVar(value=bool(cfg.get("proc_detail")))
        Check(p, t("ps_detail"), self.v_detail, C, (st.F_TXT, 10), command=self.refresh, help="detail").grid(
            row=row + 2, column=1, columnspan=2, sticky="w", pady=6)
        # --- which programs
        st.label_help(p, t("ps_filter"), "filter").grid(row=row + 3, column=0, sticky="w", padx=(0, 16), pady=6)
        fr2 = tk.Frame(p, bg=C["bg"])
        fr2.grid(row=row + 3, column=1, columnspan=2, sticky="w", pady=6)
        self.lbl_filter = tk.Label(fr2, text="", bg=C["bg"], fg=C["fg"], font=(st.F_TXT, 10), anchor="w",
                                   justify="left", wraplength=340)
        self.lbl_filter.pack(side="left", padx=(0, 10))
        st.button(fr2, t("ps_choose"), self.choose).pack(side="left")
        # --- limits
        self.v_max = tk.StringVar(value=f"{proclog.num_cfg(cfg, 'log_max_mb', 10 ** 7):g}")
        self.e_max = st.spinbox(p, self.v_max, 0, 100000, 50, "%.0f", 7)
        st._grid_row(p, row + 4, t("ps_maxmb"), self.e_max, t("ps_maxmb_hint"), help="maxmb")
        self.v_keep = tk.StringVar(value=f"{proclog.num_cfg(cfg, 'log_keep_days', 36500):g}")
        self.e_keep = st.spinbox(p, self.v_keep, 0, 36500, 1, "%.0f", 7)
        st._grid_row(p, row + 5, t("ps_keep"), self.e_keep, t("ps_keep_hint"), help="keep")
        st.add_check(st.checks, 1, [self.e_max], lambda: self.v_max.get().strip().replace(".", "", 1).isdigit())
        st.add_check(st.checks, 1, [self.e_keep], lambda: self.v_keep.get().strip().isdigit())
        # --- estimate
        st.label_help(p, t("ps_est"), "est").grid(row=row + 6, column=0, sticky="w", padx=(0, 16), pady=6)
        self.lbl_est = tk.Label(p, text="", bg=C["bg"], fg=C["yellow"], font=(st.F_TXT, 10, "bold"), anchor="w")
        self.lbl_est.grid(row=row + 6, column=1, columnspan=2, sticky="w", pady=6)
        # --- how many programs the panel's (i) balloons list
        import procwin
        self.v_pop = tk.StringVar(value=str(procwin.popup_top(cfg)))
        self.e_pop = st.spinbox(p, self.v_pop, 1, 10, 1, "%.0f", 5)
        st._grid_row(p, row + 7, t("ps_popup"), self.e_pop, t("ps_popup_hint"))
        rp = st.add_check(st.checks, 1, [self.e_pop],
                          lambda: self.v_pop.get().strip().isdigit() and 1 <= int(self.v_pop.get()) <= 10)
        self.v_pop.trace_add("write", lambda *a: rp())
        for v in (st.v_interval, st.v_logall):
            v.trace_add("write", lambda *a: self.refresh())
        st.win.bind("<FocusIn>", lambda e: self.refresh(), add="+")
        self.refresh()

    # ------------------------------------------------------------
    def values(self):
        def num(v, fn):
            try:
                return fn(float(v.get().replace(",", ".")))
            except ValueError:
                return v.get()
        return {
            "proc_top": {m: (_int(v.get(), 0) if v.get().strip().isdigit() else v.get()) for m, v in self.v_top.items()},
            "proc_detail": bool(self.v_detail.get()),
            "log_max_mb": num(self.v_max, lambda x: int(x) if x == int(x) else round(x, 1)),
            "log_keep_days": num(self.v_keep, int),
            "usage_popup_top": _int(self.v_pop.get(), 1) if self.v_pop.get().strip().isdigit() else self.v_pop.get(),
        }

    def choose(self):
        """Open the process window to tick the programs (it saves proc_log_filter itself)."""
        try:
            self.st.api.procs.open(None)
        except Exception:
            pass

    def n_base(self):
        """Number of data columns of the main CSV (without the time column and the program columns)."""
        try:
            if self.st.v_logall.get():
                return len(self.st.api.readings())
            return len(layout.all_rows(layout.build_sections(self.st.api.layout()[1])))
        except Exception:
            return 20

    def refresh(self):
        filt = proclog.filter_cfg(self.cfg)
        if filt == "all":
            txt = t("ps_filter_all")
        elif not filt:
            txt = t("ps_filter_none")
        else:
            txt = t("ps_filter_n", n=len(filt), names=", ".join(filt[:4]) + (" …" if len(filt) > 4 else ""))
        try:
            self.lbl_filter.config(text=txt)
            top = {m: max(0, min(99, _int(v.get(), 0))) for m, v in self.v_top.items()}
            try:
                interval = float(self.st.v_interval.get().replace(",", "."))
            except ValueError:
                interval = float(self.cfg.get("interval") or 5)
            self.lbl_est.config(text=proclog.estimate_text(interval, self.n_base(), top, bool(self.v_detail.get()),
                                                           filt))
        except tk.TclError:
            pass
