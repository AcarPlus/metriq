"""Settings page "Fan": master switch, STOP button, profiles, temperature limits and one card per fan header
(role, BIOS / fixed / curve mode, curve editor, live RPM). All control lives in the Windows service "metriQFan";
this page only edits its configuration through fanctl.py. Hook: settings_ui calls add_tab(settings) for the page
prepared in PAGES.

Threads: every fanctl call blocks for a moment, so they all run on one worker thread (_Gen). The worker never touches
Tk: results go into a queue that the page drains with an `after` job. The worker and the drain job only exist while
the page is on screen (Map / Unmap of the page frame) and are stopped when the page or the window goes away."""
import collections
import copy
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser

import battery
import config
import fanctl
import gfx
import helptexts
import themes
import uikit
from i18n import t
from uikit import px

PAGE = 7                                  # index in settings_ui.PAGES
POLL_S = 1.0                              # status poll while the page is visible (the service closes the hardware
                                          # again when nobody asked for 60 s)
DRAIN_MS = 120                            # worker -> Tk hand-over
SAVE_MS = 600                             # debounce of set_config
DIAG_PCT = 100                            # identify: duty ...
DIAG_SECS = 30                            # ... for at most this long per request (the service caps one override at 30 s)
RENEW_S = 10                              # while identify is on the worker renews the override this often
HIST = 60                                 # RPM history per fan: one sample per poll = the last minute
RPM_FLOOR = 1000                          # the sparkline scale is never smaller than this
FOLD_POLLS = 3                            # polls without a spinning fan before a BIOS header collapses
ROLE_ACCENT = {"cpu": "accent_power", "case": "accent_fan", "pump": "accent_clock", "gpu": "accent_net"}
ROLE_ICON = {"cpu": "chip", "case": "fan", "pump": "drop", "gpu": "gpu", "unknown": "fan", "none": "header"}
PAWNIO_URL = "https://pawnio.eu/"
T_MIN, T_MAX = 20, 100                    # temperature axis of the curve editor
MAX_POINTS, MIN_POINTS = 8, 2
ROLES = ("unknown", "case", "cpu", "pump", "gpu", "none")
MODES = ("bios", "fixed", "curve")
KIND_ORDER = {"board": 0, "other": 1, "gpu": 2}
PROFILES = (                              # (key, title key, template curve)
    ("silent", "fn_p_silent", [[40, 25], [60, 40], [75, 70], [85, 100]]),
    ("balanced", "fn_p_balanced", [[35, 30], [55, 50], [70, 75], [80, 100]]),
    ("perf", "fn_p_perf", [[30, 45], [50, 65], [65, 85], [75, 100]]),
)
REASONS = {"bios": "fn_r_bios", "fixed": "fn_r_fixed", "curve": "fn_r_curve", "safe": "fn_r_safe",
           "limit": "fn_r_limit", "override": "fn_r_override", "no sensor": "fn_r_nosensor",
           "waiting sensor": "fn_r_waiting"}
CPU_LIMIT = (60, 95)
GPU_LIMIT = (60, 90)


# ====================================================================== rules shared with the service
def min_duty(role, kind):
    """Lowest duty the service allows for a role (it enforces the same numbers)."""
    if role == "none":
        return 0
    if role == "pump":
        return 60
    if role == "case":
        return 30 if kind == "gpu" else 0
    return 30


def default_role(kind):
    return "gpu" if kind == "gpu" else "unknown"


def default_source(role, kind):
    if role == "cpu":
        return "cpu"
    return "gpu" if kind == "gpu" else "max"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def source_temp(src, st):
    """Current temperature of a curve source in a status reply (None when unknown)."""
    if not st:
        return None
    if src in ("cpu", "gpu"):
        v = st.get(src)
        return v if _num(v) else None
    if src == "max":
        vals = [v for v in (st.get("cpu"), st.get("gpu")) if _num(v)]
        return max(vals) if vals else None
    for x in st.get("temps") or []:
        if x.get("id") == src:
            v = x.get("value")
            return v if _num(v) else None
    return None


def role_color(C, role):
    """Colour of a role: its own theme accent; unknown = neutral grey, empty header = dimmed grey."""
    if role in ROLE_ACCENT:
        return C[ROLE_ACCENT[role]]
    if role == "none":
        return uikit.mix(C["faint"], C["card"], 0.35)
    return C["dim"]


def on_color(fill):
    """Text colour that reads on `fill`."""
    r, g, b = themes.rgb(fill)
    return "#10151b" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


def group_key(h):
    """Cards group the controls of one GPU (/gpu-nvidia/0/control/1 ... -> /gpu-nvidia/0); every other header is
    its own card."""
    hid = h.get("id") or ""
    if h.get("kind") == "gpu" and "/control/" in hid:
        return hid.rsplit("/control/", 1)[0]
    return hid


def _ok(r):
    return isinstance(r, dict) and bool(r.get("ok"))


def _probe():
    """Worker job: is there a service, does it answer, what is its configuration."""
    svc = fanctl.service_state()
    if svc == "missing":
        return {"svc": svc}
    st = fanctl.status()
    ok = bool(st) and st.get("ok", True)
    return {"svc": svc, "st": st if ok else None, "cfg": fanctl.get_config() if ok else None}


# ====================================================================== worker thread
class _Gen:
    """One worker thread with its own queues (a new one is made every time the page is shown). `stop` lets it finish
    the jobs already queued, then end; `poll` switches the 1 s status polling on. Identify ("diag") is a switch kept
    here: while a card is in `diag` the worker renews its override every RENEW_S seconds (the service lets one
    override live for 30 s at most, so it ends by itself when this thread or the panel dies)."""

    def __init__(self):
        self.stop = threading.Event()
        self.poll = threading.Event()
        self.jobs = queue.Queue()
        self.out = queue.Queue()
        self.diag = {}                                  # worker thread only: card key -> {ids, due, fails}
        self.nxt = 0.0
        self.thread = threading.Thread(target=self._run, name="fan-ui", daemon=True)
        self.thread.start()

    def send(self, kind, fn, *args, ctx=None):
        self.jobs.put((kind, fn, args, ctx))

    def send_diag(self, key, ids, on):
        self.send("diag", self._diag_job, key, tuple(ids), on, ctx=(key, on))

    def _diag_job(self, key, ids, on):
        """Identify on / off for all control ids of one card."""
        if on:
            ok = all([_ok(fanctl.override(i, DIAG_PCT, DIAG_SECS)) for i in ids])
            if ok:
                self.diag[key] = {"ids": ids, "due": time.monotonic() + RENEW_S, "fails": 0}
            else:
                for i in ids:                          # undo a partly started identify
                    fanctl.override(i, -1)
            return ok
        self.diag.pop(key, None)
        ok = True
        for i in ids:
            ok = _ok(fanctl.override(i, -1)) and ok
        return ok

    def _tick(self):
        now = time.monotonic()
        if self.poll.is_set() and now >= self.nxt:
            try:
                st = fanctl.status()
            except Exception:
                st = None
            self.out.put(("status", st, None))
            self.nxt = time.monotonic() + POLL_S
        for key, d in list(self.diag.items()):
            if time.monotonic() < d["due"]:
                continue
            try:
                ok = all([_ok(fanctl.override(i, DIAG_PCT, DIAG_SECS)) for i in d["ids"]])
            except Exception:
                ok = False
            if ok:
                d["due"], d["fails"] = time.monotonic() + RENEW_S, 0
                continue
            d["fails"] += 1
            d["due"] = time.monotonic() + 2.0          # retry soon: the old override still has ~20 s left
            if d["fails"] >= 3:
                self.diag.pop(key, None)
                self.out.put(("diag_lost", None, key))

    def _run(self):
        while True:
            if self.stop.is_set() and self.jobs.empty():
                return
            try:
                kind, fn, args, ctx = self.jobs.get(timeout=0.25)
            except queue.Empty:
                if not self.stop.is_set():
                    self._tick()
                continue
            try:
                res = fn(*args)
            except Exception:
                res = None
            self.out.put((kind, res, ctx))


# ====================================================================== widgets
class StopButton(uikit.Button):
    """The big red STOP button."""

    def __init__(self, parent, C, text, command, font, bg=None):
        super().__init__(parent, C, text, command, font, bg=bg, minw=120)
        self.h = px(40)
        tk.Canvas.configure(self, height=self.h)
        self.draw()

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        red = C["red"]
        fill = uikit.mix(red, "#ffffff", 0.18) if self.hot else red
        if self.down:
            fill = uikit.mix(red, "#000000", 0.25)
        self.put_rr(0, 0, self.w, self.h, px(8), fill, "#ffffff" if self.focused else None, 2)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill="#ffffff", font=self.font)


