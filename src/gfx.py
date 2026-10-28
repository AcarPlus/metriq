"""Anti-aliased widgets for the tkinter canvas (optional: needs Pillow, otherwise AVAILABLE is False and
panel.py keeps drawing with plain canvas primitives).

Every shape is rendered at 4x with Pillow, reduced with LANCZOS and handed to Tk as a PhotoImage with an alpha
channel. Images are cached by (kind, size, colours); begin() is called whenever the canvas is rebuilt, so only
the images used by the current build (and the previous one, for cheap rebuilds) stay alive."""
import math

try:
    from PIL import Image, ImageDraw, ImageTk
    AVAILABLE = True
except Exception:          # Pillow missing or broken: the caller falls back to canvas drawing
    AVAILABLE = False

SS = 4
_cache = {}
_old = {}


def begin():
    """The canvas is being rebuilt: images of the build before the previous one can go."""
    global _cache, _old
    _old, _cache = _cache, {}


def _get(key, make):
    im = _cache.get(key)
    if im is None:
        im = _old.get(key)
        if im is None:
            im = make()
        _cache[key] = im
    return im


def _render(w, h, paint):
    """PhotoImage of w x h pixels; paint(draw, k) draws in k-times magnified pixel coordinates."""
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    paint(ImageDraw.Draw(im), SS)
    return ImageTk.PhotoImage(im.reduce(SS))          # box filter of the SSxSS block = same AA, several times faster than LANCZOS


def _px(v):
    return max(1, int(math.ceil(v - 1e-6)))


def _disc(d, k, cx, cy, r, fill):
    d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=fill)


def _line(d, k, x0, y0, x1, y1, w, col, round_caps=False):
    d.line([x0 * k, y0 * k, x1 * k, y1 * k], fill=col, width=max(1, int(round(w * k))))
    if round_caps:
        _disc(d, k, x0, y0, w / 2, col)
        _disc(d, k, x1, y1, w / 2, col)


def _arc(d, k, cx, cy, r, a0, a1, w, col):
    """Arc whose centre line has radius r (PIL draws the width inwards); angles clockwise from 3 o'clock."""
    ro = r + w / 2
    d.arc([(cx - ro) * k, (cy - ro) * k, (cx + ro) * k, (cy + ro) * k], a0, a1, fill=col, width=max(1, int(round(w * k))))


def _poly(d, k, pts, col):
    d.polygon([v * k for p in pts for v in p], fill=col)


# ---------------------------------------------------------------- icons (mirror panel.draw_icon)
def _paint_icon(d, k, kind, cx, cy, s, col, bg):
    lw = max(2, s / 5)
    if kind == "gear":
        for n in range(8):
            a = math.radians(n * 45 + 22.5)
            ux, uy = math.cos(a), math.sin(a)
            px, py = -uy, ux
            r0, r1, w0, w1 = 0.55 * s, 1.0 * s, 0.30 * s, 0.19 * s
            _poly(d, k, [(cx + ux * r0 + px * w0, cy + uy * r0 + py * w0), (cx + ux * r1 + px * w1, cy + uy * r1 + py * w1),
                         (cx + ux * r1 - px * w1, cy + uy * r1 - py * w1), (cx + ux * r0 - px * w0, cy + uy * r0 - py * w0)],
                  col)
        _disc(d, k, cx, cy, s * 0.74, col)
        _disc(d, k, cx, cy, s * 0.30, bg)
    elif kind == "chart":
        w_ = max(2, s * 0.2)
        _line(d, k, cx - s * 0.85, cy - s * 0.85, cx - s * 0.85, cy + s * 0.85, w_, col)
        _line(d, k, cx - s * 0.85, cy + s * 0.85, cx + s * 0.95, cy + s * 0.85, w_, col)
        pts = [(cx - s * 0.5, cy + s * 0.35), (cx - s * 0.1, cy - s * 0.1), (cx + s * 0.25, cy + s * 0.2),
               (cx + s * 0.8, cy - s * 0.6)]
        for a, b in zip(pts, pts[1:]):
            _line(d, k, a[0], a[1], b[0], b[1], w_, col, True)
    elif kind == "reset":
        _arc(d, k, cx, cy, s * 0.78, 0, 290, max(2, s * 0.22), col)
        a = math.radians(70)
        tx, ty = cx + s * 0.78 * math.cos(a), cy - s * 0.78 * math.sin(a)
        _poly(d, k, [(tx - s * 0.42, ty - s * 0.30), (tx + s * 0.18, ty - s * 0.02), (tx - s * 0.30, ty + s * 0.42)], col)
    elif kind == "gauge":
        _arc(d, k, cx, cy, s, 150, 30, lw, col)
        ang = math.radians(50)
        _line(d, k, cx, cy, cx + s * 0.7 * math.cos(ang), cy - s * 0.7 * math.sin(ang), lw, col, True)
        _disc(d, k, cx, cy, lw, col)
    elif kind == "bolt":
        pts = [(0.2, -1), (-0.6, 0.15), (-0.05, 0.15), (-0.25, 1), (0.6, -0.2), (0.05, -0.2)]
        _poly(d, k, [(cx + x * s, cy + y * s) for x, y in pts], col)
    elif kind == "temp":
        _line(d, k, cx - s * 0.15, cy - s * 0.75, cx - s * 0.15, cy + s * 0.3, s * 0.5, col, True)
        d.ellipse([(cx - s * 0.57) * k, (cy + s * 0.08) * k, (cx + s * 0.27) * k, (cy + s * 0.92) * k], fill=col)
        for v in (-0.6, -0.3, 0.0):
            _line(d, k, cx + s * 0.3, cy + v * s, cx + s * 0.7, cy + v * s, max(1, lw * 0.6), col)
    elif kind == "fan":
        for n in range(3):
            a0 = math.radians(n * 120 + 90)
            pts = [(cx, cy)]
            for i in range(1, 25):
                f = i / 24
                ang = a0 - f * math.radians(80)
                rr = s * (0.35 + 0.65 * math.sin(f * math.pi) ** 0.5)
                pts.append((cx + rr * math.cos(ang), cy - rr * math.sin(ang)))
            _poly(d, k, pts, col)
        ow = max(1, lw * 0.7)
        _disc(d, k, cx, cy, s * 0.24 + ow / 2, col)
        _disc(d, k, cx, cy, s * 0.24 - ow / 2, bg)
    elif kind == "net":
        for dx, up in ((-0.42, False), (0.42, True)):
            xx = cx + dx * s
            y0, y1 = (cy + s * 0.9, cy - s * 0.9) if up else (cy - s * 0.9, cy + s * 0.9)
            sg = -1 if up else 1                      # direction of the arrow tip along y
            base = y1 - sg * s * 0.55
            _line(d, k, xx, y0, xx, base, lw, col)
            _poly(d, k, [(xx, y1), (xx - s * 0.38, base), (xx + s * 0.38, base)], col)
    elif kind == "fps":
        o = lw / 2
        d.rounded_rectangle([(cx - s - o) * k, (cy - s * 0.75 - o) * k, (cx + s + o) * k, (cy + s * 0.5 + o) * k],
                            radius=(s * 0.25 + o) * k, outline=col, width=max(1, int(round(lw * k))))
        _poly(d, k, [(cx - s * 0.22, cy - s * 0.42), (cx - s * 0.22, cy + s * 0.17), (cx + s * 0.33, cy - s * 0.12)], col)
        _line(d, k, cx - s * 0.45, cy + s * 0.92, cx + s * 0.45, cy + s * 0.92, lw, col, True)


