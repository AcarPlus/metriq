"""Aurora look: flowing colour clouds behind glass cards, glowing ring gauges and bars, and its own motion language.

Everything here is pure Pillow (rendered off the Tk thread's hot path and cached); panel.py places the results on its
canvas. Without Pillow AVAILABLE is False and the panel draws the Aurora palette with its plain shapes.

Motion language (used by panel.py):
    entry    cards rise into place one after another on a damped spring (spring())
    gauges   rings and bars draw themselves from zero and glide to every new value (approach())
    sky      the colour clouds drift slowly at a low frame rate (Sky.FPS); "motion off" keeps one still frame

Cost control: the sky is rendered at 1/SC of the window size and scaled up once per frame (a few ms at 3 fps),
rings / bars are cached by their quantised value (LRU), glass cards are rendered once per canvas build."""
import ctypes
import math
import time
from collections import OrderedDict

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk
    import gfx
    AVAILABLE = gfx.AVAILABLE
except Exception:            # Pillow missing: the panel keeps its plain drawing
    AVAILABLE = False

import themes

# --------------------------------------------------------------------------- palettes of the effects
NIGHT = {
    "top": "#060917", "bottom": "#0d0a24",
    # (colour, strength 0..1, size, centre x, centre y, drift x, drift y, speed x, speed y, phase, aspect, angle)
    "clouds": (("#3de0c8", 0.62, 0.95, 0.20, 0.18, 0.22, 0.10, 0.050, 0.037, 0.0, 1.9, -24),
               ("#7a5cff", 0.70, 1.05, 0.80, 0.42, 0.20, 0.14, 0.041, 0.029, 1.7, 1.5, 18),
               ("#ff5ca8", 0.48, 0.85, 0.30, 0.78, 0.24, 0.10, 0.033, 0.045, 3.1, 1.7, -12),
               ("#2f7bff", 0.40, 0.80, 0.75, 0.95, 0.18, 0.08, 0.047, 0.031, 4.4, 1.3, 30),
               ("#9ff7e6", 0.20, 0.55, 0.55, 0.05, 0.30, 0.05, 0.061, 0.052, 2.2, 3.2, -35)),
    "glass": (16, 20, 50, 122), "edge": (255, 255, 255, 34), "sheen": (255, 255, 255, 30),
    "shadow": (0, 0, 8, 120), "stripe": (255, 255, 255, 7), "sep": (255, 255, 255, 22),
    "track": (255, 255, 255, 20), "ring_track": (255, 255, 255, 26), "glow": 1.0, "bubble": 40,
}
DAY = {
    "top": "#f4f6ff", "bottom": "#eceffa",
    "clouds": (("#3de0c8", 0.42, 0.95, 0.20, 0.18, 0.22, 0.10, 0.050, 0.037, 0.0, 1.9, -24),
               ("#7a5cff", 0.32, 1.05, 0.80, 0.42, 0.20, 0.14, 0.041, 0.029, 1.7, 1.5, 18),
               ("#ff5ca8", 0.30, 0.85, 0.30, 0.78, 0.24, 0.10, 0.033, 0.045, 3.1, 1.7, -12),
               ("#6fb6ff", 0.30, 0.80, 0.75, 0.95, 0.18, 0.08, 0.047, 0.031, 4.4, 1.3, 30),
               ("#ffd6ec", 0.35, 0.55, 0.55, 0.05, 0.30, 0.05, 0.061, 0.052, 2.2, 3.2, -35)),
    "glass": (255, 255, 255, 150), "edge": (255, 255, 255, 235), "sheen": (255, 255, 255, 70),
    "shadow": (40, 30, 110, 46), "stripe": (60, 70, 140, 9), "sep": (40, 50, 120, 26),
    "track": (40, 50, 120, 22), "ring_track": (40, 50, 120, 26), "glow": 0.7, "bubble": 34,
}

MOTION = ("auto", "on", "off")


def fx(style=None):
    """The effect palette of the style when it is an Aurora theme, else None."""
    style = style or themes.current()
    if style.tokens.get("fx") != "aurora":
        return None
    return NIGHT if style.is_dark() else DAY