class DiagButton(uikit.Button):
    """The identify switch: a plain small button while off, a filled role-coloured one with a dot while on."""

    def __init__(self, parent, C, text, command, font, bg, wide_text):
        self.on, self.col = False, C["ui"]
        wide = uikit.font_obj(parent, font).measure(wide_text) + px(48)
        super().__init__(parent, C, text, command, font, small=True, bg=bg)
        self.h = px(30)
        self.w = max(self.w, wide)
        tk.Canvas.configure(self, width=self.w, height=self.h)
        self.draw()

    def set_on(self, on, text, col):
        if (on, text, col) != (self.on, self.text, self.col):
            self.on, self.text, self.col = on, text, col
            self.draw()

    def draw(self):
        if not self.on:
            return super().draw()
        C = self.C
        self.delete("all")
        self._refs = []
        fill = self.col
        if self.down:
            fill = uikit.mix(fill, "#000000", 0.22)
        elif self.hot:
            fill = uikit.mix(fill, "#ffffff", 0.16)
        fg = on_color(fill)
        self.put_rr(0, 0, self.w, self.h, px(8), fill, C["fg"] if self.focused else None, 2)
        r = px(3.5)
        self.create_oval(px(13) - r, self.h / 2 - r, px(13) + r, self.h / 2 + r, fill=fg, outline="")
        self.create_text(px(24), self.h / 2, text=self.text, fill=fg, font=self.font, anchor="w")


class FanCard(uikit.Card):
    """Settings card whose accent stripe follows the fan's role, with an optional glow ring (identify is on) and
    a padding that can shrink (collapsed header)."""
    glow = None

    def set_look(self, accent, glow):
        if (accent, glow) != (self.accent, self.glow):
            self.accent, self.glow = accent, glow
            self.draw()

    def set_pad(self, pt, pb):
        if (pt, pb) != (self.pt, self.pb):
            self.pt, self.pb = pt, pb
            self.coords("inner", self.pl, pt)
            self._inner_cfg()

    def draw(self):
        if not self.glow:
            return super().draw()
        self.delete("bg")
        self._refs = []
        w, h = self._cw, self._ch
        if w < 4 or h < 4:
            return
        C, glow = self.C, self.glow
        if gfx.AVAILABLE:
            r = self.r
            key = ("fancard", w, h, r, self.fillc, glow, self.accent)

            def paint(d, k):
                gfx.rrect(d, k, 0, 0, w, h, r, fill=self.fillc, outline=glow, w=2)
                if self.accent and h > 2 * r:
                    d.rectangle([0, r * k, 3 * k - 1, (h - r) * k], fill=self.accent)
            im = uikit._cached(key, w, h, paint)
            self.create_image(0, 0, image=self.keep(im), anchor="nw", tags="bg")
        else:
            uikit._poly_rr(self, 0, 0, w - 1, h - 1, self.r, self.fillc, glow, "bg")
            if self.accent:
                self.create_line(2, self.r, 2, h - self.r, fill=self.accent, width=3, tags="bg")
        self.tag_lower("bg")


class Badge(uikit._Cv):
    """Solid role-coloured number badge ("#3")."""

    def __init__(self, parent, C, text, bg, col, font):
        self.text, self.col, self.font, self.C = text, col, font, C
        self.w, self.h = uikit.font_obj(parent, font).measure(text) + px(14), px(22)
        super().__init__(parent, bg=bg, width=self.w, height=self.h)
        self.draw()

    def set_color(self, col):
        if col != self.col:
            self.col = col
            self.draw()

    def draw(self):
        self.delete("all")
        self._refs = []
        self.put_rr(0, 0, self.w, self.h, px(7), self.col)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=on_color(self.col), font=self.font)


class Pill(uikit._Cv):
    """Small rounded read-out: dim label on the left, bold value on the right."""

    def __init__(self, parent, C, label, bg, fonts, value="-"):
        self.C, self.label, self.val, self.fonts = C, label, value, fonts
        fl, fv = uikit.font_obj(parent, fonts[0]), uikit.font_obj(parent, fonts[1])
        self.lw = fl.measure(label)
        self.w = self.lw + fv.measure("100 °C") + px(40)
        self.h = px(30)
        self.pbg = bg
        super().__init__(parent, bg=bg, width=self.w, height=self.h)
        self.draw()

    def set(self, value):
        if value != self.val:
            self.val = value
            self.draw()

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        self.put_rr(0, 0, self.w, self.h, px(9), uikit.mix(self.pbg, C["fg"], 0.06), C["border"], 1)
        self.create_text(px(12), self.h / 2, text=self.label, fill=C["dim"], font=self.fonts[0], anchor="w")
        self.create_text(self.w - px(12), self.h / 2, text=self.val, fill=C["fg"], font=self.fonts[1], anchor="e")


class Spark(uikit._Cv):
    """RPM history of one fan (last HIST polls): filled line on a faint track, scaled to `peak`."""

    def __init__(self, parent, C, bg, w, h):
        self.C, self.pbg = C, bg
        self.W, self.H = px(w), px(h)
        super().__init__(parent, bg=bg, width=self.W, height=self.H)
        self.sig = None
        self.set([], RPM_FLOOR, C["faint"])

    def set(self, vals, peak, col):
        sig = (tuple(vals), peak, col)
        if sig == self.sig:
            return
        self.sig = sig
        C, W, H = self.C, self.W, self.H
        self.delete("all")
        self._refs = []
        track = uikit.mix(self.pbg, C["fg"], 0.05)
        self.put_rr(0, 0, W, H, px(6), track)
        vs = list(vals)
        if not vs:
            return
        pad = px(3)
        step = (W - 2 * pad) / (HIST - 1)
        base = H - pad
        pts = []
        for i, v in enumerate(vs):
            pts.append((W - pad - (len(vs) - 1 - i) * step, base - min(1.0, v / peak) * (H - 2 * pad)))
        if len(pts) >= 2:
            flat = [c for p in pts for c in p]
            self.create_polygon(pts[0][0], base, *flat, pts[-1][0], base, fill=uikit.mix(track, col, 0.30), outline="")
            self.create_line(*flat, fill=col, width=2, joinstyle="round")
        x, y = pts[-1]
        r = px(2.5)
        self.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="")


