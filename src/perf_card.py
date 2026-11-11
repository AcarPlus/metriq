"""Shareable benchmark result card: a 1200x630 PNG (dark theme) with the scores, the hardware model names, OS, date
and app version. Drawn with Pillow (bundled anyway). No personal data goes on it: no user / PC name, no paths."""
import ctypes
import io
import os
import platform
import re
import time

import config
import themes
from i18n import t
from version import __version__

W, H = 1200, 630
S = 2                                      # drawn at 2x, then scaled down (smooth shapes)
FOOTER = "metriQ \u00b7 github.com/AcarPlus/metriq"
SCORE_MAX = 2000.0
ORDER = ("cpu", "ram", "ssd", "gpu")
ACC = {"cpu": "accent_clock", "ram": "accent_net", "ssd": "accent_fan", "gpu": "accent_fps"}
_FONTS = {}


def _font(size, bold=False):
    from PIL import ImageFont
    key = (size, bold)
    if key not in _FONTS:
        win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        names = ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")
        f = None
        for n in names:
            try:
                f = ImageFont.truetype(os.path.join(win, n), size * S)
                break
            except OSError:
                pass
        _FONTS[key] = f or ImageFont.load_default(size * S)
    return _FONTS[key]


def _fit(d, text, font, width):
    """text shortened with an ellipsis until it fits into width (card pixels)."""
    text = str(text or "")
    if d.textlength(text, font=font) <= width * S:
        return text
    while len(text) > 1 and d.textlength(text + "\u2026", font=font) > width * S:
        text = text[:-1]
    return text.rstrip() + "\u2026"


def _os_name():
    try:
        if platform.system() == "Windows":
            build = int(platform.version().split(".")[-1])
            return "Windows %s" % ("11" if build >= 22000 else platform.release())
        return platform.system() + " " + platform.release()
    except Exception:
        return ""


def _clean(name):
    """Model name without a leading drive letter ('D: Samsung SSD' -> 'Samsung SSD') and with squeezed blanks."""
    name = re.sub(r"^[A-Za-z]:\s*", "", str(name or ""))
    name = re.sub(r"\s*\((R|TM|tm|r)\)", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _details(run):
    sysi = run.get("system") or {}
    rs = run.get("results") or {}
    ram = sysi.get("ram_gb")
    return {
        "cpu": _clean(sysi.get("cpu")),
        "ram": ("%g GB" % ram) if ram else "",
        "ssd": _clean((rs.get("ssd") or {}).get("drive_label") or (rs.get("ssd") or {}).get("drive")),
        "gpu": _clean((rs.get("gpu") or {}).get("adapter")),
    }


def render(run):
    """PIL image (RGB, 1200x630) of a history entry / new_run() dict."""
    from PIL import Image, ImageDraw
    import perf_core as pc
    th = themes.THEMES["dark"]
    rs = run.get("results") or {}
    good = {k: v for k, v in rs.items() if v and v.get("score")}
    total = run.get("overall") or (pc.overall(good) if good else 0.0)
    det = _details(run)

    im = Image.new("RGB", (W * S, H * S), th["bg"])
    d = ImageDraw.Draw(im)

    def text(x, y, s, size, col, bold=False, anchor="la"):
        d.text((x * S, y * S), s, font=_font(size, bold), fill=col, anchor=anchor)

    def rrect(x0, y0, x1, y1, r, fill, outline=None):
        d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), r * S, fill=fill, outline=outline,
                            width=S if outline else 0)

    # header: logo, name, title, total score
    m = 48
    try:
        logo = Image.open(os.path.join(config.APP_DIR, "metriq.png")).convert("RGBA").resize((72 * S, 72 * S),
                                                                                           Image.LANCZOS)
        im.paste(logo, (m * S, 40 * S), logo)
    except Exception:
        pass
    text(m + 90, 38, "metriQ", 40, th["text"], True)
    text(m + 92, 90, t("pf_title"), 20, th["dim"])
    text(W - m, 30, "%.0f" % total if total else "-", 84, th["accent_fps"], True, "ra")
    text(W - m, 126, t("pf_overall").upper(), 15, th["dim"], False, "ra")

    # four tiles
    gap, top, bot = 20, 190, 468
    tw = (W - 2 * m - 3 * gap) / 4.0
    for i, test in enumerate(ORDER):
        x0 = m + i * (tw + gap)
        x1 = x0 + tw
        acc = th[ACC[test]]
        rrect(x0, top, x1, bot, 16, th["card"], th["border"])
        d.rounded_rectangle((x0 * S, top * S, (x0 + 6) * S, bot * S), 3 * S, fill=acc)
        px = x0 + 26
        text(px, top + 20, t("pf_%s_t" % test), 22, th["name"], True)
        r = rs.get(test) or {}
        sc = r.get("score") if not r.get("skipped") else None
        text(px, top + 56, "%.0f" % sc if sc else "-", 56, acc if sc else th["faint"], True)
        if sc:
            text(px, top + 128, t("pf_pts"), 15, th["dim"])
            bx0, bx1, by = px, x1 - 22, top + 160
            rrect(bx0, by, bx1, by + 10, 5, th["track"])
            frac = max(0.02, min(1.0, sc / SCORE_MAX))
            rrect(bx0, by, bx0 + (bx1 - bx0) * frac, by + 10, 5, acc)
        name = det.get(test) or ""
        f = _font(15)
        # model name over up to 3 lines
        words, lines, cur = name.split(), [], ""
        for w in words:
            nxt = (cur + " " + w).strip()
            if d.textlength(nxt, font=f) <= (tw - 48) * S or not cur:
                cur = nxt
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > 3:
            lines = lines[:2] + [" ".join(lines[2:])]          # the last line gets the ellipsis
        for j, ln in enumerate(lines):
            text(px, top + 190 + j * 24, _fit(d, ln, f, tw - 48), 15, th["dim"])

    # system line
    sysi = run.get("system") or {}
    bits = [_os_name()]
    if sysi.get("cores"):
        bits.append("%d threads" % sysi["cores"])
    bits.append(time.strftime("%Y-%m-%d", time.localtime(run.get("ts") or time.time())))
    bits.append("metriQ v%s" % __version__)
    text(m, 492, _fit(d, "  \u00b7  ".join(b for b in bits if b), _font(18), W - 2 * m), 18, th["name"])
    mode = run.get("mode")
    if mode in ("quick", "detailed"):
        text(m, 522, t("pf_mode") + ": " + t("pf_mode_" + mode), 15, th["dim"])

    d.line((m * S, 572 * S, (W - m) * S, 572 * S), fill=th["border"], width=S)
    text(W / 2.0, 600, FOOTER, 17, th["dim"], False, "mm")
    return im.resize((W, H), Image.LANCZOS)


def save(run, path):
    render(run).save(path, "PNG", optimize=True)


def copy_to_clipboard(run):
    """Puts the card on the Windows clipboard as a bitmap (CF_DIB). Returns True on success."""
    try:
        buf = io.BytesIO()
        render(run).convert("RGB").save(buf, "BMP")
        data = buf.getvalue()[14:]                     # DIB = BMP without the file header
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        h = k32.GlobalAlloc(0x0002, len(data))         # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            return bool(u32.SetClipboardData(8, h))    # CF_DIB; the clipboard owns h on success
        finally:
            u32.CloseClipboard()
    except Exception:
        return False