def icon(kind, s, col, bg, bubble=None):
    """Section icon with its round bubble behind it (diameter 3.2 s), or a bare icon when bubble is None."""
    dia = _px(s * 3.2) if bubble else _px(s * 2.4)

    def make():
        def paint(d, k):
            c = dia / 2
            if bubble:
                _disc(d, k, c, c, dia / 2, bubble)
            _paint_icon(d, k, kind, c, c, s, col, bubble or bg)
        return _render(dia, dia, paint)
    return _get(("icon", kind, round(s, 2), col, bg, bubble), make)


def button(kind, hit, s, col, bgc):
    """Round toolbar button (gear / reset): disc of radius hit in bgc with the icon in col."""
    dia = _px(hit * 2)

    def make():
        def paint(d, k):
            c = dia / 2
            _disc(d, k, c, c, min(hit, dia / 2), bgc)
            _paint_icon(d, k, kind, c, c, s, col, bgc)
        return _render(dia, dia, paint)
    return _get(("btn", kind, round(hit, 2), round(s, 2), col, bgc), make)


def card(w, h, r, fill, outline, accent=None):
    """Rounded card w x h with a 1 px border and, optionally, a 3 px accent stripe on the left edge."""
    w, h = _px(w), _px(h)

    def make():
        def paint(d, k):
            d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=r * k, fill=fill, outline=outline, width=k)
            if accent:
                d.rectangle([0, r * k, 3 * k - 1, (h - r) * k], fill=accent)
        return _render(w, h, paint)
    return _get(("card", w, h, r, fill, outline, accent), make)


def pill(w, h, fill, dot=None, dot_x=0.0, dot_r=0.0):
    """Fully rounded pill; dot (colour) is a disc of radius dot_r with its centre dot_x from the left edge."""
    w, h = _px(w), _px(h)

    def make():
        def paint(d, k):
            d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=fill)
            if dot:
                _disc(d, k, dot_x, h / 2, dot_r, dot)
        return _render(w, h, paint)
    return _get(("pill", w, h, fill, dot, round(dot_x, 2), round(dot_r, 2)), make)


def track(w, h, fill, outline):
    """Slide-switch track (pill with a 1 px border)."""
    w, h = _px(w), _px(h)

    def make():
        def paint(d, k):
            d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=fill, outline=outline, width=k)
        return _render(w, h, paint)
    return _get(("track", w, h, fill, outline), make)


def knob(r, col):
    dia = _px(r * 2)

    def make():
        return _render(dia, dia, lambda d, k: _disc(d, k, dia / 2, dia / 2, r, col))
    return _get(("knob", round(r, 2), col), make)


def info(dia, col):
    """Small "i" in a ring (help marker), transparent background, dia x dia pixels."""
    dia = max(10, int(dia))

    def make():
        def paint(d, k):
            lw = max(1.1, dia * 0.085)
            d.ellipse([lw / 2 * k, lw / 2 * k, (dia - lw / 2) * k - 1, (dia - lw / 2) * k - 1], outline=col,
                      width=max(1, int(round(lw * k))))
            c = dia / 2
            _disc(d, k, c, dia * 0.30, dia * 0.075, col)
            _line(d, k, c, dia * 0.45, c, dia * 0.72, max(1.1, dia * 0.12), col)
        return _render(dia, dia, paint)
    return _get(("info", dia, col), make)


def rrect(d, k, x0, y0, x1, y1, r, fill=None, outline=None, w=1.0):
    """Rounded rectangle in the k-times magnified drawing space of _render (coordinates given unmagnified)."""
    d.rounded_rectangle([x0 * k, y0 * k, x1 * k - 1, y1 * k - 1], radius=max(0.0, r) * k, fill=fill,
                        outline=outline, width=max(1, int(round(w * k))) if outline else 0)