class CurveEditor(uikit._Cv):
    """Fan curve: x = temperature (20-100 °C), y = duty (0-100 %). Drag the points, double-click to add / remove.
    Shows the role minimum (red area), the current source temperature (vertical line) and the current duty (dot)."""
    H = 176

    def __init__(self, parent, C, font, bg, on_change):
        super().__init__(parent, bg=bg, height=px(self.H))
        self.C, self.font, self.cb = C, font, on_change
        self.pts = [[40, 30], [80, 100]]
        self.floor = 0
        self.col = C["ui"]
        self.live = (None, None)
        self.drag = self.hot = -1
        self.W = 0
        self._job = None
        self.bind("<Configure>", self._cfg)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self._set_hot(-1))

    # ---- geometry
    def _box(self):
        return px(40), px(10), max(px(120), self.W - px(16)), px(self.H) - px(24)

    def _x(self, tc):
        x0, y0, x1, y1 = self._box()
        return x0 + (tc - T_MIN) / (T_MAX - T_MIN) * (x1 - x0)

    def _y(self, pc):
        x0, y0, x1, y1 = self._box()
        return y1 - pc / 100.0 * (y1 - y0)

    def _tc(self, x):
        x0, y0, x1, y1 = self._box()
        return T_MIN + (x - x0) / max(1, x1 - x0) * (T_MAX - T_MIN)

    def _pc(self, y):
        x0, y0, x1, y1 = self._box()
        return (y1 - y) / max(1, y1 - y0) * 100.0

    # ---- data
    def set_points(self, pts):
        pts = [[int(a), int(b)] for a, b in pts]
        if pts != self.pts:
            self.pts = pts
            self.redraw()

    def set_floor(self, v):
        if v != self.floor:
            self.floor = v
            self.redraw()

    def set_color(self, col):
        if col != self.col:
            self.col = col
            self.redraw()

    def set_live(self, temp, duty):
        if (temp, duty) != self.live:
            self.live = (temp, duty)
            self._draw_live()

    # ---- drawing
    def _cfg(self, e):
        if e.width != self.W:
            self.W = e.width
            if self._job is None:
                self._job = self.after_idle(self._redraw_job)

    def _redraw_job(self):
        self._job = None
        try:
            self.redraw()
        except tk.TclError:
            pass

    def destroy(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()

    def redraw(self):
        if self.W < 40:
            return
        C = self.C
        self.delete("all")
        self._refs = []
        H = px(self.H)
        self.put_rr(0, 0, self.W, H, px(8), C["bg"], C["border"], 1)
        x0, y0, x1, y1 = self._box()
        grid = uikit.mix(C["bg"], C["border"], 0.8)
        for tc in range(T_MIN, T_MAX + 1, 10):
            x = self._x(tc)
            self.create_line(x, y0, x, y1, fill=grid)
            if tc % 20 == 0:
                self.create_text(x, y1 + px(11), text=str(tc), fill=C["faint"], font=(self.font[0], 8))
        for pc in range(0, 101, 25):
            y = self._y(pc)
            self.create_line(x0, y, x1, y, fill=grid)
            self.create_text(x0 - px(6), y, text=str(pc), anchor="e", fill=C["faint"], font=(self.font[0], 8))
        if self.floor > 0:
            self.create_rectangle(x0, self._y(self.floor), x1, y1, fill=uikit.mix(C["bg"], C["red"], 0.22), outline="")
        pts = sorted(self.pts)
        line = [x0, self._y(pts[0][1])]
        for tc, pc in pts:
            line += [self._x(tc), self._y(pc)]
        line += [x1, self._y(pts[-1][1])]
        self.create_line(*line, fill=self.col, width=2, joinstyle="round")
        for i, (tc, pc) in enumerate(pts):
            r = px(6 if i in (self.drag, self.hot) else 5)
            x, y = self._x(tc), self._y(pc)
            self.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline=self.col, width=2)
        if self.drag >= 0 and self.drag < len(pts):
            tc, pc = pts[self.drag]
            x, y = self._x(tc), self._y(pc)
            tx = min(max(x, x0 + px(40)), x1 - px(40))
            ty = y - px(16) if y - px(16) > y0 + px(6) else y + px(16)
            self.create_text(tx, ty, text=f"{tc} °C · {pc} %", fill=C["fg"], font=(self.font[0], 9, "bold"))
        self._draw_live()

    def _draw_live(self):
        self.delete("live")
        temp, duty = self.live
        if temp is None:
            return
        C = self.C
        x0, y0, x1, y1 = self._box()
        x = self._x(min(max(temp, T_MIN), T_MAX))
        self.create_line(x, y0, x, y1, fill=C["dim"], dash=(3, 3), tags="live")
        if duty is not None:
            y = self._y(min(max(duty, 0), 100))
            r = px(4)
            self.create_oval(x - r, y - r, x + r, y + r, fill=C["green"], outline=C["bg"], width=1, tags="live")

    # ---- mouse
    def _pick(self, e):
        best, bd = -1, px(11) ** 2
        for i, (tc, pc) in enumerate(sorted(self.pts)):
            d = (self._x(tc) - e.x) ** 2 + (self._y(pc) - e.y) ** 2
            if d <= bd:
                best, bd = i, d
        return best

    def _set_hot(self, i):
        if i != self.hot and self.drag < 0:
            self.hot = i
            self.configure(cursor="hand2" if i >= 0 else "")
            self.redraw()

    def _hover(self, e):
        if self.drag < 0:
            self._set_hot(self._pick(e))

    def _press(self, e):
        self.focus_set()
        i = self._pick(e)
        if i >= 0:
            self.pts = sorted(self.pts)
            self.drag = i
            self._move(e)

    def _move(self, e):
        i = self.drag
        if i < 0 or i >= len(self.pts):
            return
        pts = self.pts
        lo = pts[i - 1][0] + 1 if i > 0 else T_MIN
        hi = pts[i + 1][0] - 1 if i < len(pts) - 1 else T_MAX
        tc = int(min(max(round(self._tc(e.x)), lo), hi))
        pc = int(min(max(round(self._pc(e.y)), 0), 100))
        if [tc, pc] != pts[i]:
            pts[i] = [tc, pc]
            self.redraw()
            self.cb([list(p) for p in pts])
        else:
            self.redraw()

    def _release(self, e):
        if self.drag >= 0:
            self.drag = -1
            self.redraw()

    def _double(self, e):
        pts = sorted(self.pts)
        i = self._pick(e)
        if i >= 0:
            if len(pts) <= MIN_POINTS:
                return
            del pts[i]
        else:
            x0, y0, x1, y1 = self._box()
            if len(pts) >= MAX_POINTS or not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
                return
            tc = int(min(max(round(self._tc(e.x)), T_MIN), T_MAX))
            if any(abs(tc - p[0]) < 1 for p in pts):
                return
            pts.append([tc, int(min(max(round(self._pc(e.y)), 0), 100))])
            pts.sort()
        self.pts = pts
        self.drag = -1
        self.redraw()
        self.cb([list(p) for p in pts])


