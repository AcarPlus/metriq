"""Modern themed widgets (rounded, anti-aliased via Pillow/gfx) shared by the settings and process windows.

    entry(), spin()      rounded input fields with a focus ring (accent) and an error state
    Combo                drop-down with a themed popup list           Button   primary / secondary / icon buttons
    Switch / Check       toggle switches (Check = switch + label)     Segmented  two or three way pill selector
    Slider, Scrollbar    slim themed slider / scrollbar                Card     rounded section card with accent stripe
    Nav                  sidebar navigation (icon + label)             ScrollFrame  vertically scrolling container

Everything takes the colour dict `C` of the active theme (panel.C). Without Pillow the shapes fall back to plain
canvas rectangles / polygons, so the dialogs keep working (just less pretty)."""
import math
import tkinter as tk
import tkinter.font as tkfont

import gfx
import themes
from themes import mix

if gfx.AVAILABLE:
    from PIL import Image, ImageDraw, ImageTk          # noqa: F401  (ImageTk used through gfx._render)

SCALE = 1.0
_IM = {}
_FONTS = {}


def set_scale(w):
    global SCALE
    try:
        SCALE = max(1.0, w.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        SCALE = 1.0


def px(v):
    return int(round(v * SCALE))


def _cached(key, w, h, paint):
    im = _IM.get(key)
    if im is None:
        if len(_IM) > 1500:
            _IM.clear()                                    # widgets keep their own references to what they show
        im = _IM[key] = gfx._render(w, h, paint)
    return im


def font_obj(w, font):
    key = (str(w.winfo_toplevel()), tuple(font) if not isinstance(font, str) else font)
    f = _FONTS.get(key)
    if f is None:
        f = _FONTS[key] = tkfont.Font(root=w, font=font)
    return f


def _bg_of(w):
    try:
        return w.cget("bg")
    except tk.TclError:
        return "#000000"


# ---------------------------------------------------------------- pictures
def rr(w, h, r, fill, outline=None, ow=1):
    """Anti-aliased rounded rectangle picture (None without Pillow)."""
    if not gfx.AVAILABLE:
        return None
    w, h = max(2, int(w)), max(2, int(h))
    r = max(0.0, min(r, h / 2, w / 2))

    def paint(d, k):
        gfx.rrect(d, k, 0, 0, w, h, r, fill=fill, outline=outline, w=ow)
    return _cached(("rr", w, h, round(r, 1), fill, outline, ow), w, h, paint)


def _poly_rr(cv, x0, y0, x1, y1, r, fill, outline, tags):
    r = max(0, min(r, (y1 - y0) / 2, (x1 - x0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0,
           y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, fill=fill or "", outline=outline or "", tags=tags)


def draw_rr(cv, x0, y0, w, h, r, fill, outline=None, ow=1, tags="bg"):
    """Rounded rectangle on a canvas; returns the picture to keep referenced (or None)."""
    im = rr(w, h, r, fill, outline, ow)
    if im is not None:
        cv.create_image(x0, y0, image=im, anchor="nw", tags=tags)
    else:
        _poly_rr(cv, x0, y0, x0 + w - 1, y0 + h - 1, r, fill, outline, tags)
    return im


def disc(d, k, cx, cy, r, fill):
    gfx._disc(d, k, cx, cy, r, fill)


def glyph(kind, size, col):
    """Small stroked glyph: up | down | x | refresh | check | chev_down."""
    if not gfx.AVAILABLE:
        return None
    n = max(8, int(round(size)))

    def paint(d, k):
        w = max(1.4, n * 0.13)
        c = n / 2
        a = n * 0.24
        if kind in ("up", "down", "chev_down"):
            sgn = -1 if kind == "up" else 1
            pts = [(c - a * 1.15, c - sgn * a * 0.55), (c, c + sgn * a * 0.6), (c + a * 1.15, c - sgn * a * 0.55)]
            gfx._line(d, k, *pts[0], *pts[1], w, col, True)
            gfx._line(d, k, *pts[1], *pts[2], w, col, True)
        elif kind == "x":
            gfx._line(d, k, c - a, c - a, c + a, c + a, w, col, True)
            gfx._line(d, k, c - a, c + a, c + a, c - a, w, col, True)
        elif kind == "check":
            gfx._line(d, k, c - a * 1.2, c, c - a * 0.3, c + a * 0.9, w * 1.1, col, True)
            gfx._line(d, k, c - a * 0.3, c + a * 0.9, c + a * 1.3, c - a * 0.8, w * 1.1, col, True)
        elif kind == "refresh":
            gfx._arc(d, k, c, c, n * 0.30, 40, 330, w, col)
            ang = math.radians(40)
            tx, ty = c + n * 0.30 * math.cos(ang), c + n * 0.30 * math.sin(ang)
            gfx._poly(d, k, [(tx - n * 0.02, ty - n * 0.26), (tx + n * 0.2, ty + n * 0.04), (tx - n * 0.2, ty + n * 0.12)],
                      col)
    return _cached(("glyph", kind, n, col), n, n, paint)


def nav_icon(kind, size, col):
    """Sidebar page icon (stroked, transparent): look | log | hw | layout | theme | diag."""
    if not gfx.AVAILABLE:
        return None
    n = max(12, int(round(size)))

    def paint(d, k):
        w = max(1.5, n * 0.095)

        def L(x0, y0, x1, y1, ww=w):
            gfx._line(d, k, x0 * n, y0 * n, x1 * n, y1 * n, ww, col, True)
        if kind == "look":                                   # sliders
            for y, kx in ((0.28, 0.66), (0.50, 0.34), (0.72, 0.58)):
                L(0.14, y, 0.86, y)
                gfx._disc(d, k, kx * n, y * n, n * 0.11, col)
        elif kind == "log":                                  # record: ring + dot
            gfx._arc(d, k, n * 0.5, n * 0.5, n * 0.34, 0, 360, w, col)
            gfx._disc(d, k, n * 0.5, n * 0.5, n * 0.14, col)
        elif kind == "hw":                                   # chip
            gfx.rrect(d, k, n * 0.26, n * 0.26, n * 0.74, n * 0.74, n * 0.09, outline=col, w=w)
            gfx.rrect(d, k, n * 0.40, n * 0.40, n * 0.60, n * 0.60, n * 0.03, fill=col)
            for p in (0.38, 0.62):
                L(p, 0.10, p, 0.26)
                L(p, 0.74, p, 0.90)
                L(0.10, p, 0.26, p)
                L(0.74, p, 0.90, p)
        elif kind == "layout":                               # dashboard tiles
            g = n * 0.07
            for x0, y0, x1, y1 in ((0.14, 0.14, 0.52, 0.56), (0.58, 0.14, 0.86, 0.38), (0.58, 0.44, 0.86, 0.86),
                                   (0.14, 0.62, 0.52, 0.86)):
                gfx.rrect(d, k, x0 * n, y0 * n, x1 * n, y1 * n, n * 0.07, outline=col, w=w * 0.9)
            del g
        elif kind == "theme":                                # half-filled circle
            gfx._arc(d, k, n * 0.5, n * 0.5, n * 0.34, 0, 360, w, col)
            d.pieslice([n * 0.16 * k, n * 0.16 * k, n * 0.84 * k, n * 0.84 * k], 90, 270, fill=col)
        elif kind == "perf":                                 # speedometer
            gfx._arc(d, k, n * 0.5, n * 0.56, n * 0.36, 150, 390, w, col)
            L(0.5, 0.56, 0.68, 0.36, w * 1.1)
            gfx._disc(d, k, n * 0.5, n * 0.56, n * 0.07, col)
        else:                                                # diag: pulse line
            pts = [(0.08, 0.54), (0.30, 0.54), (0.40, 0.22), (0.54, 0.80), (0.64, 0.46), (0.72, 0.54), (0.92, 0.54)]
            for a, b in zip(pts, pts[1:]):
                L(a[0], a[1], b[0], b[1])
    return _cached(("nav", kind, n, col), n, n, paint)


def switch_img(q, w, h, on_col, off_col, outline, knob_on, knob_off, ring=None):
    """Toggle picture; q = 0 (off) .. 1 (on), intermediate values for the slide animation."""
    if not gfx.AVAILABLE:
        return None
    q = round(q * 5) / 5
    w, h = int(w), int(h)

    def paint(d, k):
        fill = mix(off_col, on_col, q)
        oc = ring or mix(outline, on_col, q)
        ow = 2 if ring else 1
        gfx.rrect(d, k, 0, 0, w, h, h / 2, fill=fill, outline=oc, w=ow)
        r = h / 2 - max(2.5, h * 0.2)
        cx = h / 2 + (w - h) * q
        gfx._disc(d, k, cx, h / 2, r, mix(knob_off, knob_on, q))
    return _cached(("sw", q, w, h, on_col, off_col, outline, knob_on, knob_off, ring), w, h, paint)


# ---------------------------------------------------------------- base: canvas that keeps its pictures alive
class _Cv(tk.Canvas):
    def __init__(self, parent, bg=None, **kw):
        super().__init__(parent, bg=bg or _bg_of(parent), highlightthickness=0, bd=0, **kw)
        self._refs = []

    def keep(self, im):
        if im is not None:
            self._refs.append(im)
        return im

    def put_rr(self, x0, y0, w, h, r, fill, outline=None, ow=1, tags="bg"):
        self.keep(draw_rr(self, x0, y0, w, h, r, fill, outline, ow, tags))


# ---------------------------------------------------------------- input fields
class Field(_Cv):
    """Rounded input shell. Subclasses / factories put an Entry (or draw text) inside."""

    def __init__(self, parent, C, font, fill=None, bg=None, height=None, radius=8):
        self.C = C
        fo = font_obj(parent, font)
        self.h = height or px(max(30, fo.metrics("linespace") + 14))
        super().__init__(parent, bg=bg, height=self.h, width=px(100))
        self.font, self.fo = font, fo
        self.fill = fill or C["track"]
        self.r = radius
        self.err = self.focused = self.hover = self.disabled = False
        self.inner, self.lead_im = None, None
        self.pad_l = self.pad_r = px(10)
        self.right = 0
        self._fw = 0
        self.bind("<Configure>", self._cfg)
        self.bind("<Enter>", lambda e: self._set("hover", True))
        self.bind("<Leave>", lambda e: self._set("hover", False))
        self.bind("<Button-1>", self._click, add="+")

    # -- state
    def _set(self, attr, v):
        if getattr(self, attr) != v:
            setattr(self, attr, v)
            self.draw()

    def set_error(self, bad):
        self._set("err", bool(bad))

    def _click(self, e):
        self.focus_set()

    def focus_set(self):
        (self.inner or super()).focus_set()

    def configure(self, cnf=None, **kw):
        st = kw.pop("state", None)
        if st is not None:
            self.disabled = st == "disabled"
            if isinstance(self.inner, tk.Entry):
                self.inner.config(state=st)
            self.draw()
        if cnf or kw:
            return super().configure(cnf, **kw)
    config = configure

    # -- geometry
    def set_inner(self, w, lead=None):
        self.inner = w
        x = self.pad_l
        if lead is not None:
            self.lead_im = lead
            x += lead.width() + px(8)
        self.create_window(x, self.h // 2, window=w, anchor="w", tags="inner")
        self.x_in = x
        tk.Canvas.configure(self, width=x + w.winfo_reqwidth() + self.pad_r + self.right)
        for ev in ("<FocusIn>", "<FocusOut>"):
            w.bind(ev, self._focus, add="+")
        w.bind("<Enter>", lambda e: self._set("hover", True), add="+")
        w.bind("<Leave>", lambda e: self._set("hover", False), add="+")

    def _focus(self, e):
        self.focused = str(e.type) == "FocusIn"
        self.draw()

    def _cfg(self, e):
        self._fw = e.width
        if self.inner is not None:
            self.itemconfigure("inner", width=max(8, e.width - self.x_in - self.pad_r - self.right))
        self.draw()

    def border(self):
        C = self.C
        if self.err:
            return C["red"], 2 if self.focused else 1
        if self.focused:
            return C["ui"], 2
        return (mix(C["border"], C["dim"], 0.55) if self.hover and not self.disabled else C["border"]), 1

    def draw(self):
        w = self._fw or int(float(self.cget("width")))
        self.delete("bg", "lead", "content")
        self._refs = []
        fill = self.fill
        oc, ow = self.border()
        if self.disabled:
            oc = mix(oc, _bg_of(self), 0.5)
        self.put_rr(0, 0, w, self.h, self.r, fill, oc, ow)
        self.tag_lower("bg")
        if self.lead_im is not None:
            self.create_image(self.pad_l, self.h // 2, image=self.keep(self.lead_im), anchor="w", tags="lead")
        self.draw_content(w)

    def draw_content(self, w):
        pass


def entry(parent, C, var, width, font, justify="left", bg=None, fill=None, icon=None, height=None, radius=8):
    f = Field(parent, C, font, fill, bg, height, radius)
    e = tk.Entry(f, textvariable=var, width=width, bg=f.fill, fg=C["fg"], insertbackground=C["fg"], relief="flat",
                 bd=0, highlightthickness=0, font=font, justify=justify, disabledbackground=f.fill,
                 disabledforeground=C["faint"], selectbackground=C["select"], selectforeground=C["fg"])
    f.set_inner(e, lead=icon)
    f.entry = e
    return f


class Spin(Field):
    def __init__(self, parent, C, var, lo, hi, inc, fmt, width, font, bg=None, fill=None):
        super().__init__(parent, C, font, fill, bg)
        self.var, self.lo, self.hi, self.inc, self.fmt = var, lo, hi, inc, fmt
        self.right = px(22)
        self._rep = None
        self._zone = None
        e = tk.Entry(self, textvariable=var, width=width, bg=self.fill, fg=C["fg"], insertbackground=C["fg"],
                     relief="flat", bd=0, highlightthickness=0, font=font, justify="left",
                     selectbackground=C["select"], selectforeground=C["fg"])
        self.set_inner(e)
        self.entry = e
        e.bind("<Up>", lambda ev: (self.step(1), "break")[1])
        e.bind("<Down>", lambda ev: (self.step(-1), "break")[1])
        self.bind("<Button-1>", self._press, add="+")
        self.bind("<ButtonRelease-1>", lambda ev: self._stop())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda ev: self._motion(None), add="+")

    def step(self, d):
        try:
            v = float(str(self.var.get()).strip().replace(",", "."))
        except ValueError:
            v = self.lo if d > 0 else self.hi
            d = 0
        v = min(self.hi, max(self.lo, v + d * self.inc))
        self.var.set(self.fmt % v)

    def _zone_at(self, e):
        w = self._fw or 1
        if e is None or e.x < w - self.right - px(4):
            return None
        return 1 if e.y < self.h / 2 else -1

    def _motion(self, e):
        z = self._zone_at(e) if e is not None else None
        if z != self._zone:
            self._zone = z
            self.draw()

    def _press(self, e):
        z = self._zone_at(e)
        if z:
            self.step(z)
            self._stop()
            self._rep = self.after(380, lambda: self._repeat(z))

    def _repeat(self, z):
        self.step(z)
        self._rep = self.after(70, lambda: self._repeat(z))

    def _stop(self):
        if self._rep:
            self.after_cancel(self._rep)
            self._rep = None

    def draw_content(self, w):
        C = self.C
        cx = w - self.right // 2 - px(6)
        for z, name in ((1, "up"), (-1, "down")):
            col = C["fg"] if self._zone == z else C["dim"]
            im = glyph(name, px(12), col)
            if im is not None:
                self.create_image(cx, self.h * (0.30 if z == 1 else 0.70), image=self.keep(im), tags="content")
            else:
                self.create_text(cx, self.h * (0.30 if z == 1 else 0.70), text="▴" if z == 1 else "▾", fill=col,
                                 font=(self.font[0], 7), tags="content")


def spin(parent, C, var, lo, hi, inc, fmt, width, font, bg=None, fill=None):
    return Spin(parent, C, var, lo, hi, inc, fmt, width, font, bg, fill)


class Combo(Field):
    """Read-only drop-down. API like ttk.Combobox: current(), configure(values=), <<ComboboxSelected>>."""
    MAXROWS = 9

    def __init__(self, parent, C, values, index, width, font, bg=None, fill=None):
        super().__init__(parent, C, font, fill, bg)
        self.values = [str(v) for v in values]
        self.index = max(0, min(index, len(self.values) - 1)) if self.values else -1
        self.right = px(26)
        self._pop = None
        self._closed_at = 0.0
        tk.Canvas.configure(self, width=self.fo.measure("0") * width + self.pad_l + self.pad_r + self.right,
                            takefocus=1, cursor="hand2")
        self.bind("<Button-1>", self._toggle, add="+")
        self.bind("<FocusIn>", self._focus)
        self.bind("<FocusOut>", self._focus)
        for k in ("<Return>", "<space>", "<Alt-Down>"):
            self.bind(k, lambda e: self.open())
        self.bind("<Down>", lambda e: self._move(1))
        self.bind("<Up>", lambda e: self._move(-1))

    def current(self, idx=None):
        if idx is None:
            return self.index
        self.index = max(0, min(int(idx), len(self.values) - 1)) if self.values else -1
        self.draw()

    def configure(self, cnf=None, **kw):
        if "values" in kw:
            self.values = [str(v) for v in kw.pop("values")]
            self.index = min(self.index, len(self.values) - 1)
            self.draw()
        return super().configure(cnf, **kw)
    config = configure

    def _toggle(self, e=None):
        import time
        if self._pop is not None:
            self.close()
        elif time.time() - self._closed_at > 0.25:
            self.open()

    def _move(self, d):
        if self.values:
            n = max(0, min(len(self.values) - 1, self.index + d))
            if n != self.index:
                self.index = n
                self.draw()
                self.event_generate("<<ComboboxSelected>>")

    def draw_content(self, w):
        C = self.C
        avail = w - self.pad_l - self.right - px(4)
        txt = self.values[self.index] if 0 <= self.index < len(self.values) else ""
        if self.fo.measure(txt) > avail:
            while txt and self.fo.measure(txt + "…") > avail:
                txt = txt[:-1]
            txt += "…"
        self.create_text(self.pad_l, self.h / 2, text=txt, anchor="w", font=self.font, tags="content",
                         fill=C["faint"] if self.disabled else C["fg"])
        im = glyph("chev_down", px(14), C["fg"] if (self.hover or self._pop) else C["dim"])
        if im is not None:
            self.create_image(w - px(14), self.h / 2, image=self.keep(im), tags="content")
        else:
            self.create_text(w - px(14), self.h / 2, text="▾", font=self.font, fill=C["dim"], tags="content")

    # ---- popup list
    def open(self):
        if self._pop is not None or not self.values:
            return
        import time
        C, fo = self.C, self.fo
        rh = px(max(28, fo.metrics("linespace") + 10))
        n = len(self.values)
        rows = min(n, self.MAXROWS)
        pop = tk.Toplevel(self)
        self._pop = pop
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(bg=C["border"])
        try:
            pop.attributes("-topmost", True)
        except tk.TclError:
            pass
        pad = px(4)
        wmax = max(fo.measure(v) for v in self.values) + px(52)
        W = max(self.winfo_width(), min(wmax, self.winfo_screenwidth() - 40))
        H = rows * rh + 2 * pad
        cv = _Cv(pop, bg=C["card"], width=W - 2, height=H)
        cv.pack(padx=1, pady=1)
        st = {"top": max(0, min(self.index - rows // 2, n - rows)), "hover": self.index}

        def draw():
            cv.delete("all")
            cv._refs = []
            for i in range(st["top"], min(n, st["top"] + rows)):
                y = pad + (i - st["top"]) * rh
                sel, hot = i == self.index, i == st["hover"]
                if sel or hot:
                    cv.put_rr(pad, y, W - 2 - 2 * pad - (px(8) if n > rows else 0), rh, px(6),
                              mix(C["card"], C["ui"], 0.20 if sel else 0.10), tags="row")
                cv.create_text(pad + px(10), y + rh / 2, text=self.values[i], anchor="w", font=self.font,
                               fill=C["fg"] if (sel or hot) else C["name"])
                if sel:
                    im = glyph("check", px(14), C["ui"])
                    if im is not None:
                        cv.create_image(W - 2 - pad - px(20) - (px(8) if n > rows else 0), y + rh / 2,
                                        image=cv.keep(im))
            if n > rows:
                th = max(px(24), (H - 2 * pad) * rows / n)
                ty = pad + (H - 2 * pad - th) * st["top"] / max(1, n - rows)
                cv.put_rr(W - 2 - px(8), ty, px(4), th, px(2), C["faint"], tags="sb")

        def at(e):
            return max(0, min(n - 1, st["top"] + (e.y - pad) // rh))

        def motion(e):
            i = at(e)
            if i != st["hover"]:
                st["hover"] = i
                draw()

        def pick(i):
            self.close()
            if i != self.index:
                self.index = i
                self.draw()
                self.event_generate("<<ComboboxSelected>>")

        def scroll(d):
            nt = max(0, min(n - rows, st["top"] + d))
            if nt != st["top"]:
                st["top"] = nt
                draw()

        def key(e):
            k = e.keysym
            if k == "Escape":
                self.close()
            elif k in ("Return", "KP_Enter", "space"):
                pick(st["hover"])
            elif k in ("Down", "Up", "Next", "Prior", "Home", "End"):
                d = {"Down": 1, "Up": -1, "Next": rows, "Prior": -rows}.get(k)
                st["hover"] = 0 if k == "Home" else n - 1 if k == "End" else max(0, min(n - 1, st["hover"] + d))
                if st["hover"] < st["top"]:
                    st["top"] = st["hover"]
                elif st["hover"] >= st["top"] + rows:
                    st["top"] = st["hover"] - rows + 1
                draw()
            elif e.char and e.char.isprintable():
                ch = e.char.lower()
                for j in list(range(st["hover"] + 1, n)) + list(range(0, st["hover"] + 1)):
                    if self.values[j].lower().startswith(ch):
                        st["hover"] = j
                        st["top"] = max(0, min(n - rows, j - rows // 2))
                        draw()
                        break
        cv.bind("<Motion>", motion)
        cv.bind("<ButtonRelease-1>", lambda e: pick(at(e)))
        cv.bind("<MouseWheel>", lambda e: scroll(-1 if e.delta > 0 else 1))
        pop.bind("<Key>", key)
        pop.bind("<FocusOut>", lambda e: pop.after(60, self._check_focus))
        draw()
        pop.update_idletasks()
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + px(4)
        if y + H + 2 > self.winfo_screenheight() - 40:
            y = self.winfo_rooty() - H - 2 - px(4)
        x = max(0, min(x, self.winfo_screenwidth() - W - 2))
        pop.geometry(f"{W}x{H + 2}+{x}+{y}")
        try:                                                 # Windows 11: rounded corners for the popup
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(pop.winfo_id()) or pop.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
        except Exception:
            pass
        pop.deiconify()
        pop.lift()
        pop.focus_force()
        self.draw()

    def _check_focus(self):
        try:
            if self._pop is not None and self._pop.focus_displayof() is None:
                self.close()
        except tk.TclError:
            self._pop = None

    def close(self):
        import time
        pop, self._pop = self._pop, None
        self._closed_at = time.time()
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass
        try:
            self.draw()
        except tk.TclError:
            pass

    def destroy(self):
        if self._pop is not None:
            try:
                self._pop.destroy()
            except tk.TclError:
                pass
            self._pop = None
        super().destroy()


# ---------------------------------------------------------------- buttons
class Button(_Cv):
    """Rounded button: primary (accent fill), secondary (field look) or ghost icon button."""

    def __init__(self, parent, C, text, command, font, primary=False, small=False, icon=None, bg=None, minw=0):
        self.C, self.cmd, self.text, self.icon = C, command, text, icon
        self.primary, self.small = primary, small
        fo = font_obj(parent, font)
        self.fo, self.font = fo, font
        self.h = px(26 if small else 32)
        if icon and not text:
            self.w = self.h
        else:
            self.w = max(px(minw), fo.measure(text) + px(20 if small else 32))
        super().__init__(parent, bg=bg, width=self.w, height=self.h, takefocus=1, cursor="hand2")
        self.state, self.hot, self.down, self.focused = "normal", False, False, False
        self.bind("<Enter>", lambda e: self._st("hot", True))
        self.bind("<Leave>", lambda e: (self._st("hot", False), self._st("down", False)))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self._st("focused", True))
        self.bind("<FocusOut>", lambda e: self._st("focused", False))
        for k in ("<space>", "<Return>"):
            self.bind(k, lambda e: self.invoke())
        self.draw()

    def _st(self, a, v):
        if getattr(self, a) != v:
            setattr(self, a, v)
            self.draw()

    def _press(self, e):
        if self.state == "normal":
            self.focus_set()
            self._st("down", True)

    def _release(self, e):
        was = self.down
        self._st("down", False)
        if was and self.state == "normal" and 0 <= e.x < self.w and 0 <= e.y < self.h:
            self.invoke()

    def invoke(self):
        if self.state == "normal" and self.cmd:
            self.cmd()

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self.state = kw.pop("state")
            tk.Canvas.configure(self, cursor="hand2" if self.state == "normal" else "arrow")
        if "text" in kw:
            self.text = kw.pop("text")
        if "command" in kw:
            self.cmd = kw.pop("command")
        self.draw()
        if cnf or kw:
            return super().configure(cnf, **kw)
    config = configure

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        pbg = _bg_of(self)
        dis = self.state != "normal"
        if self.primary:
            fill = C["ui_hover"] if self.hot else C["ui"]
            if self.down:
                fill = mix(C["ui"], C["bg"], 0.25)
            fg, oc = C["on_ui"], fill
        elif self.icon and not self.text:                    # ghost
            fill = mix(pbg, C["fg"], 0.14 if self.down else 0.09) if self.hot else pbg
            fg, oc = C["dim"] if not self.hot else C["fg"], fill
        else:
            fill = mix(C["track"], C["fg"], 0.14 if self.down else 0.08) if self.hot else C["track"]
            fg, oc = C["fg"], mix(C["border"], C["dim"], 0.6) if self.hot else C["border"]
        if dis:
            fill, oc, fg = mix(fill, pbg, 0.55), mix(oc, pbg, 0.55), C["faint"]
        if self.focused and not dis:
            oc = C["ui"] if not self.primary else C["fg"]
        ow = 2 if (self.focused and not dis) else 1
        self.put_rr(0, 0, self.w, self.h, px(8 if not self.small else 7), fill, oc if oc != fill else None, ow)
        if self.icon:
            im = glyph(self.icon, px(14 if self.small else 16), fg)
            if im is not None:
                self.create_image(self.w / 2, self.h / 2, image=self.keep(im))
            else:
                self.create_text(self.w / 2, self.h / 2, text={"up": "▲", "down": "▼", "x": "✕"}.get(self.icon, "•"),
                                 fill=fg, font=self.font)
        if self.text:
            self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=self.font)


# ---------------------------------------------------------------- toggles
class Switch(_Cv):
    def __init__(self, parent, C, var, command=None, bg=None, w=40, h=22):
        self.C, self.var, self.cmd = C, var, command
        self.w, self.h = px(w), px(h)
        super().__init__(parent, bg=bg, width=self.w, height=self.h, takefocus=1, cursor="hand2")
        self.q = 1.0 if var.get() else 0.0
        self.hot = self.focused = False
        self._anim = None
        self.bind("<Button-1>", lambda e: (self.focus_set(), self.toggle()))
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<FocusIn>", lambda e: self._st("focused", True))
        self.bind("<FocusOut>", lambda e: self._st("focused", False))
        self.bind("<Enter>", lambda e: self._st("hot", True))
        self.bind("<Leave>", lambda e: self._st("hot", False))
        self._tr = var.trace_add("write", lambda *a: self._sync())
        self.bind("<Destroy>", lambda e: self._untrace(), add="+")
        self.draw()

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._tr)
        except (tk.TclError, ValueError):
            pass

    def _st(self, a, v):
        setattr(self, a, v)
        self.draw()

    def toggle(self, e=None):
        self.var.set(not self.var.get())
        if self.cmd:
            self.cmd()

    def _sync(self):
        try:
            tgt = 1.0 if self.var.get() else 0.0
            if self._anim:
                self.after_cancel(self._anim)
            self._step(tgt, 6)
        except tk.TclError:
            pass

    def _step(self, tgt, n):
        try:
            self.q = tgt if n <= 1 else self.q + (tgt - self.q) / n
            self.draw()
            self._anim = self.after(16, lambda: self._step(tgt, n - 1)) if n > 1 else None
        except tk.TclError:
            pass

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        white = "#ffffff" if themes.contrast("#ffffff", C["ui"]) >= 2.2 else C["on_ui"]
        ring = C["ui"] if self.focused and self.q < 0.5 else (mix(C["border"], C["dim"], 0.6) if self.hot and self.q < 0.5
                                                                else None)
        im = switch_img(self.q, self.w, self.h, C["ui"], C["track"], C["border"], white, C["dim"], ring)
        if im is not None:
            self.create_image(0, 0, image=self.keep(im), anchor="nw")
        else:
            on = self.q > 0.5
            self.create_rectangle(1, 3, self.w - 1, self.h - 3, fill=C["ui"] if on else C["track"], outline=C["border"])
            kx = self.w - self.h / 2 if on else self.h / 2
            self.create_oval(kx - 6, self.h / 2 - 6, kx + 6, self.h / 2 + 6, fill=white if on else C["dim"], outline="")


class Check(tk.Frame):
    """Switch with a label on its right (drop-in for the old check box)."""

    def __init__(self, parent, text, var, C, font, bg=None, command=None, help=None, small=False):
        bg = bg or _bg_of(parent)
        super().__init__(parent, bg=bg)
        self.var = var
        self.sw = Switch(self, C, var, command, bg=bg, w=34 if small else 40, h=18 if small else 22)
        self.sw.pack(side="left")
        self.lbl = None
        if text:
            lbl = self.lbl = tk.Label(self, text=text, bg=bg, fg=C["fg"], font=font, cursor="hand2", anchor="w",
                                      justify="left")
            lbl.pack(side="left", padx=(8, 0))
            lbl.bind("<Button-1>", lambda e: self.sw.toggle())
        if help:
            import helptexts
            helptexts.info_icon(self, help, bg=bg).pack(side="left", padx=(8, 0))

    def toggle(self, e=None):
        self.sw.toggle()


class Segmented(_Cv):
    def __init__(self, parent, C, options, index, command, font, bg=None):
        self.C, self.opts, self.index, self.cmd, self.font = C, list(options), index, command, font
        fo = font_obj(parent, font)
        self.sw = max(fo.measure(o) for o in self.opts) + px(30)
        self.h = px(32)
        super().__init__(parent, bg=bg, width=self.sw * len(self.opts) + px(6), height=self.h, takefocus=1,
                         cursor="hand2")
        self.hot = -1
        self.focused = False
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", lambda e: self._hot(self._at(e)))
        self.bind("<Leave>", lambda e: self._hot(-1))
        self.bind("<FocusIn>", lambda e: (setattr(self, "focused", True), self.draw()))
        self.bind("<FocusOut>", lambda e: (setattr(self, "focused", False), self.draw()))
        self.bind("<Left>", lambda e: self.select(self.index - 1))
        self.bind("<Right>", lambda e: self.select(self.index + 1))
        self.draw()

    def _at(self, e):
        return max(0, min(len(self.opts) - 1, int((e.x - px(3)) // self.sw)))

    def _hot(self, i):
        if i != self.hot:
            self.hot = i
            self.draw()

    def _click(self, e):
        self.focus_set()
        self.select(self._at(e))

    def select(self, i):
        i = max(0, min(len(self.opts) - 1, i))
        if i != self.index:
            self.index = i
            self.draw()
            if self.cmd:
                self.cmd(i)

    def set(self, i):
        self.index = i
        self.draw()

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        W = self.sw * len(self.opts) + px(6)
        self.put_rr(0, 0, W, self.h, px(9), C["track"], C["ui"] if self.focused else C["border"],
                    2 if self.focused else 1)
        for i, o in enumerate(self.opts):
            x = px(3) + i * self.sw
            on = i == self.index
            if on:
                self.put_rr(x, px(3), self.sw, self.h - px(6), px(7), C["ui"], tags="seg")
            elif i == self.hot:
                self.put_rr(x, px(3), self.sw, self.h - px(6), px(7), mix(C["track"], C["fg"], 0.10), tags="seg")
            self.create_text(x + self.sw / 2, self.h / 2, text=o, font=self.font,
                             fill=C["on_ui"] if on else (C["fg"] if i == self.hot else C["dim"]))


class Slider(_Cv):
    def __init__(self, parent, C, lo, hi, res, length, command, bg=None):
        self.C, self.lo, self.hi, self.res, self.cmd = C, lo, hi, res, command
        self.w, self.h = px(length), px(24)
        super().__init__(parent, bg=bg, width=self.w, height=self.h, takefocus=1, cursor="hand2")
        self.v = lo
        self.drag = self.hot = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", False) or self.draw())
        self.bind("<Enter>", lambda e: (setattr(self, "hot", True), self.draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "hot", False), self.draw()))
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())
        self.bind("<Left>", lambda e: self._user(self.v - self.res))
        self.bind("<Right>", lambda e: self._user(self.v + self.res))
        self.draw()

    def set(self, v):
        self.v = min(self.hi, max(self.lo, float(v)))
        self.draw()

    def get(self):
        return self.v

    def _user(self, v):
        v = min(self.hi, max(self.lo, round((v - self.lo) / self.res) * self.res + self.lo))
        if abs(v - self.v) > 1e-9:
            self.v = v
            self.draw()
            if self.cmd:
                self.cmd(v)

    def _press(self, e):
        self.focus_set()
        self.drag = True
        self._move(e)

    def _move(self, e):
        m = px(10)
        f = min(1.0, max(0.0, (e.x - m) / max(1, self.w - 2 * m)))
        self._user(self.lo + f * (self.hi - self.lo))

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        m, th = px(10), px(6)
        f = (self.v - self.lo) / max(1e-9, self.hi - self.lo)
        x = m + f * (self.w - 2 * m)
        y = (self.h - th) / 2
        self.put_rr(m - px(4), y, self.w - 2 * m + px(8), th, th / 2, C["track"], C["border"])
        if x - m + px(8) > th:
            self.put_rr(m - px(4), y, x - m + px(4) + px(2), th, th / 2, C["ui"], tags="fill")
        r = px(9 if (self.hot or self.drag) else 8)
        if gfx.AVAILABLE:
            d = 2 * r + 2

            def paint(dr, k):
                gfx._disc(dr, k, d / 2, d / 2, r, C["ui"])
                gfx._disc(dr, k, d / 2, d / 2, r - 2.2, "#ffffff")
            im = _cached(("knob", d, r, C["ui"]), d, d, paint)
            self.create_image(x, self.h / 2, image=self.keep(im))
        else:
            self.create_oval(x - r, self.h / 2 - r, x + r, self.h / 2 + r, fill="#ffffff", outline=C["ui"], width=2)


# ---------------------------------------------------------------- scrolling
class Scrollbar(_Cv):
    """Slim rounded scrollbar (use like ttk.Scrollbar: command=widget.yview, widget.configure(yscrollcommand=sb.set))."""

    def __init__(self, parent, C, command, bg=None, width=10):
        self.C, self.cmd = C, command
        self.bw = px(width)
        super().__init__(parent, bg=bg, width=self.bw)
        self.lo, self.hi = 0.0, 1.0
        self.hot = self.drag = False
        self._grab = 0.0
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: (setattr(self, "drag", False), self.draw()))
        self.bind("<Enter>", lambda e: (setattr(self, "hot", True), self.draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "hot", False), self.draw()))

    def set(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self.draw()

    def _thumb(self):
        H = max(1, self.winfo_height())
        y0 = self.lo * H
        y1 = max(y0 + px(28), self.hi * H)
        return y0, min(H, y1), H

    def _press(self, e):
        y0, y1, H = self._thumb()
        if y0 <= e.y <= y1:
            self.drag, self._grab = True, e.y - y0
            self.draw()
        else:
            self.cmd("scroll", -1 if e.y < y0 else 1, "pages")

    def _move(self, e):
        if self.drag:
            y0, y1, H = self._thumb()
            self.cmd("moveto", max(0.0, (e.y - self._grab) / H))

    def draw(self):
        self.delete("all")
        self._refs = []
        if self.lo <= 0.0 and self.hi >= 1.0:
            return
        y0, y1, H = self._thumb()
        C = self.C
        col = C["dim"] if self.drag else (mix(C["faint"], C["dim"], 0.5) if self.hot else C["faint"])
        w = px(6)
        self.put_rr((self.bw - w) / 2, y0 + 1, w, max(px(10), y1 - y0 - 2), w / 2, col, tags="thumb")


class ScrollFrame(tk.Frame):
    """Vertically scrolling container; put widgets into .inner."""

    def __init__(self, parent, bg, C=None):
        super().__init__(parent, bg=bg)
        self.cap, self.reserve = None, 0      # max natural height counted for the window size; fixed space above
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = Scrollbar(self, C or themes.palette(), self.cv.yview, bg=bg)
        self.cv.configure(yscrollcommand=self.sb.set)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.wid = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self.wid, width=e.width))
        self.sb.pack(side="right", fill="y", padx=(4, 0))
        self.cv.pack(side="left", fill="both", expand=True)

    def req_height(self):
        self.inner.update_idletasks()
        return self.inner.winfo_reqheight()

    def req_width(self):
        self.inner.update_idletasks()
        return self.inner.winfo_reqwidth()

    def scroll(self, delta):
        lo, hi = self.cv.yview()
        if lo <= 0.0 and hi >= 1.0:
            return
        self.cv.yview_scroll(int(-delta / 120 * 3) or (-1 if delta > 0 else 1), "units")


# ---------------------------------------------------------------- cards
def autowrap(lbl, minw=120):
    """Label that re-wraps to the width it is given (start with a modest wraplength so it never forces a huge width)."""
    lbl.config(wraplength=360)
    lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(minw, e.width - 2)) if abs(
        int(float(lbl.cget("wraplength"))) - (e.width - 2)) > 3 else None)
    return lbl


class Card(_Cv):
    """Rounded section card with an optional accent stripe and a title row. Fill .body (grid, 2 columns) or use
    row()/full() of the SettingsWindow helpers."""

    def __init__(self, parent, C, title=None, accent=None, icon=None, font_head=None, bg=None, radius=None,
                 icon_kind=None, fill=None, pad=None):
        self.C, self.accent = C, accent
        self.fillc = fill or C["card"]
        super().__init__(parent, bg=bg, height=px(40), width=px(200))
        r = themes.current().radius if radius is None else radius
        self.r = px(max(8, min(14, r)))
        self.pl, self.pr, self.pt, self.pb = pad or (px(22 if accent else 18), px(18), px(14), px(14))
        self.inner = tk.Frame(self, bg=self.fillc)
        self.create_window(self.pl, self.pt, window=self.inner, anchor="nw", tags="inner")
        self.hdr = None
        if title:
            self.hdr = tk.Frame(self.inner, bg=self.fillc)
            self.hdr.pack(fill="x", pady=(0, px(6)))
            if icon_kind and gfx.AVAILABLE and accent:
                bub = mix(self.fillc, accent, 0.16)
                im = gfx.icon(icon_kind, 6.0, accent, bub, bub)
                lb = tk.Label(self.hdr, image=im, bg=self.fillc, bd=0)
                lb.image = im
                lb.pack(side="left", padx=(0, px(10)))
            tk.Label(self.hdr, text=title, bg=self.fillc, fg=C["fg"], font=font_head or ("Segoe UI", 11, "bold"),
                     anchor="w").pack(side="left")
        self.body = tk.Frame(self.inner, bg=self.fillc)
        self.body.pack(fill="x")
        self.body.grid_columnconfigure(0, weight=1)
        self._n = 0
        self._cw = self._ch = 0
        self.inner.bind("<Configure>", self._inner_cfg)
        self.bind("<Configure>", self._cfg)

    def slot(self, divider=True):
        """Next free grid row of the body (3 rows per item: content, extra content, divider)."""
        n = self._n
        self._n += 1
        if n and divider:
            tk.Frame(self.body, bg=mix(self.fillc, self.C["border"], 0.7), height=1).grid(
                row=3 * n - 1, column=0, columnspan=2, sticky="ew")
        return 3 * n

    def _inner_cfg(self, e=None):
        h = self.inner.winfo_reqheight() + self.pt + self.pb
        w = self.inner.winfo_reqwidth() + self.pl + self.pr
        tk.Canvas.configure(self, height=h, width=w)

    def _cfg(self, e):
        self._cw, self._ch = e.width, e.height
        self.itemconfigure("inner", width=max(10, e.width - self.pl - self.pr))
        self.draw()

    def draw(self):
        self.delete("bg")
        self._refs = []
        w, h = self._cw, self._ch
        if w < 4 or h < 4:
            return
        C = self.C
        if gfx.AVAILABLE:
            r = self.r
            key = ("card", w, h, r, self.fillc, C["border"], self.accent)

            def paint(d, k):
                gfx.rrect(d, k, 0, 0, w, h, r, fill=self.fillc, outline=C["border"], w=1)
                if self.accent:
                    d.rectangle([0, r * k, 3 * k - 1, (h - r) * k], fill=self.accent)
            im = _cached(key, w, h, paint)
            self.create_image(0, 0, image=self.keep(im), anchor="nw", tags="bg")
        else:
            _poly_rr(self, 0, 0, w - 1, h - 1, self.r, self.fillc, C["border"], "bg")
            if self.accent:
                self.create_line(2, self.r, 2, h - self.r, fill=self.accent, width=3, tags="bg")
        self.tag_lower("bg")


class Swatch(_Cv):
    """Rounded colour chip; `own` (customised value) draws an accent ring."""

    def __init__(self, parent, C, color, command=None, bg=None, w=44, h=24):
        self.C, self.color, self.own, self.cmd = C, color, False, command
        self.w, self.h = px(w), px(h)
        super().__init__(parent, bg=bg, width=self.w, height=self.h, cursor="hand2")
        self.hot = False
        self.bind("<Button-1>", lambda e: self.cmd() if self.cmd else None)
        self.bind("<Enter>", lambda e: (setattr(self, "hot", True), self.draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "hot", False), self.draw()))
        self.draw()

    def set(self, color, own=False):
        self.color, self.own = color, own
        self.draw()

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        oc = C["ui"] if self.own else (C["dim"] if self.hot else C["border"])
        ow = 2 if self.own else 1
        self.put_rr(0, 0, self.w, self.h, px(7), self.color, oc, ow)


# ---------------------------------------------------------------- sidebar navigation
class Nav(_Cv):
    """Left sidebar: brand row + one row per page (icon bubble + label), accent highlight, hover state."""

    def __init__(self, parent, C, items, command, font, font_head, title, bg=None):
        self.C, self.items, self.cmd, self.font, self.fhead, self.title = C, items, command, font, font_head, title
        fo = font_obj(parent, (font[0], font[1], "bold"))
        self.rh, self.top = px(44), px(66)
        self.w = max(px(176), max(fo.measure(it[0]) for it in items) + px(92))
        super().__init__(parent, bg=bg, width=self.w, takefocus=1)
        self.active, self.hot = 0, -1
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", lambda e: self._hot(self._at(e)))
        self.bind("<Leave>", lambda e: self._hot(-1))
        self.bind("<Button-1>", self._click)
        self.bind("<Up>", lambda e: self.cmd(max(0, self.active - 1)))
        self.bind("<Down>", lambda e: self.cmd(min(len(self.items) - 1, self.active + 1)))
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())

    def _at(self, e):
        i = (e.y - self.top) // self.rh
        return int(i) if 0 <= i < len(self.items) and px(8) <= e.x <= self.w - px(8) else -1

    def _hot(self, i):
        if i != self.hot:
            self.hot = i
            self.configure(cursor="hand2" if i >= 0 else "")
            self.draw()

    def _click(self, e):
        self.focus_set()
        i = self._at(e)
        if i >= 0:
            self.cmd(i)

    def set_active(self, i):
        self.active = i
        self.draw()

    def draw(self):
        C = self.C
        self.delete("all")
        self._refs = []
        # brand
        d = px(34)
        im = gfx.button("gear", d / 2, 6.5, C["ui"], mix(C["bg"], C["ui"], 0.16)) if gfx.AVAILABLE else None
        if im is not None:
            self.create_image(px(22), px(34), image=self.keep(im), anchor="w")
        self.create_text(px(22) + d + px(10), px(34), text=self.title, anchor="w", font=self.fhead, fill=C["fg"])
        pad = px(10)
        for i, (label, kind, acc) in enumerate(self.items):
            y = self.top + i * self.rh
            on, hot = i == self.active, i == self.hot
            h = self.rh - px(4)
            if on:
                self.put_rr(pad, y, self.w - 2 * pad, h, px(10), mix(C["bg"], acc, 0.15), tags="row")
                self.put_rr(pad, y + h * 0.22, px(3), h * 0.56, px(1.5), acc, tags="bar")
                if self.focus_get() is self:
                    self.put_rr(pad, y, self.w - 2 * pad, h, px(10), None, acc, 1, tags="row")
            elif hot:
                self.put_rr(pad, y, self.w - 2 * pad, h, px(10), C["card"], tags="row")
            bub = px(30)
            cx, cy = pad + px(24), y + h / 2
            if gfx.AVAILABLE:
                bg_c = mix(C["card"] if not on else mix(C["bg"], acc, 0.15), acc, 0.16 if not on else 0.20)
                bi = _cached(("navbub", bub, bg_c), bub, bub, lambda dr, k: gfx._disc(dr, k, bub / 2, bub / 2, bub / 2, bg_c))
                self.create_image(cx, cy, image=self.keep(bi))
                ic = nav_icon(kind, px(18), acc)
                self.create_image(cx, cy, image=self.keep(ic))
            else:
                self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=acc, outline="")
            self.create_text(pad + px(48), cy, text=label, anchor="w", font=(self.font[0], self.font[1], "bold" if on else "normal"),
                             fill=C["fg"] if (on or hot) else C["name"])
