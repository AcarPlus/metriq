"""Theme system: one source of truth for every colour / style token of the panel, its graphics, the value
colouring and the settings window.

A theme is a complete dict of tokens (THEMES). The user's choice lives in config.json:
    "theme": "dark" | "oled" | "nord" | ...            built-in theme name
    "theme_overrides": {"bg": "#101010", "radius": 8}   any token on top of the theme (see TOKENS)
    "value_color_mode": "smooth" | "stepped"            256-step gradient or 3-5 flat bands
    "value_steps": []                                    [] = 4 equal bands, or 2-4 thresholds in % of each
                                                         row's range, e.g. [50, 75, 90]
configure(cfg) builds the active Style; the rest of the program reads it through current() / palette() /
accent_for() / value_color(). Pure Python, no tkinter."""
import bisect
import re

import i18n

DEFAULT = "dark"
MAIN = ("bg", "card", "stripe", "border", "track", "text", "name", "dim", "faint")
ACCENTS = ("accent_clock", "accent_power", "accent_temp", "accent_fan", "accent_net", "accent_fps")
GRADIENT = ("grad0", "grad1", "grad2", "grad3", "grad4")
STATUS = ("green", "yellow", "orange", "red")
UI = ("ui", "select")                      # settings / switch accent, text selection
COLORS = MAIN + ACCENTS + GRADIENT + STATUS + UI
NUMBERS = {"radius": (0, 28), "row_spacing": (0.8, 1.5)}
FONTS = ("font_text", "font_num", "font_head")          # "" = automatic
TOKENS = COLORS + tuple(NUMBERS) + FONTS
ICON_TOKEN = {"gauge": "accent_clock", "bolt": "accent_power", "temp": "accent_temp", "fan": "accent_fan",
              "net": "accent_net", "fps": "accent_fps"}
MODES = ("smooth", "stepped")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _theme(bg, card, stripe, border, track, text, name, dim, faint, clock, power, temp, fan, net, fps, grad,
           status, ui, select, radius=16, spacing=1.0):
    d = dict(bg=bg, card=card, stripe=stripe, border=border, track=track, text=text, name=name, dim=dim,
             faint=faint, accent_clock=clock, accent_power=power, accent_temp=temp, accent_fan=fan,
             accent_net=net, accent_fps=fps, ui=ui, select=select, radius=radius, row_spacing=spacing,
             font_text="", font_num="", font_head="")
    d.update(zip(GRADIENT, grad))
    d.update(zip(STATUS, status))
    return d