# ====================================================================== one fan header / one GPU
class HeaderCard:
    """One card: a board / other header, or all fans of one GPU (`g["ids"]`: several control ids that share one
    configuration; the card edits the first entry and copies it to the others)."""

    def __init__(self, tab, parent, g):
        self.tab = tab
        self.key, self.ids, self.kind = g["key"], list(g["ids"]), g["kind"]
        self.id = self.ids[0]
        self.group = self.kind == "gpu"
        self.lhm = "" if self.group else (g.get("name") or "")
        self.hw = g.get("hw") or ""
        self.num = g.get("num")
        self.hist = {cid: collections.deque(maxlen=HIST) for cid in self.ids}
        self.peak = RPM_FLOOR
        self.expanded = None                      # None = automatic, True = the user opened a collapsed header
        self.empty_n = 0
        self.collapsed = self.can_fold = False
        self.loading = True
        self.last = {}
        self.ikey = None
        sw, C = tab.sw, tab.C
        F = sw.F_TXT
        self._normalise()
        self.col = role_color(C, self.role())
        self.card = card = FanCard(parent, C, None, accent=self.col, bg=tab.bgc)
        card.pack(fill="x", pady=(0, 10))
        bg = self.bg = card.fillc

        # -- header row: icon, number badge, name (GPU: its name), hardware | history, RPM, show / hide
        ra = tk.Frame(card.body, bg=bg)
        sw.full(card, ra, pady=(0, 0), divider=False)
        self.ic = tk.Label(ra, bg=bg, bd=0)
        self.ic.pack(side="left", padx=(0, px(10)))
        self.badge = self.v_name = self.ent_name = self.cb_role = None
        if self.group:
            tk.Label(ra, text=self.hw or t("fn_gpu"), bg=bg, fg=C["fg"], font=(F, 11, "bold")).pack(side="left")
            sw.label(ra, t("fn_gpu_fans", n=len(self.ids)), dim=True, size=9, bg=bg).pack(side="left", padx=(10, 0))
        else:
            if self.num:
                self.badge = Badge(ra, C, f"#{self.num}", bg, self.col, (F, 9, "bold"))
                self.badge.pack(side="left", padx=(0, 8))
            self.v_name = tk.StringVar(value=self.lhm)
            self.ent_name = uikit.entry(ra, C, self.v_name, 16, (F, 11, "bold"), bg=bg)
            self.ent_name.pack(side="left")
            self.v_name.trace_add("write", lambda *a: self._on_name())
            sw.label(ra, self.hw, dim=True, size=8, bg=bg).pack(side="left", padx=(10, 0))
        rr = tk.Frame(ra, bg=bg)
        rr.pack(side="right")
        self.fr_fold = tk.Frame(rr, bg=bg)
        self.fr_fold.pack(side="left", padx=(8, 0))
        self.fr_live = tk.Frame(rr, bg=bg)
        self.fr_live.pack(side="left", before=self.fr_fold)
        self.lbl_chip = sw.label(rr, "", dim=True, size=9, bg=bg)
        self.btn_fold = sw.small_btn(self.fr_fold, max(t("fn_show"), t("fn_hide"), key=len), self._on_fold, bg=bg)
        if not self.group:
            self.spark = Spark(self.fr_live, C, bg, 150, 30)
            self.spark.pack(side="left", padx=(0, 12))
            self.lbl_rpm = tk.Label(self.fr_live, text=t("fn_no_rpm"), bg=bg, fg=C["faint"],
                                    font=(sw.F_NUM, 15, "bold"), width=9, anchor="e")
            self.lbl_rpm.pack(side="left")

        # -- everything below the header row (hidden while the header is collapsed)
        self.fr_full = fl = tk.Frame(card.body, bg=bg)
        sw.full(card, fl, pady=(8, 0), divider=False)
        self.rows = {}
        if self.group:                                  # compact row per fan: history, RPM, duty
            fans = tk.Frame(fl, bg=bg)
            fans.pack(fill="x", pady=(0, 6))
            for i, cid in enumerate(self.ids):
                r = tk.Frame(fans, bg=bg)
                r.pack(fill="x", pady=2)
                tk.Label(r, text=t("fn_fan_n", n=i + 1), bg=bg, fg=C["dim"], font=(F, 9), width=7,
                         anchor="w").pack(side="left")
                sp = Spark(r, C, bg, 150, 22)
                sp.pack(side="left", padx=(6, 12))
                lr = tk.Label(r, text=t("fn_no_rpm"), bg=bg, fg=C["faint"], font=(sw.F_NUM, 11, "bold"), width=9,
                              anchor="e")
                lr.pack(side="left")
                ld = tk.Label(r, text="", bg=bg, fg=C["dim"], font=(F, 9), width=6, anchor="w")
                ld.pack(side="left", padx=(8, 0))
                self.rows[cid] = (sp, lr, ld)

        # -- row B: role, mode, identify
        rb = tk.Frame(fl, bg=bg)
        rb.pack(fill="x", pady=(0, 6))
        if not self.group:
            sw.label(rb, t("fn_role"), dim=True, size=9, bg=bg).pack(side="left")
            self.cb_role = sw.combo(rb, [t("fn_role_" + r) for r in ROLES], 0, 11)
            self.cb_role.pack(side="left", padx=(6, 4))
            self.cb_role.bind("<<ComboboxSelected>>", lambda e: self._on_role())
            helptexts.info_icon(rb, "fan_role", bg=bg).pack(side="left", padx=(0, 16))
        sw.label(rb, t("fn_mode"), dim=True, size=9, bg=bg).pack(side="left")
        self.seg = uikit.Segmented(rb, C, [t("fn_mode_" + m) for m in MODES], 0, self._on_mode, (F, 10), bg=bg)
        self.seg.pack(side="left", padx=(6, 0))
        self.btn_diag = DiagButton(rb, C, t("fn_diag"), lambda: tab.toggle_diag(self), (F, 9), bg,
                                   t("fn_diag_on", t="99:59"))
        if not self.group:                              # a GPU's fans need no identifying: built, not shown
            helptexts.info_icon(rb, "fan_diag", bg=bg).pack(side="right")
            self.btn_diag.pack(side="right", padx=(0, 6))
        self.lbl_diag = sw.label(rb, "", dim=True, size=9, bg=bg)
        self.lbl_diag.config(fg=C["red"])
        self.lbl_diag.pack(side="right", padx=(0, 8))
        if self.group:
            lb = sw.label(fl, t("fn_gpu_all"), dim=True, size=8, bg=bg)
            lb.pack(anchor="w", pady=(0, 4))

        # -- row C: live state
        rc = tk.Frame(fl, bg=bg)
        rc.pack(fill="x", pady=(0, 4))
        self.lbl_state = sw.label(rc, "", dim=True, size=9, bg=bg)
        self.lbl_state.pack(side="left")
        self.lbl_alarm = sw.label(rc, t("fn_stall"), bold=True, size=9, bg=bg)
        self.lbl_alarm.config(fg=C["red"])
        self.lbl_min = sw.label(rc, "", size=9, bg=bg)
        self.lbl_min.config(fg=C["faint"])
        self.lbl_min.pack(side="right")

        # -- row D: what the mode needs
        self.host = tk.Frame(fl, bg=bg)
        self.host.pack(fill="x", pady=(2, 0))
        self.fr_note = tk.Frame(self.host, bg=bg)
        self.lbl_note = sw.label(self.fr_note, "", dim=True, size=9, bg=bg)
        self.lbl_note.pack(anchor="w")
        self.fr_fixed = tk.Frame(self.host, bg=bg)
        sw.label(self.fr_fixed, t("fn_fixed"), size=9, bg=bg).pack(side="left", padx=(0, 10))
        self.slider = uikit.Slider(self.fr_fixed, C, 0, 100, 1, 300, self._on_fixed, bg=bg)
        self.slider.pack(side="left")
        self.lbl_fixed = tk.Label(self.fr_fixed, text="", bg=bg, fg=C["fg"], font=(sw.F_NUM, 11, "bold"), width=6,
                                  anchor="w")
        self.lbl_fixed.pack(side="left", padx=(8, 0))
        self.fr_curve = tk.Frame(self.host, bg=bg)
        top = tk.Frame(self.fr_curve, bg=bg)
        top.pack(fill="x", pady=(0, 6))
        sw.label(top, t("fn_source"), size=9, bg=bg).pack(side="left", padx=(0, 8))
        self.src_vals = []
        self.cb_src = sw.combo(top, ["CPU"], 0, 34)
        self.cb_src.pack(side="left")
        self.cb_src.bind("<<ComboboxSelected>>", lambda e: self._on_source())
        helptexts.info_icon(top, "fan_curve", bg=bg).pack(side="left", padx=(8, 0))
        self.editor = CurveEditor(self.fr_curve, C, (F, 9), bg, self._on_points)
        self.editor.pack(fill="x")
        hint = sw.label(self.fr_curve, t("fn_curve_hint"), dim=True, size=8, bg=bg)
        uikit.autowrap(hint)
        hint.pack(fill="x", pady=(4, 0))

        self.set_sources((tab.st or {}).get("temps") or [])
        self.load()
        self.loading = False
        self._refresh_diag()
        self._fold(self.role() == "none", self.role() == "none")

    # ---- config access
    def hc(self, create=False):
        return self.tab.hcfg(self.id, self.kind, create)

    def role(self):
        if self.group:
            return "gpu"
        r = self.hc().get("role")
        return r if r in ROLES else default_role(self.kind)

    def mode(self):
        hc = self.hc()
        return "bios" if self.role() == "none" or hc.get("mode") not in MODES else hc.get("mode")

    def minimum(self):
        return min_duty(self.role(), self.kind)

    def sync(self):
        """GPU card: every control of the GPU gets the same entry (role, mode, fixed speed, curve) as the first."""
        if not self.group:
            return
        src = self.hc(create=True)
        src["role"] = "gpu"
        for cid in self.ids[1:]:
            h = self.tab.hcfg(cid, self.kind, create=True)
            for k in ("role", "mode", "fixed", "curve"):
                h[k] = copy.deepcopy(src[k])

    def _normalise(self):
        """Old configurations may hold different entries for the fans of one GPU: the first stored one wins."""
        if not self.group or len(self.ids) < 2:
            return
        hs = (self.tab.cfg or {}).get("headers") or {}
        ref = next((c for c in self.ids if c in hs), None)
        if ref is None:
            return
        src = self.tab.hcfg(ref, self.kind)
        same = all(c in hs and all(self.tab.hcfg(c, self.kind).get(k) == src.get(k) for k in ("mode", "fixed", "curve"))
                   and hs[c].get("role") == "gpu" for c in self.ids)
        if same:
            return
        first = self.tab.hcfg(self.id, self.kind, create=True)
        if ref != self.id:
            for k in ("mode", "fixed", "curve"):
                first[k] = copy.deepcopy(src[k])
        self.sync()
        self.tab.changed()

    # ---- config -> widgets
    def load(self):
        """Show the stored configuration (also after the service returned its clamped version)."""
        was = self.loading
        self.loading = True
        try:
            hc = self.hc()
            role, mode = self.role(), self.mode()
            if self.v_name is not None:
                name = hc.get("name") or self.lhm
                if self.v_name.get() != name and self.tab.focus_widget() is not self.ent_name.entry:
                    self.v_name.set(name)
            if self.cb_role is not None:
                self.cb_role.current(ROLES.index(role))
            self.seg.set(MODES.index(mode))
            mn = self.minimum()
            self.slider.set(max(mn, _int(hc.get("fixed"), 50)))
            cv = hc.get("curve") or {}
            pts = cv.get("points") or []
            self.editor.set_points(pts if len(pts) >= MIN_POINTS else [[40, 30], [80, 100]])
            self.editor.set_floor(mn)
            self._pick_source(cv.get("source") or default_source(role, self.kind))
            self._layout()
            self._recolor()
        finally:
            self.loading = was

    def _recolor(self):
        """Role colour: accent stripe, icon bubble, badge, identify button; glow ring while identify is on."""
        role = self.role()
        col = role_color(self.tab.C, role)
        self.col = col
        d = self.tab.diag.get(self.key)
        self.card.set_look(col, col if d else None)
        self.editor.set_color(self.tab.C["ui"] if role in ("unknown", "none") else col)
        if self.badge is not None:
            self.badge.set_color(col)
        ik = (ROLE_ICON.get(role, "fan"), col)
        if ik != self.ikey:
            self.ikey = ik
            if gfx.AVAILABLE:
                bub = uikit.mix(self.bg, col, 0.10 if role == "none" else 0.18)
                im = gfx.icon(ik[0], 6.0, col, bub, bub)
                self.ic.configure(image=im)
                self.ic.image = im
        self._refresh_diag()

    def _layout(self):
        """Mode panel, minimum note and fixed value for the stored role / mode."""
        role, mode = self.role(), self.mode()
        mn = self.minimum()
        self.lbl_min.config(text=t("fn_min", n=mn) if mn > 0 else t("fn_min0"))
        self.lbl_fixed.config(text=f"{int(round(self.slider.get()))} %")
        for fr in (self.fr_note, self.fr_fixed, self.fr_curve):
            fr.pack_forget()
        if role == "none":
            self.lbl_note.config(text=t("fn_none_note"))
            self.fr_note.pack(fill="x")
        elif mode == "bios":
            self.lbl_note.config(text=t("fn_bios_note"))
            self.fr_note.pack(fill="x")
        elif mode == "fixed":
            self.fr_fixed.pack(fill="x", pady=(2, 2))
        else:
            self.fr_curve.pack(fill="x")
            self.editor.set_floor(mn)

    # ---- collapsing (empty headers)
    def _fold(self, collapsed, can_fold):
        if can_fold != self.can_fold:
            self.can_fold = can_fold
            if can_fold:
                self.btn_fold.pack(side="left")
            else:
                self.btn_fold.pack_forget()
        if can_fold:
            self.btn_fold.configure(text=t("fn_show") if collapsed else t("fn_hide"))
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        if collapsed:
            self.fr_live.pack_forget()
            self.lbl_chip.config(text=t("fn_chip_none") if self.role() == "none" else t("fn_chip_nofan"))
            self.lbl_chip.pack(side="left", before=self.fr_fold)
            self.fr_full.grid_remove()
            self.card.set_pad(px(8), px(8))
            self.card.pack_configure(pady=(0, 6))
        else:
            self.lbl_chip.pack_forget()
            self.fr_live.pack(side="left", before=self.fr_fold)
            self.fr_full.grid()
            self.card.set_pad(px(14), px(14))
            self.card.pack_configure(pady=(0, 10))

    def _on_fold(self):
        self.expanded = bool(self.collapsed)             # collapsed -> open it; open -> collapse it
        self._auto_fold(self.empty_n >= FOLD_POLLS or self.role() == "none")

    def _auto_fold(self, stable):
        busy = bool(self.tab.diag.get(self.key))
        self._fold(stable and self.expanded is not True and not busy, stable)

    # ---- sources
    def set_sources(self, temps):
        cur = self.src_vals[self.cb_src.current()] if self.src_vals and self.cb_src.current() >= 0 else None
        cur = cur or (self.hc().get("curve") or {}).get("source") or default_source(self.role(), self.kind)
        vals = [("cpu", t("fn_src_cpu")), ("gpu", t("fn_src_gpu")), ("max", t("fn_src_max"))]
        for x in temps:
            if x.get("id"):
                vals.append((x["id"], f"{x.get('hw') or ''}: {x.get('name') or ''}".strip(": ")))
        if cur not in [v for v, _ in vals]:
            vals.append((cur, cur))
        self.src_vals = [v for v, _ in vals]
        self.cb_src.configure(values=[n for _, n in vals])
        self.cb_src.current(self.src_vals.index(cur))

    def _pick_source(self, src):
        if src not in self.src_vals:
            self.src_vals.append(src)
            self.cb_src.configure(values=list(self.cb_src.values) + [src])
        self.cb_src.current(self.src_vals.index(src))

    # ---- widget -> config
    def _edit(self):
        """The stored header entry to change (created on the first edit); None while the widgets are being loaded."""
        return None if self.loading else self.hc(create=True)

    def _commit(self):
        self.sync()
        self.tab.changed()

    def _on_name(self):
        h = self._edit()
        if h is not None:
            nm = self.v_name.get().strip()
            h["name"] = "" if nm == self.lhm else nm[:40]
            self.tab.changed()

    def _on_role(self):
        h = self._edit()
        if h is None:
            return
        role = ROLES[max(0, self.cb_role.current())]
        h["role"] = role
        if role == "none":
            h["mode"] = "bios"
            self.seg.set(0)
            self.expanded = True                         # keep it open: the user is working on it
        mn = self.minimum()
        h["fixed"] = max(mn, _int(h.get("fixed"), 50))
        self.slider.set(h["fixed"])
        self._layout()
        self._recolor()
        self._commit()

    def _on_mode(self, i):
        h = self._edit()
        if h is None:
            return
        if self.role() == "none":
            self.seg.set(0)
            return
        old = h.get("mode")
        h["mode"] = MODES[i]
        if MODES[i] == "fixed" and old != "fixed":
            d = self.tab.duty_of(self.id)
            h["fixed"] = max(self.minimum(), int(round(d)) if _num(d) else _int(h.get("fixed"), 50))
            self.slider.set(h["fixed"])
        self._layout()
        self._commit()

    def _on_fixed(self, v):
        h = self._edit()
        if h is None:
            return
        mn = self.minimum()
        v = int(round(v))
        if v < mn:
            v = mn
            self.slider.set(v)
        h["fixed"] = v
        self.lbl_fixed.config(text=f"{v} %")
        self._commit()

    def _on_source(self):
        h = self._edit()
        if h is None or not self.src_vals:
            return
        h.setdefault("curve", {})["source"] = self.src_vals[max(0, self.cb_src.current())]
        self._commit()

    def _on_points(self, pts):
        h = self._edit()
        if h is None:
            return
        h.setdefault("curve", {})["points"] = pts
        h["curve"].setdefault("source", default_source(self.role(), self.kind))
        self._commit()

    # ---- live status
    def update(self, hs, st):
        C = self.tab.C
        by = {h.get("id"): h for h in hs}
        stall = False
        for cid in self.ids:
            h = by.get(cid) or {}
            rpm = h.get("rpm")
            self.hist[cid].append(float(rpm) if _num(rpm) else 0.0)
            stall = stall or h.get("alarm") == "stall"
        self.peak = max([self.peak] + [max(d) for d in self.hist.values()])

        def paint(cid, spark, lbl):
            h = by.get(cid) or {}
            rpm = h.get("rpm")
            run = _num(rpm) and rpm > 0
            col = C["red"] if h.get("alarm") == "stall" else (self.col if run else C["faint"])
            self._set(lbl, "rpm" + cid, t("fn_rpm", v=int(round(rpm))) if _num(rpm) else t("fn_no_rpm"))
            lbl.config(fg=col)
            spark.set(self.hist[cid], self.peak, col)
        if self.group:
            for i, cid in enumerate(self.ids):
                sp, lr, ld = self.rows[cid]
                paint(cid, sp, lr)
                d = (by.get(cid) or {}).get("duty")
                self._set(ld, "duty" + cid, t("fn_pct", d=int(round(d))) if _num(d) else "")
        else:
            paint(self.id, self.spark, self.lbl_rpm)
        h0 = by.get(self.id) or {}
        duty, tgt = h0.get("duty"), h0.get("target")
        parts = []
        if _num(duty):
            s = t("fn_duty", d=int(round(duty)))
            if _num(tgt) and abs(tgt - duty) >= 1:
                s += t("fn_target", g=int(round(tgt)))
            parts.append(s)
        key = REASONS.get(h0.get("reason") or "")
        if key:
            parts.append(t(key))
        self._set(self.lbl_state, "state", "  ·  ".join(parts))
        if stall != self.last.get("stall"):
            self.last["stall"] = stall
            if stall:
                self.lbl_alarm.pack(side="left", padx=(12, 0))
            else:
                self.lbl_alarm.pack_forget()
        self._refresh_diag()
        i = self.cb_src.current()
        temp = source_temp(self.src_vals[i], st) if 0 <= i < len(self.src_vals) else None
        self.editor.set_live(temp, duty if _num(duty) else (tgt if _num(tgt) else None))
        if not self.group:                               # an empty header collapses after a few quiet polls
            rpm0 = h0.get("rpm")
            role = self.role()
            cand = role == "none" or (self.mode() == "bios" and not stall and not (_num(rpm0) and rpm0 > 0))
            self.empty_n = self.empty_n + 1 if cand else 0
            if not cand:
                self.expanded = None
            self._auto_fold(cand and (role == "none" or self.empty_n >= FOLD_POLLS))

    def _refresh_diag(self):
        d = self.tab.diag.get(self.key)
        if d:
            el = int(max(0, time.monotonic() - d["t0"]))
            self.btn_diag.set_on(True, t("fn_diag_on", t=f"{el // 60}:{el % 60:02d}"), self.col)
        else:
            self.btn_diag.set_on(False, t("fn_diag"), self.col)

    def _set(self, lbl, key, text):
        if self.last.get(key) != text:
            self.last[key] = text
            lbl.config(text=text)

    def diag_changed(self, failed=None):
        """Identify was switched (by the user, or it ended): glow, button and message follow."""
        self.lbl_diag.config(text=failed or "")
        if self.tab.diag.get(self.key):
            self.expanded = True if self.collapsed else self.expanded
            self._fold(False, self.can_fold)
        self._recolor()


