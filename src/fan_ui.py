"""Settings page "Fan": master switch, STOP button, profiles, temperature limits and one card per fan header
(role, BIOS / fixed / curve mode, curve editor, live RPM). All control lives in the Windows service "metriQFan";
this page only edits its configuration through fanctl.py. Hook: settings_ui calls add_tab(settings) for the page
prepared in PAGES.

Threads: every fanctl call blocks for a moment, so they all run on one worker thread (_Gen). The worker never touches
Tk: results go into a queue that the page drains with an `after` job. The worker and the drain job only exist while
the page is on screen (Map / Unmap of the page frame) and are stopped when the page or the window goes away."""
import copy
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser

import config
import fanctl
import gfx
import helptexts
import uikit
from i18n import t
from uikit import px

PAGE = 7                                  # index in settings_ui.PAGES
POLL_S = 1.0                              # status poll while the page is visible (the service closes the hardware
                                          # again when nobody asked for 60 s)
DRAIN_MS = 120                            # worker -> Tk hand-over
SAVE_MS = 600                             # debounce of set_config
DIAG_PCT, DIAG_SECS = 100, 8
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
    the jobs already queued, then end; `poll` switches the 1 s status polling on."""

    def __init__(self):
        self.stop = threading.Event()
        self.poll = threading.Event()
        self.jobs = queue.Queue()
        self.out = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="fan-ui", daemon=True)
        self.thread.start()

    def send(self, kind, fn, *args, ctx=None):
        self.jobs.put((kind, fn, args, ctx))

    def _run(self):
        nxt = 0.0
        while True:
            if self.stop.is_set() and self.jobs.empty():
                return
            try:
                kind, fn, args, ctx = self.jobs.get(timeout=0.25)
            except queue.Empty:
                if self.poll.is_set() and not self.stop.is_set() and time.monotonic() >= nxt:
                    try:
                        st = fanctl.status()
                    except Exception:
                        st = None
                    self.out.put(("status", st, None))
                    nxt = time.monotonic() + POLL_S
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


class CurveEditor(uikit._Cv):
    """Fan curve: x = temperature (20-100 °C), y = duty (0-100 %). Drag the points, double-click to add / remove.
    Shows the role minimum (red area), the current source temperature (vertical line) and the current duty (dot)."""
    H = 176

    def __init__(self, parent, C, font, bg, on_change):
        super().__init__(parent, bg=bg, height=px(self.H))
        self.C, self.font, self.cb = C, font, on_change
        self.pts = [[40, 30], [80, 100]]
        self.floor = 0
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
        self.create_line(*line, fill=C["ui"], width=2, joinstyle="round")
        for i, (tc, pc) in enumerate(pts):
            r = px(6 if i in (self.drag, self.hot) else 5)
            x, y = self._x(tc), self._y(pc)
            self.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline=C["ui"], width=2)
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


# ====================================================================== one fan header
class HeaderCard:
    def __init__(self, tab, parent, h):
        self.tab, self.id = tab, h["id"]
        self.kind = h.get("kind") or "other"
        self.lhm = h.get("name") or ""
        self.hw = h.get("hw") or ""
        self.loading = True
        self.last = {}
        sw, C = tab.sw, tab.C
        F = sw.F_TXT
        acc = C["accent_fan"]
        self.card = card = sw.card(parent, None, "fan", acc=acc, pady=(0, 10))
        bg = card.fillc
        self.bg = bg

        # -- row A: icon, name, hardware, RPM
        ra = tk.Frame(card.body, bg=bg)
        sw.full(card, ra, pady=(0, 8), divider=False)
        if gfx.AVAILABLE:
            bub = uikit.mix(bg, acc, 0.16)
            im = gfx.icon("fan", 5.0, acc, bub, bub)
            ic = tk.Label(ra, image=im, bg=bg, bd=0)
            ic.image = im
            ic.pack(side="left", padx=(0, px(10)))
        self.v_name = tk.StringVar(value=self.lhm)
        self.ent_name = uikit.entry(ra, C, self.v_name, 16, (F, 11, "bold"), bg=bg)
        self.ent_name.pack(side="left")
        self.v_name.trace_add("write", lambda *a: self._on_name())
        sw.label(ra, self.hw, dim=True, size=8, bg=bg).pack(side="left", padx=(10, 0))
        self.lbl_rpm = tk.Label(ra, text=t("fn_no_rpm"), bg=bg, fg=C["fg"], font=(sw.F_NUM, 15, "bold"), anchor="e")
        self.lbl_rpm.pack(side="right")

        # -- row B: role, mode, identify
        rb = tk.Frame(card.body, bg=bg)
        sw.full(card, rb, pady=(0, 6), divider=False)
        sw.label(rb, t("fn_role"), dim=True, size=9, bg=bg).pack(side="left")
        self.cb_role = sw.combo(rb, [t("fn_role_" + r) for r in ROLES], 0, 11)
        self.cb_role.pack(side="left", padx=(6, 4))
        self.cb_role.bind("<<ComboboxSelected>>", lambda e: self._on_role())
        helptexts.info_icon(rb, "fan_role", bg=bg).pack(side="left", padx=(0, 16))
        sw.label(rb, t("fn_mode"), dim=True, size=9, bg=bg).pack(side="left")
        self.seg = uikit.Segmented(rb, C, [t("fn_mode_" + m) for m in MODES], 0, self._on_mode, (F, 10), bg=bg)
        self.seg.pack(side="left", padx=(6, 0))
        helptexts.info_icon(rb, "fan_diag", bg=bg).pack(side="right")
        self.btn_diag = sw.small_btn(rb, t("fn_diag"), lambda: tab.diagnose(self.id), bg=bg)
        self.btn_diag.pack(side="right", padx=(0, 6))
        self.lbl_diag = sw.label(rb, "", dim=True, size=9, bg=bg)
        self.lbl_diag.pack(side="right", padx=(0, 8))

        # -- row C: live state
        rc = tk.Frame(card.body, bg=bg)
        sw.full(card, rc, pady=(0, 4), divider=False)
        self.lbl_state = sw.label(rc, "", dim=True, size=9, bg=bg)
        self.lbl_state.pack(side="left")
        self.lbl_alarm = sw.label(rc, t("fn_stall"), bold=True, size=9, bg=bg)
        self.lbl_alarm.config(fg=C["red"])
        self.lbl_min = sw.label(rc, "", size=9, bg=bg)
        self.lbl_min.config(fg=C["faint"])
        self.lbl_min.pack(side="right")

        # -- row D: what the mode needs
        self.host = tk.Frame(card.body, bg=bg)
        sw.full(card, self.host, pady=(2, 0), divider=False)
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

    # ---- config access
    def hc(self, create=False):
        return self.tab.hcfg(self.id, self.kind, create)

    def role(self):
        r = self.hc().get("role")
        return r if r in ROLES else default_role(self.kind)

    def minimum(self):
        return min_duty(self.role(), self.kind)

    # ---- config -> widgets
    def load(self):
        """Show the stored configuration (also after the service returned its clamped version)."""
        was = self.loading
        self.loading = True
        try:
            hc = self.hc()
            role, mode = self.role(), hc.get("mode")
            mode = mode if mode in MODES else "bios"
            if role == "none":
                mode = "bios"
            name = hc.get("name") or self.lhm
            if self.v_name.get() != name and self.tab.focus_widget() is not self.ent_name.entry:
                self.v_name.set(name)
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
        finally:
            self.loading = was

    def _layout(self):
        """Mode panel, minimum note and fixed value for the stored role / mode."""
        hc = self.hc()
        role = self.role()
        mode = hc.get("mode") if hc.get("mode") in MODES else "bios"
        if role == "none":
            mode = "bios"
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
        mn = self.minimum()
        h["fixed"] = max(mn, _int(h.get("fixed"), 50))
        self.slider.set(h["fixed"])
        self._layout()
        self.tab.changed()

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
        self.tab.changed()

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
        self.tab.changed()

    def _on_source(self):
        h = self._edit()
        if h is None or not self.src_vals:
            return
        h.setdefault("curve", {})["source"] = self.src_vals[max(0, self.cb_src.current())]
        self.tab.changed()

    def _on_points(self, pts):
        h = self._edit()
        if h is None:
            return
        h.setdefault("curve", {})["points"] = pts
        h["curve"].setdefault("source", default_source(self.role(), self.kind))
        self.tab.changed()

    # ---- live status
    def update(self, h, st):
        rpm, duty, tgt = h.get("rpm"), h.get("duty"), h.get("target")
        self._set(self.lbl_rpm, "rpm", t("fn_rpm", v=int(round(rpm))) if _num(rpm) else t("fn_no_rpm"))
        stall = h.get("alarm") == "stall"
        self.lbl_rpm.config(fg=self.tab.C["red"] if stall else self.tab.C["fg"])
        parts = []
        if _num(duty):
            s = t("fn_duty", d=int(round(duty)))
            if _num(tgt) and abs(tgt - duty) >= 1:
                s += t("fn_target", g=int(round(tgt)))
            parts.append(s)
        key = REASONS.get(h.get("reason") or "")
        if key:
            parts.append(t(key))
        self._set(self.lbl_state, "state", "  ·  ".join(parts))
        if stall != self.last.get("stall"):
            self.last["stall"] = stall
            if stall:
                self.lbl_alarm.pack(side="left", padx=(12, 0))
            else:
                self.lbl_alarm.pack_forget()
        ov = h.get("override") or 0
        self._set(self.lbl_diag, "diag", t("fn_diag_on", n=int(round(ov))) if _num(ov) and ov > 0 else "")
        i = self.cb_src.current()
        temp = source_temp(self.src_vals[i], st) if 0 <= i < len(self.src_vals) else None
        self.editor.set_live(temp, duty if _num(duty) else (tgt if _num(tgt) else None))

    def _set(self, lbl, key, text):
        if self.last.get(key) != text:
            self.last[key] = text
            lbl.config(text=text)

    def diag_failed(self):
        self.last["diag"] = None
        self.lbl_diag.config(text=t("fn_diag_fail"))


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
            if self.dirty and self.cfg is not None:           # do not lose an edit made just before leaving
                self.dirty = False
                gen.send("save", fanctl.set_config, copy.deepcopy(self.cfg))
            gen.stop.set()

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
        elif kind == "override":
            self._on_override(res, ctx)

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
        tk.Label(left, text=t("fn_master"), bg=bg, fg=C["fg"], font=(sw.F_HEAD, 12, "bold")).pack(side="left", padx=(10, 6))
        helptexts.info_icon(left, "fan_master", bg=bg).pack(side="left")
        stop = StopButton(row, C, t("fn_stop"), self.stop_all, (sw.F_TXT, 13, "bold"), bg=bg)
        stop.pack(side="right")
        helptexts.info_icon(row, "fan_stop", bg=bg).pack(side="right", padx=(0, 8))
        temps = tk.Frame(row, bg=bg)
        temps.pack(side="right", padx=(0, 24))
        self.lbl_t = {}
        for key in ("cpu", "gpu"):
            fr = tk.Frame(temps, bg=bg)
            fr.pack(side="left", padx=(0, 16))
            tk.Label(fr, text=t("fn_" + key), bg=bg, fg=C["dim"], font=(sw.F_TXT, 9)).pack(side="left", padx=(0, 6))
            lb = tk.Label(fr, text="-", bg=bg, fg=C["fg"], font=(sw.F_NUM, 13, "bold"), width=6, anchor="w")
            lb.pack(side="left")
            self.lbl_t[key] = lb
        msg = tk.Frame(card.body, bg=bg, height=px(38))
        msg.pack_propagate(False)
        sw.full(card, msg, pady=(2, 0), divider=False)
        self.msg_lbl = []
        for col in ("red", "orange"):
            lb = tk.Label(msg, text="", bg=bg, fg=C[col], font=(sw.F_TXT, 9), anchor="w", justify="left")
            uikit.autowrap(lb)
            lb.pack(fill="x")
            self.msg_lbl.append(lb)
        card.inner.update_idletasks()
        sf.reserve = card.inner.winfo_reqheight() + card.pt + card.pb + px(6)
        sw._size_scrolls()

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
        for hid, c in self.cards.items():
            h = self.hcfg(hid, c.kind, create=True)
            role = c.role()
            if role == "none":
                continue
            if role == "pump":
                h["mode"], h["fixed"] = "fixed", 100
            else:
                h["mode"] = "curve"
                h["curve"] = {"source": default_source(role, c.kind), "points": copy.deepcopy(pts)}
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

    def _on_enabled(self, ok, on):
        self.enable_busy = False
        if ok:
            self.notes.pop("enable", None)
        else:
            self.v_enable.set(not on)
            self.cfg["enabled"] = not on
            self.notes["enable"] = t("fn_enable_err")
        self._refresh_msg()

    def stop_all(self):
        """STOP: everything back to the BIOS, at once (the switch shows off straight away)."""
        self._flush()
        self.v_enable.set(False)
        if self.cfg is not None:
            self.cfg["enabled"] = False
        self.enable_busy = True
        self._send("stop", fanctl.stop)

    def _on_stopped(self, ok):
        self.enable_busy = False
        if ok:
            self.notes.pop("stop", None)
        else:
            self.notes["stop"] = t("fn_stop_err")
        self._refresh_msg()

    def diagnose(self, hid):
        self._send("override", fanctl.override, hid, DIAG_PCT, DIAG_SECS, ctx=hid)

    def _on_override(self, res, hid):
        c = self.cards.get(hid)
        if c is not None and not (isinstance(res, dict) and res.get("ok")):
            c.diag_failed()

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
            txt = f"{v:.0f} °C" if _num(v) else "-"
            if self.lbl_t[key].cget("text") != txt:
                self.lbl_t[key].config(text=txt)
        self._refresh_msg()
        self._update_banner(st)
        headers = sorted(st.get("headers") or [], key=lambda h: (KIND_ORDER.get(h.get("kind"), 1), h.get("hw") or "",
                                                                  h.get("index") or 0, h.get("id") or ""))
        order = tuple(h.get("id") for h in headers)
        if order != self.order:
            self._build_cards(headers)
            self.order = order
            self.temps_sig = None
        sig = tuple(x.get("id") for x in st.get("temps") or [])
        if sig != self.temps_sig:
            self.temps_sig = sig
            for c in self.cards.values():
                c.set_sources(st.get("temps") or [])
        for h in headers:
            c = self.cards.get(h.get("id"))
            if c is not None:
                c.update(h, st)

    def _build_cards(self, headers):
        for w in self.hdr_host.winfo_children():
            w.destroy()
        self.cards = {}
        if not headers:
            msg = t("fn_hw_idle") if (self.st or {}).get("state") != "error" else t("fn_no_headers")
            self.sw.label(self.hdr_host, msg, dim=True, bg=self.bgc).pack(anchor="w", pady=4)
            return
        self.win.update_idletasks()
        for h in headers:
            self.cards[h["id"]] = HeaderCard(self, self.hdr_host, h)

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

    def _update_banner(self, st):
        need = not st.get("pawnio", True)
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