# order = order in the picker
THEMES = {
    "dark": _theme(      # the original look
        "#0b0f14", "#121820", "#151c25", "#1f2833", "#1e2630", "#f0f6fc", "#c9d1d9", "#9198a1", "#6e7681",
        "#58a6ff", "#e3b341", "#ff7b72", "#39c5cf", "#bc8cff", "#7ee787",
        ("#2ea043", "#7cc242", "#e3b341", "#f0883e", "#f85149"),
        ("#3fb950", "#e3b341", "#f0883e", "#f85149"), "#58a6ff", "#1f3a5f"),
    "oled": _theme(
        "#000000", "#0b0b0d", "#101013", "#1d1d22", "#1c1c21", "#f5f5f7", "#d2d2d8", "#9b9ba3", "#6f6f78",
        "#4da3ff", "#ffc83d", "#ff6b63", "#2fd0dc", "#c490ff", "#6eea84",
        ("#25b04a", "#8bd23a", "#ffc83d", "#ff8a2b", "#ff4a40"),
        ("#34d058", "#ffc83d", "#ff8a2b", "#ff4a40"), "#4da3ff", "#17314f"),
    "nord": _theme(
        "#242933", "#2e3440", "#333a47", "#3f4859", "#3b4252", "#eceff4", "#d8dee9", "#a5aec0", "#7684a0",
        "#88c0d0", "#ebcb8b", "#e0838c", "#8fbcbb", "#b48ead", "#a3be8c",
        ("#a3be8c", "#c8d08a", "#ebcb8b", "#d99a7b", "#d9707a"),
        ("#a3be8c", "#ebcb8b", "#d08770", "#d9707a"), "#88c0d0", "#434c5e", radius=14),
    "dracula": _theme(
        "#1b1c26", "#242633", "#292b39", "#363a4d", "#32354a", "#f8f8f2", "#dcdcd6", "#a9afcf", "#7c84ad",
        "#8be9fd", "#f1fa8c", "#ff6e6e", "#50fa7b", "#bd93f9", "#ffb86c",
        ("#50fa7b", "#a8f27a", "#f1fa8c", "#ffb86c", "#ff5555"),
        ("#50fa7b", "#f1fa8c", "#ffb86c", "#ff5555"), "#bd93f9", "#44475a"),
    "catppuccin": _theme(    # Mocha
        "#11111b", "#1a1a28", "#1f1f2e", "#2b2c3f", "#2f3045", "#cdd6f4", "#bac2de", "#a6adc8", "#7f849c",
        "#89b4fa", "#f9e2af", "#f38ba8", "#94e2d5", "#cba6f7", "#a6e3a1",
        ("#a6e3a1", "#cde3a1", "#f9e2af", "#fab387", "#f38ba8"),
        ("#a6e3a1", "#f9e2af", "#fab387", "#f38ba8"), "#89b4fa", "#3b4261"),
    "tokyo": _theme(         # Tokyo Night
        "#13141c", "#1a1b26", "#1f2030", "#292e42", "#2a2e45", "#c0caf5", "#a9b1d6", "#8c96c4", "#636da6",
        "#7aa2f7", "#e0af68", "#f7768e", "#7dcfff", "#bb9af7", "#9ece6a",
        ("#9ece6a", "#c3d36a", "#e0af68", "#ff9e64", "#f7768e"),
        ("#9ece6a", "#e0af68", "#ff9e64", "#f7768e"), "#7aa2f7", "#2f3b61"),
    "gruvbox": _theme(
        "#1d2021", "#282828", "#2d2d2d", "#3c3836", "#3c3836", "#ebdbb2", "#d5c4a1", "#a89984", "#8a7d6e",
        "#83a598", "#fabd2f", "#fb4934", "#8ec07c", "#d3869b", "#b8bb26",
        ("#98971a", "#b8bb26", "#fabd2f", "#fe8019", "#fb4934"),
        ("#b8bb26", "#fabd2f", "#fe8019", "#fb4934"), "#83a598", "#504945", radius=10),
    "solarized": _theme(     # Solarized Dark
        "#002b36", "#073642", "#0a3d4b", "#14505f", "#0f4555", "#eee8d5", "#a9b4b4", "#8a9c9e", "#6f8a91",
        "#3a9be0", "#d9a21a", "#e8504d", "#2aa198", "#8e93e0", "#9bb000",
        ("#859900", "#b4a60a", "#d9a21a", "#e0661f", "#e8504d"),
        ("#9bb000", "#d9a21a", "#e0661f", "#e8504d"), "#3a9be0", "#0f5a73", radius=8),
    "light": _theme(
        "#eef0f4", "#ffffff", "#f5f7fa", "#d3d9e0", "#e2e7ee", "#1f2328", "#37404a", "#566170", "#69737f",
        "#0969da", "#9a6700", "#cf222e", "#127c86", "#8250df", "#1a7f37",
        ("#1a7f37", "#5f9112", "#a07800", "#c4570f", "#cf222e"),
        ("#1a7f37", "#9a6700", "#bc4c00", "#cf222e"), "#0969da", "#c5dcf8"),
    "contrast": _theme(      # high contrast
        "#000000", "#050505", "#171717", "#ffffff", "#3d3d3d", "#ffffff", "#ffffff", "#e6e6e6", "#c4c4c4",
        "#4db8ff", "#ffe14d", "#ff7070", "#2ff0d0", "#e0a8ff", "#6bff6b",
        ("#00f070", "#b6ff00", "#ffee00", "#ff9100", "#ff4040"),
        ("#00f070", "#ffee00", "#ff9100", "#ff4040"), "#ffe14d", "#0a4a8a", radius=8, spacing=1.1),
}

_NAMES = {
    "en": {"dark": "Dark (default)", "oled": "OLED Black", "nord": "Nord", "dracula": "Dracula",
           "catppuccin": "Catppuccin Mocha", "tokyo": "Tokyo Night", "gruvbox": "Gruvbox Dark",
           "solarized": "Solarized Dark", "light": "Light", "contrast": "High contrast"},
    "tr": {"dark": "Koyu (varsayılan)", "oled": "OLED Siyah", "nord": "Nord", "dracula": "Dracula",
           "catppuccin": "Catppuccin Mocha", "tokyo": "Tokyo Night", "gruvbox": "Gruvbox Dark",
           "solarized": "Solarized Dark", "light": "Açık", "contrast": "Yüksek kontrast"},
}
i18n.register({lang: {"theme_" + k: v for k, v in d.items()} for lang, d in _NAMES.items()})


def theme_label(name):
    return i18n.t("theme_" + name)


# ---------------------------------------------------------------- colour helpers
def rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def mix(c1, c2, tt):
    a, b = rgb(c1), rgb(c2)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * tt) for x, y in zip(a, b))


def luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(c1, c2):
    a, b = sorted((luminance(c1), luminance(c2)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def norm_color(v):
    """'#rgb' / '#rrggbb' (any case) -> '#rrggbb', else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if re.match(r"^#[0-9a-fA-F]{3}$", v):
        v = "#" + "".join(ch * 2 for ch in v[1:])
    return v.lower() if _HEX.match(v) else None


def clean_overrides(ov):
    """Only known tokens with valid values survive (a hand-edited config can contain anything)."""
    out = {}
    if not isinstance(ov, dict):
        return out
    for k, v in ov.items():
        if k in COLORS:
            c = norm_color(v)
            if c:
                out[k] = c
        elif k in NUMBERS:
            lo, hi = NUMBERS[k]
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            x = min(hi, max(lo, x))
            out[k] = int(round(x)) if k == "radius" else round(x, 2)
        elif k in FONTS and isinstance(v, str):
            out[k] = v.strip()
    return out


def norm_mode(v):
    v = str(v or "smooth").strip().lower()
    return v if v in MODES else "smooth"


def norm_steps(v):
    """2-4 ascending percentages strictly between 0 and 100, else [] (= automatic)."""
    try:
        xs = sorted({round(float(x), 1) for x in v})
    except (TypeError, ValueError):
        return []
    xs = [x for x in xs if 0 < x < 100]
    return [int(x) if x == int(x) else x for x in xs] if 2 <= len(xs) <= 4 else []


# ---------------------------------------------------------------- the active style
class Style:
    """Resolved tokens (theme + overrides) plus the value-colouring state; one per configuration."""

    def __init__(self, name=DEFAULT, overrides=None, mode="smooth", steps=None):
        self.name = name if name in THEMES else DEFAULT
        self.overrides = clean_overrides(overrides)
        self.tokens = dict(THEMES[self.name])
        self.tokens.update(self.overrides)
        self.mode, self.steps = norm_mode(mode), norm_steps(steps or [])
        self.lut = self._build_lut()
        self.thresholds = [x / 100.0 for x in self.steps] if self.steps else [0.25, 0.5, 0.75]
        n = len(self.thresholds) + 1
        self.bands = [self.lut[round(i / (n - 1) * 255)] for i in range(n)]

    def __getitem__(self, k):
        return self.tokens[k]

    @property
    def radius(self):
        return int(self.tokens["radius"])

    @property
    def spacing(self):
        return float(self.tokens["row_spacing"])

    def is_dark(self):
        return luminance(self.tokens["bg"]) < 0.3

    def _build_lut(self, n=256):
        stops = [self.tokens[k] for k in GRADIENT]
        lut = []
        for k in range(n):
            x = k / (n - 1) * (len(stops) - 1)
            i = min(len(stops) - 2, int(x))
            lut.append(mix(stops[i], stops[i + 1], x - i))
        return lut

    def palette(self):
        """The colour dict the drawing code reads (panel.C): token names plus short aliases."""
        t = self.tokens
        p = {k: t[k] for k in COLORS}
        p["fg"] = t["text"]
        p["ui_hover"] = mix(t["ui"], t["text"], 0.25)
        p["on_ui"] = max((t["bg"], t["text"]), key=lambda c: contrast(c, t["ui"]))
        for icon, tok in ICON_TOKEN.items():
            p[icon] = t[tok]
        return p

    def accent(self, icon):
        return self.tokens[ICON_TOKEN.get(icon, "accent_clock")]

    def accent_for(self, icon, color):
        """Section colour: the theme's accent unless the section carries its own (non built-in) colour."""
        c = norm_color(color) if color else None
        if c is None or c in builtin_accents(icon):
            return self.accent(icon)
        return c

    def tt(self, limits, v):
        """Position of v between the 'green' and the 'red' limit (0..1), None without limits."""
        if not limits:
            return None
        green, red = limits
        return min(1.0, max(0.0, (v - green) / (red - green)))

    def color_at(self, tt):
        if self.mode == "stepped":
            return self.bands[bisect.bisect_right(self.thresholds, tt)]
        return self.lut[round(tt * 255)]

    def value_color(self, limits, v, default):
        tt = self.tt(limits, v)
        return default if tt is None else self.color_at(tt)


_ACC_CACHE = {}


def builtin_accents(icon):
    """Every built-in theme's accent for this icon (a stored section colour equal to one of them follows the theme)."""
    s = _ACC_CACHE.get(icon)
    if s is None:
        tok = ICON_TOKEN.get(icon, "accent_clock")
        s = _ACC_CACHE[icon] = {th[tok].lower() for th in THEMES.values()}
    return s


_cur = Style()


def configure(cfg):
    """Make the theme described by cfg the active one. Returns the Style."""
    global _cur
    _cur = Style(str(cfg.get("theme") or DEFAULT).strip().lower(), cfg.get("theme_overrides"),
                 cfg.get("value_color_mode"), cfg.get("value_steps"))
    return _cur


def current():
    return _cur


def palette():
    return _cur.palette()


def accent(icon):
    return _cur.accent(icon)


def accent_for(icon, color):
    return _cur.accent_for(icon, color)


def value_color(row, v, default):
    return _cur.value_color(row.limits, v, default)
