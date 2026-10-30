"""Recording viewer: opens the CSV recordings of the log folder as charts (no Excel / LibreOffice needed).

    logview.show(api, folder=None, path=None, at=None)   open / raise the window (at = seconds, see logdata.to_secs)
    logview.add_button(sw, parent, folder=None)           "View recordings" button for the settings tabs

Layout (same look as the settings window: uikit cards, buttons, switches):
    toolbar    recording picker (date, length, size, tags; opens a searchable list), statistics table toggle,
               remove markers
    left       series: search, quick picks (Temperatures, Power, ... Show all), a tree hardware group (CPU, GPU,
               RAM, Disk, Network, ...) > kind > series with tri-state group tick boxes, programs at the cursor
    centre     chart (min-max envelope per pixel column for any number of rows, smooth curves, at most two axes
               (left / right), inline legend, hover balloon) and a searchable min / avg / max / cursor table
Mouse wheel = zoom, drag = pan, click = pin a marker, double click = show everything. Data work lives in
logdata.py; crash markers come from crashscan."""
import gc
import math
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_left, bisect_right
from datetime import datetime
from tkinter import filedialog

import config
import crashscan
import gfx
import helptexts
import i18n
import logdata
import themes
import tooltip
import uikit
from i18n import t

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:                                    # chart falls back to canvas items
    Image = None

i18n.register({
    "en": {
        "lv_title": "Recording viewer", "lv_open": "View recordings", "lv_open_at": "Open in viewer",
        "lv_recs": "Recordings", "lv_search": "Search...", "lv_series_search": "Search series...",
        "lv_series": "Series", "lv_hide_series": "Series", "lv_clear": "Clear", "lv_all": "Show all",
        "lv_p_temp": "Temperatures", "lv_p_power": "Power", "lv_p_usage": "Usage", "lv_p_clock": "Clocks",
        "lv_p_fan": "Fans", "lv_p_fps": "FPS",
        "lv_cat_temp": "Temperatures", "lv_cat_clock": "Clocks", "lv_cat_power": "Power", "lv_cat_usage": "Usage / load",
        "lv_cat_fan": "Fans", "lv_cat_volt": "Voltage and current", "lv_cat_io": "Network and disk",
        "lv_cat_mem": "Memory and data", "lv_cat_fps": "FPS and frame time", "lv_cat_prog": "Program columns",
        "lv_cat_other": "Other",
        "lv_comp_cpu": "CPU", "lv_comp_gpu": "GPU", "lv_comp_ram": "Memory (RAM)", "lv_comp_disk": "Disk",
        "lv_comp_net": "Network", "lv_comp_mb": "Motherboard and system", "lv_comp_fan": "Fans",
        "lv_comp_fps": "FPS and frame time", "lv_comp_prog": "Programs", "lv_comp_other": "Other",
        "lv_no_rec": "No recording", "lv_pick_rec": "Choose a recording",
        "lv_stats": "Statistics table", "lv_stats_tip": "Show or hide the table of min / avg / max values",
        "lv_clear_pins": "Remove markers",
        "lv_tip_check": "Select / unselect every series of this group. Other groups are not touched.",
        "lv_tip_open": "Only opens or closes the list. Your selection stays as it is.",
        "lv_tip_preset": "Adds this group's main series. Press again to remove exactly those.",
        "lv_tip_all": "Draws every series of the groups you already have series from and shows the whole range. "
                      "Press again to remove just those.",
        "lv_tip_clear": "Unselect all series", "lv_no_match": "No series matches.",
        "lv_tip_prog": "Show or hide the programs running at the cursor",
        "lv_sel_of": "{a} of {b}",
        "lv_loading": "Loading...", "lv_empty": "Pick a recording above.",
        "lv_nosel": "Pick a quick group or tick series on the left.", "lv_no_recs": "No recordings in this folder.",
        "lv_col_min": "Min", "lv_col_avg": "Avg", "lv_col_max": "Max", "lv_col_cursor": "Cursor", "lv_col_pin": "Pin",
        "lv_gap": "Gap {d}", "lv_abrupt": "Ended abruptly", "lv_live": "live", "lv_prog": "programs",
        "lv_rows": "{n} rows", "lv_row1": "1 row", "lv_selected": "{n} selected",
        "lv_range": "{a} - {b}   ({d})   {n} rows", "lv_delta": "Pins: {d}",
        "lv_hint": "Wheel: zoom   Drag: pan   Click: pin   Double click: reset",
        "lv_programs": "Programs", "lv_prog_none": "No program data at this moment.",
        "lv_src_side": "process file", "lv_src_top": "top programs of the recording",
        "lv_browse": "Open file...", "lv_refresh": "Refresh",
        "lv_err": "Could not read the recording: {e}", "lv_more": "+{n} more", "lv_stride": "every {k}th row shown",
        "lv_gap_lbl": "Gap {d}", "lv_end_lbl": "Ended abruptly", "lv_pick_file": "Recording (CSV)",
        "lv_no_data": "No data in this range.", "lv_sort": "Sort by",
        "hp_lv_nav": "Mouse wheel zooms around the pointer, dragging moves the view, a click pins a marker (click it "
                     "again to remove it) and a double click shows the whole recording. Shift + wheel, the arrow keys "
                     "and + / - work too.",
        "hp_lv_recs": "The recording picker lists your recordings, newest first. A red tag means the recording "
                      "ended abruptly (crash, freeze or power loss), a yellow one means rows are missing in "
                      "between. Green means it is still being written.",
        "hp_lv_series": "Tick the series to draw. A group's tick box selects or clears only the series of that "
                        "group and the arrow only folds the list: neither touches your other picks. The round "
                        "buttons add a ready-made set of main series (press again to remove just those). "
                        "Series with the same unit share an axis. The first unit gets the left axis and the "
                        "second unit the right axis; further units are still drawn (each on its own scale) but "
                        "have no ruler, so read them from the hover balloon or the statistics table.",
    },
    "tr": {
        "lv_title": "Kayıt görüntüleyici", "lv_open": "Kayıtları görüntüle", "lv_open_at": "Görüntüleyicide aç",
        "lv_recs": "Kayıtlar", "lv_search": "Ara...", "lv_series_search": "Seri ara...",
        "lv_series": "Seriler", "lv_hide_series": "Seriler", "lv_clear": "Temizle", "lv_all": "Tümünü göster",
        "lv_p_temp": "Sıcaklıklar", "lv_p_power": "Güç", "lv_p_usage": "Kullanım", "lv_p_clock": "Saatler",
        "lv_p_fan": "Fanlar", "lv_p_fps": "FPS",
        "lv_cat_temp": "Sıcaklıklar", "lv_cat_clock": "Saat hızları", "lv_cat_power": "Güç", "lv_cat_usage": "Kullanım / yük",
        "lv_cat_fan": "Fanlar", "lv_cat_volt": "Voltaj ve akım", "lv_cat_io": "Ağ ve disk",
        "lv_cat_mem": "Bellek ve veri", "lv_cat_fps": "FPS ve kare süresi", "lv_cat_prog": "Program sütunları",
        "lv_cat_other": "Diğer",
        "lv_comp_cpu": "CPU", "lv_comp_gpu": "GPU", "lv_comp_ram": "Bellek (RAM)", "lv_comp_disk": "Disk",
        "lv_comp_net": "Ağ", "lv_comp_mb": "Anakart ve sistem", "lv_comp_fan": "Fanlar",
        "lv_comp_fps": "FPS ve kare süresi", "lv_comp_prog": "Programlar", "lv_comp_other": "Diğer",
        "lv_no_rec": "Kayıt yok", "lv_pick_rec": "Kayıt seçin",
        "lv_stats": "İstatistik tablosu", "lv_stats_tip": "En düşük / ortalama / en yüksek tablosunu göster ya da gizle",
        "lv_clear_pins": "İşaretleri kaldır",
        "lv_tip_check": "Bu gruptaki tüm serileri seçer ya da bırakır. Diğer gruplara dokunmaz.",
        "lv_tip_open": "Yalnızca listeyi açar ya da kapatır. Seçimin olduğu gibi kalır.",
        "lv_tip_preset": "Bu grubun başlıca serilerini ekler. Tekrar basınca yalnızca onları kaldırır.",
        "lv_tip_all": "Serisi seçili olan grupların tüm serilerini çizer ve kaydın tamamını gösterir. "
                      "Tekrar basınca yalnızca bunları kaldırır.",
        "lv_tip_clear": "Tüm seçimleri kaldır", "lv_no_match": "Eşleşen seri yok.",
        "lv_tip_prog": "İmlecin olduğu andaki programları göster ya da gizle",
        "lv_sel_of": "{a} / {b}",
        "lv_loading": "Yükleniyor...", "lv_empty": "Yukarıdan bir kayıt seçin.",
        "lv_nosel": "Soldan hazır bir grup seçin ya da seri işaretleyin.",
        "lv_no_recs": "Bu klasörde kayıt yok.",
        "lv_col_min": "En düşük", "lv_col_avg": "Ort.", "lv_col_max": "En yüksek", "lv_col_cursor": "İmleç", "lv_col_pin": "İşaret",
        "lv_gap": "Boşluk {d}", "lv_abrupt": "Ani bitti", "lv_live": "canlı", "lv_prog": "programlar",
        "lv_rows": "{n} satır", "lv_row1": "1 satır", "lv_selected": "{n} seçili",
        "lv_range": "{a} - {b}   ({d})   {n} satır", "lv_delta": "İşaretler: {d}",
        "lv_hint": "Tekerlek: yakınlaştır   Sürükle: kaydır   Tıkla: işaretle   Çift tık: sıfırla",
        "lv_programs": "Programlar", "lv_prog_none": "Bu anda program verisi yok.",
        "lv_src_side": "işlem dosyası", "lv_src_top": "kaydın en çok çalışan programları",
        "lv_browse": "Dosya aç...", "lv_refresh": "Yenile",
        "lv_err": "Kayıt okunamadı: {e}", "lv_more": "+{n} tane daha", "lv_stride": "her {k}. satır gösteriliyor",
        "lv_gap_lbl": "Boşluk {d}", "lv_end_lbl": "Ani bitti", "lv_pick_file": "Kayıt (CSV)",
        "lv_no_data": "Bu aralıkta veri yok.", "lv_sort": "Sırala",
        "hp_lv_nav": "Tekerlek imlecin olduğu yeri yakınlaştırır, sürükleme görünümü kaydırır, tıklama bir işaret "
                     "bırakır (tekrar tıklayınca kalkar), çift tıklama kaydın tamamını gösterir. Shift + tekerlek, "
                     "ok tuşları ve + / - da çalışır.",
        "hp_lv_recs": "Kayıt seçici, kayıtlarını en yenisi üstte olacak şekilde listeler. Kırmızı etiket "
                      "kaydın ani bittiğini (çökme, donma ya da elektrik kesintisi), sarı etiket arada satır "
                      "eksik olduğunu gösterir. Yeşil: kayıt hâlâ yazılıyor.",
        "hp_lv_series": "Çizilecek serileri işaretle. Bir grubun kutusu yalnızca o gruptaki serileri seçer ya "
                        "da bırakır, ok ise sadece listeyi açıp kapatır: ikisi de diğer seçimlerine dokunmaz. "
                        "Yuvarlak düğmeler hazır bir ana seri takımı ekler (tekrar basınca yalnızca onları "
                        "kaldırır). Birimi aynı olan seriler aynı ekseni paylaşır. İlk birim sol, ikinci birim "
                        "sağ ekseni alır; diğer birimler yine çizilir (her biri kendi ölçeğinde) ama cetveli "
                        "olmaz: değerlerini imleç balonundan ya da istatistik tablosundan oku.",
    },
})

_bg = uikit._bg_of
PRESET_KEYS = ("temp", "power", "usage", "clock", "fan", "fps")
FLOATING = ("°C", "MHz", "GHz", "V", "mV", "x")
UNITS = {"CPU": "%", "RAM": "MB", "GPU": "%", "VRAM": "MB", "Disk": "MB/s"}
_STATE = {"win": None}


# ====================================================================== small helpers
def _comma():
    return i18n.language() == "tr"


def fnum(v, unit="", decimals=None):
    """Value with a sensible number of decimals (and the language's decimal sign)."""
    if v is None or v != v:
        return "-"
    a = abs(v)
    if decimals is None:
        decimals = 0 if a >= 1000 else 1 if a >= 10 else 2 if a >= 1 else 3
        if unit == "V":
            decimals = 3
        elif unit in ("RPM", "MHz") and a >= 100:
            decimals = 0
        elif unit in ("°C", "%", "W", "FPS"):
            decimals = 0 if a >= 1000 else 1 if a >= 1 else 2
    s = f"{v:.{decimals}f}"
    if s.startswith("-") and float(s) == 0:
        s = s[1:]
    if _comma():
        s = s.replace(".", ",")
    return f"{s} {unit}".strip() if unit else s


def fmt_mb(v):
    if v is None or v != v:
        return "-"
    return fnum(v / 1024.0, "GB") if v >= 1024 else fnum(v, "MB", 0 if v >= 100 else 1)


def fmt_size(n):
    for u, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k:
            return fnum(n / k, u, 1)
    return f"{n} B"


def fmt_clock(secs, full=False):
    return logdata.from_secs(secs).strftime("%Y-%m-%d %H:%M:%S" if full else "%H:%M:%S")


def rows_text(n):
    return t("lv_row1") if n == 1 else t("lv_rows", n=fmt_count(n))


def fmt_count(n):
    s = f"{n:,}"
    return s.replace(",", "." if _comma() else ",")


