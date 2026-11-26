"""Text drawn as an anti-aliased image at any (fractional) pixel size: smooth text scaling for the hover swell.

Tk fonts only come in whole pixel sizes, so a name growing from 15 to 17 px on hover showed 2 - 3 different pictures
in its whole motion (it read as stutter at any frame rate). A Sprite renders the same text with FreeType (Pillow) at
4x size and box-filters it down: every frame gets its own size (to 1/8 px) and the text stays centred on its line to
a quarter pixel, exactly where Tk's own text item (anchor "w") sits.

    sp = Sprite.for_family("Segoe UI Variable Text")        # None: font file not found / no Pillow
    photo, dx = sp.photo(text, 16.25, "#e6edf3", max_px=18)   # PhotoImage + its x offset (place with anchor "w")

The font file is looked up in the Windows font registry (machine + user fonts). Segoe UI Variable is one variable
font file: "Segoe UI Variable Text / Display / Small" pick its named instance. Never raises: a missing font, a missing
Pillow or any render error gives None and the caller keeps whole-pixel Tk fonts.
"""
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except Exception:          # Pillow missing
    Image = None

SS = 4                     # supersampling factor
_FILES = None              # lower-case registry font name -> file path
_SPRITES = {}              # family -> Sprite or None


def _font_files():
    global _FILES
    if _FILES is not None:
        return _FILES
    _FILES = {}
    if sys.platform != "win32":
        return _FILES
    try:
        import winreg
    except ImportError:
        return _FILES
    windir = os.environ.get("WINDIR", r"C:\Windows")
    roots = ((winreg.HKEY_LOCAL_MACHINE, os.path.join(windir, "Fonts")),
             (winreg.HKEY_CURRENT_USER, os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows",
                                                     "Fonts")))
    for hive, base in roots:
        try:
            k = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        i = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            if isinstance(val, str) and val:
                path = val if os.path.isabs(val) else os.path.join(base, val)
                nm = name.lower().replace("(truetype)", "").replace("(opentype)", "").strip()
                _FILES.setdefault(nm, path)
    return _FILES


def _resolve(family):
    """(file, variation name or None) for a Tk family name, None when it is not a plain installed font file."""
    files = _font_files()
    fam = family.strip().lower()
    if fam.startswith("segoe ui variable"):
        path = files.get("segoe ui variable")
        if not path:
            return None
        rest = fam[len("segoe ui variable"):].strip()
        inst = {"": b"Regular", "text": b"Regular", "display": b"Regular Display", "small": b"Regular Small"}.get(rest)
        return (path, inst) if inst else None
    for key in (fam, fam + " regular"):
        p = files.get(key)
        if p and os.path.isfile(p) and p.lower().endswith((".ttf", ".otf")):
            return p, None
    return None


class Sprite:
    def __init__(self, path, inst):
        self.path, self.inst = path, inst
        self._fonts = {}           # size key -> FreeTypeFont (at SS x size)
        self._cache = {}           # (text, size key, colour, max_px) -> (PhotoImage, dx)

    @classmethod
    def for_family(cls, family):
        if Image is None:
            return None
        if family in _SPRITES:
            return _SPRITES[family]
        sp = None
        try:
            r = _resolve(family)
            if r:
                sp = cls(*r)
                sp._font(16.0)      # fails here (bad file, no variation support) rather than mid-animation
        except Exception:
            sp = None
        _SPRITES[family] = sp
        return sp

    def _font(self, px):
        k = round(px * 8)
        f = self._fonts.get(k)
        if f is None:
            f = ImageFont.truetype(self.path, k / 8 * SS)
            if self.inst:
                f.set_variation_by_name(self.inst)
            self._fonts[k] = f
        return f

    def photo(self, text, px, colour, max_px):
        """(PhotoImage, dx): `text` at `px` pixels in `colour`, vertically centred like a Tk text item with anchor
        "w" of that size. The image is sized for max_px (constant through a swell, so its centre never moves); dx is
        how far left of the text's x the image starts. None on any error."""
        key = (text, round(px * 8), colour, round(max_px))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        try:
            big = self._font(max_px)
            a_m, d_m = big.getmetrics()
            pad = 2 * SS
            f = self._font(px)
            a, d = f.getmetrics()
            w = int(f.getlength(text)) + 2 * pad + SS
            H = a_m + d_m + 2 * pad
            H += (-H) % (2 * SS)                 # whole pixels after the reduce, an even height: a sharp centre
            w += (-w) % SS
            im = Image.new("L", (w, H), 0)
            # Tk centres the line box (ascent + descent): baseline = centre + (ascent - descent) / 2
            ImageDraw.Draw(im).text((pad, H / 2 + (a - d) / 2), text, font=f, fill=255, anchor="ls")
            mask = im.reduce(SS)
            rgb = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
            out = Image.new("RGBA", mask.size, rgb + (0,))
            out.putalpha(mask)
            res = (ImageTk.PhotoImage(out), pad // SS)
        except Exception:
            return None
        if len(self._cache) > 1500:
            self._cache.clear()
        self._cache[key] = res
        return res
