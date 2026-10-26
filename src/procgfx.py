"""Small anti-aliased pictures for the process window and the panel's info icon (Pillow via gfx, optional).

Two caches: ``_S`` for pictures of a fixed size (icons, arrows, pills: never released, a handful of colours),
and a per-redraw cache for pictures whose size depends on the window (cards, row highlights): ``begin()`` is called
before the canvas is redrawn and keeps the pictures of the current and the previous redraw alive, like gfx.begin().
Every function returns None when Pillow is missing: the caller then draws with canvas primitives."""
import math

import gfx

AVAILABLE = gfx.AVAILABLE
if AVAILABLE:
    from PIL import Image, ImageDraw, ImageTk

_S = {}
_cur, _old = {}, {}


def begin():
    global _cur, _old
    _old, _cur = _cur, {}


def _fixed(key, make):
    im = _S.get(key)
    if im is None:
        im = _S[key] = make()
    return im


def _frame(key, make):
    im = _cur.get(key)
    if im is None:
        im = _old.get(key)
        if im is None:
            im = make()
        _cur[key] = im
    return im


# ---------------------------------------------------------------- panel: the (i) icon
def info(d, fill, fg, outline=None):
    """Round info icon of diameter d: filled disc, 'i' in fg; outline (colour) draws a 1 px ring."""
    if not AVAILABLE:
        return None
    d = max(10, int(round(d)))

    def make():
        def paint(dr, k):
            c = d / 2
            gfx._disc(dr, k, c, c, c - 0.2, outline or fill)
            if outline:
                gfx._disc(dr, k, c, c, c - 0.2 - max(1.0, d / 14), fill)
            r = d * 0.075
            gfx._disc(dr, k, c, d * 0.30, r + 0.15, fg)
            w = max(1.6, d * 0.14)
            dr.rounded_rectangle([(c - w / 2) * k, d * 0.44 * k, (c + w / 2) * k, d * 0.76 * k], radius=w / 2 * k,
                                 fill=fg)
        return gfx._render(d, d, paint)
    return _fixed(("info", d, fill, fg, outline), make)


# ---------------------------------------------------------------- list pictures
def cat_icon(kind, s, col, bubble):
    """Round bubble (diameter 2.9 s) with a small category picture: apps | background | windows."""
    if not AVAILABLE:
        return None
    dia = max(8, int(round(s * 2.9)))

    def make():
        def paint(d, k):
            c = dia / 2
            gfx._disc(d, k, c, c, dia / 2, bubble)
            u = s * 0.62
            if kind == "apps":                                   # 2 x 2 rounded tiles
                g = u * 0.16
                for dx in (-1, 1):
                    for dy in (-1, 1):
                        x0, x1 = (c + g, c + u) if dx > 0 else (c - u, c - g)
                        y0, y1 = (c + g, c + u) if dy > 0 else (c - u, c - g)
                        d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=u * 0.22 * k, fill=col)
            elif kind == "windows":                              # window logo: four panes
                g = u * 0.14
                for dx in (-1, 1):
                    for dy in (-1, 1):
                        x0, x1 = (c + g, c + u) if dx > 0 else (c - u, c - g)
                        y0, y1 = (c + g, c + u) if dy > 0 else (c - u, c - g)
                        d.rectangle([x0 * k, y0 * k, x1 * k, y1 * k], fill=col)
            else:                                                # background: two stacked layers
                lw = max(1.4, u * 0.30)
                gfx._poly(d, k, [(c, c - u * 1.05), (c + u * 1.1, c - u * 0.35), (c, c + u * 0.35),
                                 (c - u * 1.1, c - u * 0.35)], col)
                gfx._line(d, k, c - u * 1.05, c + u * 0.30, c, c + u * 0.95, lw, col, True)
                gfx._line(d, k, c, c + u * 0.95, c + u * 1.05, c + u * 0.30, lw, col, True)
        return gfx._render(dia, dia, paint)
    return _fixed(("cat", kind, round(s, 1), col, bubble), make)