def _int(v, default):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


# ====================================================================== the page
def add_tab(sw):
    """Fill the fan page of the SettingsWindow `sw`."""
    tab = sw.add_page(PAGE)
    FanTab(sw, tab)
    return tab


class FanTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p, self.win = sw, sw.C, p, sw.win
        self.sf = sw.pages[PAGE]["frame"]               # ScrollFrame of the page
        self.bgc = uikit._bg_of(p)
        self.gen = None
        self.active = False
        self.view = None                                # None | wait | unavail | consent | main
        self.drain_job = self.save_job = None
        self.cfg = None
        self.st = None
        self.probe = None
        self.dirty = False
        self.inflight = 0
        self.fails = 0
        self.enable_busy = False
        self.notes = {}                                 # transient problems shown under the top bar
        self.cards, self.order, self.temps_sig = {}, (), None
        self.defaults = {}
        self.diag = {}                                  # card key -> {"ids", "t0"} for every identify that is on
        self.bar = None
        self.holder = tk.Frame(p, bg=self.bgc)
        self.holder.pack(fill="x")
        self.sf.bind("<Map>", self._on_map, add="+")
        self.sf.bind("<Unmap>", self._on_unmap, add="+")
        self.holder.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------ page visibility, worker, drain
    def _on_map(self, e):
        if e.widget is self.sf:
            self._activate()

    def _on_unmap(self, e):
        if e.widget is self.sf:
            self._deactivate()

    def _on_destroy(self, e):
        if e.widget is self.holder:
            self._deactivate()

    def _activate(self):
        if self.active:
            return
        self.active = True
        self.gen = _Gen()
        self.inflight = 0
        self.drain_job = self.win.after(DRAIN_MS, self._drain)
        if self.view == "main":
            self.gen.poll.set()
        elif self.view != "consent":
            self._start_probe()

    def _deactivate(self):
        if not self.active:
            return
        self.active = False
        for job in (self.drain_job, self.save_job):
            if job is not None:
                try:
                    self.win.after_cancel(job)
                except tk.TclError:
                    pass
        self.drain_job = self.save_job = None
        gen, self.gen = self.gen, None
        if gen is not None:
            for key, d in list(self.diag.items()):             # identify never outlives the page
                gen.send_diag(key, d["ids"], False)
            if self.dirty and self.cfg is not None:           # do not lose an edit made just before leaving
                self.dirty = False
                gen.send("save", fanctl.set_config, copy.deepcopy(self.cfg))
            gen.stop.set()
        self.diag = {}

    def _send(self, kind, fn, *args, ctx=None):
        if self.gen is not None:
            self.gen.send(kind, fn, *args, ctx=ctx)

    def _drain(self):
        self.drain_job = None
        gen = self.gen
        if gen is None or not self.active:
            return
        last, got = None, False
        try:
            while True:
                kind, res, ctx = gen.out.get_nowait()
                if kind == "status":
                    last, got = res, True
                else:
                    self._handle(kind, res, ctx)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        except Exception:
            traceback.print_exc()
        try:
            if got and self.view == "main":
                self._apply_status(last)
        except tk.TclError:
            return
        except Exception:
            traceback.print_exc()
        if self.active and self.gen is gen:
            self.drain_job = self.win.after(DRAIN_MS, self._drain)

    def _handle(self, kind, res, ctx):
        if kind == "probe":
            self._on_probe(res)
        elif kind == "save":
            self._on_saved(res)
        elif kind == "enable":
            self._on_enabled(res, ctx)
        elif kind == "stop":
            self._on_stopped(res)
        elif kind == "diag":
            key, on = ctx
            if on and not res and key in self.diag:        # did not start
                self._diag_end(key, t("fn_diag_fail"))
        elif kind == "diag_lost":
            self._diag_end(ctx, t("fn_diag_lost"))

    # ------------------------------------------------------------ views
    def _clear(self):
        for w in self.holder.winfo_children():
            w.destroy()
        if self.bar is not None:
            self.bar.destroy()
            self.bar = None
        self.sf.reserve = 0
        self.sw._size_scrolls()
        self.cards, self.order, self.temps_sig = {}, (), None
        self.banner = None
        if self.gen is not None:
            self.gen.poll.clear()

    def _card(self, title):
        return self.sw.card(self.holder, title, acc=self.C["accent_fan"], kind="fan")

    def _start_probe(self):
        self._clear()
        self.view = "wait"
        card = self._card(t("fn_title"))
        self.sw.full(card, self.sw.label(card.body, t("fn_checking"), dim=True, bg=card.fillc), divider=False)
        self._send("probe", _probe)

    def _text(self, card, text, dim=False, size=10):
        lb = self.sw.label(card.body, text, dim=dim, size=size, bg=card.fillc)
        uikit.autowrap(lb)
        self.sw.full(card, lb, pady=(0, 6), divider=False)
        return lb

    def _show_unavailable(self, res):
        self._clear()
        self.view = "unavail"
        svc, st = res.get("svc"), res.get("st")
        if svc == "missing":
            msg, retry = t("fn_missing"), False
        elif svc == "stopped":
            msg, retry = t("fn_stopped"), True
        elif svc in ("starting", "stopping"):
            msg, retry = t("fn_starting"), True
        else:
            msg, retry = t("fn_unreach"), True
        card = self._card(t("fn_title"))
        self._text(card, msg)
        if retry:
            row = tk.Frame(card.body, bg=card.fillc)
            self.sw.full(card, row, pady=(4, 0), divider=False, sticky="w")
            self.sw.button(row, t("fn_retry"), self._start_probe, primary=True, bg=card.fillc).pack(side="left")

    def _show_consent(self):
        self._clear()
        self.view = "consent"
        sw = self.sw
        card = self._card(t("fn_risk_title"))
        bg = card.fillc
        self._text(card, t("fn_risk_intro"))
        sw.full(card, sw.label(card.body, t("fn_risk_rules"), bold=True, bg=bg), pady=(6, 2), divider=False)
        for k in ("fn_risk_1", "fn_risk_2", "fn_risk_3", "fn_risk_4"):
            self._text(card, "•  " + t(k), size=9)
        v = tk.BooleanVar(value=False)
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(10, 0), divider=False, sticky="w")
        btn = sw.button(row, t("fn_continue"), self._accept, primary=True, bg=bg)
        btn.config(state="disabled")
        chk = uikit.Check(row, t("fn_risk_ok"), v, self.C, (sw.F_TXT, 10), bg=bg,
                          command=lambda: btn.config(state="normal" if v.get() else "disabled"))
        chk.pack(side="left", padx=(0, 20))
        btn.pack(side="left")

    def _accept(self):
        cfg = self.sw.api.cfg
        cfg["fan_risk_ok"] = True
        config.save_keys(cfg, "fan_risk_ok")
        self._show_main()

    def _on_probe(self, res):
        if not isinstance(res, dict):
            res = {"svc": "unknown"}
        st = res.get("st")
        if res.get("svc") == "missing" or not st:
            self._show_unavailable(res)
            return
        self.probe = res
        if self.sw.api.cfg.get("fan_risk_ok"):
            self._show_main()
        else:
            self._show_consent()

    # ------------------------------------------------------------ main view
    def _show_main(self):
        self._clear()
        self.view = "main"
        res = self.probe or {}
        st = res.get("st") or {}
        cfg = res.get("cfg")
        if not isinstance(cfg, dict):
            cfg = {"version": 1, "enabled": bool(st.get("enabled")), "cpu_limit": 90, "gpu_limit": 85, "headers": {}}
        cfg.setdefault("headers", {})
        self.cfg, self.st, self.dirty, self.fails = cfg, st, False, 0
        self.notes = {}
        sw, C = self.sw, self.C
        self._build_bar()
        self.banner = tk.Frame(self.holder, bg=self.bgc)
        self.banner.pack(fill="x")
        self.pawn_shown = None
        self._build_profiles()
        self._build_limits()
        title = tk.Frame(self.holder, bg=self.bgc)
        title.pack(fill="x", pady=(4, 6))
        tk.Label(title, text=t("fn_headers"), bg=self.bgc, fg=C["fg"], font=(sw.F_HEAD, 11, "bold")).pack(anchor="w")
        lb = sw.label(title, t("fn_headers_hint"), dim=True, size=9, bg=self.bgc)
        uikit.autowrap(lb)
        lb.pack(fill="x", pady=(2, 0))
        leg = tk.Frame(title, bg=self.bgc)                  # role colours at a glance
        leg.pack(fill="x", pady=(6, 0))
        for role in ("cpu", "case", "pump", "gpu", "unknown", "none"):
            col = role_color(C, role)
            item = tk.Frame(leg, bg=self.bgc)
            item.pack(side="left", padx=(0, 16))
            if gfx.AVAILABLE:
                im = gfx.icon(ROLE_ICON[role], 4.2, col, self.bgc, None)
                ic = tk.Label(item, image=im, bg=self.bgc, bd=0)
                ic.image = im
                ic.pack(side="left", padx=(0, 5))
            sw.label(item, t("fn_role_" + role), dim=True, size=9, bg=self.bgc).pack(side="left")
        self.hdr_host = tk.Frame(self.holder, bg=self.bgc)
        self.hdr_host.pack(fill="x")
        self.win.configure(cursor="watch")
        try:
            self._apply_status(st)
        finally:
            self.win.configure(cursor="")
        if self.gen is not None:
            self.gen.poll.set()

    def _build_bar(self):
        """Master switch, STOP and live temperatures stay above the scrolling area, so STOP is never scrolled away."""
        sw, C = self.sw, self.C
        sf = self.sf
        self.bar = tk.Frame(sf, bg=C["bg"])
        self.bar.pack(side="top", fill="x", before=sf.sb, padx=(0, sf.sb.winfo_reqwidth() + px(4)), pady=(0, 6))
        card = uikit.Card(self.bar, C, None, accent=C["accent_fan"], bg=C["bg"], pad=(px(22), px(16), px(10), px(10)))
        card.pack(fill="x")
        bg = card.fillc
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 0), divider=False)
        self.v_enable = tk.BooleanVar(value=bool(self.st.get("enabled")))
        left = tk.Frame(row, bg=bg)
        left.pack(side="left")
        sw.switch(left, self.v_enable, self._on_enable).pack(side="left")
        ttl = tk.Frame(left, bg=bg)
        ttl.pack(side="left", padx=(10, 6))
        tk.Label(ttl, text=t("fn_master"), bg=bg, fg=C["fg"], font=(sw.F_HEAD, 12, "bold"), anchor="w").pack(anchor="w")
        self.lbl_sub = tk.Label(ttl, text="", bg=bg, fg=C["dim"], font=(sw.F_TXT, 9), anchor="w")
        self.lbl_sub.pack(anchor="w")
        helptexts.info_icon(left, "fan_master", bg=bg).pack(side="left")
        stop = StopButton(row, C, t("fn_stop"), self.stop_all, (sw.F_TXT, 13, "bold"), bg=bg)
        stop.pack(side="right")
        helptexts.info_icon(row, "fan_stop", bg=bg).pack(side="right", padx=(0, 8))
        # second row: live temperatures on the left, problems / hints next to them
        row2 = tk.Frame(card.body, bg=bg)
        sw.full(card, row2, pady=(10, 0), divider=False)
        self.lbl_t = {}
        for key in ("cpu", "gpu"):
            pill = Pill(row2, C, t("fn_" + key), bg, ((sw.F_TXT, 9), (sw.F_NUM, 12, "bold")))
            pill.pack(side="left", padx=(0, 8))
            self.lbl_t[key] = pill
        msg = tk.Frame(row2, bg=bg, height=px(34))
        msg.pack_propagate(False)
        msg.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.msg_lbl = []
        for col in ("red", "orange"):
            lb = tk.Label(msg, text="", bg=bg, fg=C[col], font=(sw.F_TXT, 9), anchor="w", justify="left")
            uikit.autowrap(lb)
            lb.pack(fill="x")
            self.msg_lbl.append(lb)
        self._refresh_sub()
        card.inner.update_idletasks()
        sf.reserve = card.inner.winfo_reqheight() + card.pt + card.pb + px(6)
        sw._size_scrolls()

    def _refresh_sub(self):
        """Sub-title of the master switch: who drives the fans."""
        txt = t("fn_sub_on") if self.v_enable.get() else t("fn_sub_off")
        if self.lbl_sub.cget("text") != txt:
            self.lbl_sub.config(text=txt)

    def _build_profiles(self):
        sw = self.sw
        card = sw.card(self.holder, t("fn_profiles"), acc=self.C["accent_fan"], kind="fan")
        bg = card.fillc
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 4), divider=False, sticky="w")
        for key, title, pts in PROFILES:
            sw.button(row, t(title), lambda p=pts: self.apply_profile(p), bg=bg).pack(side="left", padx=(0, 8))
        helptexts.info_icon(row, "fan_profiles", bg=bg).pack(side="left", padx=(4, 0))
        lb = sw.label(card.body, t("fn_profiles_hint"), dim=True, size=9, bg=bg)
        uikit.autowrap(lb)
        sw.full(card, lb, pady=(0, 0), divider=False)

    def _build_limits(self):
        sw = self.sw
        card = sw.card(self.holder, t("fn_limits"), acc=self.C["accent_fan"], kind="temp")
        self.v_lim = {}
        for key, (lo, hi), default in (("cpu", CPU_LIMIT, 90), ("gpu", GPU_LIMIT, 85)):
            v = tk.StringVar(value=str(_int(self.cfg.get(key + "_limit"), default)))
            self.v_lim[key] = v
            sp = sw.spinbox(card.body, v, lo, hi, 1, "%d", 5)
            v.trace_add("write", lambda *a, k=key, s=sp, l=lo, h=hi: self._on_limit(k, s, l, h))
            sw.row(card, t("fn_%s_limit" % key), sp, help="fan_limits" if key == "cpu" else None)
        lb = sw.label(card.body, t("fn_limit_hint"), dim=True, size=9, bg=card.fillc)
        uikit.autowrap(lb)
        sw.full(card, lb, pady=(0, 0), divider=False)

    def _on_limit(self, key, spin, lo, hi):
        try:
            v = int(round(float(self.v_lim[key].get().strip().replace(",", "."))))
        except ValueError:
            v = None
        ok = v is not None and lo <= v <= hi
        spin.set_error(not ok)
        if ok and self.cfg is not None and self.cfg.get(key + "_limit") != v:
            self.cfg[key + "_limit"] = v
            self.changed()

    # ------------------------------------------------------------ config handling
    def hcfg(self, hid, kind="other", create=False):
        """Stored config of a header; a default one (not stored) while the user has not touched it."""
        hs = self.cfg.setdefault("headers", {})
        h = hs.get(hid)
        if h is None:
            h = self.defaults.get(hid)
            if h is None:
                role = default_role(kind)
                h = self.defaults[hid] = {
                    "name": "", "role": role, "mode": "bios", "fixed": 50,
                    "curve": {"source": default_source(role, kind), "points": copy.deepcopy(PROFILES[1][2])}}
            if create:
                hs[hid] = self.defaults.pop(hid)
        else:
            h.setdefault("name", "")
            h.setdefault("role", default_role(kind))
            h.setdefault("mode", "bios")
            h.setdefault("fixed", 50)
            cv = h.setdefault("curve", {})
            cv.setdefault("source", default_source(h["role"], kind))
            cv.setdefault("points", copy.deepcopy(PROFILES[1][2]))
        return h

    def focus_widget(self):
        try:
            return self.win.focus_get()
        except (KeyError, tk.TclError):
            return None

    def duty_of(self, hid):
        for h in (self.st or {}).get("headers") or []:
            if h.get("id") == hid:
                return h.get("duty")
        return None

    def changed(self):
        """A setting changed: send the whole configuration once the user paused for SAVE_MS."""
        if not self.active:
            return
        self.dirty = True
        self.notes.pop("save", None)
        if self.save_job is not None:
            self.win.after_cancel(self.save_job)
        self.save_job = self.win.after(SAVE_MS, self._flush)

    def _flush(self):
        if self.save_job is not None:
            try:
                self.win.after_cancel(self.save_job)
            except tk.TclError:
                pass
            self.save_job = None
        if self.dirty and self.cfg is not None and self.gen is not None:
            self.dirty = False
            self.inflight += 1
            self._send("save", fanctl.set_config, copy.deepcopy(self.cfg))

    def _on_saved(self, res):
        self.inflight = max(0, self.inflight - 1)
        if not isinstance(res, dict):
            self.notes["save"] = t("fn_save_err")
            self._refresh_msg()
            return
        self.notes.pop("save", None)
        self._refresh_msg()
        if self.dirty or self.inflight or self.save_job is not None:
            return                                           # newer edits are on their way: they win
        res.setdefault("headers", {})
        self.cfg = res                                       # the service's (clamped) version is the truth
        self.cfg["enabled"] = bool(self.v_enable.get())
        for key, v in self.v_lim.items():
            lim = self.cfg.get(key + "_limit")
            if lim is not None and _int(v.get(), -1) != lim:
                v.set(str(lim))
        for c in self.cards.values():
            c.load()

    def apply_profile(self, pts):
        if self.cfg is None:
            return
        for c in self.cards.values():
            h = c.hc(create=True)
            role = c.role()
            if role == "none":
                continue
            if role == "pump":
                h["mode"], h["fixed"] = "fixed", 100
            else:
                h["mode"] = "curve"
                h["curve"] = {"source": default_source(role, c.kind), "points": copy.deepcopy(pts)}
            c.sync()
        for c in self.cards.values():
            c.load()
        self.changed()

    # ------------------------------------------------------------ master switch, STOP, identify
    def _on_enable(self):
        on = bool(self.v_enable.get())
        self._flush()
        self.enable_busy = True
        self.cfg["enabled"] = on
        self._send("enable", fanctl.enable, on, ctx=on)
        self._refresh_sub()

    def _on_enabled(self, ok, on):
        self.enable_busy = False
        if ok:
            self.notes.pop("enable", None)
        else:
            self.v_enable.set(not on)
            self.cfg["enabled"] = not on
            self.notes["enable"] = t("fn_enable_err")
        self._refresh_sub()
        self._refresh_msg()

    def stop_all(self):
        """STOP: everything back to the BIOS, at once (the switch shows off straight away)."""
        self._flush()
        self.diag_all_off()
        self.v_enable.set(False)
        if self.cfg is not None:
            self.cfg["enabled"] = False
        self.enable_busy = True
        self._send("stop", fanctl.stop)
        self._refresh_sub()

    def _on_stopped(self, ok):
        self.enable_busy = False
        if ok:
            self.notes.pop("stop", None)
        else:
            self.notes["stop"] = t("fn_stop_err")
        self._refresh_msg()

    def toggle_diag(self, card):
        """Identify is a switch: on = every control of the card at 100 % (renewed by the worker every RENEW_S s),
        off = back to normal."""
        if self.gen is None:
            return
        key = card.key
        if key in self.diag:
            d = self.diag.pop(key)
            self.gen.send_diag(key, d["ids"], False)
        else:
            self.diag[key] = {"ids": tuple(card.ids), "t0": time.monotonic()}
            self.gen.send_diag(key, card.ids, True)
        card.diag_changed()

    def _diag_end(self, key, msg=None):
        self.diag.pop(key, None)
        c = self.cards.get(key)
        if c is not None:
            c.diag_changed(msg)

    def diag_all_off(self):
        """Identify off everywhere (STOP)."""
        for key, d in list(self.diag.items()):
            self.diag.pop(key, None)
            if self.gen is not None:
                self.gen.send_diag(key, d["ids"], False)
            c = self.cards.get(key)
            if c is not None:
                c.diag_changed()

    # ------------------------------------------------------------ status -> widgets
    def _apply_status(self, st):
        if not isinstance(st, dict) or not st.get("ok", True):
            self.fails += 1
            if self.fails >= 2:
                self.notes["lost"] = t("fn_lost")
                self._refresh_msg()
            return
        self.fails = 0
        self.notes.pop("lost", None)
        self.st = st
        if not self.enable_busy and bool(st.get("enabled")) != bool(self.v_enable.get()):
            self.v_enable.set(bool(st.get("enabled")))
            self.cfg["enabled"] = bool(st.get("enabled"))
        for key in ("cpu", "gpu"):
            v = st.get(key)
            self.lbl_t[key].set(f"{v:.0f} °C" if _num(v) else "-")
        self._refresh_sub()
        self._refresh_msg()
        self._update_banner(st)
        groups = self._groups(st.get("headers") or [])
        order = tuple((g["key"], tuple(g["ids"])) for g in groups)
        if not groups:                       # no fan control found: the text depends on the hardware state
            order = (None, st.get("state"), bool(st.get("pawnio", True)))
        if order != self.order:
            self._build_cards(groups)
            self.order = order
            self.temps_sig = None
        sig = tuple(x.get("id") for x in st.get("temps") or [])
        if sig != self.temps_sig:
            self.temps_sig = sig
            for c in self.cards.values():
                c.set_sources(st.get("temps") or [])
        for g in groups:
            c = self.cards.get(g["key"])
            if c is not None:
                c.update(g["hs"], st)

    @staticmethod
    def _groups(headers):
        """Status headers -> one entry per card: every board / other header alone, all controls of a GPU together."""
        heads = sorted(headers, key=lambda h: (KIND_ORDER.get(h.get("kind"), 1), h.get("hw") or "", group_key(h),
                                               h.get("index") or 0, h.get("id") or ""))
        groups, by_key, n = [], {}, 0
        for h in heads:
            key = group_key(h)
            g = by_key.get(key)
            if g is None:
                kind = h.get("kind") or "other"
                g = by_key[key] = {"key": key, "kind": kind, "ids": [], "hs": [], "hw": h.get("hw") or "",
                                   "name": h.get("name") or "", "num": None}
                if kind != "gpu":
                    n += 1
                    g["num"] = int(h["index"]) + 1 if _num(h.get("index")) else n
                groups.append(g)
            g["ids"].append(h.get("id"))
            g["hs"].append(h)
        return groups

    def _build_cards(self, groups):
        for w in self.hdr_host.winfo_children():
            w.destroy()
        self.cards = {}
        if not groups:
            msg = t("fn_hw_idle") if (self.st or {}).get("state") != "error" else t("fn_no_headers")
            if (self.st or {}).get("state") == "open":           # hardware is open and offers no fan control at all
                msg = t("fn_unsupported") if self._laptop() or (self.st or {}).get("pawnio", True)                     else t("fn_no_headers")
            self.sw.label(self.hdr_host, msg, dim=True, bg=self.bgc).pack(anchor="w", pady=4)
        else:
            self.win.update_idletasks()
            for g in groups:
                self.cards[g["key"]] = HeaderCard(self, self.hdr_host, g)
        for key in [k for k in self.diag if k not in self.cards]:      # its card is gone: do not leave it spinning
            d = self.diag.pop(key)
            if self.gen is not None:
                self.gen.send_diag(key, d["ids"], False)

    def _refresh_msg(self):
        st = self.st or {}
        reds, oranges = [], []
        if st.get("panic"):
            reds.append(f"{t('fn_panic')} ({st['panic']})")
        if st.get("state") == "error" and st.get("error"):
            reds.append(t("fn_hw_error", e=st["error"]))
        for k in ("lost", "enable", "stop", "save"):
            if self.notes.get(k):
                reds.append(self.notes[k])
        if st.get("safe_mode"):
            oranges.append(t("fn_safe"))
        for lb, lines in zip(self.msg_lbl, ("\n".join(reds[:2]), "\n".join(oranges))):
            if lb.cget("text") != lines:
                lb.config(text=lines)
                if lines:
                    lb.pack(fill="x")
                else:
                    lb.pack_forget()

    @staticmethod
    def _laptop():
        """True on a PC with a system battery (its fans belong to the embedded controller)."""
        try:
            return battery.exists()
        except Exception:
            return False

    def _update_banner(self, st):
        # PawnIO only helps Super I/O fans: pointless on a laptop that offers no fan control at all
        need = not st.get("pawnio", True) and not (st.get("state") == "open" and not st.get("headers")
                                                    and self._laptop())
        if need == self.pawn_shown:
            return
        self.pawn_shown = need
        for w in self.banner.winfo_children():
            w.destroy()
        if not need:
            return
        sw = self.sw
        card = sw.card(self.banner, None, acc=self.C["orange"], kind="fan")
        bg = card.fillc
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 0), divider=False)
        sw.button(row, t("fn_pawnio_btn"), lambda: webbrowser.open(PAWNIO_URL), bg=bg).pack(side="right",
                                                                                          padx=(12, 0))
        lb = sw.label(row, t("fn_pawnio"), size=10, bg=bg)
        uikit.autowrap(lb)
        lb.pack(side="left", fill="x", expand=True)