def norm_motion(v):
    v = str(v or "auto").strip().lower()
    return v if v in MOTION else "auto"


def windows_animations():
    """Windows Settings > Accessibility > Visual effects > Animation effects (True when on or unknown)."""
    try:
        b = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(b), 0):     # SPI_GETCLIENTAREAANIMATION
            return bool(b.value)
    except Exception:
        pass
    return True


def motion_on(setting):
    """Should the panel move? "on" / "off", or "auto" = follow the Windows animation setting."""
    s = norm_motion(setting)
    return windows_animations() if s == "auto" else s == "on"


# --------------------------------------------------------------------------- timing curves
def spring(x, zeta=0.52, periods=1.25):
    """Damped spring from 0 to 1 over x = 0..1 (overshoots ~12 % once, settles by x = 1)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    w = 2 * math.pi * periods
    wd = w * math.sqrt(1 - zeta * zeta)
    e = math.exp(-zeta * w * x)
    v = 1 - e * (math.cos(wd * x) + zeta * w / wd * math.sin(wd * x))
    return v * (1 - x ** 6) + x ** 6          # the last few percent glide exactly onto 1


def approach(cur, target, dt, tau=0.14):
    """Exponential glide of cur towards target (time constant tau seconds); snaps when close."""
    if abs(target - cur) < 0.003:
        return target
    return cur + (target - cur) * (1 - math.exp(-max(0.0, dt) / tau))


# --------------------------------------------------------------------------- helpers
def _rgb(c):
    return themes.rgb(c)


def _rgba(c, a):
    return _rgb(c) + (int(max(0, min(255, a))),)


_LRU_MAX = 640
_lru = OrderedDict()


def _cached(key, make):
    im = _lru.get(key)
    if im is None:
        im = make()
        _lru[key] = im
        if len(_lru) > _LRU_MAX:
            _lru.popitem(last=False)
    else:
        _lru.move_to_end(key)
    return im


def cache_size():
    return len(_lru)


_MASK = None


def _soft_mask():
    """Round soft spot (smoothstep falloff), 128 x 128 'L'."""
    global _MASK
    if _MASK is None:
        n = 128
        m = Image.new("L", (n, n))
        px = m.load()
        for y in range(n):
            for x in range(n):
                d = math.hypot(x - n / 2 + 0.5, y - n / 2 + 0.5) / (n / 2)
                if d < 1:
                    s = 1 - d
                    px[x, y] = int(255 * s * s * (3 - 2 * s))
        _MASK = m
    return _MASK


def _vgrad(w, h, top, bottom):
    col = Image.new("RGB", (1, 256))
    a, b = _rgb(top), _rgb(bottom)
    col.putdata([tuple(round(x + (y - x) * i / 255) for x, y in zip(a, b)) for i in range(256)])
    return col.resize((w, h), Image.BILINEAR)


def _round_mask(w, h, r, ss=3):
    """Anti-aliased rounded-rectangle mask, w x h 'L'."""
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=max(0, r) * ss, fill=255)
    return m.reduce(ss)


# --------------------------------------------------------------------------- the sky
class Sky:
    """Flowing aurora behind everything. frame(w, h, t) -> PIL image (RGB, w x h)."""
    SC = 6            # rendered at 1/SC of the window size, then scaled up (the clouds are soft anyway)
    FPS = 3.0         # background frames per second while motion is on (the clouds move a few px per frame)
    SPEED = 1.0

    def __init__(self, pal):
        self.pal = pal
        self._base = {}

    def small(self, w, h, t):
        sw, sh = max(24, int(w / self.SC)), max(24, int(h / self.SC))
        key = (sw, sh)
        base = self._base.get(key)
        if base is None:
            base = self._base[key] = _vgrad(sw, sh, self.pal["top"], self.pal["bottom"])
        im = base.copy()
        big = max(sw, sh)
        t *= self.SPEED
        for col, k, size, cx, cy, ax, ay, fx_, fy, ph, aspect, ang in self.pal["clouds"]:
            x = (cx + ax * math.sin(t * fx_ + ph)) * sw
            y = (cy + ay * math.cos(t * fy + ph * 1.3)) * sh
            rx = big * size * 0.42 * (1 + 0.08 * math.sin(t * fx_ * 1.7 + ph))
            ry = rx / aspect
            mw, mh = max(2, int(rx * 2)), max(2, int(ry * 2))
            m = _soft_mask().resize((mw, mh), Image.BILINEAR)
            a = ang + 9 * math.sin(t * fy * 1.3 + ph)
            m = m.rotate(a, resample=Image.BILINEAR, expand=True)
            if k < 1:
                m = m.point(lambda v, k=k: int(v * k))
            full = Image.new("L", (sw, sh), 0)
            full.paste(m, (int(x - m.width / 2), int(y - m.height / 2)))
            im = Image.composite(Image.new("RGB", (sw, sh), col), im, full)
        return im

    def frame(self, w, h, t):
        return self.small(w, h, t).resize((max(1, int(w)), max(1, int(h))), Image.BICUBIC)


def sky_photo(sky, w, h, t):
    return ImageTk.PhotoImage(sky.frame(w, h, t))


def thumb(w, h, style, r=8, t=12.0):
    """Small still picture of the sky (theme picker cards, settings preview): PhotoImage with rounded corners."""
    pal = fx(style) or NIGHT

    def make():
        sky = Sky(pal)
        im = sky.frame(w, h, t).convert("RGBA")
        im.putalpha(_round_mask(w, h, r))
        return ImageTk.PhotoImage(im)
    return _cached(("thumb", w, h, r, pal is NIGHT, t), make)


# --------------------------------------------------------------------------- glass card
def card_pad(h):
    """Room around a card image for its shadow (the image is placed this many px up / left of the card)."""
    return max(6, min(18, int(h * 0.04) + 6))


def glass_card(w, h, r, pal, accent=None, glow_at=None, stripes=(), sep=None, tracks=()):
    """Glass card w x h (+ shadow): PhotoImage of (w + 2 pad) x (h + 2 pad); place it at (x0 - pad, y0 - pad).
    glow_at: (x, y, radius) of the accent glow (card coordinates); stripes: [(y0, y1, inset)];
    sep: y of the header line; tracks: [(x0, y0, x1, y1)] bar tracks."""
    w, h = max(4, int(w)), max(4, int(h))
    pad = card_pad(h)
    W, H = w + 2 * pad, h + 2 * pad
    key = ("card", w, h, r, id(pal), accent, glow_at, tuple(stripes), sep, tuple(tracks))

    def make():
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # soft shadow (blurred at half size)
        sh = Image.new("L", (max(1, W // 2), max(1, H // 2)), 0)
        ImageDraw.Draw(sh).rounded_rectangle([pad // 2, pad // 2 + 2, (pad + w) // 2, (pad + h) // 2 + 2],
                                             radius=r / 2, fill=pal["shadow"][3])
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, pad / 3))).resize((W, H), Image.BILINEAR)
        shadow = Image.new("RGBA", (W, H), pal["shadow"][:3] + (0,))
        shadow.putalpha(sh)
        out = Image.alpha_composite(out, shadow)
        mask = _round_mask(w, h, r)
        # body: glass tint, a sheen at the top, the accent glow behind the icon
        body = Image.new("RGBA", (w, h), pal["glass"])
        sheen_col = Image.new("L", (1, 64))
        sheen_col.putdata([int(pal["sheen"][3] * max(0.0, 1 - i / 40) ** 2) for i in range(64)])
        sheen = Image.new("RGBA", (w, h), pal["sheen"][:3] + (0,))
        sheen.putalpha(sheen_col.resize((w, min(h, max(16, int(h * 0.5)))), Image.BILINEAR).crop((0, 0, w, h)))
        body = Image.alpha_composite(body, sheen)
        if accent and glow_at:
            gx, gy, gr = glow_at
            gr = max(8, int(gr))
            g = _soft_mask().resize((gr * 2, gr * 2), Image.BILINEAR).point(lambda v: int(v * 0.34 * pal["glow"]))
            glow = Image.new("RGBA", (w, h), _rgb(accent) + (0,))
            ga = Image.new("L", (w, h), 0)
            ga.paste(g, (int(gx - gr), int(gy - gr)))
            glow.putalpha(ga)
            body = Image.alpha_composite(body, glow)
        d = ImageDraw.Draw(body)
        for y0, y1, inset in stripes:
            d.rectangle([int(inset), int(round(y0)), int(w - inset) - 1, int(round(y1)) - 1], fill=pal["stripe"])
        if sep is not None:
            d.line([int(w * 0.02), int(sep), int(w * 0.98), int(sep)], fill=pal["sep"], width=1)
        lay = Image.new("RGBA", (w * 3, h * 3), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        for x0, y0, x1, y1 in tracks:
            ld.rounded_rectangle([x0 * 3, y0 * 3, x1 * 3 - 1, y1 * 3 - 1], radius=(y1 - y0) * 1.5, fill=pal["track"])
        if tracks:
            body = Image.alpha_composite(body, lay.reduce(3))
        a = ImageChops.multiply(body.getchannel("A"), mask)
        body.putalpha(a)
        out.alpha_composite(body, (pad, pad))
        # 1 px edge, brighter at the top
        e = Image.new("RGBA", (w * 3, h * 3), (0, 0, 0, 0))
        ImageDraw.Draw(e).rounded_rectangle([1, 1, w * 3 - 2, h * 3 - 2], radius=r * 3, outline=pal["edge"], width=3)
        e = e.reduce(3)
        fade = Image.new("L", (1, 64))
        fade.putdata([int(255 * (1 - 0.55 * min(1.0, i / 40))) for i in range(64)])
        e.putalpha(ImageChops.multiply(e.getchannel("A"), fade.resize((w, h), Image.BILINEAR)))
        out.alpha_composite(e, (pad, pad))
        return ImageTk.PhotoImage(out)
    return _cached(key, make), pad


# --------------------------------------------------------------------------- ring gauge
RING_STEPS = 120


def ring_q(frac):
    return 0 if frac is None else int(round(min(1.0, max(0.0, frac)) * RING_STEPS))


def ring(d, q, color, accent, icon, pal):
    """Glowing ring gauge (diameter d, value q / RING_STEPS) around the section icon.
    PhotoImage of (d + 2 g) square, g = ring_pad(d); place it centred."""
    d = max(12, int(d))
    g = ring_pad(d)
    key = ("ring", d, q, color, accent, icon, id(pal))

    def make():
        S = d + 2 * g
        k = 4
        lw = max(2.0, d * 0.085)
        c = S / 2
        rr = d / 2 - lw / 2
        base = Image.new("RGBA", (S * k, S * k), (0, 0, 0, 0))
        bd = ImageDraw.Draw(base)
        # glass bubble + track
        rb = rr - lw * 0.9
        bd.ellipse([(c - rb) * k, (c - rb) * k, (c + rb) * k, (c + rb) * k], fill=_rgba(accent, pal["bubble"]))
        ro = rr + lw / 2
        bd.ellipse([(c - ro) * k, (c - ro) * k, (c + ro) * k, (c + ro) * k], outline=pal["ring_track"],
                   width=int(round(lw * k)))
        gfx._paint_icon(bd, k, icon, c, c, d * 0.2, accent, (0, 0, 0, 0))
        arc = Image.new("RGBA", (S * k, S * k), (0, 0, 0, 0))
        ad = ImageDraw.Draw(arc)
        frac = q / RING_STEPS
        if frac > 0:
            n = max(2, int(48 * frac))
            a0 = -90.0
            span = 360.0 * frac
            for i in range(n):                        # colour runs from the section accent to the value colour
                f0, f1 = i / n, (i + 1) / n
                col = _rgba(themes.mix(accent, color, min(1.0, (f0 + f1) / 2 * 1.4)), 255)
                ad.arc([(c - ro) * k, (c - ro) * k, (c + ro) * k, (c + ro) * k], a0 + span * f0,
                       a0 + span * f1 + 0.8, fill=col, width=int(round(lw * k)))
            for f, col in ((0.0, accent), (1.0, color)):              # round caps
                ang = math.radians(a0 + span * f)
                px, py = c + rr * math.cos(ang), c + rr * math.sin(ang)
                ad.ellipse([(px - lw / 2) * k, (py - lw / 2) * k, (px + lw / 2) * k, (py + lw / 2) * k],
                           fill=_rgba(col if f else themes.mix(accent, color, 0.05), 255))
        arc = arc.reduce(k)
        out = base.reduce(k)
        if frac > 0:
            glow = arc.filter(ImageFilter.GaussianBlur(max(1.5, g * 0.45)))
            ga = glow.getchannel("A").point(lambda v: int(min(255, v * 1.6 * pal["glow"])))
            glow.putalpha(ga)
            out = Image.alpha_composite(out, glow)
            out = Image.alpha_composite(out, arc)
            ang = math.radians(-90 + 360 * frac)                    # bright spark at the tip
            px, py = c + rr * math.cos(ang), c + rr * math.sin(ang)
            sp = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            sr = lw * 0.32
            ImageDraw.Draw(sp).ellipse([px - sr, py - sr, px + sr, py + sr], fill=(255, 255, 255, 220))
            out = Image.alpha_composite(out, sp.filter(ImageFilter.GaussianBlur(0.6)))
        return ImageTk.PhotoImage(out)
    return _cached(key, make)


def ring_pad(d):
    return max(4, int(d * 0.24))


# --------------------------------------------------------------------------- glowing bar
def bar_pad(bh):
    return max(3, int(round(bh * 0.9)))


def bar(length, bh, px, color, accent, pal):
    """Value bar: a glowing gradient capsule px pixels long on a track of `length` (the track is part of the card).
    PhotoImage of (length + 2 g) x (bh + 2 g), g = bar_pad(bh); place its top-left at (x0 - g, y0 - g)."""
    length, bh = max(4, int(round(length))), max(3, int(round(bh)))
    px = int(max(0, min(length, px)))
    g = bar_pad(bh)
    key = ("bar", length, bh, px, color, accent, id(pal))

    def make():
        W, H = length + 2 * g, bh + 2 * g
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if px <= 0:
            return ImageTk.PhotoImage(out)
        k = 3
        L = max(px, bh)
        grad = Image.new("RGBA", (256, 1))
        c0 = themes.mix(accent, color, 0.35)
        grad.putdata([_rgba(themes.mix(c0, color, i / 255), 200 + 55 * i / 255) for i in range(256)])
        fill = grad.resize((L * k, bh * k), Image.BILINEAR)
        m = Image.new("L", (L * k, bh * k), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, L * k - 1, bh * k - 1], radius=bh * k / 2, fill=255)
        fill.putalpha(ImageChops.multiply(fill.getchannel("A"), m))
        fill = fill.reduce(k)
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        lay.alpha_composite(fill, (g, g))
        glow = lay.filter(ImageFilter.GaussianBlur(g * 0.55))
        glow.putalpha(glow.getchannel("A").point(lambda v: int(min(255, v * 1.3 * pal["glow"]))))
        out = Image.alpha_composite(out, glow)
        out = Image.alpha_composite(out, lay)
        hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))          # a thin highlight along the top of the capsule
        ImageDraw.Draw(hl).line([g + bh / 2, g + bh * 0.28, g + L - bh / 2, g + bh * 0.28],
                                fill=(255, 255, 255, 70), width=max(1, int(bh * 0.18)))
        out = Image.alpha_composite(out, hl)
        return ImageTk.PhotoImage(out)
    return _cached(key, make)


# --------------------------------------------------------------------------- small glass controls
def hexa(c, a):
    """'#rrggbb' + alpha (0..255) -> '#rrggbbaa' (Pillow understands it; gfx passes it straight through)."""
    return "%s%02x" % (c[:7], int(max(0, min(255, a))))


def clock():
    return time.monotonic()