def chevron(opened, size, col):
    """Small disclosure arrow: pointing right (closed) or down (open)."""
    if not AVAILABLE:
        return None
    n = max(8, int(round(size)))

    def make():
        def paint(d, k):
            c, a = n / 2, n * 0.22
            w = max(1.5, n * 0.17)
            pts = [(c - a, c - a * 0.5), (c, c + a * 0.7), (c + a, c - a * 0.5)] if opened else \
                  [(c - a * 0.5, c - a), (c + a * 0.7, c), (c - a * 0.5, c + a)]
            gfx._line(d, k, *pts[0], *pts[1], w, col, True)
            gfx._line(d, k, *pts[1], *pts[2], w, col, True)
        return gfx._render(n, n, paint)
    return _fixed(("chev", opened, n, col), make)


def arrow(desc, size, col):
    """Sort arrow (filled triangle): down for descending, up for ascending."""
    if not AVAILABLE:
        return None
    w, h = max(8, int(round(size))), max(5, int(round(size * 0.6)))

    def make():
        def paint(d, k):
            m = w / 2
            pts = [(m - w * 0.38, h * 0.18), (m + w * 0.38, h * 0.18), (m, h * 0.88)] if desc else \
                  [(m - w * 0.38, h * 0.82), (m + w * 0.38, h * 0.82), (m, h * 0.12)]
            gfx._poly(d, k, pts, col)
        return gfx._render(w, h, paint)
    return _fixed(("arrow", desc, w, h, col), make)


def magnifier(size, col):
    if not AVAILABLE:
        return None
    n = max(12, int(round(size)))

    def make():
        def paint(d, k):
            r, cx = n * 0.30, n * 0.42
            w = max(1.4, n * 0.12)
            gfx._arc(d, k, cx, cx, r, 0, 360, w, col)
            a = math.radians(45)
            gfx._line(d, k, cx + r * math.cos(a) + w * 0.3, cx + r * math.sin(a) + w * 0.3, n * 0.86, n * 0.86, w, col,
                      True)
        return gfx._render(n, n, paint)
    return _fixed(("mag", n, col), make)


def switch(on, w, h, track_on, track_off, outline, knob_on, knob_off):
    """Pill toggle (track + knob in one picture)."""
    if not AVAILABLE:
        return None
    w, h = int(w), int(h)

    def make():
        def paint(d, k):
            fill = track_on if on else track_off
            d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=fill,
                                outline=track_on if on else outline, width=k)
            r = h / 2 - max(2.0, h * 0.17)
            cx = (w - h / 2) if on else h / 2
            gfx._disc(d, k, cx, h / 2, r, knob_on if on else knob_off)
        return gfx._render(w, h, paint)
    return _fixed(("sw", on, w, h, track_on, track_off, outline, knob_on, knob_off), make)


def corner(r, which, fill, outline):
    """Quarter of a rounded card (which: 'tl' 'tr' 'bl' 'br'), r x r px, transparent outside the card."""
    if not AVAILABLE or r < 2:
        return None
    r = int(r)

    def make():
        k = gfx.SS
        im = Image.new("RGBA", (2 * r * k, 2 * r * k), (0, 0, 0, 0))
        ImageDraw.Draw(im).rounded_rectangle([0, 0, 2 * r * k - 1, 2 * r * k - 1], radius=r * k, fill=fill,
                                             outline=outline, width=k)
        box = {"tl": (0, 0), "tr": (r * k, 0), "bl": (0, r * k), "br": (r * k, r * k)}[which]
        im = im.crop((box[0], box[1], box[0] + r * k, box[1] + r * k))
        return ImageTk.PhotoImage(im.resize((r, r), Image.LANCZOS))
    return _frame(("corner", r, which, fill, outline), make)


def rrect(w, h, r, fill):
    """Rounded rectangle, used for row highlights."""
    if not AVAILABLE or w < 4 or h < 4:
        return None
    w, h = int(w), int(h)

    def make():
        def paint(d, k):
            d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=min(r, h / 2) * k, fill=fill)
        return gfx._render(w, h, paint)
    return _frame(("rr", w, h, r, fill), make)
