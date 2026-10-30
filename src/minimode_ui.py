"""Settings page "Mini mode" (the in-game strip of minimode.py): enable / hotkey, position, look and the metric list.
Hook: settings_ui calls add_tab(settings) for the page prepared in PAGES; read() gives the "minimode" config dict."""
import tkinter as tk

import config
import minimode
import uikit
from i18n import t

PAGE = 8                                  # index in settings_ui.PAGES
CORNERS = config.MINI_CORNERS
BG_VALUES = ("plate", "none")
ORIENT = ("horizontal", "vertical")


def _num(s, default):
    try:
        return int(round(float(str(s).strip().replace(",", "."))))
    except ValueError:
        return default


class MiniTab:
    def __init__(self, sw, p):
        self.sw, self.C = sw, sw.C
        conf = config.norm_minimode(sw.api.cfg.get("minimode"))
        self.conf = conf
        C, F = sw.C, sw.F_TXT

        # ---- overlay: enable + hotkey
        card = sw.card(p, t("mini_card_general"), "fps")
        self.v_enabled = tk.BooleanVar(value=conf["enabled"])
        sw.row(card, t("mini_enable"), sw.switch(card.body, self.v_enabled), help="mini")
        self.hk_vals = list(config.MINI_HOTKEYS)
        names = [minimode.HOTKEYS[h][2] if h in minimode.HOTKEYS else t("mini_hk_off") for h in self.hk_vals]
        self.cb_hk = sw.combo(card.body, names, self.hk_vals.index(conf["hotkey"]), 20)
        sw.row(card, t("mini_hotkey"), self.cb_hk, help="mini_hotkey")

        # ---- position
        card = sw.card(p, t("mini_card_pos"), "net")
        mons = sw.api.monitors() or []
        self.mon_vals = ["primary"] + [m["index"] for m in mons]
        cur = conf["monitor"]
        self.cb_mon = sw.combo(card.body, [t("mini_mon_primary")] + sw.monitor_labels(mons),
                               self.mon_vals.index(cur) if cur in self.mon_vals else 0, 34)
        sw.row(card, t("st_monitor"), self.cb_mon)
        self.cb_corner = sw.combo(card.body, [t("mini_c_" + c) for c in CORNERS], CORNERS.index(conf["corner"]), 20)
        sw.row(card, t("mini_corner"), self.cb_corner)
        self.v_x, self.e_x = self._spin(card, "mini_off_x", conf["x"], 0, 9999, 10, "%d", 1)
        self.v_y, self.e_y = self._spin(card, "mini_off_y", conf["y"], 0, 9999, 10, "%d", 2)

        # ---- look
        card = sw.card(p, t("mini_card_look"), "gauge")
        self.seg_or = uikit.Segmented(card.body, C, [t("mini_horizontal"), t("mini_vertical")],
                                      ORIENT.index(conf["orientation"]), None, (F, 10))
        sw.row(card, t("mini_orient"), self.seg_or)
        self.v_opacity, self.e_opacity = self._spin(card, "mini_opacity", conf["opacity"], 30, 100, 5, "%d", 3)
        self.v_font, self.e_font = self._spin(card, "mini_font", conf["font_size"], 8, 40, 1, "%d", 4)
        self.v_labels = tk.BooleanVar(value=conf["labels"])
        sw.row(card, t("mini_labels"), sw.switch(card.body, self.v_labels))
        self.seg_bg = uikit.Segmented(card.body, C, [t("mini_bg_plate"), t("mini_bg_none")],
                                      BG_VALUES.index(conf["background"]), None, (F, 10))
        sw.row(card, t("mini_bg"), self.seg_bg, help="mini_bg")

        # ---- metrics: the rows of the layout, 2 per line
        card = sw.card(p, t("mini_card_metrics"), "temp")
        bg = card.fillc
        sw.row(card, t("mini_metrics_hint", n=config.MINI_MAX_METRICS), None, help="mini_metrics")
        self.rows = sw.api.mini_rows()
        self.vars = []                                      # [(metric key, BooleanVar)] in layout order
        want = conf["metrics"] if isinstance(conf["metrics"], list) else minimode.default_keys(self.rows)
        self.keys0 = [k for k in map(minimode.metric_key, self.rows) if k in want]       # what the checklist shows at the start
        if not self.rows:
            sw.full(card, sw.label(card.body, t("mini_no_rows"), dim=True, size=9, bg=bg), divider=False)
        else:
            grid = tk.Frame(card.body, bg=bg)
            grid.grid_columnconfigure(0, weight=1, uniform="m")
            grid.grid_columnconfigure(1, weight=1, uniform="m")
            for i, r in enumerate(self.rows):
                key = minimode.metric_key(r)
                v = tk.BooleanVar(value=key in want)
                chk = uikit.Check(grid, r.name, v, C, (F, 10), bg=bg, small=True)
                chk.grid(row=i // 2, column=i % 2, sticky="w", pady=3, padx=(0, 12))
                v.trace_add("write", lambda *a, v=v: self._limit(v))
                self.vars.append((key, v))
            sw.full(card, grid, pady=(2, 6), divider=False)

    # ------------------------------------------------------------
    def _spin(self, card, key, value, lo, hi, inc, fmt, n):
        sw = self.sw
        v = tk.StringVar(value=fmt % value)
        e = sw.spinbox(card.body, v, lo, hi, inc, fmt, 7)
        sw.row(card, t(key), e)

        def ok():
            return lo <= float(v.get().strip().replace(",", ".")) <= hi
        rc = sw.add_check(sw.checks, PAGE, [e], ok)
        v.trace_add("write", lambda *a: rc())
        return v, e

    def _limit(self, var):
        """At most MINI_MAX_METRICS ticks: the one that would be the ninth is switched off again."""
        if var.get() and sum(1 for _, v in self.vars if v.get()) > config.MINI_MAX_METRICS:
            var.set(False)
            self.sw.say(t("mini_max", n=config.MINI_MAX_METRICS), error=True)

    def read(self):
        """The "minimode" config dict as the page shows it (untouched metrics stay "auto" / as stored)."""
        c = self.conf
        keys = [k for k, v in self.vars if v.get()]
        metrics = c["metrics"] if (keys == self.keys0 or not self.vars) else keys
        d = {
            "enabled": bool(self.v_enabled.get()),
            "hotkey": self.hk_vals[max(0, self.cb_hk.current())],
            "corner": CORNERS[max(0, self.cb_corner.current())],
            "x": _num(self.v_x.get(), c["x"]), "y": _num(self.v_y.get(), c["y"]),
            "monitor": self.mon_vals[max(0, self.cb_mon.current())],
            "orientation": ORIENT[self.seg_or.index],
            "opacity": _num(self.v_opacity.get(), c["opacity"]),
            "font_size": _num(self.v_font.get(), c["font_size"]),
            "labels": bool(self.v_labels.get()),
            "background": BG_VALUES[self.seg_bg.index],
            "metrics": metrics,
        }
        return config.norm_minimode(d)


def add_tab(sw):
    tab = sw.add_page(PAGE)
    sw.mini_tab = MiniTab(sw, tab)
    return tab
