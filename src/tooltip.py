"""Themed hover popups (not windows of their own in the taskbar): help texts and live info balloons.

    Tooltip(widget, "text")                      # plain help text on hover
    Tooltip(widget, lambda: build_text())        # text computed each time it opens
    Tooltip.for_canvas(cv, tag, content)         # same for a canvas item / tag
    Tooltip(widget, content, live_ms=1000)       # refresh the content while it stays open

`content` may also be a callable returning a list of rows: [("left", "right"), ...] for a small two-column table,
or a string. Colours always come from the active theme, so balloons follow theme changes.
tip.note = callable -> str: an explanation shown above the content (e.g. Basic view: what a row means + its top programs).
"""
import sys
import tkinter as tk
import tkinter.font as tkfont

import themes

DELAY_MS = 350
WRAP_PX = 360
FADE_IN_S = 0.14          # opening: fades in and rises a few pixels into place (ease-out)
FADE_OUT_S = 0.09         # closing: fades out
RISE_PX = 6
SWITCH_S = 0.5            # a balloon closed less than this ago: the next one opens at once (moving along rows)
FULL_ALPHA = 0.999        # not 1.0: Tk would drop the layered style, and with it the pointer pass-through
_last_open = [0.0]        # time a balloon was last visible (shared by all balloons)
_SHARED = {}              # root -> the one balloon window (Tooltip._take)
FRAME_MS = 10             # (Windows timers round this up to the next ~15.6 ms tick: ~60 fps)


