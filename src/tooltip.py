"""Themed hover popups (not windows of their own in the taskbar): help texts and live info balloons.

    Tooltip(widget, "text")                      # plain help text on hover
    Tooltip(widget, lambda: build_text())        # text computed each time it opens
    Tooltip.for_canvas(cv, tag, content)         # same for a canvas item / tag
    Tooltip(widget, content, live_ms=1000)       # refresh the content while it stays open

`content` may also be a callable returning a list of rows: [("left", "right"), ...] for a small two-column table,
or a string. Colours always come from the active theme, so balloons follow theme changes.
"""
import tkinter as tk
import tkinter.font as tkfont

import themes

DELAY_MS = 350
WRAP_PX = 360


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
        if _bind:
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.bind("<Motion>", self._motion, add="+")
            widget.bind("<ButtonPress>", self._leave, add="+")

    @classmethod
    def for_canvas(cls, cv, tag, content, live_ms=0, delay_ms=DELAY_MS, wrap=WRAP_PX):
        tt = cls(cv, content, live_ms, delay_ms, wrap, _bind=False)
        cv.tag_bind(tag, "<Enter>", tt._enter, add="+")
        cv.tag_bind(tag, "<Leave>", tt._leave, add="+")
        cv.tag_bind(tag, "<Motion>", tt._motion, add="+")
        return tt

    # ---------------------------------------------------------------- events
    def _enter(self, e):
        self._xy = (e.x_root, e.y_root)
        self._cancel()
        self._after = self.w.after(self.delay_ms, self.show)

    def _motion(self, e):
        self._xy = (e.x_root, e.y_root)
        if self.tip:
            self._place()

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

    # ---------------------------------------------------------------- drawing
    def _resolve(self):
        c = self.content
        try:
            return c() if callable(c) else c
        except Exception:
            return ""

    def show(self):
        self._after = None
        data = self._resolve()
        if not data:
            return
        p = themes.palette()
        if self.tip is None:
            self.tip = tk.Toplevel(self.w)
            self.tip.withdraw()                       # shown by _place, once it has its position
            self.tip.wm_overrideredirect(True)
            try:
                self.tip.attributes("-topmost", True)
            except tk.TclError:
                pass
        for ch in self.tip.winfo_children():
            ch.destroy()
        outer = tk.Frame(self.tip, bg=p["border"], bd=0)
        outer.pack()
        body = tk.Frame(outer, bg=p["card"], padx=10, pady=7)
        body.pack(padx=1, pady=1)
        fnt = tkfont.nametofont("TkDefaultFont").copy()
        fnt.configure(size=max(9, abs(fnt.cget("size"))))
        if isinstance(data, str):
            tk.Label(body, text=data, bg=p["card"], fg=p["fg"], font=fnt, justify="left",
                     wraplength=self.wrap).pack(anchor="w")
        else:
            for i, row in enumerate(data):
                left, right = (row[0], row[1]) if isinstance(row, (tuple, list)) and len(row) > 1 else (row, "")
                bold = i == 0 and isinstance(row, (tuple, list)) and len(row) > 2 and row[2] == "head"
                f = fnt.copy()
                if bold:
                    f.configure(weight="bold")
                tk.Label(body, text=left, bg=p["card"], fg=p["fg"] if bold else p["name"], font=f,
                         anchor="w").grid(row=i, column=0, sticky="w")
                tk.Label(body, text=right, bg=p["card"], fg=p["fg"], font=f,
                         anchor="e").grid(row=i, column=1, sticky="e", padx=(14, 0))
        self._place()
        if self.live_ms and self._live is None:
            self._live = self.w.after(self.live_ms, self._refresh)

    def _refresh(self):
        self._live = None
        if self.tip:
            self.show()

    def _place(self):
        if not self.tip:
            return
        self.tip.update_idletasks()
        x, y = self._xy
        w, h = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        sx = self.w.winfo_vrootx() if hasattr(self.w, "winfo_vrootx") else 0
        # keep the balloon on the screen the pointer is on (monitors may have negative coordinates)
        x, y = x + 14, y + 18
        try:
            right = self.w.winfo_rootx() + self.w.winfo_width()
            if x + w > right + 400:
                x = self._xy[0] - w - 10
        except tk.TclError:
            pass
        try:
            import uikit
            ml, mt, mr, mb = uikit.monitor_work(self._xy[0], self._xy[1], self.w)
            x, y = max(ml, min(x, mr - w)), max(mt, min(y, mb - h))
        except Exception:
            pass
        self.tip.geometry(f"+{int(x)}+{int(y)}")
        if self.tip.state() == "withdrawn":
            self.tip.deiconify()

    def hide(self):
        if self._live:
            try:
                self.w.after_cancel(self._live)
            except tk.TclError:
                pass
            self._live = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None
