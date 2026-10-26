"""Generates the metriQ logo files with Pillow:  metriq.ico (16..256), metriq.png (256) and ../docs/logo.png (512,
transparent, no plate).  Run:  python src/make_icon.py   (dev tool only, needs `pip install pillow`).

Design: a round-capped green-to-red gradient ring that ends in red next to the Q tail, nine small grey tick marks
inside the ring, a white tapered needle running from the centre pivot out through the ring's opening at 4:30 (the Q
tail), a soft red glow at the red end, all on a dark rounded-square plate. The 16/24/32 px icons drop ticks and glow
so they stay crisp."""
import math
import os
import struct
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")

SS = 4                                   # supersampling factor
BG = (18, 24, 32, 255)
WHITE = (240, 246, 252, 255)
TICK = (139, 148, 158, 255)
RED = (248, 81, 73)
STOPS = [(0.0, (46, 160, 67)), (0.3, (124, 194, 66)), (0.55, (227, 179, 65)), (0.78, (240, 136, 62)),
         (1.0, RED)]
TAIL = -45                               # needle / Q tail direction: 4:30 (degrees, 0 = right, counter-clockwise)
START = TAIL - 22                        # the ring starts just after the tail (clockwise) ...
SWEEP = 360 - 44                         # ... and ends just before it
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL = 32                               # icons up to this size: no ticks / glow


def grad(t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(int(a + (b - a) * f) for a, b in zip(c0, c1)) + (255,)
    return STOPS[-1][1] + (255,)


def pt(cx, cy, r, ang):
    a = math.radians(ang)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def arc(d, cx, cy, r, w, n=720):
    """Clockwise gradient arc with round caps (dots along the path)."""
    for i in range(n + 1):
        f = i / n
        x, y = pt(cx, cy, r, START - SWEEP * f)
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=grad(f))


def needle(d, cx, cy, ang, l1, w0, w1, col):
    """Tapered needle from the pivot (half width w0) to length l1 (half width w1), round at both ends."""
    ux, uy = math.cos(math.radians(ang)), -math.sin(math.radians(ang))
    px, py = -uy, ux
    d.polygon([(cx + px * w0, cy + py * w0), (cx + ux * l1 + px * w1, cy + uy * l1 + py * w1),
               (cx + ux * l1 - px * w1, cy + uy * l1 - py * w1), (cx - px * w0, cy - py * w0)], fill=col)
    for l, w in ((0, w0), (l1, w1)):
        x, y = cx + ux * l, cy + uy * l
        d.ellipse((x - w, y - w, x + w, y + w), fill=col)


def draw_logo(px, plate=True, detail=True, outline=False):
    """The logo as an RGBA image of px x px pixels. detail=False: no ticks / glow, heavier strokes (tiny icons).
    outline=True: the needle gets a thin dark rim so it stays visible on light backgrounds (plate-less logo)."""
    S = px * SS
    c, r = S * 0.47, S * 0.285
    k = 1.0 if detail else 1.3                      # stroke weight factor
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if plate:
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.22), fill=BG)
    if detail:
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        x, y = pt(c, c, r, START - SWEEP)
        ImageDraw.Draw(glow).ellipse((x - S * .12, y - S * .12, x + S * .12, y + S * .12), fill=RED + (120,))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.05)))
        d = ImageDraw.Draw(im)
    arc(d, c, c, r, S * 0.09 * k)
    if detail:
        for i in range(9):
            a = START - SWEEP * i / 8
            x0, y0 = pt(c, c, r - S * 0.085, a)
            x1, y1 = pt(c, c, r - S * 0.125, a)
            d.line((x0, y0, x1, y1), fill=TICK, width=int(S * 0.012))
    if outline:                                     # dark rim: needle + pivot drawn slightly bigger first
        e = S * 0.012
        needle(d, c, c, TAIL, S * 0.45 + e, S * 0.038 + e, S * 0.020 + e, BG)
        d.ellipse((c - S * .05 - e, c - S * .05 - e, c + S * .05 + e, c + S * .05 + e), fill=BG)
    needle(d, c, c, TAIL, S * 0.45, S * 0.038 * k, S * 0.020 * k, WHITE)
    d.ellipse((c - S * .05 * k, c - S * .05 * k, c + S * .05 * k, c + S * .05 * k), fill=WHITE)
    return im.resize((px, px), Image.LANCZOS)


def png_bytes(im):
    b = BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()


def write_ico(path):
    blobs = [png_bytes(draw_logo(s, detail=s > SMALL)) for s in ICO_SIZES]
    hdr = struct.pack("<HHH", 0, 1, len(ICO_SIZES))
    off = 6 + 16 * len(ICO_SIZES)
    ent, data = b"", b""
    for s, b in zip(ICO_SIZES, blobs):
        ent += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), off + len(data))
        data += b
    with open(path, "wb") as f:
        f.write(hdr + ent + data)


def main():
    write_ico(os.path.join(HERE, "metriq.ico"))
    draw_logo(256).save(os.path.join(HERE, "metriq.png"), optimize=True)
    os.makedirs(DOCS, exist_ok=True)
    draw_logo(512, plate=False, outline=True).save(os.path.join(DOCS, "logo.png"), optimize=True)
    print("metriq.ico, metriq.png and docs/logo.png written")


if __name__ == "__main__":
    main()