class Tooltip:
    def __init__(self, widget, content, live_ms=0, delay_ms=DELAY_MS, wrap=WRAP_PX, _bind=True):
        self.w = widget
        self.content = content
        self.live_ms = live_ms
        self.delay_ms = delay_ms
        self.wrap = wrap
        self.tip = None
        self._after = None
        self._live = None
        self._xy = (0, 0)
        self._tag = None                              # canvas tag (for_canvas), None for a plain widget
        self._watch = None
        self._fade = None                             # running fade: {"job", "t0", "a0", "a1", "dur", "done"}
        self._alpha = 0.0
        self._closing = False
        self._shape = self._size = self._pos = self._anchor = None
        self._up = False
        self.note = None                              # optional callable -> text shown above the content
        if _bind:
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.bind("<Motion>", self._motion, add="+")
            widget.bind("<ButtonPress>", self._leave, add="+")

    @classmethod
    def for_canvas(cls, cv, tag, content, live_ms=0, delay_ms=DELAY_MS, wrap=WRAP_PX):
        tt = cls(cv, content, live_ms, delay_ms, wrap, _bind=False)
        tt._tag = tag
        cv.tag_bind(tag, "<Enter>", tt._enter, add="+")
        cv.tag_bind(tag, "<Leave>", tt._leave, add="+")
        cv.tag_bind(tag, "<Motion>", tt._motion, add="+")
        cv.tag_bind(tag, "<ButtonPress>", tt._leave, add="+")
        return tt

    # ---------------------------------------------------------------- events
    def _enter(self, e):
        import time
        self._xy = (e.x_root, e.y_root)
        self._cancel()
        quick = self.delay_ms and time.perf_counter() - _last_open[0] < SWITCH_S
        self._after = self.w.after(15 if quick else self.delay_ms, self.show)

    def _motion(self, e):
        # the balloon stays where it opened (under the row): chasing the pointer made it jitter on every mouse move
        self._xy = (e.x_root, e.y_root)

    def _leave(self, e=None):
        self._cancel()
        self.hide()

    def _cancel(self):
        if self._after:
            try:
                self.w.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _pointer_over(self):
        """True while the pointer is still on the widget (or on an item with our tag). <Leave> is not reliable when
        the panel redraws the items under the pointer, so an open balloon also checks this itself."""
        try:
            px, py = self.w.winfo_pointerxy()
            under = self.w.winfo_containing(px, py)
            if under is None or not str(under).startswith(str(self.w)):
                return False
            if self._tag is None:
                return True
            cx, cy = px - self.w.winfo_rootx(), py - self.w.winfo_rooty()
            # (an item id or a tag: canvas items are found by either)
            return any(i == self._tag or self._tag in self.w.gettags(i)
                       for i in self.w.find_overlapping(cx, cy, cx, cy))
        except tk.TclError:
            return False

    def _watch_tick(self):
        self._watch = None
        if not self.tip:
            return
        if not self._pointer_over():
            self.hide()
            return
        self._watch = self.w.after(250, self._watch_tick)

    # ---------------------------------------------------------------- drawing
    def _resolve(self):
        c = self.content
        try:
            return c() if callable(c) else c
        except Exception:
            return ""

    @staticmethod
    def _fonts():
        """The balloon fonts (normal, bold): made once and shared, never one per refresh."""
        f = Tooltip.__dict__.get("_FONTS")
        if f is None:
            base = tkfont.nametofont("TkDefaultFont")
            n = base.copy()
            n.configure(size=max(9, abs(base.cget("size"))))
            b = n.copy()
            b.configure(weight="bold")
            f = Tooltip._FONTS = (n, b)
        return f

    def show(self):
        self._after = None
        if not self._pointer_over():              # left (or the item was redrawn away) before the delay ran out
            self.hide()
            return
        data = self._resolve()
        if not data:
            return
        note = ""
        if self.note is not None:
            try:
                note = self.note() or ""
            except Exception:
                note = ""
        if isinstance(data, str):
            rows = [((note + "\n\n" + data) if note else data, None, False)]
            note = ""
        else:
            rows = []
            for i, row in enumerate(data):
                left, right = (row[0], row[1]) if isinstance(row, (tuple, list)) and len(row) > 1 else (row, "")
                bold = i == 0 and isinstance(row, (tuple, list)) and len(row) > 2 and row[2] == "head"
                rows.append((left, right, bold))
        p = themes.palette()
        shape = (bool(note), tuple((r[1] is None, r[2]) for r in rows), p["card"], p["fg"], p["name"])
        win = self._take()
        if self._closing:                         # shown again while it fades out: back in, at the new place
            self._closing = False
            self._anchor = None
        if getattr(win, "_mshape", None) == shape:
            # same rows as on screen (a live refresh, or the next row of the same kind): only the texts change
            labels = win._mlabels
            k = 0
            if note:
                labels[0].config(text=note)
                k = 1
            for left, right, _ in rows:
                labels[k].config(text=left)
                k += 1
                if right is not None:
                    labels[k].config(text=right)
                    k += 1
        else:
            for ch in win.winfo_children():
                ch.destroy()
            win._mshape = shape
            win._mlabels = labels = []
            fnt, fbold = self._fonts()
            outer = tk.Frame(win, bg=p["border"], bd=0)
            outer.pack()
            body = tk.Frame(outer, bg=p["card"], padx=10, pady=7)
            body.pack(padx=1, pady=1)
            if len(rows) == 1 and rows[0][1] is None:
                lb = tk.Label(body, text=rows[0][0], bg=p["card"], fg=p["fg"], font=fnt, justify="left",
                              wraplength=self.wrap)
                lb.pack(anchor="w")
                labels.append(lb)
            else:
                off = 0
                if note:
                    lb = tk.Label(body, text=note, bg=p["card"], fg=p["fg"], font=fnt, justify="left",
                                  wraplength=self.wrap)
                    lb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
                    labels.append(lb)
                    off = 1
                for i, (left, right, bold) in enumerate(rows):
                    f = fbold if bold else fnt
                    lb = tk.Label(body, text=left, bg=p["card"], fg=p["fg"] if bold else p["name"], font=f,
                                  anchor="w")
                    lb.grid(row=i + off, column=0, sticky="w")
                    labels.append(lb)
                    rb = tk.Label(body, text=right or "", bg=p["card"], fg=p["fg"], font=f, anchor="e")
                    rb.grid(row=i + off, column=1, sticky="e", padx=(14, 0))
                    labels.append(rb)
        self._place(resize=True)
        f = self._fade
        if self._alpha < FULL_ALPHA and not (f and f["a1"] == FULL_ALPHA):
            self._fade_to(FULL_ALPHA, FADE_IN_S)
        self._arm()

    def _take(self):
        """The balloon window: ONE per application, made once and never destroyed. It stays mapped, invisible
        (alpha 0) and pointer-transparent between balloons, so opening one costs no window creation or mapping
        (that held the UI thread for 20 - 45 ms, right in the middle of the row's hover swell). A balloon that
        takes it from another one (moving along the rows) goes on from that one's opacity: it changes in place."""
        win = prepare(self.w)
        prev = win._owner
        if prev is not None and prev is not self:
            prev._let_go()
        win._owner = self
        if self.tip is not win:
            self.tip = win
            self._alpha = win._a
            self._anchor = None
            self._pos = self._size = None
        return win

    def _let_go(self):
        """Another balloon took the shared window: stop fading it, forget it (nothing is destroyed)."""
        f = self._fade
        if f and f.get("job"):
            try:
                self.w.after_cancel(f["job"])
            except tk.TclError:
                pass
        self._fade = None
        for name in ("_watch", "_live"):
            job = getattr(self, name)
            if job:
                try:
                    self.w.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self.tip = None
        self._closing = False
        self._alpha = 0.0
        self._size = self._pos = self._anchor = None

    def _arm(self):
        if self._watch is None:
            self._watch = self.w.after(250, self._watch_tick)
        if self.live_ms and self._live is None:
            self._live = self.w.after(self.live_ms, self._refresh)

    def _refresh(self):
        self._live = None
        if self.tip:
            self.show()

    def _pass_through(self):
        _pass_through(self.tip)

    def _anchor_rect(self):
        """Screen rectangle (x0, y0, x1, y1) the balloon belongs to: the canvas item(s) with our tag, else the widget."""
        w = self.w
        rx, ry = w.winfo_rootx(), w.winfo_rooty()
        if self._tag is not None:
            bb = w.bbox(self._tag)
            if bb:
                return rx + bb[0], ry + bb[1], rx + bb[2], ry + bb[3]
        if w.winfo_height() > 80:                     # a big widget (a chart, a table): the spot under the pointer
            px, py = self._xy
            return px - 1, py - 10, px + 1, py + 12
        return rx, ry, rx + w.winfo_width(), ry + w.winfo_height()

    def _place(self, resize=False):
        """Put the balloon just under its row (above it when there is no room below), horizontally at the pointer.
        It does not follow the pointer. Its size is measured only when its content changed (resize): a layout pass
        is not free."""
        if not self.tip:
            return
        if resize or not getattr(self, "_size", None):
            self.tip.update_idletasks()
            self._size = (self.tip.winfo_reqwidth(), self.tip.winfo_reqheight())
        w, h = self._size
        if getattr(self, "_anchor", None) is None:
            try:
                self._anchor = (self._xy[0],) + tuple(self._anchor_rect())
            except tk.TclError:
                self._anchor = (self._xy[0], self._xy[0], self._xy[1], self._xy[0], self._xy[1] + 4)
        px, x0, y0, x1, y1 = self._anchor
        x, y, up = px - 12, y1 + 6, False
        try:
            import uikit
            ml, mt, mr, mb = uikit.monitor_work(px, y1, self.w)
            if y + h > mb and y0 - 6 - h >= mt:
                y, up = y0 - 6 - h, True              # no room below the row: open above it
            x, y = max(ml, min(x, mr - w)), max(mt, min(y, mb - h))
        except Exception:
            pass
        self._up = up
        pos = (int(x), int(y))
        if pos != getattr(self, "_pos", None) or resize:
            self._pos = pos
            self._move()
        if self.tip.state() == "withdrawn":
            self.tip.deiconify()
            self._closing = False
            self._fade_to(FULL_ALPHA, FADE_IN_S)

    def _move(self):
        """Geometry for the current fade: while it opens it rises the last few pixels into place."""
        x, y = self._pos
        off = int(round((1.0 - self._alpha) * RISE_PX)) if not self._closing else 0
        self.tip.geometry(f"+{x}+{y + (-off if getattr(self, '_up', False) else off)}")

    def _fade_to(self, target, dur, done=None):
        import time
        f = self._fade
        if f and f.get("job"):
            try:
                self.w.after_cancel(f["job"])
            except tk.TclError:
                pass
        self._fade = {"job": None, "t0": time.perf_counter(), "a0": self._alpha, "a1": target,
                      "dur": max(0.01, dur * abs(target - self._alpha)), "done": done}
        self._fade_step()

    def _fade_step(self):
        import time
        f = self._fade
        if f is None or self.tip is None:
            return
        f["job"] = None
        x = min(1.0, (time.perf_counter() - f["t0"]) / f["dur"])
        e = 1.0 - (1.0 - x) ** 3                      # ease-out
        self._alpha = f["a0"] + (f["a1"] - f["a0"]) * e
        try:
            # 1.0 would make Windows drop the layered style mid-fade; it is set exactly once at the end
            self.tip.attributes("-alpha", self._alpha if x < 1.0 else f["a1"])
            if getattr(self.tip, "_shared", False):
                self.tip._a = self._alpha
            if self._pos is not None:
                self._move()
        except tk.TclError:
            return
        if x < 1.0:
            f["job"] = self.w.after(FRAME_MS, self._fade_step)
        else:
            self._fade = None
            if f["done"]:
                f["done"]()

    def hide(self):
        self._cancel()                            # a pending show must not open the balloon after a hide
        if self._watch:
            try:
                self.w.after_cancel(self._watch)
            except tk.TclError:
                pass
            self._watch = None
        if self._live:
            try:
                self.w.after_cancel(self._live)
            except tk.TclError:
                pass
            self._live = None
        if self.tip is not None and self._alpha > 0.05:
            import time
            _last_open[0] = time.perf_counter()
        if self.tip is not None and not self._closing:
            self._closing = True
            self._fade_to(0.0, FADE_OUT_S, self._destroy)

    def _destroy(self):
        tip = self.tip
        if tip is not None and getattr(tip, "_shared", False):
            if tip._owner is self:                # faded out: it stays, invisible, for the next balloon
                tip._owner = None
                tip._a = 0.0
        elif tip is not None:                     # a window of its own (logview.ChartTip)
            try:
                tip.destroy()
            except tk.TclError:
                pass
        self.tip = None
        self._closing = False
        self._alpha = 0.0
        self._shape = self._size = self._pos = self._anchor = None


