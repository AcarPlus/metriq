"""Small drawn widgets of the Performance page: progress / comparison bar and a mini line chart."""
import tkinter as tk

import gfx
import uikit
from uikit import px


class Bar(tk.Canvas):
    """Progress / comparison bar: pill track, pill fill, optional reference tick."""

    def __init__(self, parent, C, h=8, bg=None):
        super().__init__(parent, bg=bg or uikit._bg_of(parent), height=px(h + 2), highlightthickness=0, bd=0)
        self.C, self.h = C, px(h)
        self.frac, self.col, self.tick = 0.0, C["ui"], None
        self._refs = []
        self._drawn = None
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, frac, col=None, tick=None):
        self.frac = max(0.0, min(1.0, frac))
        if col:
            self.col = col
        self.tick = tick
        self.draw()

    def draw(self):
        w = self.winfo_width()
        key = (w, round(self.frac * 330), self.col, self.tick)       # redraw only when something visible changes
        if key == self._drawn:
            return
        self._drawn = key
        self.delete("all")
        self._refs = []
        if w < 8:
            return
        h, y = self.h, px(1)
        C = self.C
        fw = max(h, int(round((w * self.frac) / 3.0)) * 3) if self.frac > 0 else 0
        fw = min(fw, w)
        if gfx.AVAILABLE:
            tr = gfx.pill(w, h, C["track"])
            self._refs.append(tr)
            self.create_image(0, y, image=tr, anchor="nw")
            if fw:
                fi = gfx.pill(fw, h, self.col)
                self._refs.append(fi)
                self.create_image(0, y, image=fi, anchor="nw")
        else:
            self.create_rectangle(0, y, w, y + h, fill=C["track"], outline="")
            if fw:
                self.create_rectangle(0, y, fw, y + h, fill=self.col, outline="")
        if self.tick is not None:
            x = int(w * self.tick)
            self.create_line(x, 0, x, h + 2 * y, fill=C["dim"], width=max(1, px(1.5)))


class Chart(tk.Canvas):
    """Mini line chart. series: list of dicts {color, values: [number | None], span: minimum visible range as a fraction
    of the maximum (a flat curve stays flat instead of magnifying tiny noise)}. Every series uses its own vertical
    scale, so clock, temperature and throughput can share one picture. xs: x values (seconds) or None (equally
    spaced); xlabels: texts under the x axis (category charts, e.g. block sizes)."""

    def __init__(self, parent, C, series, xs=None, xlabels=None, height=96, bg=None, font=("Segoe UI", 8)):
        super().__init__(parent, bg=bg or uikit._bg_of(parent), height=px(height), highlightthickness=0, bd=0)
        self.C, self.series, self.xs, self.xlabels, self.font = C, series, xs, xlabels, font
        self.bind("<Configure>", lambda e: self.draw())

    @staticmethod
    def _range(vals, span):
        v = [x for x in vals if x is not None]
        if not v:
            return 0.0, 1.0
        lo, hi = min(v), max(v)
        need = max(abs(hi), 1e-9) * span
        if hi - lo < need:
            mid = (hi + lo) / 2
            lo, hi = mid - need / 2, mid + need / 2
        pad = (hi - lo) * 0.08
        return lo - pad, hi + pad

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40 or h < 30:
            return
        C = self.C
        lab_h = px(14) if self.xlabels else 0
        x0, x1, y0, y1 = px(4), w - px(4), px(4), h - px(4) - lab_h
        for i in range(4):                                           # faint horizontal grid
            y = y0 + (y1 - y0) * i / 3.0
            self.create_line(x0, y, x1, y, fill=C["track"], width=1)
        n = max((len(s["values"]) for s in self.series), default=0)
        if n < 1:
            return
        xs = self.xs
        if xs and len(xs) >= 2 and xs[-1] > xs[0]:
            def fx(i):
                return x0 + (x1 - x0) * (xs[min(i, len(xs) - 1)] - xs[0]) / (xs[-1] - xs[0])
        else:
            def fx(i):
                return x0 + (x1 - x0) * (i / float(n - 1) if n > 1 else 0.5)
            if self.xlabels and n > 1:                               # keep the first / last label inside the canvas
                m = px(24)

                def fx(i, m=m):
                    return x0 + m + (x1 - x0 - 2 * m) * i / float(n - 1)
        for s in self.series:
            vals = s["values"]
            lo, hi = self._range(vals, s.get("span", 0.15))
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                pts += [fx(i), y1 - (y1 - y0) * (v - lo) / (hi - lo)]
            if len(pts) >= 4:
                self.create_line(*pts, fill=s["color"], width=max(2, px(2)), capstyle="round", joinstyle="round")
            elif len(pts) == 2:
                self.create_oval(pts[0] - 2, pts[1] - 2, pts[0] + 2, pts[1] + 2, fill=s["color"], outline="")
            if s.get("dots"):
                for j in range(0, len(pts), 2):
                    self.create_oval(pts[j] - px(3), pts[j + 1] - px(3), pts[j] + px(3), pts[j + 1] + px(3),
                                     fill=s["color"], outline="")
            if s.get("values_text"):                                   # value above each point
                for j, txt in enumerate(s["values_text"]):
                    if j * 2 + 1 < len(pts):
                        self.create_text(pts[j * 2], max(px(8), pts[j * 2 + 1] - px(9)), text=txt, fill=C["dim"],
                                         font=self.font)
        if self.xlabels:
            for i, txt in enumerate(self.xlabels):
                self.create_text(fx(i), h - px(2), text=txt, fill=C["dim"], font=self.font, anchor="s")
