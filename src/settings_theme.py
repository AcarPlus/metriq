"""Settings tab "Theme": theme picker with preview cards, colour / shape / font customisation, value colouring.

ThemeTab(settings_window, cfg) fills the Theme page of the settings window (cards on a scrolling page). Nothing is applied to the panel until Apply / Save
(like every other setting); the preview strip at the top of the tab shows the unsaved choice at once.
read() -> {"theme", "theme_overrides", "value_color_mode", "value_steps"} for SettingsWindow.read_ui()."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser

import gfx
import helptexts
import i18n
import themes
import uikit
from i18n import t

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:
    Image = None

i18n.register({
    "tr": {
        "st_tab_theme": "Tema", "th_pick": "Tema seç", "th_pick_hint": "Bir tema seçmek özelleştirmelerini sıfırlar.",
        "th_colors": "Renkler", "th_colors_hint": "Rengi değiştirmek için kutuya tıkla, sıfırlamak için sağ tıkla.",
        "th_reset": "Özelleştirmeleri sıfırla", "th_pick_color": "Renk seç",
        "th_bg": "Arka plan", "th_card": "Kart", "th_stripe": "Satır şeridi", "th_border": "Kenarlık",
        "th_track": "Çubuk zemini", "th_text": "Metin", "th_name": "Satır adı", "th_dim": "Soluk metin",
        "th_faint": "Çok soluk metin",
        "th_accent_clock": "Saat / kullanım", "th_accent_power": "Güç", "th_accent_temp": "Sıcaklık",
        "th_accent_fan": "Fanlar", "th_accent_net": "Ağ / disk", "th_accent_fps": "FPS",
        "th_grad": "Değer rengi {n}", "th_grad_hint": "1 = iyi … 5 = kötü (yeşilden kırmızıya)",
        "th_shape": "Şekil ve yazı tipi", "th_radius": "Köşe yuvarlaklığı", "th_spacing": "Satır aralığı",
        "th_font_text": "Metin yazı tipi", "th_font_num": "Sayı yazı tipi", "th_font_head": "Başlık yazı tipi",
        "th_font_auto": "(otomatik)",
        "th_values": "Değer renkleri", "th_mode": "Mod", "th_mode_smooth": "Yumuşak (degrade)",
        "th_mode_stepped": "Kademeli (3-5 bant)",
        "th_auto": "Otomatik: her satırın sınırlarından 4 eşit bant",
        "th_thresholds": "Eşikler", "th_steps_hint": "Satır aralığının yüzdesi: 2-4 artan değer (örn. 50, 75, 90) = 3-5 bant.",
        "th_pv_hint": "Önizleme: kaydedilmemiş seçimin görünümü",
    },
    "en": {
        "st_tab_theme": "Theme", "th_pick": "Choose a theme",
        "th_pick_hint": "Picking a theme clears your customisations.",
        "th_colors": "Colours", "th_colors_hint": "Click a swatch to change it, right-click to reset it.",
        "th_reset": "Reset customisations", "th_pick_color": "Pick a colour",
        "th_bg": "Background", "th_card": "Card", "th_stripe": "Row stripe", "th_border": "Border",
        "th_track": "Bar track", "th_text": "Text", "th_name": "Row name", "th_dim": "Dim text",
        "th_faint": "Faint text",
        "th_accent_clock": "Clock / usage", "th_accent_power": "Power", "th_accent_temp": "Temperatures",
        "th_accent_fan": "Fans", "th_accent_net": "Network / disk", "th_accent_fps": "FPS",
        "th_grad": "Value colour {n}", "th_grad_hint": "1 = good … 5 = bad (green to red)",
        "th_shape": "Shape and fonts", "th_radius": "Corner radius", "th_spacing": "Row spacing",
        "th_font_text": "Text font", "th_font_num": "Number font", "th_font_head": "Heading font",
        "th_font_auto": "(automatic)",
        "th_values": "Value colours", "th_mode": "Mode", "th_mode_smooth": "Smooth (gradient)",
        "th_mode_stepped": "Stepped (3-5 bands)",
        "th_auto": "Automatic: 4 equal bands from each row's limits",
        "th_thresholds": "Thresholds", "th_steps_hint": "% of each row's range: 2-4 ascending values "
                                                       "(e.g. 50, 75, 90) = 3-5 bands.",
        "th_pv_hint": "Preview: the unsaved choice",
    },
})

PREVIEW_H = 186
CARD_W, CARD_H = 118, 70
DEFAULT_STEPS = [50, 75, 90]
_FAMS = []


def families():
    if not _FAMS:
        names = {f for f in tkfont.families() if not f.startswith("@")}
        _FAMS.extend(sorted(names, key=str.lower))
    return _FAMS


def _font(sty, token, size, *auto, bold=False):
    want = sty[token]
    fams = set(families())
    fam = want if want and want in fams else next((n for n in auto if n in fams), "TkDefaultFont")
    return (fam, -size, "bold") if bold else (fam, -size)


def _txt(sty, size, bold=False):
    return _font(sty, "font_text", size, "Segoe UI Variable Text", "Segoe UI", bold=bold)


def _num(sty, size):
    return _font(sty, "font_num", size, "Cascadia Mono", "Consolas")


def _head(sty, size):
    return _font(sty, "font_head", size, "Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", bold=True)


def _strip_image(w, h, colors):
    """Rounded colour strip (one colour per pixel column) as PhotoImage; None without Pillow."""
    if Image is None:
        return None
    k = 3
    im = Image.new("RGB", (w * k, h * k))
    d = ImageDraw.Draw(im)
    for x in range(w):
        d.rectangle([x * k, 0, (x + 1) * k - 1, h * k], fill=colors[x])
    mask = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=255)
    out = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return ImageTk.PhotoImage(out.resize((w, h), Image.LANCZOS))


# ====================================================================== the tab
class ThemeTab:
    TOKEN_ROWS = (("th_bg", "bg"), ("th_card", "card"), ("th_stripe", "stripe"), ("th_border", "border"),
                  ("th_track", "track"), ("th_text", "text"), ("th_name", "name"), ("th_dim", "dim"),
                  ("th_faint", "faint"))

    @staticmethod
    def initial_values(cfg):
        """What read() returns for a theme page that has not been touched (the saved settings, normalised)."""
        name = str(cfg.get("theme") or themes.DEFAULT).strip().lower()
        if name not in themes.THEMES:
            name = themes.DEFAULT
        return {"theme": name, "theme_overrides": themes.clean_overrides(cfg.get("theme_overrides")),
                "value_color_mode": themes.norm_mode(cfg.get("value_color_mode")),
                "value_steps": list(themes.norm_steps(cfg.get("value_steps")))}

    def __init__(self, ui, cfg):
        import settings_ui as su          # (imported late: settings_ui imports this module)
        self.ui, self.C, self.su = ui, ui.C, su
        self.name = str(cfg.get("theme") or themes.DEFAULT).strip().lower()
        if self.name not in themes.THEMES:
            self.name = themes.DEFAULT
        self.ov = themes.clean_overrides(cfg.get("theme_overrides"))
        self.mode = themes.norm_mode(cfg.get("value_color_mode"))
        self.steps = themes.norm_steps(cfg.get("value_steps"))
        self._lock = False
        self._imgs = []
        self.swatch, self.cards, self.fcombo = {}, {}, {}

        C = self.C
        self.tab = ui.add_page(4)
        self.idx = 4
        self.pv = tk.Canvas(self.tab, height=PREVIEW_H, bg=C["bg"], highlightthickness=0, bd=0)
        self.pv.pack(fill="x", padx=(0, 6), pady=(0, 8))
        self.pv.bind("<Configure>", lambda e: self.draw_preview())
        sf = su.ScrollFrame(self.tab, C["bg"], C)
        sf.reserve, sf.cap = PREVIEW_H + 18, 560
        sf.pack(fill="both", expand=True)
        sf.inner.configure(padx=0, pady=2)
        ui.scrolls.append(sf)
        ui.pages[4]["sf"] = sf
        self.sf = sf
        p = sf.inner
        self._build_themes(p)
        self._build_colors(p)
        self._build_shape(p)
        self._build_values(p)
        self.sync()

    # ------------------------------------------------------------ state
    def style(self):
        return themes.Style(self.name, self.ov, self.mode, self.steps)

    def read(self):
        return {"theme": self.name, "theme_overrides": dict(self.ov), "value_color_mode": self.mode,
                "value_steps": list(self.steps)}

    def set_token(self, tok, val):
        """Override a token; a value equal to the theme's own value just removes the override."""
        if val == themes.THEMES[self.name][tok]:
            self.ov.pop(tok, None)
        else:
            self.ov[tok] = val
        self.sync()

    # ------------------------------------------------------------ sections
    def _card(self, p, text, hint=None):
        card = self.ui.card(p, text)
        if hint:
            h = self.ui.label(card.body, hint, dim=True, size=9, bg=card.fillc)
            uikit.autowrap(h)
            self.ui.full(card, h, pady=(0, 8), divider=False)
        return card

    def _build_themes(self, p):
        card = self._card(p, t("th_pick"), t("th_pick_hint"))
        bg = card.fillc
        grid = tk.Frame(card.body, bg=bg)
        self.ui.full(card, grid, pady=(0, 4), divider=False, sticky="w")
        self._cardimgs = {}
        for i, name in enumerate(themes.THEMES):
            cv = tk.Canvas(grid, width=CARD_W + 4, height=CARD_H + 26, bg=bg, highlightthickness=0, bd=0,
                           cursor="hand2")
            cv.grid(row=i // 5, column=i % 5, padx=(0, 8), pady=(0, 8))
            cv.bind("<Button-1>", lambda e, n=name: self.pick_theme(n))
            cv.bind("<Enter>", lambda e, n=name: self.draw_card(n, hover=True))
            cv.bind("<Leave>", lambda e, n=name: self.draw_card(n))
            self.cards[name] = cv
            self.draw_card(name)

    def _build_colors(self, p):
        C, ui = self.C, self.ui
        card = self._card(p, t("th_colors"), t("th_colors_hint"))
        bg = card.fillc
        ui.button(card.hdr, t("th_reset"), self.reset_overrides, bg=bg).pack(side="right")
        grid = tk.Frame(card.body, bg=bg)
        ui.full(card, grid, pady=(0, 4), divider=False, sticky="w")
        items = [(t(lbl), tok) for lbl, tok in self.TOKEN_ROWS]
        items += [(t("th_" + tok), tok) for tok in themes.ACCENTS]
        items += [(t("th_grad", n=i + 1), tok) for i, tok in enumerate(themes.GRADIENT)]
        per_col = 8
        for i, (label, tok) in enumerate(items):
            col, row = divmod(i, per_col)
            ui.label(grid, label, size=9, bg=bg).grid(row=row, column=col * 2, sticky="w",
                                                      padx=(0 if col == 0 else 26, 10), pady=4)
            b = uikit.Swatch(grid, C, "#000000", lambda tk_=tok: self.pick_color(tk_), bg=bg)
            b.bind("<Button-3>", lambda e, tk_=tok: self.reset_token(tk_))
            b.grid(row=row, column=col * 2 + 1, sticky="w", pady=4)
            self.swatch[tok] = b
        gh = tk.Frame(card.body, bg=bg)
        ui.full(card, gh, pady=(4, 2), divider=False, sticky="w")
        ui.label(gh, t("th_grad_hint"), dim=True, size=9, bg=bg).pack(side="left")
        helptexts.info_icon(gh, "th_grad", bg=bg, size=12).pack(side="left", padx=(6, 0))

    def _build_shape(self, p):
        C, ui = self.C, self.ui
        card = self._card(p, t("th_shape"))
        bg = card.fillc
        self.vl = {}
        for key, lbl, lo, hi, res in (("radius", "th_radius", 0, 28, 1), ("row_spacing", "th_spacing", 0.8, 1.5, 0.05)):
            fr = tk.Frame(card.body, bg=bg)
            sc = uikit.Slider(fr, C, lo, hi, res, 220, lambda v, k=key: self.on_slider(k, v), bg=bg)
            sc.pack(side="left")
            self.vl[key] = ui.label(fr, "", dim=True, size=9, bg=bg)
            self.vl[key].config(width=8, anchor="e")
            self.vl[key].pack(side="left", padx=(8, 0))
            setattr(self, "sc_" + key, sc)
            ui.row(card, t(lbl), fr, help="th_" + key)
        auto = t("th_font_auto")
        fams = [auto] + families()
        for tok, lbl in (("font_text", "th_font_text"), ("font_num", "th_font_num"), ("font_head", "th_font_head")):
            cb = ui.combo(card.body, fams, 0, 28)
            cb.bind("<<ComboboxSelected>>", lambda e, k=tok, c=cb: self.on_font(k, c))
            self.fcombo[tok] = cb
            ui.row(card, t(lbl), cb, help="th_fonts")

    def _build_values(self, p):
        C, ui, su = self.C, self.ui, self.su
        card = self._card(p, t("th_values"))
        bg = card.fillc
        self.mode_vals = list(themes.MODES)
        self.cb_mode = ui.combo(card.body, [t("th_mode_smooth"), t("th_mode_stepped")],
                                self.mode_vals.index(self.mode), 22)
        self.cb_mode.bind("<<ComboboxSelected>>", self.on_mode)
        ui.row(card, t("th_mode"), self.cb_mode, help="th_mode")
        self.v_auto = tk.BooleanVar(value=not self.steps)
        sw_auto = ui.switch(card.body, self.v_auto, self.on_auto)
        self._adv = [(ui.row(card, t("th_auto"), sw_auto, help="th_auto"), sw_auto)]
        row = tk.Frame(card.body, bg=bg)
        self.step_vars, self.step_entries = [], []
        init = self.steps or DEFAULT_STEPS
        for i in range(4):
            v = tk.StringVar(value=su._fmt_num(init[i]) if i < len(init) else "")
            e = ui.entry(row, v, 4, bg=bg)
            e.pack(side="left", padx=(0 if i == 0 else 6, 0))
            v.trace_add("write", lambda *a: self.on_steps())
            self.step_vars.append(v)
            self.step_entries.append(e)
        ui.label(row, "%", dim=True, bg=bg).pack(side="left", padx=(6, 0))
        self._adv.append((ui.row(card, t("th_thresholds"), row, t("th_steps_hint"), help="th_steps"), row))
        self._adv_card, self._adv_parts = card, None
        self._show_adv()
        self.rc = ui.add_check(ui.checks, self.idx, self.step_entries, lambda: self.mode != "stepped" or self.parse_steps() is not None)

    def _show_adv(self):
        """The automatic / threshold rows only exist for the stepped mode: hidden for smooth."""
        body = self._adv_card.body
        if self._adv_parts is None:                      # first call: everything is still shown, remember the pieces
            parts = []
            for left, widget in self._adv:
                r = int(left.grid_info().get("row", 0))
                parts += [left, widget] + list(body.grid_slaves(row=r - 1))      # + the divider above the row
            self._adv_parts = parts
            self._adv_shown = True
        want = self.mode == "stepped"
        if want == self._adv_shown:
            return
        self._adv_shown = want
        for w in self._adv_parts:
            try:
                w.grid() if want else w.grid_remove()
            except tk.TclError:
                pass

    # ------------------------------------------------------------ actions
    def pick_theme(self, name):
        self.name, self.ov = name, {}
        self.sync()

    def reset_overrides(self):
        self.ov = {}
        self.sync()

    def reset_token(self, tok):
        if self.ov.pop(tok, None) is not None:
            self.sync()

    def pick_color(self, tok):
        cur = self.style()[tok]
        res = colorchooser.askcolor(color=cur, parent=self.ui.win, title=t("th_pick_color"))
        c = themes.norm_color(res[1]) if res and res[1] else None
        if c:
            self.set_token(tok, c)

    def on_slider(self, key, v):
        if self._lock:
            return
        v = float(v)
        self.set_token(key, int(round(v)) if key == "radius" else round(v, 2))

    def on_font(self, tok, cb):
        i = cb.current()
        self.set_token(tok, "" if i <= 0 else families()[i - 1])

    def on_mode(self, e=None):
        self.mode = self.mode_vals[max(0, self.cb_mode.current())]
        self.sync()

    def on_auto(self):
        if not self.v_auto.get() and not any(v.get().strip() for v in self.step_vars):
            for v, x in zip(self.step_vars, DEFAULT_STEPS + [""]):
                v.set(self.su._fmt_num(x) if x != "" else "")
        self.on_steps()

    def parse_steps(self):
        """[] for automatic, a valid list of 2-4 ascending percentages, or None (invalid entry)."""
        if self.v_auto.get():
            return []
        xs = []
        for v in self.step_vars:
            s = v.get().strip()
            if not s:
                continue
            try:
                xs.append(self.su._to_float(s))
            except ValueError:
                return None
        if not 2 <= len(xs) <= 4 or any(not 0 < x < 100 for x in xs) or any(a >= b for a, b in zip(xs, xs[1:])):
            return None
        return [int(x) if x == int(x) else round(x, 1) for x in xs]

    def on_steps(self):
        st = self.parse_steps()
        for e in self.step_entries:
            try:
                e.config(state="disabled" if self.v_auto.get() else "normal")
            except tk.TclError:
                return
        self.rc()
        if st is not None:
            self.steps = st
            self.draw_preview()

    # ------------------------------------------------------------ keep widgets and preview in sync
    def sync(self):
        sty = self.style()
        self._lock = True
        try:
            for tok, b in self.swatch.items():
                b.set(sty[tok], tok in self.ov)
            self.sc_radius.set(sty.radius)
            self.sc_row_spacing.set(sty.spacing)
            self.vl["radius"].config(text=f"{sty.radius} px")
            self.vl["row_spacing"].config(text=f"× {sty.spacing:.2f}")
            fams = families()
            for tok, cb in self.fcombo.items():
                f = sty[tok]
                cb.current(fams.index(f) + 1 if f in fams else 0)
            self.cb_mode.current(self.mode_vals.index(self.mode))
        finally:
            self._lock = False
        for n in self.cards:
            self.draw_card(n)
        self._show_adv()
        self.on_steps()
        self.draw_preview()

    # ------------------------------------------------------------ theme cards
    def draw_card(self, name, hover=False):
        cv = self.cards[name]
        cv.delete("all")
        th = themes.Style(name)
        P = th.palette()
        C = self.C
        sel = name == self.name
        w, h = CARD_W, CARD_H
        W, H = w + 4, h + 26
        ox = 2
        imgs = self._cardimgs[name] = []

        def pic(im, x, y):
            if im is None:
                return False
            imgs.append(im)
            cv.create_image(x, y, image=im, anchor="nw")
            return True
        oc = C["ui"] if sel else (C["dim"] if hover else C["border"])
        ok = pic(uikit.rr(W, H, 11, themes.mix(C["card"], C["bg"], 0.5), oc, 2 if sel else 1), 0, 0)
        r = min(10, max(2, th.radius * 0.55))
        if ok:
            pic(uikit.rr(W - 6, h - 1, 8, P["bg"]), 3, 3)
            pic(uikit.rr(w - 13, h - 14, r, P["card"], P["border"]), ox + 8, 9)
            pic(uikit.rr(3, max(4, h - 14 - 2 * r), 1.5, P["gauge"]), ox + 9, 9 + r)
        else:
            cv.create_rectangle(0, 0, w + 3, h + 25, outline=oc, width=2 if sel else 1)
            cv.create_rectangle(ox + 1, 3, ox + w, h + 2, fill=P["bg"], outline="")
            cv.create_rectangle(ox + 8, 9, ox + w - 7, h - 5, fill=P["card"], outline=P["border"])
            cv.create_line(ox + 9.5, 9 + r, ox + 9.5, h - 5 - r, fill=P["gauge"], width=2)
        cv.create_oval(ox + 16, 14, ox + 24, 22, fill=themes.mix(P["card"], P["gauge"], 0.35), outline="")
        cv.create_rectangle(ox + 28, 16, ox + 28 + 34, 20, fill=P["text"], outline="")
        for i, (nw, fr) in enumerate(((30, 0.15), (38, 0.55), (26, 0.9))):
            y = 27 + i * 12
            if i % 2:
                cv.create_rectangle(ox + 12, y - 2, ox + w - 11, y + 10, fill=P["stripe"], outline="")
            cv.create_rectangle(ox + 16, y + 2, ox + 16 + nw, y + 5, fill=P["name"], outline="")
            bx0, bx1 = ox + w - 62, ox + w - 18
            cv.create_rectangle(bx0, y + 1, bx1, y + 6, fill=P["track"], outline="")
            cv.create_rectangle(bx0, y + 1, bx0 + (bx1 - bx0) * max(0.18, fr), y + 6, fill=th.lut[round(fr * 255)],
                                outline="")
        cv.create_text(W / 2, h + 14, text=themes.theme_label(name), fill=C["fg"] if sel else C["dim"],
                       font=(self.ui.F_TXT, -11, "bold" if sel else "normal"))

    # ------------------------------------------------------------ live preview
    def _rr(self, x0, y0, x1, y1, r, fill, outline=None, accent=None):
        w, h = int(round(x1 - x0)), int(round(y1 - y0))
        if w < 3 or h < 3:
            return
        r = max(0, min(r, h / 2, w / 2))
        cv = self.pv
        if gfx.AVAILABLE:
            im = gfx.card(w, h, r, fill, outline or fill, accent)
            self._imgs.append(im)
            cv.create_image(int(round(x0)), int(round(y0)), image=im, anchor="nw")
            return
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0,
               y1 - r, x0, y0 + r, x0, y0]
        cv.create_polygon(pts, smooth=True, fill=fill, outline=outline or fill)
        if accent:
            cv.create_line(x0 + 1.5, y0 + r, x0 + 1.5, y1 - r, fill=accent, width=3)

    def _img(self, im, x, y, anchor="nw"):
        self._imgs.append(im)
        self.pv.create_image(x, y, image=im, anchor=anchor)

    def draw_preview(self):
        cv = self.pv
        try:
            W = max(cv.winfo_width(), 300)
        except tk.TclError:
            return
        cv.delete("all")
        self._imgs = []
        sty = self.style()
        P = sty.palette()
        H = PREVIEW_H
        cv.configure(bg=self.C["bg"])
        self._rr(0, 0, W, H, 12, P["bg"], P["border"])
        m = 12
        R, sp = sty.radius, sty.spacing
        # ---- section card
        cw = min(int((W - 3 * m) * 0.56), 400)
        rh, hh = 20.0, 32.0
        rp = rh * sp
        ch = hh + 4 * rp + 8
        x0, y0 = m, m
        accent = P["temp"]
        self._rr(x0, y0, x0 + cw, y0 + ch, R, P["card"], P["border"], accent)
        isz = 6.5
        hy = y0 + hh / 2
        if gfx.AVAILABLE:
            self._img(gfx.icon("temp", isz, accent, themes.mix(P["card"], accent, 0.16), themes.mix(P["card"], accent, 0.16)),
                      x0 + 14 + isz * 1.6, hy, "center")
        cv.create_text(x0 + 14 + isz * 3.9, hy, text=t("sec_temp"), anchor="w", font=_head(sty, 12), fill=P["fg"])
        lblc = themes.mix(P["dim"], accent, 0.45)
        nx, mx_ = x0 + cw * 0.74, x0 + cw - 14
        cv.create_text(nx, hy, text=t("col_now"), anchor="e", font=_txt(sty, 9, True), fill=lblc)
        cv.create_text(mx_, hy, text=t("col_max"), anchor="e", font=_txt(sty, 9, True), fill=lblc)
        cv.create_line(x0 + 9, y0 + hh - 1, x0 + cw - 9, y0 + hh - 1, fill=P["border"])
        rows = (("cpu_usage", 14, 31, "%", (0, 100)), ("gpu_temp", 66, 74, "°C", (30, 88)),
                ("cpu_pkg_power", 128, 141, "W", (10, 150)), ("gpu_hot", 98, 101, "°C", (35, 100)))
        inset = max(6.0, R * 0.4)
        ry = y0 + hh
        for i, (key, v, mxv, unit, lim) in enumerate(rows):
            if i % 2:
                cv.create_rectangle(x0 + inset, ry, x0 + cw - inset, ry + rp, fill=P["stripe"], outline="")
            my = ry + rp / 2
            cv.create_text(x0 + 14, my, text=t(key), anchor="w", font=_txt(sty, 11), fill=P["name"])
            col = sty.value_color(lim, v, P["fg"])
            bx0, bx1 = x0 + cw * 0.40, x0 + cw * 0.56
            bh = max(4, rh * 0.22)
            self._rr(bx0, my - bh / 2, bx1, my + bh / 2, bh / 2, P["track"])
            fr = min(1.0, max(0.0, (v - lim[0]) / (lim[1] - lim[0])))
            self._rr(bx0, my - bh / 2, bx0 + (bx1 - bx0) * max(fr, 0.06), my + bh / 2, bh / 2, col)
            cv.create_text(nx - 14, my, text=str(v), anchor="e", font=_num(sty, 12), fill=col)
            cv.create_text(nx - 10, my, text=unit, anchor="w", font=_txt(sty, 9), fill=P["faint"])
            cv.create_text(mx_, my, text=str(mxv), anchor="e", font=_num(sty, 12),
                           fill=themes.mix(sty.value_color(lim, mxv, P["fg"]), P["card"], 0.18))
            ry += rp
        # ---- right column: accents, value strip, status bar
        x1 = x0 + cw + m
        rw = W - x1 - m
        if rw < 180:
            return
        self._rr(x1, m, x1 + rw, m + 36, R, P["card"], P["border"])
        s = 6.0
        for i, icon in enumerate(("gauge", "bolt", "temp", "fan", "net", "fps")):
            c = P[icon]
            cx = x1 + rw * (i + 0.5) / 6
            if gfx.AVAILABLE:
                bub = themes.mix(P["card"], c, 0.16)
                self._img(gfx.icon(icon, s, c, bub, bub), cx, m + 18, "center")
            else:
                cv.create_oval(cx - 9, m + 9, cx + 9, m + 27, fill=c, outline="")
        sy = m + 36 + 8
        self._rr(x1, sy, x1 + rw, sy + 52, R, P["card"], P["border"])
        sw = int(rw - 28)
        sx = x1 + 14
        colors = [sty.color_at(i / max(1, sw - 1)) for i in range(sw)]
        im = _strip_image(sw, 12, colors)
        if im:
            self._img(im, sx, sy + 10)
        else:
            for i in range(0, sw, 2):
                cv.create_rectangle(sx + i, sy + 10, sx + i + 2, sy + 22, fill=colors[i], outline="")
        tf = _txt(sty, 8)
        cv.create_text(sx, sy + 32, text="0 %", anchor="w", font=tf, fill=P["dim"])
        cv.create_text(sx + sw, sy + 32, text="100 %", anchor="e", font=tf, fill=P["dim"])
        if self.mode == "stepped":
            for th_ in sty.thresholds:
                xx = sx + th_ * (sw - 1)
                cv.create_line(xx, sy + 8, xx, sy + 24, fill=P["card"], width=2)
                cv.create_text(xx, sy + 32, text=f"{th_ * 100:g}", font=tf, fill=P["dim"])
        by = sy + 52 + 8
        bh = H - m - by
        if bh < 20:
            return
        self._rr(x1, by, x1 + rw, by + bh, R, P["card"], P["border"])
        mid = by + bh / 2
        ph = min(bh - 8, 20)
        lbl = t("log_off")
        f = _txt(sty, 10, True)
        pw = tkfont.Font(family=f[0], size=f[1], weight="bold").measure(lbl) + ph * 1.4
        if gfx.AVAILABLE:
            self._img(gfx.pill(pw, ph, themes.mix(P["card"], P["red"], 0.18), P["red"], 6 + ph * 0.3, ph * 0.17),
                      x1 + 10, mid - ph / 2)
        cv.create_text(x1 + 10 + ph * 0.9, mid, text=lbl, anchor="w", font=f, fill=P["fg"])
        # switch + toast
        tx = x1 + 10 + pw + 12
        if gfx.AVAILABLE and tx + 40 < x1 + rw:
            tx, ty = int(round(tx)), int(round(mid - 8))
            self._img(gfx.track(34, 16, themes.mix(P["track"], P["ui"], 0.55), P["border"]), tx, ty)
            self._img(gfx.knob(5.0, P["fg"]), tx + 34 - 5 - 3, ty + 8, "center")      # centre of the "all" end
            toast = "✓ " + t("reset_done")
            tfn = _txt(sty, 9, True)
            tw = tkfont.Font(family=tfn[0], size=tfn[1], weight="bold").measure(toast) + 22
            tx2 = tx + 34 + 12
            if tx2 + tw < x1 + rw - 6:
                th2 = 18
                self._img(gfx.card(tw, th2, th2 / 2, themes.mix(P["card"], P["green"], 0.22),
                                   themes.mix(P["card"], P["green"], 0.6)), tx2, mid - th2 / 2)
                cv.create_text(tx2 + 11, mid, text=toast, anchor="w", font=tfn, fill=P["green"])