def _pass_through(win):
    """The balloon never takes the pointer (Windows: layered + transparent, no activation): moving on to the row
    below it reaches that row at once instead of the balloon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        u = ctypes.windll.user32
        u.GetWindowLongW.restype = ctypes.c_long
        ex = u.GetWindowLongW(hwnd, -20)
        u.SetWindowLongW(hwnd, -20, ex | 0x20 | 0x80000 | 0x80 | 0x08000000)
    except Exception:
        pass


def prepare(widget):
    """The application's one balloon window (made on the first call): the panel calls this once after start, so
    even the first balloon opens without making a window."""
    root = widget._root()
    win = _SHARED.get(root)
    try:
        if win is not None and win.winfo_exists():
            return win
    except tk.TclError:
        pass
    win = tk.Toplevel(root)
    win.withdraw()
    win.wm_overrideredirect(True)
    try:
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    win._owner, win._a, win._shared, win._mshape = None, 0.0, True, None
    _pass_through(win)
    win.geometry("+%d+%d" % (root.winfo_rootx() + 20, root.winfo_rooty() + 20))
    win.deiconify()
    _SHARED[root] = win
    try:                                          # fonts and a first layout pass, also before the first hover
        fnt, fbold = Tooltip._fonts()
        tk.Label(win, text="0", font=fnt).pack()
        tk.Label(win, text="0", font=fbold).pack()
        win.update_idletasks()
    except tk.TclError:
        pass
    return win