def nice_step(span, target):
    raw = span / max(1, target)
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw * 0.999:
            return m * mag
    return 10 * mag


TIME_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
              172800, 604800)


def time_ticks(t0, t1, width):
    raw = (t1 - t0) / max(2.0, width / 110.0)
    step = next((s for s in TIME_STEPS if s >= raw), TIME_STEPS[-1])
    first = math.ceil(t0 / step) * step
    return step, [first + k * step for k in range(int((t1 - first) // step) + 1)]


def unit_range(lo, hi, unit, h):
    """Axis range and tick values for data between lo and hi."""
    if hi - lo < 1e-9:
        pad = abs(hi) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    if unit in FLOATING:                      # zero says nothing for these: fit the data instead
        pad = (hi - lo) * 0.06
        lo, hi = lo - pad, hi + pad
    elif lo >= 0 and lo < 0.45 * hi:
        lo = 0.0
    if unit == "%" and lo >= 0 and hi > 55:
        hi = max(hi, 100.0) if hi <= 100 else hi
    step = nice_step(hi - lo, max(2, h / 52))
    nlo, nhi = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    if lo >= 0 > nlo:
        nlo = 0.0
    n = int(round((nhi - nlo) / step))
    return nlo, nhi, [nlo + k * step for k in range(n + 1)], step


def fit_text(text, font, maxw):
    if maxw <= 8:
        return ""
    if font.measure(text) <= maxw:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= maxw:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def rgba(c, a=255):
    r, g, b = themes.rgb(c)
    return (r, g, b, a)


def series_colors(C):
    """Distinct line colours from the active theme (stable order; variants when more than 8 series)."""
    base = [C["accent_clock"], C["accent_temp"], C["accent_power"], C["accent_net"], C["accent_fan"],
            C["accent_fps"], C["orange"], C["grad2"]]
    seen, out = set(), []
    for c in base:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    out += [themes.mix(c, C["fg"], 0.42) for c in out[:8]]
    out += [themes.mix(c, C["bg"], 0.45) for c in out[:8]]
    return out


def smooth_pts(pts, step=3.0, max_sub=24):
    """Monotone cubic (Fritsch-Carlson) curve through the points (x ascending): extra points between samples that
    are further apart than `step` pixels. Between two samples the curve never leaves their min..max, so spikes and
    flat runs are not overshot; narrow intervals (many samples per pixel) are returned untouched."""
    n = len(pts)
    if n < 3:
        return list(pts)
    if any(pts[i + 1][0] - pts[i][0] <= 1e-9 for i in range(n - 1)):      # equal timestamps: a vertical step, so
        out, run = [], [pts[0]]                                           # smooth every strictly rising run alone
        for p in pts[1:]:
            if p[0] - run[-1][0] <= 1e-9:
                out += smooth_pts(run, step, max_sub)
                run = []
            run.append(p)
        return out + smooth_pts(run, step, max_sub)
    h = [pts[i + 1][0] - pts[i][0] for i in range(n - 1)]
    d = [(pts[i + 1][1] - pts[i][1]) / h[i] for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for k in range(n - 1):                                     # keep each interval monotone
        if d[k] == 0:
            m[k] = m[k + 1] = 0.0
            continue
        a, b = m[k] / d[k], m[k + 1] / d[k]
        r = a * a + b * b
        if r > 9:
            tau = 3 / math.sqrt(r)
            m[k], m[k + 1] = tau * a * d[k], tau * b * d[k]
    out = [pts[0]]
    for i in range(n - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        sub = min(max_sub, int(h[i] / step))
        for j in range(1, sub):
            u = j / sub
            u2, u3 = u * u, u * u * u
            y = ((2 * u3 - 3 * u2 + 1) * y0 + (u3 - 2 * u2 + u) * h[i] * m[i]
                 + (3 * u2 - 2 * u3) * y1 + (u3 - u2) * h[i] * m[i + 1])
            out.append((x0 + h[i] * u, min(max(y, min(y0, y1)), max(y0, y1))))
        out.append((x1, y1))
    return out


# ====================================================================== painters
class PilPainter:
    """Everything the chart paints (background, bands, lines) into one RGB image at 2x, reduced to the canvas size:
    smooth lines and real transparency for the min/max bands."""
    SS = 2

    def __init__(self, cv, w, h, bg):
        self.cv, self.w, self.h = cv, w, h
        S = self.SS
        self.im = Image.new("RGB", (w * S, h * S), bg)
        self.d = ImageDraw.Draw(self.im, "RGBA")

    def rect(self, x0, y0, x1, y1, fill, alpha=255):
        S = self.SS
        if x1 > x0 and y1 > y0:
            self.d.rectangle([round(x0 * S), round(y0 * S), round(x1 * S) - 1, round(y1 * S) - 1],
                             fill=rgba(fill, alpha))

    def hline(self, x0, x1, y, col, alpha=255):
        self.rect(x0, y, x1, y + 1, col, alpha)

    def vline(self, x, y0, y1, col, alpha=255):
        self.rect(x, y0, x + 1, y1, col, alpha)

    def polyline(self, pts, col, width=1.6, smooth=False):
        if len(pts) < 2:
            return
        if smooth:
            pts = smooth_pts(pts)
        S = self.SS
        self.d.line([v * S for p in pts for v in p], fill=rgba(col), width=max(1, round(width * S)), joint="curve")

    def polygon(self, pts, col, alpha):
        if len(pts) >= 3:
            S = self.SS
            self.d.polygon([v * S for p in pts for v in p], fill=rgba(col, alpha))

    def dots(self, pts, r, col):
        S = self.SS
        for x, y in pts:
            self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=rgba(col))

    def finish(self):
        small = self.im.reduce(self.SS)
        self.cv._lv_photo = ImageTk.PhotoImage(small)             # the canvas must keep the image alive
        self.cv.create_image(0, 0, image=self.cv._lv_photo, anchor="nw", tags=("gfx",))


class CanvasPainter:
    """Fallback without Pillow: plain canvas items (transparency is pre-mixed with the plot colour)."""

    def __init__(self, cv, w, h, bg, under=None):
        self.cv, self.w, self.h, self.bg = cv, w, h, bg
        self.under = under or bg

    def rect(self, x0, y0, x1, y1, fill, alpha=255):
        if x1 > x0 and y1 > y0:
            c = fill if alpha >= 255 else themes.mix(self.under, fill, alpha / 255.0)
            self.cv.create_rectangle(x0, y0, x1, y1, fill=c, width=0)

    def hline(self, x0, x1, y, col, alpha=255):
        self.cv.create_line(x0, y + 0.5, x1, y + 0.5, fill=themes.mix(self.under, col, alpha / 255.0))

    def vline(self, x, y0, y1, col, alpha=255):
        self.cv.create_line(x + 0.5, y0, x + 0.5, y1, fill=themes.mix(self.under, col, alpha / 255.0))

    def polyline(self, pts, col, width=1.6, smooth=False):
        if len(pts) >= 2:
            if smooth:
                pts = smooth_pts(pts)
            self.cv.create_line(*[v for p in pts for v in p], fill=col, width=width, joinstyle="round",
                                capstyle="round")

    def polygon(self, pts, col, alpha):
        if len(pts) >= 3:
            self.cv.create_polygon(*[v for p in pts for v in p], fill=themes.mix(self.under, col, alpha / 255.0),
                                   outline="")

    def dots(self, pts, r, col):
        for x, y in pts:
            self.cv.create_oval(x - r, y - r, x + r, y + r, fill=col, width=0)

    def finish(self):
        pass


# ====================================================================== list widget (virtual rows)
class VList(tk.Frame):
    """Scrolling list of fixed-height rows drawn on a canvas: fast with hundreds of rows, themed scrollbar."""

    def __init__(self, parent, C, row_h, draw, click=None, hover=None, bg=None):
        bg = bg or C["card"]
        super().__init__(parent, bg=bg)
        self.C, self.row_h, self.draw_row, self.on_click, self.on_hover = C, row_h, draw, click, hover
        self.rows, self.off, self.hot, self._drag, self.empty = [], 0, None, None, ""
        self.sb = tk.Canvas(self, width=10, bg=bg, highlightthickness=0, bd=0)
        self.sb.pack(side="right", fill="y")
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack(side="left", fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self._set_hot(None))
        self.cv.bind("<Button-1>", self._click)
        self.sb.bind("<ButtonPress-1>", self._sb_press)
        self.sb.bind("<B1-Motion>", self._sb_drag)
        self.sb.bind("<Configure>", lambda e: self.redraw())

    # ---- model
    def set_rows(self, rows, keep=True):
        self.rows = rows
        if not keep:
            self.off = 0
        self._clamp()
        self.redraw()

    def total(self):
        return len(self.rows) * self.row_h

    def _clamp(self):
        self.off = max(0, min(self.off, max(0, self.total() - self.cv.winfo_height())))

    def scroll(self, delta):
        self.off -= delta / 120.0 * self.row_h * 1.6
        self._clamp()
        self.redraw()

    def see(self, i):
        y0, y1 = i * self.row_h, (i + 1) * self.row_h
        h = self.cv.winfo_height()
        if y0 < self.off:
            self.off = y0
        elif y1 > self.off + h:
            self.off = y1 - h
        self._clamp()
        self.redraw()

    # ---- drawing
    def redraw(self):
        cv = self.cv
        try:
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
        except tk.TclError:
            return
        self._clamp()
        first = int(self.off // self.row_h)
        n = len(self.rows)
        y = first * self.row_h - self.off
        i = first
        while i < n and y < h:
            self.draw_row(cv, i, self.rows[i], y, w, i == self.hot)
            y += self.row_h
            i += 1
        if not n and self.empty:
            cv.create_text(14, 16, text=self.empty, anchor="nw", fill=self.C["dim"], font=("Segoe UI", 9), width=max(40, w - 28))
        self._draw_sb(h)

    def _draw_sb(self, h):
        sb, C = self.sb, self.C
        sb.delete("all")
        tot = self.total()
        if tot <= h or h <= 0:
            return
        sh = max(28, h * h / tot)
        top = (h - sh) * (self.off / (tot - h))
        sb.create_rectangle(3, top, 8, top + sh, fill=C["border"] if self._drag is None else C["faint"], width=0)

    def _sb_press(self, e):
        h, tot = self.cv.winfo_height(), self.total()
        if tot <= h:
            return
        sh = max(28, h * h / tot)
        top = (h - sh) * (self.off / (tot - h))
        if top <= e.y <= top + sh:
            self._drag = (e.y, self.off)
        else:
            self.off = (e.y - sh / 2) / (h - sh) * (tot - h)
            self._clamp()
            self.redraw()

    def _sb_drag(self, e):
        if not self._drag:
            return
        h, tot = self.cv.winfo_height(), self.total()
        sh = max(28, h * h / tot)
        self.off = self._drag[1] + (e.y - self._drag[0]) / max(1, (h - sh)) * (tot - h)
        self._clamp()
        self.redraw()

    # ---- mouse
    def index_at(self, y):
        i = int((y + self.off) // self.row_h)
        return i if 0 <= i < len(self.rows) else None

    def _set_hot(self, i):
        if i != self.hot:
            self.hot = i
            self.redraw()
            if self.on_hover:
                self.on_hover(i)

    def _motion(self, e):
        self._set_hot(self.index_at(e.y))

    def _click(self, e):
        i = self.index_at(e.y)
        if i is not None and self.on_click:
            self.on_click(i, self.rows[i], e)


# ====================================================================== the hover balloon of the chart
class ChartTip(tooltip.Tooltip):
    """tooltip.Tooltip that follows the crosshair: timestamp + one coloured line per series."""

    def __init__(self, widget):
        super().__init__(widget, None, delay_ms=0, _bind=False)
        self.head, self.lines, self._built = "", [], None

    def update(self, head, lines, xroot, yroot):
        self.head, self.lines, self._xy = head, lines, (xroot, yroot)
        self.show()

    def show(self):
        self._after = None
        p = themes.palette()
        if self.tip is None:
            self.tip = tk.Toplevel(self.w)
            self.tip.withdraw()                       # shown by _place, once it has its position
            self.tip.wm_overrideredirect(True)
            try:
                self.tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            self._built = None
        shape = len(self.lines)
        if self._built != shape:
            for ch in self.tip.winfo_children():
                ch.destroy()
            outer = tk.Frame(self.tip, bg=p["border"], bd=0)
            outer.pack()
            body = tk.Frame(outer, bg=p["card"], padx=10, pady=7)
            body.pack(padx=1, pady=1)
            fnt = tkfont.nametofont("TkDefaultFont").copy()
            fnt.configure(size=max(9, abs(fnt.cget("size"))))
            bold = fnt.copy()
            bold.configure(weight="bold")
            self.head_lbl = tk.Label(body, text="", bg=p["card"], fg=p["fg"], font=bold, anchor="w")
            self.head_lbl.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 3))
            self.cells = []
            for k in range(shape):
                chip = tk.Canvas(body, width=10, height=10, bg=p["card"], highlightthickness=0)
                chip.grid(row=k + 1, column=0, sticky="w", padx=(0, 7))
                name = tk.Label(body, text="", bg=p["card"], fg=p["name"], font=fnt, anchor="w")
                name.grid(row=k + 1, column=1, sticky="w")
                val = tk.Label(body, text="", bg=p["card"], fg=p["fg"], font=fnt, anchor="e")
                val.grid(row=k + 1, column=2, sticky="e", padx=(16, 0))
                self.cells.append((chip, name, val))
            self._built = shape
        self.head_lbl.config(text=self.head)
        for (chip, name, val), (col, nm, vs) in zip(self.cells, self.lines):
            chip.delete("all")
            if col:
                chip.create_oval(1, 1, 9, 9, fill=col, width=0)
            name.config(text=nm, fg=p["name"] if col else p["dim"])
            val.config(text=vs)
        self._place()

    def _place(self):
        if not self.tip:
            return
        self.tip.update_idletasks()
        w, h = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        x, y = self._xy
        import uikit
        vx, vy, vr, vb = uikit.monitor_work(x, y, self.w)       # the monitor under the pointer (may be negative)
        gx, gy = x + 18, y + 16
        if gx + w > vr - 6 and x - w - 14 > vx:
            gx = x - w - 14                      # flip to the left of the pointer near the screen edge
        if gy + h > vb - 6:
            gy = max(vy + 4, y - h - 10)
        self.tip.geometry(f"+{int(gx)}+{int(gy)}")
        if self.tip.state() == "withdrawn":
            self.tip.deiconify()


# ====================================================================== widgets of the viewer window
P = uikit.px


def hover_tip(widget, cell_at, text_of):
    """Tooltip whose text depends on what is under the pointer: cell_at(x, y) -> key (None = nothing),
    text_of(key) -> text or rows. The balloon is re-armed whenever the pointer moves to another key."""
    tip = tooltip.Tooltip(widget, None, _bind=False)
    st = {"k": None}
    tip.content = lambda: text_of(st["k"]) if st["k"] is not None else ""

    def enter(e):
        st["k"] = cell_at(e.x, e.y)
        tip._enter(e)

    def motion(e):
        k = cell_at(e.x, e.y)
        if k != st["k"]:
            st["k"] = k
            tip._leave()
            tip._enter(e)
    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", lambda e: tip._leave(), add="+")
    widget.bind("<Motion>", motion, add="+")
    widget.bind("<ButtonPress>", lambda e: tip._leave(), add="+")
    return tip


class FillCard(uikit.Card):
    """uikit.Card that stretches over the space it is given (the stock card is only as tall as its content)."""

    def __init__(self, parent, C, width=None, **kw):
        self._fixed_w = width
        super().__init__(parent, C, **kw)
        self.body.pack_configure(fill="both", expand=True)

    def _inner_cfg(self, e=None):
        tk.Canvas.configure(self, height=P(60), width=self._fixed_w or P(120))

    def _cfg(self, e):
        if (e.width, e.height) == (self._cw, self._ch):
            return
        self._cw, self._ch = e.width, e.height
        self.itemconfigure("inner", width=max(10, e.width - self.pl - self.pr),
                           height=max(10, e.height - self.pt - self.pb))
        if self._job is None:
            self._job = self.after_idle(self._paint_job)


class ChipBar(uikit._Cv):
    """Wrapping row of pill buttons. chips = [(key, text)]; set_states({key: 'on' | 'off' | 'dis'}); command(key)."""

    def __init__(self, parent, C, font, chips, command, bg=None):
        self.C, self.font, self.chips, self.cmd = C, font, list(chips), command
        self.fo = uikit.font_obj(parent, font)
        self.ch, self.gap = P(26), P(6)
        super().__init__(parent, bg=bg, height=self.ch)
        self.states = {k: "off" for k, _ in self.chips}
        self.hot = None
        self.rects = []
        self._rows = 1
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", lambda e: self._set_hot(self.key_at(e.x, e.y)))
        self.bind("<Leave>", lambda e: self._set_hot(None))
        self.bind("<Button-1>", self._click)

    def set_states(self, states):
        if states != self.states:
            self.states = dict(states)
            self.draw()

    def key_at(self, x, y):
        for k, x0, y0, w in self.rects:
            if x0 <= x <= x0 + w and y0 <= y <= y0 + self.ch:
                return k
        return None

    def _set_hot(self, k):
        if k != self.hot:
            self.hot = k
            tk.Canvas.configure(self, cursor="hand2" if k and self.states.get(k) != "dis" else "")
            self.draw()

    def _click(self, e):
        k = self.key_at(e.x, e.y)
        if k and self.states.get(k) != "dis":
            self.cmd(k)

    def draw(self):
        C = self.C
        W = max(40, self.winfo_width())
        self.delete("all")
        self._refs = []
        self.rects = []
        x = y = 0
        rows = 1
        for k, text in self.chips:
            w = self.fo.measure(text) + P(22)
            if x + w > W and x > 0:
                x, y, rows = 0, y + self.ch + self.gap, rows + 1
            self.rects.append((k, x, y, w))
            st = self.states.get(k, "off")
            hot = k == self.hot and st != "dis"
            if st == "on":
                fill, oc, fg = (C["ui_hover"] if hot else C["ui"]), None, C["on_ui"]
            elif st == "dis":
                fill, oc, fg = themes.mix(C["track"], C["card"], 0.5), None, C["faint"]
            else:
                fill = themes.mix(C["track"], C["fg"], 0.10) if hot else C["track"]
                oc, fg = themes.mix(C["border"], C["dim"], 0.6) if hot else C["border"], C["fg"]
            self.put_rr(x, y, w, self.ch, self.ch / 2, fill, oc)
            self.create_text(x + w / 2, y + self.ch / 2, text=text, fill=fg, font=self.font)
            x += w + self.gap
        want = rows * self.ch + (rows - 1) * self.gap
        if rows != self._rows:
            self._rows = rows
            tk.Canvas.configure(self, height=want)


class RecPicker(uikit._Cv):
    """The recording picker of the toolbar: date, length, size and tags of the open recording; click for the list."""

    def __init__(self, viewer):
        self.v = viewer
        self.C = viewer.C
        self.h = P(34)
        super().__init__(viewer.toolbar, bg=viewer.C["bg"], height=self.h, takefocus=1, cursor="hand2")
        self.hot = self.focused = False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._st("hot", True))
        self.bind("<Leave>", lambda e: self._st("hot", False))
        self.bind("<FocusIn>", lambda e: self._st("focused", True))
        self.bind("<FocusOut>", lambda e: self._st("focused", False))
        self.bind("<Button-1>", lambda e: (self.focus_set(), viewer.toggle_rec_popup()))
        for k in ("<Return>", "<space>", "<Down>"):
            self.bind(k, lambda e: viewer.toggle_rec_popup())

    def _st(self, a, v):
        if getattr(self, a) != v:
            setattr(self, a, v)
            self.draw()

    def draw(self):
        v, C = self.v, self.C
        self.delete("all")
        self._refs = []
        W, H = max(40, self.winfo_width()), self.h
        pop = v.pop is not None
        hi = self.focused or pop
        oc = C["ui"] if hi else (themes.mix(C["border"], C["dim"], 0.55) if self.hot else C["border"])
        self.put_rr(0, 0, W, H, P(8), C["track"], oc, 2 if hi else 1)
        title, detail, badges = v.rec_summary()
        x = P(12)
        right = W - P(34)
        for text, col in reversed(badges):                         # tags, right-aligned (only what fits)
            tw = v.f9.measure(text) + P(12)
            if right - tw < x + P(150):
                break
            self.put_rr(right - tw, (H - P(18)) / 2, tw, P(18), P(5), themes.mix(C["track"], col, 0.22),
                        tags="chip")
            self.create_text(right - tw / 2, H / 2, text=text, fill=col, font=(v.F_TXT, 8))
            right -= tw + P(6)
        self.create_text(x, H / 2, text=title, anchor="w", fill=C["fg"], font=v.f10b)
        x += v.f10b.measure(title) + P(12)
        if detail:
            self.create_text(x, H / 2, text=fit_text(detail, v.f9, right - x - P(6)), anchor="w", fill=C["dim"],
                             font=v.f9)
        im = uikit.glyph("chev_down", P(14), C["fg"] if (self.hot or pop) else C["dim"])
        if im is not None:
            self.create_image(W - P(16), H / 2, image=self.keep(im))
        else:
            self.create_text(W - P(16), H / 2, text="▾", fill=C["dim"], font=v.f10)


# ====================================================================== the viewer


class Viewer:
    PAD_L, PAD_R, PAD_B, AXIS_W = 62, 16, 40, 58

    def __init__(self, api, folder, autoload=True):
        self.api = api
        self.folder = folder
        self.autoload = autoload
        self.C = themes.palette()
        self.palette_key = tuple(sorted(self.C.items()))
        try:
            self.F_TXT, self.F_HEAD, self.F_NUM = api.fonts_now()
        except Exception:
            self.F_TXT, self.F_HEAD, self.F_NUM = "Segoe UI", "Segoe UI Semibold", "Consolas"
        self.win = tk.Toplevel(api.root)
        self.win.withdraw()
        uikit.set_scale(self.win)
        self.f9 = tkfont.Font(family=self.F_TXT, size=9)
        self.f10 = tkfont.Font(family=self.F_TXT, size=10)
        self.f10b = tkfont.Font(family=self.F_TXT, size=10, weight="bold")
        self.f9b = tkfont.Font(family=self.F_TXT, size=9, weight="bold")
        self.fnum9 = tkfont.Font(family=self.F_NUM, size=9)
        self.PAD_L, self.PAD_R, self.PAD_B, self.AXIS_W = P(62), P(16), P(40), P(58)
        self.colors = series_colors(self.C)
        self.rec, self.sel, self.slot, self._keys, self.presets = None, [], {}, [], {}
        self.f0 = self.f1 = self.v0 = self.v1 = 0.0
        self.pins, self.hover_t, self.hover_i = [], None, None
        self.hl = None                                 # series picked in the statistics table (emphasised in the chart)
        self._strip_list = []                          # rows the statistics table shows now (after the search filter)
        self._draw_job = self._load_job = self._ens_job = self._probe_job = self._note_job = None
        self._jobs, self._traces, self._tips = set(), [], []
        self._closed = False
        self.paths, self.probes, self.cur_path = [], {}, None
        self.rec_rows = []
        self.pq = queue.Queue()
        self._probe_state = [0]
        self.probe_gen = 0
        self.expanded, self.last_pick_key = set(), "temp"
        self._all_added = set()                        # series that "Show all" added (so that pressing it again removes just those)
        self._preset_added = {}                        # quick pick -> the series it added
        self.drag = None
        self.strip_off = 0
        self._pending_at = None
        self.msg = self.note = ""
        self.pop = self.rlist = None                   # the recording list popup
        self._pop_closed_at = 0.0
        self.prog_open = True
        self._prog_user = None                         # None: the programs list folds itself on a small window
        self._prog_shown = False
        self.strip_rows = 5
        self._adapt_job = None

        self.win.title(t("lv_title"))
        self.win.configure(bg=self.C["bg"])
        uikit.set_window_icon(self.win)
        try:
            self.win.attributes("-topmost", bool(api.root.attributes("-topmost")))     # stay above a topmost panel
        except tk.TclError:
            pass
        self._place()
        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self._escape())
        self.win.bind("<F5>", lambda e: self.reload_list())
        self.win.bind("<Configure>", self._on_configure, add="+")
        self.win.bind("<MouseWheel>", self._wheel_any)
        for k, fn in (("<Home>", self.reset_view), ("<Left>", lambda: self.pan_by(-0.2)),
                      ("<Right>", lambda: self.pan_by(0.2)), ("<plus>", lambda: self.zoom_by(0.7)),
                      ("<minus>", lambda: self.zoom_by(1 / 0.7)), ("<KP_Add>", lambda: self.zoom_by(0.7)),
                      ("<KP_Subtract>", lambda: self.zoom_by(1 / 0.7))):
            self.win.bind(k, lambda e, fn=fn: None if isinstance(self.win.focus_get(), tk.Entry) else fn())
        self._after(30, self._dark_title)
        self.win.deiconify()
        self.reload_list()

    @property
    def probe_gen(self):
        return self._probe_state[0]

    @probe_gen.setter
    def probe_gen(self, v):
        self._probe_state[0] = v

    # ------------------------------------------------------------ scheduled work (everything is cancelled on close)
    def _after(self, ms, fn):
        box = []

        def run():
            self._jobs.discard(box[0])
            if not self._closed:
                fn()
        try:
            box.append(self.win.after(ms, run))
        except tk.TclError:
            return None
        self._jobs.add(box[0])
        return box[0]

    def _idle(self, fn):
        box = []

        def run():
            self._jobs.discard(box[0])
            if not self._closed:
                fn()
        try:
            box.append(self.win.after_idle(run))
        except tk.TclError:
            return None
        self._jobs.add(box[0])
        return box[0]

    def _cancel(self, job):
        if job is not None:
            self._jobs.discard(job)
            try:
                self.win.after_cancel(job)
            except tk.TclError:
                pass

    # ------------------------------------------------------------ window
    def _dark_title(self):
        try:
            import ctypes
            hwnd = int(self.win.wm_frame(), 16)
            val = ctypes.c_int(1 if themes.current().is_dark() else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        except Exception:
            pass

    def _place(self):
        """Open on the monitor under the pointer; a narrow / portrait one (the panel's side display) is skipped."""
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        mon = None
        try:
            mons = config.monitors()
            px, py = self.win.winfo_pointerxy()
            mon = next((m for m in mons if m["x"] <= px < m["x"] + m["w"] and m["y"] <= py < m["y"] + m["h"]), None)
            if mon is None or mon["ww"] < 1150 or mon["wh"] > mon["ww"]:
                mon = next((m for m in mons if m["primary"] and m["ww"] >= 1150), None)
                if mon is None and mons:
                    mon = max(mons, key=lambda m: m["ww"] * m["wh"])
        except Exception:
            mon = None
        mx, my, mw, mh = (mon["wx"], mon["wy"], mon["ww"], mon["wh"]) if mon else (0, 0, sw, sh)
        w, h = min(P(1400), mw - 40), min(P(860), mh - 70)
        self.win.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + max(10, (mh - h) // 3)}")
        self.win.minsize(min(P(1060), w), min(P(600), h))
        self.prog_open = h >= P(760)                   # on a small window the programs list starts folded

    def alive(self):
        if self._closed:
            return False
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _escape(self):
        if self.pop is not None:
            self.close_rec_popup()
        elif self.hl is not None:
            self.hl = None
            self.request_draw()
            self.draw_strip()
        else:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.probe_gen += 1
        for j in list(self._jobs):
            try:
                self.win.after_cancel(j)
            except tk.TclError:
                pass
        self._jobs.clear()
        self.close_rec_popup()
        for tp in [self.tip, self.ptip] + self._tips:
            try:
                tp._leave()
            except Exception:
                pass
        for var, tid in self._traces:                    # a trace is a Tcl command that keeps this viewer alive
            try:
                var.trace_remove("write", tid)
            except (tk.TclError, ValueError):
                pass
        self._traces.clear()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        uikit.forget_fonts(self.win)
        if _STATE["win"] is self:
            _STATE["win"] = None
        try:                                                  # let the Tk variables / fonts of this window go on the main thread
            self.api.root.after(300, gc.collect)
        except (tk.TclError, AttributeError):
            pass

    # ------------------------------------------------------------ widgets
    def _btn(self, parent, text, cmd, primary=False, small=False, bg=None, font_size=10):
        return uikit.Button(parent, self.C, text, cmd, (self.F_TXT, font_size, "bold" if primary else "normal"),
                            primary=primary, small=small, bg=bg or _bg(parent))

    def _search_field(self, parent, var, ph, bg):
        """Rounded search box with a placeholder; Escape clears it."""
        C = self.C
        f = uikit.entry(parent, C, var, 10, (self.F_TXT, 10), bg=bg)
        e = f.entry
        hint = tk.Label(e, text=ph, bg=f.fill, fg=C["faint"], font=(self.F_TXT, 10), cursor="xterm")
        hint.bind("<Button-1>", lambda ev: e.focus_set())

        def upd(*a):
            if var.get():
                hint.place_forget()
            else:
                hint.place(x=0, rely=0.5, anchor="w")
        self._traces.append((var, var.trace_add("write", upd)))
        upd()
        e.bind("<Escape>", lambda ev: (var.set(""), "break")[1])
        return f

    def _tipped(self, widget, text):
        tp = tooltip.Tooltip(widget, text)
        self._tips.append(tp)
        return tp

    def _build(self):
        C, win = self.C, self.win
        card = C["card"]
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(2, weight=1)

        # ---- toolbar: recording picker | statistics table toggle, remove markers
        tb = self.toolbar = tk.Frame(win, bg=C["bg"])
        tb.grid(row=0, column=0, sticky="ew", padx=P(16), pady=(P(14), P(10)))
        tb.grid_columnconfigure(0, weight=1, minsize=P(250))
        self.picker = RecPicker(self)
        self.picker.grid(row=0, column=0, sticky="ew", padx=(0, P(14)))
        self.v_stats = tk.BooleanVar(value=True)
        self.stats_btn = self._btn(tb, t("lv_stats"), self.toggle_stats, primary=True)
        self.stats_btn.grid(row=0, column=1, padx=(0, P(8)))
        self._tipped(self.stats_btn, t("lv_stats_tip"))
        self._btn(tb, t("lv_clear_pins"), self.clear_pins).grid(row=0, column=2)

        # ---- body: series sidebar | chart + statistics
        body = tk.Frame(win, bg=C["bg"])
        body.grid(row=2, column=0, sticky="nsew", padx=P(16), pady=(0, P(16)))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # sidebar
        self.side = FillCard(body, C, width=P(310), fill=card, pad=(P(14), P(14), P(12), P(12)), bg=C["bg"])
        self.side.grid(row=0, column=0, sticky="ns", padx=(0, P(12)))
        sb = self.side.body
        ph = tk.Frame(sb, bg=card)
        ph.pack(fill="x")
        tk.Label(ph, text=t("lv_series"), bg=card, fg=C["fg"], font=(self.F_HEAD, 11, "bold")).pack(side="left")
        self.sel_count = tk.Label(ph, text="", bg=card, fg=C["dim"], font=(self.F_TXT, 9))
        self.sel_count.pack(side="left", padx=(8, 0), pady=(2, 0))
        helptexts.info_icon(ph, "lv_series", bg=card).pack(side="right")
        self.clear_btn = self._btn(ph, t("lv_clear"), self.clear_sel, small=True, bg=card)
        self.clear_btn.pack(side="right", padx=(0, P(8)))
        self._tipped(self.clear_btn, t("lv_tip_clear"))
        self.v_ssearch = tk.StringVar()
        self._traces.append((self.v_ssearch, self.v_ssearch.trace_add("write", lambda *a: self.rebuild_pick_rows())))
        self._search_field(sb, self.v_ssearch, t("lv_series_search"), card).pack(fill="x", pady=(P(10), P(8)))
        chips = [(k, t("lv_p_" + k)) for k in PRESET_KEYS] + [("all", t("lv_all"))]
        self.chips = ChipBar(sb, C, (self.F_TXT, 9), chips, self._chip, bg=card)
        self.chips.pack(fill="x", pady=(0, P(8)))
        hover_tip(self.chips, lambda x, y: self.chips.key_at(x, y),
                  lambda k: t("lv_tip_all") if k == "all" else t("lv_tip_preset"))
        # programs at the cursor (packed above the series list while the recording has any)
        self.prog = tk.Frame(sb, bg=card)
        ph2 = self.prog_head = tk.Frame(self.prog, bg=card, cursor="hand2")
        ph2.pack(fill="x", pady=(P(8), P(4)))
        self.prog_chev = tk.Label(ph2, text="▾", bg=card, fg=C["dim"], font=(self.F_TXT, 10), cursor="hand2")
        self.prog_chev.pack(side="left")
        self.prog_title = tk.Label(ph2, text=t("lv_programs"), bg=card, fg=C["fg"], font=(self.F_HEAD, 11, "bold"),
                                   cursor="hand2")
        self.prog_title.pack(side="left", padx=(6, 0))
        self.prog_time = tk.Label(ph2, text="", bg=card, fg=C["dim"], font=(self.F_TXT, 9), cursor="hand2")
        self.prog_time.pack(side="left", padx=(8, 0), pady=(2, 0))
        for w in (ph2, self.prog_chev, self.prog_title, self.prog_time):
            w.bind("<Button-1>", lambda e: self.toggle_prog())
        self._tipped(self.prog_title, t("lv_tip_prog"))
        self.prog_body = tk.Frame(self.prog, bg=card, height=P(230))
        self.prog_body.pack_propagate(False)
        self.sort_bar = tk.Frame(self.prog_body, bg=card)
        self.sort_bar.pack(fill="x", pady=(0, P(4)))
        self.sort_metric, self.sort_btns = "CPU", {}
        for m in logdata.Recording.METRICS:
            b = tk.Label(self.sort_bar, text=m, bg=C["track"], fg=C["dim"], font=(self.F_TXT, 8), padx=7, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, m=m: self.set_sort(m))
            self.sort_btns[m] = b
        self.prog_rows, self.prog_src = [], None
        self.pglist = VList(self.prog_body, C, P(38), self._draw_prog, bg=card)
        self.pglist.pack(fill="both", expand=True)
        # the series list
        self.plist = VList(sb, C, P(28), self._draw_pick, self._click_pick, None, bg=card)
        self.plist.pack(fill="both", expand=True)
        self.plist.empty = t("lv_no_match")
        self.ptip = hover_tip(self.plist.cv, self._pick_cell, self._pick_tip)
        self._tips.append(self.ptip)

        # chart card and the statistics table below it
        main = tk.Frame(body, bg=C["bg"])
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.chart_card = FillCard(main, C, fill=card, pad=(P(10), P(10), P(10), P(8)), bg=C["bg"])
        self.chart_card.grid(row=0, column=0, sticky="nsew")
        foot = tk.Frame(self.chart_card.body, bg=card)
        foot.pack(side="bottom", fill="x", pady=(P(6), 0))
        self.info_l = tk.Label(foot, text="", bg=card, fg=C["name"], font=(self.F_TXT, 9), anchor="w")
        self.info_l.pack(side="left", padx=(P(4), 0))
        helptexts.info_icon(foot, "lv_nav", bg=card).pack(side="right")
        self.cv = tk.Canvas(self.chart_card.body, bg=card, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.request_draw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", self._leave)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Double-Button-1>", self._double)
        self.tip = ChartTip(self.cv)
        self.stats_card = uikit.Card(main, C, fill=card, pad=(P(10), P(10), P(6), P(6)), bg=C["bg"])
        self.stats_card.grid(row=1, column=0, sticky="ew", pady=(P(12), 0))
        sh = tk.Frame(self.stats_card.body, bg=card)                 # search box above the table
        sh.pack(fill="x", pady=(0, P(4)))
        self.v_stsearch = tk.StringVar()
        self._traces.append((self.v_stsearch, self.v_stsearch.trace_add("write", self._stats_search)))
        sf = self._search_field(sh, self.v_stsearch, t("lv_series_search"), card)
        sf.config(width=P(260))
        sf.pack(side="left")
        self.strip = tk.Canvas(self.stats_card.body, height=P(34), bg=card, highlightthickness=0, bd=0)
        self.strip.pack(fill="x")
        self.strip.bind("<Configure>", lambda e: self.draw_strip())
        self.strip.bind("<Button-1>", self._strip_click)
        self.stats_card.grid_remove()
        self._stats_on = False

    def toggle_stats(self):
        on = not self.v_stats.get()
        self.v_stats.set(on)
        self.stats_btn.primary = on                                # lit while the table is shown
        self.stats_btn.draw()
        self.request_draw()

    def _stats_search(self, *a):
        self.strip_off = 0
        self.draw_strip()

    def clear_pins(self):
        self.pins = []
        self.request_draw()

    def flash(self, text, secs=5.0):
        """A short note in the info line under the chart."""
        self.note = text
        self._cancel(self._note_job)
        self._note_job = self._after(int(secs * 1000), self._unflash)
        self._update_info()

    def _unflash(self):
        self._note_job = None
        self.note = ""
        self._update_info()

    # ------------------------------------------------------------ wheel routing
    def _wheel_any(self, e):
        try:
            w = self.win.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            return
        while w is not None:
            if w is self.cv:
                return self._wheel_chart(e)
            if w is self.strip:
                self.strip_off = max(0, self.strip_off + (-1 if e.delta > 0 else 1))
                return self.draw_strip()
            for lst in (self.plist, self.pglist):
                if w is lst or w is lst.cv:
                    lst.scroll(e.delta)
                    return
            w = getattr(w, "master", None)

    # ------------------------------------------------------------ recordings list
    def reload_list(self):
        self.paths = logdata.list_recordings(self.folder)
        self.probe_gen += 1
        gen = self.probe_gen
        todo = list(self.paths)
        pq, state = self.pq, self._probe_state                  # (the worker must not hold on to the viewer itself)

        def work():
            for p in todo:
                if gen != state[0]:
                    return
                pq.put((gen, p, logdata.probe(p)))
        threading.Thread(target=work, daemon=True).start()
        self.rebuild_rec_rows()
        self._cancel(self._probe_job)
        self._probe_job = self._after(60, self._poll_probe)
        try:
            if self.rec is not None and self.cur_path and os.path.getsize(self.cur_path) != self.rec.size:
                self.open_path(self.cur_path, keep_view=True)           # the recording is still being written
        except OSError:
            pass
        if self.autoload and self.rec is None and self.paths and self.cur_path is None:
            self.open_path(self.paths[0])
        self.picker.draw()

    def _poll_probe(self):
        self._probe_job = None
        if not self.alive():
            return
        changed = False
        try:
            while True:
                gen, p, d = self.pq.get_nowait()
                if gen == self.probe_gen:
                    self.probes[p] = d
                    changed = True
        except queue.Empty:
            pass
        if changed:
            self.picker.draw()
            if self.rlist is not None:
                self.rlist.redraw()
        if any(p not in self.probes for p in self.paths):
            self._probe_job = self._after(120, self._poll_probe)

    def rebuild_rec_rows(self):
        q = (self.v_rsearch.get() if self.pop is not None else "").strip().lower()
        rows = []
        for p in self.paths:
            if q:
                d = self.probes.get(p, {})
                hay = os.path.basename(p).lower() + " " + (fmt_clock(d["start"], True) if d.get("start") else "")
                if q not in hay:
                    continue
            rows.append(p)
        self.rec_rows = rows
        if self.rlist is not None:
            self.rlist.empty = t("lv_no_recs")
            self.rlist.set_rows(rows)

    def _rec_time(self, p, d):
        if d.get("start"):
            return d["start"]
        try:
            return logdata.to_secs(datetime.strptime(os.path.basename(p)[:19], "%Y-%m-%d_%H-%M-%S"))
        except ValueError:
            try:
                return logdata.to_secs(datetime.fromtimestamp(os.path.getmtime(p)))
            except OSError:
                return 0.0

    def _badges(self, d):
        C = self.C
        out = []
        if d.get("abrupt"):
            out.append((t("lv_abrupt"), C["red"]))
        if d.get("gaps"):
            n = len(d["gaps"])
            out.append((t("lv_gap", d=f"{n}x" if n > 1 else crashscan.fmt_dur(d["gaps"][0][1] - d["gaps"][0][0])),
                        C["yellow"]))
        if d.get("live"):
            out.append((t("lv_live"), C["green"]))
        if d.get("side"):
            out.append((t("lv_prog"), C["accent_net"]))
        return out

    def rec_summary(self):
        """(date, 'length · size', tags) of the open recording for the toolbar picker."""
        p = self.cur_path
        if not p:
            return (t("lv_pick_rec") if self.paths else t("lv_no_rec")), "", []
        d = self.probes.get(p, {})
        rec = self.rec if self.rec is not None and self.rec.path == p and len(self.rec) else None
        title = fmt_clock(self._rec_time(p, d), True)[:16]
        bits = []
        if rec is not None:
            bits.append(crashscan.fmt_dur(rec.end - rec.start))
        elif d.get("start") and d.get("end"):
            bits.append(crashscan.fmt_dur(d["end"] - d["start"]))
        try:
            bits.append(fmt_size(d.get("size") or os.path.getsize(p)))
        except OSError:
            pass
        badges = self._badges(d)
        if rec is not None:                                    # the loaded file knows more than the quick probe
            badges = self._badges({"abrupt": rec.abrupt, "gaps": rec.gaps, "live": rec.live,
                                   "side": os.path.exists(logdata.side_path(rec.path))})
        return title, "  ·  ".join(bits), badges

    def _draw_rec(self, cv, i, p, y, w, hot):
        C = self.C
        RH = self.rlist.row_h
        d = self.probes.get(p, {})
        if p == self.cur_path:
            cv.create_rectangle(P(4), y + 1, w - P(4), y + RH - 1, fill=C["select"], width=0)
            cv.create_rectangle(P(4), y + P(6), P(7), y + RH - P(6), fill=C["ui"], width=0)
        elif hot:
            cv.create_rectangle(P(4), y + 1, w - P(4), y + RH - 1, fill=C["stripe"], width=0)
        cv.create_line(P(12), y + RH - 1, w - P(12), y + RH - 1, fill=C["border"])
        cv.create_text(P(16), y + P(13), text=fmt_clock(self._rec_time(p, d), True)[:16], anchor="w", fill=C["fg"],
                       font=self.f10b)
        if d.get("start") and d.get("end"):
            cv.create_text(w - P(14), y + P(13), text=crashscan.fmt_dur(d["end"] - d["start"]), anchor="e",
                           fill=C["name"], font=self.f9)
        sub = []
        try:
            sub.append(fmt_size(d.get("size") or os.path.getsize(p)))
        except OSError:
            pass
        if d.get("rows"):
            sub.append(rows_text(d["rows"]))
        cv.create_text(P(16), y + P(33), text="  ·  ".join(sub) if sub else "...", anchor="w", fill=C["dim"],
                       font=self.f9)
        x = P(16)
        for text, col in self._badges(d):
            tw = self.f9.measure(text) + P(12)
            if x + tw > w - P(10):
                break
            cv.create_rectangle(x, y + P(43), x + tw, y + P(57), fill=themes.mix(C["card"], col, 0.2), width=0)
            cv.create_text(x + tw / 2, y + P(50), text=text, fill=col, font=(self.F_TXT, 8))
            x += tw + P(5)

    def _click_rec(self, i, p, e):
        self.close_rec_popup()
        self.open_path(p)

    # ---- the popup with the list of recordings
    def toggle_rec_popup(self):
        if self.pop is not None:
            self.close_rec_popup()
        elif time.time() - self._pop_closed_at > 0.25:
            self.open_rec_popup()

    def open_rec_popup(self):
        if self.pop is not None:
            return
        C = self.C
        pk = self.picker
        W = max(pk.winfo_width(), P(430))
        rows_vis = max(1, min(6, len(self.paths) or 1))
        lh = rows_vis * P(62)
        pop = tk.Toplevel(self.win)
        self.pop = pop
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(bg=C["border"])
        try:
            pop.attributes("-topmost", bool(self.win.attributes("-topmost")))
        except tk.TclError:
            pass
        inner = tk.Frame(pop, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.v_rsearch = tk.StringVar()
        self._rsearch_trace = self.v_rsearch.trace_add("write", lambda *a: self.rebuild_rec_rows())
        self._traces.append((self.v_rsearch, self._rsearch_trace))
        sf = self._search_field(inner, self.v_rsearch, t("lv_search"), C["card"])
        sf.pack(fill="x", padx=P(10), pady=(P(10), P(6)))
        self.rlist = VList(inner, C, P(62), self._draw_rec, self._click_rec, None, bg=C["card"])
        self.rlist.cv.configure(height=lh, width=W - P(30))
        self.rlist.pack(fill="x", padx=(P(6), P(6)))
        foot = tk.Frame(inner, bg=C["card"])
        foot.pack(fill="x", padx=P(10), pady=P(8))
        self._btn(foot, t("lv_refresh"), self.reload_list, small=True, bg=C["card"]).pack(side="left")
        self._btn(foot, t("lv_browse"), self._browse_from_popup, small=True, bg=C["card"]).pack(side="left",
                                                                                                padx=(P(6), 0))
        helptexts.info_icon(foot, "lv_recs", bg=C["card"]).pack(side="right")
        tk.Label(foot, text=str(len(self.paths)) if self.paths else "", bg=C["card"], fg=C["dim"],
                 font=(self.F_TXT, 9)).pack(side="right", padx=(0, 8))
        pop.bind("<Escape>", lambda e: self.close_rec_popup())
        pop.bind("<MouseWheel>", lambda e: self.rlist.scroll(e.delta) if self.rlist is not None else None)
        pop.bind("<FocusOut>", lambda e: self._after(80, self._check_pop_focus))
        sf.entry.bind("<Return>", lambda e: self._click_rec(0, self.rec_rows[0], e) if self.rec_rows else None)
        self.rebuild_rec_rows()
        pop.update_idletasks()
        H = pop.winfo_reqheight()
        ml, mt, mr, mb = uikit.monitor_work(pk.winfo_rootx(), pk.winfo_rooty(), pk)
        x, y = pk.winfo_rootx(), pk.winfo_rooty() + pk.winfo_height() + P(4)
        if y + H > mb - 8:
            y = max(mt, pk.winfo_rooty() - H - P(4))
        x = max(ml, min(x, mr - W))
        pop.geometry(f"{W}x{H}+{x}+{y}")
        try:                                                    # Windows 11: rounded corners
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(pop.winfo_id()) or pop.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
        except Exception:
            pass
        pop.deiconify()
        pop.lift()
        pop.focus_force()
        sf.entry.focus_set()
        if self.cur_path in self.rec_rows:
            self.rlist.see(self.rec_rows.index(self.cur_path))
        pk.draw()

    def _check_pop_focus(self):
        try:
            if self.pop is not None and self.pop.focus_displayof() is None:
                self.close_rec_popup()
        except tk.TclError:
            self.pop = self.rlist = None

    def close_rec_popup(self):
        pop, self.pop, self.rlist = self.pop, None, None
        self._pop_closed_at = time.time()
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass
            uikit.forget_fonts(pop)                            # its font cache entries are keyed by its name
            tid = getattr(self, "_rsearch_trace", None)
            keep = []
            for var, t_ in self._traces:                       # the popup's search variable is gone with it
                if t_ == tid:
                    try:
                        var.trace_remove("write", t_)
                    except (tk.TclError, ValueError):
                        pass
                else:
                    keep.append((var, t_))
            self._traces = keep
            try:
                self.picker.draw()
            except tk.TclError:
                pass

    def _browse_from_popup(self):
        self.close_rec_popup()
        self.browse()

    def browse(self):
        p = filedialog.askopenfilename(parent=self.win, initialdir=self.folder if os.path.isdir(self.folder) else None,
                                       filetypes=[(t("lv_pick_file"), "*.csv"), ("*", "*.*")])
        if p:
            self.open_path(os.path.normpath(p))

    # ------------------------------------------------------------ loading a recording
    def open_path(self, path, at=None, span=300.0, keep_view=False):
        if not path or not os.path.exists(path):
            return
        self.cur_path = path
        self._pending_at = (at, span) if at is not None else None
        self._keep = (self.v0, self.v1, list(self.pins)) if keep_view and self.rec is not None else None
        self.msg = t("lv_loading")
        self.tip.hide()
        if self._keep is None:
            self.rec = None
        self.picker.draw()
        self.request_draw()
        hint = config.csv_format(self.api.cfg)[1] if getattr(self.api, "cfg", None) is not None else None
        box = {}

        def work():
            try:
                box["rec"] = rec = logdata.Recording(path, hint)
                rec.load_side()
            except Exception as e:
                box["err"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()

        def poll():
            self._load_job = None
            if not self.alive() or self.cur_path != path:
                return
            if th.is_alive():
                self._load_job = self._after(40, poll)
                return
            if "rec" in box:
                self.set_recording(box["rec"])
            else:
                self.msg = t("lv_err", e=box.get("err", "?"))
                self.request_draw()
        self._cancel(self._load_job)
        self._load_job = self._after(30, poll)

    def set_recording(self, rec):
        if not self._keys and getattr(self.api, "cfg", None) is not None:
            self._keys = list(self.api.cfg.get("viewer_series") or [])
        keep = [rec.header.index(k) for k in self._keys if k in rec.header]
        self.presets = {k: [s.idx for s in logdata.preset(rec, k)] for k in PRESET_KEYS}
        self.rec = rec
        self.msg = ""
        self.sel, self.slot, self.pins = [], {}, []
        self.hl = None
        self._all_added, self._preset_added = set(), {}
        self.hover_t = self.hover_i = None
        self._leave()
        self.f0 = rec.start
        self.f1 = rec.end + max(rec.interval, (rec.end - rec.start) * 0.015) if len(rec) else rec.start + 1
        self.v0, self.v1 = self.f0, self.f1
        self.expanded = set()
        keep = [i for i in keep if i in rec.by_idx]
        if keep:
            self._expand_for(keep)
            self.set_selection(keep)
        elif len(rec):
            first = next((k for k in (self.last_pick_key,) + PRESET_KEYS if self.presets[k]), None)
            if first:
                self.apply_preset(first)
        at, self._pending_at = self._pending_at, None
        keep_view, self._keep = getattr(self, "_keep", None), None
        if at is not None and len(rec):
            self.focus_time(at[0], at[1])
        elif keep_view and len(rec):
            self._set_view(keep_view[0], keep_view[1])
            self.pins = [p for p in keep_view[2] if rec.start <= p <= rec.end]
        self._update_prog_visibility()
        self.rebuild_pick_rows()
        self._mark_presets()
        self.picker.draw()
        self.request_draw()

    def focus_time(self, ts, span=300.0, pin=None):
        """Show a window of `span` seconds around ts and pin a marker at `pin` (default ts)."""
        rec = self.rec
        if rec is None or not len(rec):
            return
        self._leave()
        span = min(span, self.f1 - self.f0)
        lo = min(max(self.f0, ts - span / 2), self.f1 - span)
        self.v0, self.v1 = lo, lo + span
        self.pins = [rec.t[rec.nearest(ts if pin is None else pin)]]
        self.request_draw()

    # ------------------------------------------------------------ series selection
    # A group's tick box only ever touches the series listed in that group; collapsing a group, a quick pick or
    # "Show all" never drops a series that was chosen somewhere else.
    def set_selection(self, idxs):
        rec = self.rec
        self.sel = list(dict.fromkeys(i for i in idxs if rec and i in rec.by_idx))
        self.slot = {i: s for i, s in self.slot.items() if i in self.sel}
        used = set(self.slot.values())
        for i in self.sel:
            if i not in self.slot:
                k = next(n for n in range(len(self.colors) + len(self.sel) + 1) if n not in used)
                self.slot[i] = k
                used.add(k)
        self._keys = [rec.header[i] for i in self.sel] if rec else []
        self._save_sel()
        self.rebuild_pick_rows()
        self._mark_presets()
        self.request_draw()

    def color_of(self, idx):
        return self.colors[self.slot.get(idx, 0) % len(self.colors)]

    def toggle_series(self, idx):
        sel = list(self.sel)
        if idx in sel:
            sel.remove(idx)
        else:
            sel.append(idx)
        self.set_selection(sel)

    def toggle_group(self, members):
        """Tick box of a group: everything listed in it on, or (when all of it was on) off. Nothing else changes."""
        idxs = [s.idx for s in members]
        have = set(self.sel)
        if idxs and all(i in have for i in idxs):
            drop = set(idxs)
            self.set_selection([i for i in self.sel if i not in drop])
        else:
            self.set_selection(list(self.sel) + [i for i in idxs if i not in have])

    def _expand_for(self, idxs):
        by = self.rec.by_idx
        for i in idxs:
            s = by.get(i)
            if s is not None:
                self.expanded.add(s.comp)
                self.expanded.add(f"{s.comp}/{s.cat}")

    def _chip(self, key):
        if key == "all":
            self.show_all()
        else:
            self.apply_preset(key)

    def apply_preset(self, key):
        """Quick pick: adds the group's main series. Pressed again (all of them on) it removes only the ones it
        added itself: a series you had ticked by hand before stays."""
        idxs = self.presets.get(key) if self.rec is not None else None
        if not idxs:
            return
        self.last_pick_key = key
        if set(idxs) <= set(self.sel):
            drop = {i for i in self._preset_added.pop(key, set()) if i in idxs} or set(idxs)
            self.set_selection([i for i in self.sel if i not in drop])
        else:
            self._expand_for(idxs)
            added = [i for i in idxs if i not in self.sel]
            self._preset_added[key] = set(added)
            self.set_selection(list(self.sel) + added)

    def show_all(self):
        """Draws every series of the hardware groups you already have series from and shows the whole range;
        pressed again while those are on, it removes just the series it added."""
        rec = self.rec
        if rec is None:
            return
        if self._all_added and self._all_added <= set(self.sel):
            drop, self._all_added = self._all_added, set()
            self.set_selection([i for i in self.sel if i not in drop])
            return
        comps = {rec.by_idx[i].comp for i in self.sel if i in rec.by_idx}
        if not comps:
            first = next((k for k in (self.last_pick_key,) + PRESET_KEYS if self.presets.get(k)), None)
            comps = {rec.by_idx[i].comp for i in (self.presets.get(first) or [])}
        idxs = [s.idx for s in rec.series if s.comp in comps]
        added = [i for i in idxs if i not in self.sel]
        self._all_added = set(added)
        self._expand_for(idxs)
        self.set_selection(list(self.sel) + added)
        self.reset_view()

    def _save_sel(self):
        cfg = getattr(self.api, "cfg", None)
        if cfg is None or not self._keys:
            return
        try:
            cfg["viewer_series"] = list(self._keys)
            config.save_keys(cfg, "viewer_series")
        except Exception:
            pass

    def clear_sel(self):
        self._all_added = set()
        self.set_selection([])

    def _mark_presets(self):
        states = {}
        for k in PRESET_KEYS:
            ser = self.presets.get(k) if self.rec is not None else None
            states[k] = "dis" if not ser else ("on" if set(ser) <= set(self.sel) else "off")
        states["all"] = ("dis" if self.rec is None else
                         "on" if self._all_added and self._all_added <= set(self.sel) else "off")
        self.chips.set_states(states)

    # ------------------------------------------------------------ the series list (hardware group > kind > series)
    def rebuild_pick_rows(self):
        rec = self.rec
        rows = []
        q = (self.v_ssearch.get() or "").strip().lower()
        if rec is not None:
            groups = {}
            for s in rec.series:
                if q and q not in (s.key + " " + s.label).lower():
                    continue
                groups.setdefault(s.comp, {}).setdefault(s.cat, []).append(s)
            have = set(self.sel)
            for comp in logdata.COMPS:
                cats = groups.get(comp)
                if not cats:
                    continue
                allser = [s for cat in logdata.CATS for s in cats.get(cat, [])]
                nsel = sum(1 for s in allser if s.idx in have)
                open_ = comp in self.expanded or bool(q)
                rows.append(("grp", 0, comp, t("lv_comp_" + comp), len(allser), nsel, open_, allser))
                if not open_:
                    continue
                if len(cats) > 1 and len(allser) > 10:                   # kinds (temperatures, clocks...) as sub-groups
                    for cat in logdata.CATS:
                        lst = cats.get(cat)
                        if not lst:
                            continue
                        key = f"{comp}/{cat}"
                        o2 = key in self.expanded or bool(q)
                        rows.append(("grp", 1, key, t("lv_cat_" + cat), len(lst),
                                     sum(1 for s in lst if s.idx in have), o2, lst))
                        if o2:
                            rows += [("ser", s, 2) for s in lst]
                else:
                    rows += [("ser", s, 1) for s in allser]
        self.plist.empty = t("lv_no_match") if rec is not None else ""
        self.plist.set_rows(rows)
        self.sel_count.config(text=t("lv_selected", n=len(self.sel)) if self.sel else "")

    def _box(self, cv, x, cy, state, col):
        """Tick box: 0 empty, 1 partly (dash), 2 full (check)."""
        C = self.C
        b = P(14)
        y0 = cy - b / 2
        if state == 2:
            cv.create_rectangle(x, y0, x + b, y0 + b, fill=col, outline=col)
            cv.create_line(x + P(3), cy, x + P(6), cy + P(3), x + b - P(3), cy - P(3), fill=C["bg"], width=2)
        elif state == 1:
            cv.create_rectangle(x, y0, x + b, y0 + b, fill=C["track"], outline=col)
            cv.create_rectangle(x + P(3), cy - 1, x + b - P(3), cy + 1, fill=col, outline=col)
        else:
            cv.create_rectangle(x, y0, x + b, y0 + b, fill=C["track"], outline=C["faint"])

    def _draw_pick(self, cv, i, row, y, w, hot):
        C = self.C
        RH = self.plist.row_h
        cy = y + RH / 2
        if hot:
            cv.create_rectangle(P(4), y + 1, w - P(4), y + RH - 1, fill=C["stripe"], width=0)
        if row[0] == "grp":
            _, lvl, key, title, n, nsel, open_, members = row
            if lvl == 0 and i > 0:
                cv.create_line(P(8), y, w - P(8), y, fill=themes.mix(C["card"], C["border"], 0.7))
            cx = P(14 + 14 * lvl)
            pts = ([cx - P(4), cy - P(2), cx + P(4), cy - P(2), cx, cy + P(3)] if open_
                   else [cx - P(2), cy - P(4), cx + P(3), cy, cx - P(2), cy + P(4)])
            cv.create_polygon(*pts, fill=C["dim"], outline="")
            self._box(cv, cx + P(12), cy, 2 if nsel == n else 1 if nsel else 0, C["ui"])
            tx = cx + P(34)
            cv.create_text(tx, cy, text=fit_text(title, self.f10b if lvl == 0 else self.f9b, w - tx - P(54)),
                           anchor="w", fill=C["fg"] if lvl == 0 else C["name"],
                           font=self.f10b if lvl == 0 else self.f9b)
            right = t("lv_sel_of", a=nsel, b=n) if nsel else str(n)
            cv.create_text(w - P(10), cy, text=right, anchor="e", fill=C["ui"] if nsel else C["faint"], font=self.f9)
            return
        s, lvl = row[1], row[2]
        on = s.idx in self.sel
        col = self.color_of(s.idx) if on else None
        bx = P(14 + 14 * lvl)
        self._box(cv, bx, cy, 2 if on else 0, col)
        tx = bx + P(22)
        cv.create_text(tx, cy, text=fit_text(s.label, self.f10, w - tx - P(10)), anchor="w",
                       fill=C["fg"] if on else C["name"], font=self.f10)

    def _pick_cell(self, x, y):
        """(row index, 'chk' | 'open' | 'row') under the pointer of the series list."""
        i = self.plist.index_at(y)
        if i is None:
            return None
        row = self.plist.rows[i]
        if row[0] == "grp":
            box_x = P(14 + 14 * row[1]) + P(12)
            return (i, "chk" if box_x - P(4) <= x <= box_x + P(20) else "open")
        return (i, "row")

    def _pick_tip(self, key):
        i, zone = key
        try:
            row = self.plist.rows[i]
        except IndexError:
            return ""
        if row[0] == "grp":
            if zone == "chk":
                q = (self.v_ssearch.get() or "").strip()
                return t("lv_tip_check") + (f"\n({t('lv_search').rstrip('.')}: {q})" if q else "")
            return t("lv_tip_open")
        s = row[1]
        rows = [(s.name, s.unit, "head")]
        if s.section:
            rows.append((s.section, ""))
        return rows

    def _click_pick(self, i, row, e):
        if row[0] == "grp":
            cell = self._pick_cell(e.x, e.y)
            if cell and cell[1] == "chk":
                self.toggle_group(row[7])
            else:
                key = row[2]
                if key in self.expanded:
                    self.expanded.discard(key)
                else:
                    self.expanded.add(key)
                self.rebuild_pick_rows()
        else:
            self.toggle_series(row[1].idx)

    # ------------------------------------------------------------ programs panel
    def _on_configure(self, e):
        if e.widget is self.win and not self._closed:
            self._cancel(self._adapt_job)
            self._adapt_job = self._after(120, self._adapt)

    def _adapt(self):
        """Window size changed: fold the programs list and shorten the statistics table on a small window."""
        self._adapt_job = None
        h = self.win.winfo_height()
        if self._prog_user is None:
            want = h >= P(760)
            if want != self.prog_open:
                self.prog_open = want
                self._show_prog_body()
                self._prog_i = None
                self.update_programs(self.cursor_row())
        rows = 5 if h >= P(760) else 4 if h >= P(680) else 3
        if rows != self.strip_rows:
            self.strip_rows = rows
            self.draw_strip()

    def toggle_prog(self):
        self.prog_open = not self.prog_open
        self._prog_user = self.prog_open
        self._show_prog_body()
        self._prog_i = None
        self.update_programs(self.cursor_row())

    def _show_prog_body(self):
        if self.prog_open:
            self.prog_body.pack(fill="x")
        else:
            self.prog_body.pack_forget()
        self.prog_chev.config(text="▾" if self.prog_open else "▸")

    def _update_prog_visibility(self):
        has = self.rec is not None and self.rec.has_programs()
        if has and not self._prog_shown:
            self.prog.pack(fill="x", side="bottom", before=self.plist)
            self._prog_shown = True
        elif not has and self._prog_shown:
            self.prog.pack_forget()
            self._prog_shown = False
        if has:
            self._show_prog_body()
            if self.rec.side is None:
                self.rec.load_side()
            present = set(self.rec.top_cols) | ({"CPU", "RAM", "GPU", "VRAM", "Disk"} if self.rec.side["t"] else set())
            for m, b in self.sort_btns.items():
                b.pack_forget()
            for m in logdata.Recording.METRICS:
                if m in present:
                    self.sort_btns[m].pack(side="left", padx=(0, 4))
            if self.sort_metric not in present and present:
                self.sort_metric = next(m for m in logdata.Recording.METRICS if m in present)
            self._style_sort()
            self._prog_i = None
            self.update_programs(self.cursor_row())

    def _style_sort(self):
        C = self.C
        for m, b in self.sort_btns.items():
            on = m == self.sort_metric
            b.config(bg=C["ui"] if on else C["track"], fg=C["on_ui"] if on else C["dim"])

    def set_sort(self, m):
        self.sort_metric = m
        self._style_sort()
        self.update_programs(self.cursor_row())

    def cursor_row(self):
        rec = self.rec
        if rec is None or not len(rec):
            return None
        if self.hover_i is not None:
            return self.hover_i
        if self.pins:
            return rec.nearest(self.pins[-1])
        return rec.nearest((self.v0 + self.v1) / 2)

    def update_programs(self, i):
        rec = self.rec
        if rec is None or i is None or not self._prog_shown or not self.prog_open:
            return
        if getattr(self, "_prog_i", None) == (rec.path, i, self.sort_metric):
            return
        self._prog_i = (rec.path, i, self.sort_metric)
        rows, src = rec.programs_at(i)
        m = self.sort_metric
        rows.sort(key=lambda r: (-(r[1].get(m) if r[1].get(m) is not None else -1.0), r[0].lower()))
        self.prog_rows, self.prog_src = rows, src
        self.prog_time.config(text=fmt_clock(rec.t[i]))
        self.pglist.empty = t("lv_prog_none")
        self.pglist.set_rows(rows[:60], keep=False)

    def _draw_prog(self, cv, i, row, y, w, hot):
        C = self.C
        name, vals = row
        m = self.sort_metric
        main = vals.get(m)
        top = max((r[1].get(m) or 0 for r in self.pglist.rows), default=0)
        fmt = lambda k, v: (fmt_mb(v) if UNITS[k] == "MB" else fnum(v, UNITS[k]))
        cv.create_text(P(6), y + P(11), text=fit_text(name, self.f10, w - P(100)), anchor="w", fill=C["fg"],
                       font=self.f10)
        if main is not None:
            cv.create_text(w - P(6), y + P(11), text=fmt(m, main), anchor="e", fill=C["fg"], font=self.f10b)
        parts = [f"{k} {fmt(k, v)}" for k, v in vals.items() if k != m and v is not None]
        cv.create_text(P(6), y + P(26), text=fit_text("   ".join(parts), self.f9, w - P(14)), anchor="w",
                       fill=C["dim"], font=self.f9)
        if main and top > 0:
            cv.create_rectangle(P(6), y + P(34), P(6) + (w - P(14)) * min(1.0, main / top), y + P(36), fill=C["ui"],
                                width=0)
        cv.create_line(P(6), y + P(37), w - P(6), y + P(37), fill=C["border"])

    # ------------------------------------------------------------ view
    def reset_view(self):
        if self.rec is None:
            return
        self.v0, self.v1 = self.f0, self.f1
        self.request_draw()

    def _set_view(self, lo, hi):
        full = self.f1 - self.f0
        min_span = max(5 * (self.rec.interval if self.rec else 1.0), 4.0)
        span = min(full, max(min_span, hi - lo))
        lo = min(max(self.f0, lo), self.f1 - span)
        self.v0, self.v1 = lo, lo + span
        self.request_draw()

    def zoom_by(self, f, anchor=None):
        if self.rec is None:
            return
        a = anchor if anchor is not None else (self.v0 + self.v1) / 2
        self._set_view(a - (a - self.v0) * f, a + (self.v1 - a) * f)

    def pan_by(self, frac):
        if self.rec is None:
            return
        d = (self.v1 - self.v0) * frac
        self._set_view(self.v0 + d, self.v1 + d)

    def _wheel_chart(self, e):
        if self.rec is None or not len(self.rec):
            return
        if e.state & 0x1:                                        # Shift: pan
            return self.pan_by(-0.15 if e.delta > 0 else 0.15)
        box = self.box
        if not box:
            return
        x = e.x_root - self.cv.winfo_rootx()
        x = min(max(x, box[0]), box[2])
        anchor = self.v0 + (x - box[0]) / (box[2] - box[0]) * (self.v1 - self.v0)
        self.zoom_by(0.8 if e.delta > 0 else 1.25, anchor)

    box = None

    def x_to_t(self, x):
        b = self.box
        return self.v0 + (x - b[0]) / (b[2] - b[0]) * (self.v1 - self.v0)

    def t_to_x(self, ts):
        b = self.box
        return b[0] + (ts - self.v0) / (self.v1 - self.v0) * (b[2] - b[0])

    # ------------------------------------------------------------ mouse on the chart
    def _in_plot(self, x, y):
        b = self.box
        return bool(b) and b[0] <= x <= b[2] and b[1] <= y <= b[3]

    def _press(self, e):
        self.cv.focus_set()
        if not self._in_plot(e.x, e.y) or self.rec is None:
            self.drag = None
            return
        self.drag = {"x": e.x, "v0": self.v0, "v1": self.v1, "moved": False}
        self._pins_before = list(self.pins)

    def _drag(self, e):
        d = self.drag
        if not d or self.rec is None:
            return
        if abs(e.x - d["x"]) > 3:
            d["moved"] = True
            self.cv.config(cursor="fleur")
            self.tip.hide()
        if d["moved"]:
            b = self.box
            dt = (e.x - d["x"]) / (b[2] - b[0]) * (d["v1"] - d["v0"])
            span = d["v1"] - d["v0"]
            lo = min(max(self.f0, d["v0"] - dt), self.f1 - span)
            self.v0, self.v1 = lo, lo + span
            self.hover_t = self.hover_i = None
            self.request_draw()

    def _release(self, e):
        d, self.drag = self.drag, None
        self.cv.config(cursor="crosshair")
        if not d or d["moved"] or self.rec is None or not len(self.rec):
            return
        ts = self.rec.t[self.rec.nearest(self.x_to_t(e.x))]
        near = [p for p in self.pins if abs(self.t_to_x(p) - e.x) <= 6]
        if near:
            self.pins = [p for p in self.pins if p not in near]
        else:
            self.pins = (self.pins + [ts])[-6:]
        self.request_draw()

    def _double(self, e):
        self.drag = None
        self.pins = list(getattr(self, "_pins_before", self.pins))      # the first click of the double click pinned
        self.reset_view()

    def _motion(self, e):
        if self.drag and self.drag["moved"]:
            return
        rec = self.rec
        if rec is None or not len(rec) or not self._in_plot(e.x, e.y):
            if self.hover_t is not None:
                self._leave()
            return
        i = rec.nearest(self.x_to_t(e.x))
        if i == self.hover_i and self.hover_t is not None:
            self.tip._xy = (e.x_root, e.y_root)
            self.tip._place()
            return
        self.hover_i, self.hover_t = i, rec.t[i]
        self.draw_cursor(e.x_root, e.y_root)

    def _leave(self, e=None):
        self.hover_t = self.hover_i = None
        self.cv.delete("cross")
        self.tip.hide()
        self.draw_strip()

    # ------------------------------------------------------------ drawing: scheduling
    def request_draw(self):
        if self._draw_job is None:
            self._draw_job = self._idle(self.draw)

    def start_ensure(self, missing):
        rec = self.rec
        if len(rec) * len(missing) < 400000:
            rec.ensure(missing)
            return True
        if self._ens_job is not None:
            return False
        box = {"done": False}

        def work():
            try:
                rec.ensure(missing)
            finally:
                box["done"] = True
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not self.alive():
                return
            if box["done"]:
                self._ens_job = None
                self.request_draw()
            else:
                self._ens_job = self._after(40, poll)
        self._ens_job = self._after(40, poll)
        return False

    # ------------------------------------------------------------ drawing: the chart
    def draw(self):
        self._draw_job = None
        if not self.alive():
            return
        cv, C = self.cv, self.C
        W, H = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        if W < 120 or H < 100:
            return
        rec = self.rec
        if rec is None or not len(rec):
            text = self.msg or (t("lv_empty") if rec is None else t("lv_no_data"))
            cv.create_text(W / 2, H / 2, text=text, fill=C["dim"], font=(self.F_TXT, 11))
            self.box = None
            self._update_stats_visibility()
            self._update_info()
            return
        sel = [rec.by_idx[i] for i in self.sel if i in rec.by_idx]
        missing = [s.idx for s in sel if s.idx not in rec.cols]
        if missing and not self.start_ensure(missing):
            sel = [s for s in sel if s.idx in rec.cols]
            loading = True
        else:
            loading = False
        self._sel_drawn = sel
        self._update_stats_visibility()
        LH = P(20)                                                      # legend row height

        # ---- legend layout (rows of chips above the plot)
        legend, ly = [], 0
        x, row = 0, 0
        avail = W - 4
        for s in sel:
            label = s.label
            tw = self.f9.measure(fit_text(label, self.f9, P(220))) + P(28)
            if x + tw > avail and x > 0:
                row += 1
                x = 0
            if row >= 3:
                break
            legend.append((s, x, row, tw))
            x += tw + 6
        shown = len(legend)
        leg_rows = (legend[-1][2] + 1) if legend else 0
        top = P(8) + leg_rows * LH + (P(18) if sel else 0)

        # ---- units / axes
        units = []
        for s in sel:
            if s.unit not in units:
                units.append(s.unit)
        pt, pb = top + 4, H - self.PAD_B
        pl = self.PAD_L                                                 # left axis: first unit, right axis: second unit
        pr = W - self.PAD_R - (self.AXIS_W if len(units) > 1 else 0)   # (further units are drawn without a ruler)
        if pr - pl < 80 or pb - pt < 60:
            return
        self.box = (pl, pt, pr, pb)
        v0, v1 = self.v0, self.v1
        i0, i1 = rec.window(v0, v1)

        ranges = {}
        for u in units:
            lo, hi = None, None
            for s in sel:
                if s.unit != u:
                    continue
                st = rec.stats(s.idx, i0, i1)
                if st:
                    lo = st[0] if lo is None else min(lo, st[0])
                    hi = st[2] if hi is None else max(hi, st[2])
            if lo is not None:
                ranges[u] = unit_range(lo, hi, u, pb - pt)

        outer, inner = C["card"], C["bg"]                                # the chart sits on a card, the plot is inset
        if gfx.AVAILABLE and Image is not None:
            pw = PilPainter(cv, W, H, outer)
        else:
            pw = CanvasPainter(cv, W, H, outer, inner)
        pw.rect(0, 0, W, H, outer)
        pw.rect(pl, pt, pr, pb, inner)

        # gaps / abrupt end
        x_of = lambda ts: pl + (ts - v0) / (v1 - v0) * (pr - pl)
        marks = []
        for a, b in rec.gaps:
            if b >= v0 and a <= v1:
                xa, xb = max(pl, x_of(a)), min(pr, x_of(b))
                pw.rect(xa, pt, max(xb, xa + 1), pb, C["red"], 44)
                marks.append(("gap", xa, xb, b - a))
        if rec.abrupt and v1 > rec.end:
            xe = max(pl, x_of(rec.end))
            pw.rect(xe, pt, pr, pb, C["red"], 50)
            pw.vline(int(xe), pt, pb, C["red"])
            marks.append(("end", xe, pr, 0))

        # grid
        step, ticks = time_ticks(v0, v1, pr - pl)
        for tk_ in ticks:
            pw.vline(int(round(x_of(tk_))), pt, pb, C["border"], 150)
        first = units[0] if units else None
        if first in ranges:
            for val in ranges[first][2]:
                y = int(round(self._ymap(val, ranges[first], pt, pb)))
                if pt < y < pb:
                    pw.hline(pl, pr, y, C["border"], 190)

        # series
        plot_w = max(1, int(pr - pl))
        dense = (i1 - i0) > 2.2 * plot_w
        edges = rec.edges(v0, v1, plot_w) if dense else None
        if not loading and self.hl is not None and self.hl not in {s.idx for s in sel}:
            self.hl = None                                               # the highlighted series is no longer drawn
        hl = self.hl
        for s in sorted(sel, key=lambda s: s.idx == hl):                # the highlighted series is drawn last (on top)
            rg = ranges.get(s.unit)
            if rg is None:
                continue
            col = self.color_of(s.idx)
            w, band = 1.7, 70
            if hl is not None:
                if s.idx == hl:
                    w, band = 2.8, 90
                else:
                    col, w, band = themes.mix(inner, col, 0.5), 1.3, 30     # the others step back
            ymap = lambda v, rg=rg: self._ymap(v, rg, pt, pb)
            if dense:
                self._paint_dense(pw, rec, s, edges, col, ymap, pl, pt, pb, w, band)
            else:
                self._paint_sparse(pw, rec, s, i0, i1, col, ymap, x_of, w)
        pw.rect(0, pt - 2, pl, pb + 2, outer)                         # lines of the neighbouring rows end at the frame
        pw.rect(pr + 1, pt - 2, W, pb + 2, outer)
        pw.hline(pl - 1, pr + 1, pb, C["faint"], 130)
        pw.hline(pl - 1, pr + 1, pt - 1, C["border"])
        pw.vline(pl - 1, pt - 1, pb + 1, C["border"])
        pw.vline(pr, pt - 1, pb + 1, C["border"])
        pw.finish()

        # ---- texts: time axis
        day0 = None
        for tk_ in ticks:
            x = x_of(tk_)
            if x < pl - 2 or x > pr + 2:
                continue
            dt = logdata.from_secs(tk_)
            if step >= 86400:
                lab = dt.strftime("%m-%d")
            elif dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                lab = dt.strftime("%m-%d")
            elif step < 60:
                lab = dt.strftime("%H:%M:%S")
            else:
                lab = dt.strftime("%H:%M")
            bold = lab[2] == "-"
            cv.create_text(x, pb + 7, text=lab, anchor="n", fill=C["fg"] if bold else C["dim"],
                           font=self.f9b if bold else self.f9)
            cv.create_line(x, pb, x, pb + 4, fill=C["faint"])
        d0, d1 = logdata.from_secs(v0).strftime("%Y-%m-%d"), logdata.from_secs(v1).strftime("%Y-%m-%d")
        cv.create_text(pl, pb + 25, text=d0 if d0 == d1 else f"{d0}  -  {d1}", anchor="nw", fill=C["faint"],
                       font=self.f9)
        # y axes: the first unit on the left, the second on the right, no ruler for the rest
        for k, u in enumerate(units[:2]):
            rg = ranges.get(u)
            if rg is None:
                continue
            members = [s for s in sel if s.unit == u]
            tint = self.color_of(members[0].idx) if len(members) == 1 else C["dim"]
            if k == 0:
                for val in rg[2]:
                    y = self._ymap(val, rg, pt, pb)
                    cv.create_text(pl - 8, y, text=fnum(val, "", self._tick_dec(rg[3])), anchor="e", fill=tint,
                                   font=self.fnum9)
                cv.create_text(pl - 8, pt - 17, text=u or "", anchor="e", fill=tint, font=self.f9b)
            else:
                x = pr + 10
                cv.create_line(x - 4, pt, x - 4, pb, fill=tint)
                for val in rg[2]:
                    y = self._ymap(val, rg, pt, pb)
                    cv.create_line(x - 4, y, x, y, fill=tint)
                    cv.create_text(x + 3, y, text=fnum(val, "", self._tick_dec(rg[3])), anchor="w", fill=tint,
                                   font=self.fnum9)
                cv.create_text(x + 3, pt - 17, text=u or "", anchor="w", fill=tint, font=self.f9b)
        # gap / end labels
        for kind, xa, xb, dur in marks:
            if kind == "gap" and xb - xa > 46:
                cv.create_text((xa + xb) / 2, pt + 6, text=t("lv_gap_lbl", d=crashscan.fmt_dur(dur)), anchor="n",
                               fill=C["red"], font=(self.F_TXT, 8, "bold"))
            elif kind == "end":
                cv.create_text(min(pr - 4, xa + 6) if pr - xa > 80 else xa - 6, pt + 6,
                               text=t("lv_end_lbl"), anchor="nw" if pr - xa > 80 else "ne", fill=C["red"],
                               font=(self.F_TXT, 8, "bold"))
        # legend
        for s, lx, row, tw in legend:
            y = P(8) + row * LH + P(9)
            col = self.color_of(s.idx)
            tag = f"lg{s.idx}"
            cv.create_rectangle(pl - P(52) + lx, y - P(5), pl - P(52) + lx + P(10), y + P(5), fill=col, width=0,
                                tags=tag)
            cv.create_text(pl - P(52) + lx + P(16), y, text=fit_text(s.label, self.f9, P(220)), anchor="w",
                           fill=C["name"], font=self.f9, tags=tag)
            cv.tag_bind(tag, "<Button-1>", lambda e, i=s.idx: self.toggle_series(i))
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor="crosshair"))
        if len(sel) > shown:
            cv.create_text(W - 6, P(8) + (leg_rows - 1) * LH + P(9), text=t("lv_more", n=len(sel) - shown), anchor="e",
                           fill=C["dim"], font=self.f9)
        # empty / loading hints
        if loading:
            cv.create_text((pl + pr) / 2, (pt + pb) / 2, text=t("lv_loading"), fill=C["dim"], font=(self.F_TXT, 11))
        elif not sel:
            cv.create_text((pl + pr) / 2, (pt + pb) / 2, text=t("lv_nosel"), fill=C["dim"], font=(self.F_TXT, 11))
        elif not ranges:
            cv.create_text((pl + pr) / 2, (pt + pb) / 2, text=t("lv_no_data"), fill=C["dim"], font=(self.F_TXT, 11))
        # pins and cursor
        self.draw_pins()
        self._ranges, self._units = ranges, units
        self._update_info()
        if self.hover_i is not None and self.hover_i < len(rec):
            self.draw_cursor(None, None)                               # also refreshes the strip and the programs
        else:
            self.draw_strip()
            self.update_programs(self.cursor_row())

    @staticmethod
    def _tick_dec(step):
        for d in range(5):
            if abs(step * 10 ** d - round(step * 10 ** d)) < 1e-6:
                return d
        return 4

    @staticmethod
    def _ymap(v, rg, pt, pb):
        lo, hi = rg[0], rg[1]
        return pb - (v - lo) / (hi - lo) * (pb - pt)

    def _paint_sparse(self, pw, rec, s, i0, i1, col, ymap, x_of, width=1.7):
        col_vals = rec.cols[s.idx]
        t_ = rec.t
        gap = rec.itv_gap
        seg, prev_t = [], None
        segs = []
        for i in range(i0, i1):
            v = col_vals[i]
            if v != v:
                if seg:
                    segs.append(seg)
                seg, prev_t = [], None
                continue
            ts = t_[i]
            if seg and prev_t is not None and ts - prev_t > gap:
                segs.append(seg)
                seg = []
            seg.append((x_of(ts), ymap(v)))
            prev_t = ts
        if seg:
            segs.append(seg)
        for sg in segs:
            if len(sg) >= 2:
                pw.polyline(sg, col, width, smooth=True)                 # no marker per sample: a smooth monotone curve
        pw.dots([sg[0] for sg in segs if len(sg) == 1], 2.2, col)       # a lone sample would be invisible otherwise

    def _paint_dense(self, pw, rec, s, edges, col, ymap, pl, pt, pb, width=1.7, band=70):
        env = rec.envelope(s.idx, edges)
        t_ = rec.t
        gap = rec.itv_gap
        segs, cur, prev = [], [], None
        for ent in env:
            if prev is not None and ent[0] - prev[0] > 1 and t_[ent[4]] - t_[prev[5]] > gap:
                segs.append(cur)
                cur = []
            cur.append(ent)
            prev = ent
        if cur:
            segs.append(cur)
        for sg in segs:
            xs = [pl + e[0] + 0.5 for e in sg]
            mean = [(x, ymap(e[2])) for x, e in zip(xs, sg)]
            if len(sg) >= 2:
                upper = [(x, ymap(e[3])) for x, e in zip(xs, sg)]
                lower = [(x, ymap(e[1])) for x, e in zip(xs, sg)]
                for a, b in zip(upper, lower):                                # min..max band: one translucent bar per pixel column
                    if b[1] - a[1] > 0.8:
                        pw.rect(a[0] - 0.5, a[1], a[0] + 0.5, b[1], col, band)
                pw.polyline(mean, col, width)
            else:
                pw.dots(mean, 1.6, col)

    # ------------------------------------------------------------ pins and cursor
    def draw_pins(self):
        cv, C = self.cv, self.C
        cv.delete("pin")
        b = self.box
        if not b or self.rec is None:
            return
        for ts in self.pins:
            if ts < self.v0 or ts > self.v1:
                continue
            x = self.t_to_x(ts)
            cv.create_line(x, b[1], x, b[3], fill=C["ui"], dash=(4, 3), tags="pin")
            lab = fmt_clock(ts)
            w = self.f9.measure(lab) + 12
            lx = min(max(x, b[0] + w / 2), b[2] - w / 2)
            cv.create_rectangle(lx - w / 2, b[1] + 3, lx + w / 2, b[1] + 21, fill=C["ui"], width=0, tags="pin")
            cv.create_text(lx, b[1] + 12, text=lab, fill=C["on_ui"], font=self.f9b, tags="pin")

    def values_at(self, i):
        rec = self.rec
        out = []
        for s in getattr(self, "_sel_drawn", []):
            if s.idx in rec.cols:
                out.append((s, rec.value(s.idx, i)))
        return out

    def draw_cursor(self, xroot, yroot):
        cv, C = self.cv, self.C
        cv.delete("cross")
        rec, b = self.rec, self.box
        i = self.hover_i
        if rec is None or b is None or i is None or i >= len(rec):
            return
        ts = rec.t[i]
        if ts < self.v0 or ts > self.v1:
            return
        x = self.t_to_x(ts)
        cv.create_line(x, b[1], x, b[3], fill=C["dim"], tags="cross")
        rgs = getattr(self, "_ranges", {})
        lines = []
        for s, v in self.values_at(i):
            rg = rgs.get(s.unit)
            if v is not None and rg is not None:
                y = self._ymap(v, rg, b[1], b[3])
                col = self.color_of(s.idx)
                cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill=col, outline=C["bg"], width=2, tags="cross")
            lines.append((self.color_of(s.idx), s.label, fnum(v, s.unit)))
        lab = fmt_clock(ts, full=(self.v1 - self.v0) > 86400)
        w = self.f9b.measure(lab) + 14
        lx = min(max(x, b[0] + w / 2), b[2] - w / 2)
        cv.create_rectangle(lx - w / 2, b[3] + 4, lx + w / 2, b[3] + 22, fill=C["ui"], width=0, tags="cross")
        cv.create_text(lx, b[3] + 13, text=lab, fill=C["on_ui"], font=self.f9b, tags="cross")
        more = 0
        if len(lines) > 16:
            more = len(lines) - 16
            lines = lines[:16]
        if more:
            lines.append((None, t("lv_more", n=more), ""))
        if xroot is None:
            try:
                xroot, yroot = cv.winfo_pointerxy()
                inside = (cv.winfo_rootx() <= xroot < cv.winfo_rootx() + cv.winfo_width()
                          and cv.winfo_rooty() <= yroot < cv.winfo_rooty() + cv.winfo_height())
            except tk.TclError:
                return
            if not inside:
                self._leave()
                return
        head = fmt_clock(ts, True)
        self.tip.update(head, lines, xroot, yroot)
        self.draw_strip()
        self.update_programs(i)

    def _update_stats_visibility(self):
        on = bool(self.v_stats.get()) and self.rec is not None and len(self.rec) > 0 and bool(self.sel)
        if on != self._stats_on:
            self._stats_on = on
            if on:
                self.stats_card.grid()
            else:
                self.stats_card.grid_remove()

    # ------------------------------------------------------------ info line and stats strip
    def _update_info(self):
        rec = self.rec
        if self.note:
            self.info_l.config(text=self.note)
            return
        if rec is None or not len(rec):
            self.info_l.config(text="")
            return
        j0, j1 = bisect_left(rec.t, self.v0), bisect_right(rec.t, self.v1)
        text = t("lv_range", a=fmt_clock(max(self.v0, rec.start)), b=fmt_clock(min(self.v1, rec.end)),
                 d=crashscan.fmt_dur(min(self.v1, rec.end) - max(self.v0, rec.start)), n=fmt_count(max(0, j1 - j0)))
        if len(self.pins) >= 2:
            p = sorted(self.pins)
            text += "      " + t("lv_delta", d=crashscan.fmt_dur(p[-1] - p[-2]))
        text = rec.name + (f" ({t('lv_stride', k=rec.stride)})" if rec.stride > 1 else "") + "      " + text
        self.info_l.config(text=text)

    def _strip_click(self, e):
        """Click a row of the statistics table: highlight its series in the chart (the same row again clears it)."""
        i = (e.y - P(26)) // P(22)
        if e.y < P(26) or i >= self.strip_rows:
            return
        i += self.strip_off
        if 0 <= i < len(self._strip_list):
            idx = self._strip_list[i].idx
            self.hl = None if self.hl == idx else idx
            self.draw_strip()
            self.request_draw()

    def draw_strip(self):
        st, C = self.strip, self.C
        try:
            st.delete("all")
            W = st.winfo_width()
        except tk.TclError:
            return
        rec = self.rec
        sel = getattr(self, "_sel_drawn", [])
        if rec is None or not len(rec) or not sel:
            self._strip_list = []
            return                                                      # the card is hidden then
        q = self.v_stsearch.get().strip().lower()
        if q:
            sel = [s for s in sel if q in s.label.lower() or q in s.name.lower()]
        self._strip_list = sel
        RH, HH = P(22), P(26)
        nrows = len(sel)
        vis = min(nrows, self.strip_rows)
        want = HH + max(1, vis) * RH + P(2)
        if int(st.cget("height")) != want:
            st.config(height=want)
        self.strip_off = max(0, min(self.strip_off, nrows - vis))
        j0, j1 = bisect_left(rec.t, self.v0), bisect_right(rec.t, self.v1)
        ci = self.hover_i if self.hover_i is not None else (rec.nearest(self.pins[-1]) if self.pins else None)
        cw = P(96)
        heads = [t("lv_col_min"), t("lv_col_avg"), t("lv_col_max"),
                 t("lv_col_cursor") if self.hover_i is not None or not self.pins else t("lv_col_pin")]
        for k, h in enumerate(heads):
            st.create_text(W - P(10) - cw * (3 - k), HH / 2, text=h, anchor="e", fill=C["dim"], font=self.f9b)
        st.create_line(P(4), HH, W - P(4), HH, fill=C["border"])
        for r in range(vis):
            s = sel[self.strip_off + r]
            y = HH + r * RH + RH / 2 + 1
            if s.idx == self.hl:                                        # picked row (click again or Escape to clear)
                st.create_rectangle(0, y - RH / 2, W, y + RH / 2, fill=themes.mix(C["card"], C["ui"], 0.28), width=0)
            elif r % 2:
                st.create_rectangle(0, y - RH / 2, W, y + RH / 2, fill=C["stripe"], width=0)
            col = self.color_of(s.idx)
            st.create_rectangle(P(8), y - P(4), P(16), y + P(4), fill=col, width=0)
            st.create_text(P(24), y, text=fit_text(s.label, self.f9, W - P(24) - cw * 4 - P(10)), anchor="w",
                           fill=C["fg"], font=self.f9)
            stat = rec.stats(s.idx, j0, j1) if s.idx in rec.cols else None
            vals = [stat[0], stat[1], stat[2]] if stat else [None] * 3
            vals.append(rec.value(s.idx, ci) if ci is not None else None)
            for k, v in enumerate(vals):
                st.create_text(W - P(10) - cw * (3 - k), y, text=fnum(v, s.unit) if v is not None else "-",
                               anchor="e", fill=C["fg"] if k == 3 else C["name"], font=self.fnum9)
        if not nrows:
            st.create_text(P(10), HH + RH / 2 + 1, text=t("lv_no_match"), anchor="w", fill=C["dim"], font=self.f9)
        if nrows > vis:                                                # thin scroll bar
            span = vis * RH
            h = max(14, span * vis / nrows)
            y0 = HH + 2 + (span - h) * (self.strip_off / (nrows - vis))
            st.create_rectangle(W - 4, y0, W - 1, y0 + h, fill=C["border"], width=0)


# ====================================================================== entry points
def _folder(api, folder=None):
    if folder:
        return folder
    try:
        return config.log_dir(api.cfg)
    except Exception:
        return config.log_dir({})


def show(api, folder=None, path=None, at=None, span=300.0):
    """Open (or raise) the viewer; path / at: start with that recording / that moment (seconds, logdata.to_secs)."""
    folder = _folder(api, folder)
    v = _STATE["win"]
    try:
        if v is not None and v.alive():
            if v.palette_key != tuple(sorted(themes.palette().items())) or (path is None and folder != v.folder
                                                                            and os.path.isdir(folder)):
                v.close()
                v = None
        else:
            v = None
    except Exception:
        v = None
    fresh = v is None
    if fresh:
        v = Viewer(api, folder, autoload=not path)
        _STATE["win"] = v
    v.win.deiconify()
    v.win.lift()
    try:
        v.win.focus_force()
    except tk.TclError:
        pass
    if path:
        if os.path.normcase(os.path.dirname(path)) != os.path.normcase(v.folder):
            v.folder = os.path.dirname(path)
            v.reload_list()
        v.open_path(path, at=at, span=span)
    elif not fresh:
        v.reload_list()                                 # new recordings may exist; a live one is re-read
    return v


def add_button(sw, parent, folder=None, pack=None):
    """"View recordings" button using the settings window's button factory. `folder`: callable -> folder or None."""
    b = sw.button(parent, t("lv_open"), lambda: show(sw.api, folder() if folder else None))
    b.pack(**(pack or {"side": "left", "padx": (8, 0)}))
    return b


def open_event(sw, ev, folder=None):
    """Open the viewer on a crash-detective event: an abrupt end (the last rows) or a gap (centred on it)."""
    last = logdata.to_secs(ev["ts_last"])
    resume = logdata.to_secs(ev["resume"]) if ev.get("resume") else None
    span = max(300.0, (resume - last) * 1.6 + 120.0) if resume else 300.0
    v = show(sw.api, folder, path=ev["path"], at=(last + resume) / 2 if resume else last, span=span)
    return v


def add_event_button(sw, parent, current, folder=None, pack=None):
    """"Open in viewer" button for the crash detective: shows the selected event (current() -> event or None)."""
    def go():
        ev = current()
        if ev:
            open_event(sw, ev, folder() if folder else None)
        else:
            show(sw.api, folder() if folder else None)
    b = sw.button(parent, t("lv_open_at"), go)
    b.pack(**(pack or {"side": "left", "padx": (8, 0)}))
    return b
