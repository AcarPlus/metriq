"""Recording viewer: opens the CSV recordings of the log folder as charts (no Excel / LibreOffice needed).

    logview.show(api, folder=None, path=None, at=None)   open / raise the window (at = seconds, see logdata.to_secs)
    logview.add_button(sw, parent, folder=None)           "View recordings" button for the settings tabs

Left: recordings (date, length, size, a marker for gaps / abrupt ends / live), centre: chart (min-max envelope per
pixel column for any number of rows, one axis per unit), right: series picker + the programs running at the cursor.
Mouse wheel = zoom, drag = pan, click = pin a marker, double click = show everything. Data work lives in
logdata.py; crash markers come from crashscan."""
import math
import os
import queue
import threading
import types
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
        "lv_loading": "Loading...", "lv_empty": "Pick a recording on the left.",
        "lv_nosel": "Pick a preset above or tick series on the right.", "lv_no_recs": "No recordings in this folder.",
        "lv_col_min": "Min", "lv_col_avg": "Avg", "lv_col_max": "Max", "lv_col_cursor": "Cursor", "lv_col_pin": "Pin",
        "lv_gap": "Gap {d}", "lv_abrupt": "Ended abruptly", "lv_live": "live", "lv_prog": "programs",
        "lv_rows": "{n} rows", "lv_row1": "1 row", "lv_selected": "{n} selected",
        "lv_range": "{a} - {b}   ({d})   {n} rows", "lv_delta": "Pins: {d}",
        "lv_hint": "Wheel: zoom   Drag: pan   Click: pin   Double click: reset",
        "lv_programs": "Programs", "lv_prog_none": "No program data at this moment.",
        "lv_src_side": "process file", "lv_src_top": "top programs of the recording",
        "lv_open_dir": "Open folder", "lv_browse": "Open file...", "lv_refresh": "Refresh",
        "lv_err": "Could not read the recording: {e}", "lv_more": "+{n} more", "lv_stride": "every {k}th row shown",
        "lv_gap_lbl": "Gap {d}", "lv_end_lbl": "Ended abruptly", "lv_pick_file": "Recording (CSV)",
        "lv_no_data": "No data in this range.", "lv_sort": "Sort by",
        "hp_lv_nav": "Mouse wheel zooms around the pointer, dragging moves the view, a click pins a marker (click it "
                     "again to remove it) and a double click shows the whole recording. Shift + wheel, the arrow keys "
                     "and + / - work too.",
        "hp_lv_recs": "Your recordings, newest first. A red tag means the recording ended abruptly (crash, freeze or "
                      "power loss), a yellow one means rows are missing in between. Green means it is still being written.",
        "hp_lv_series": "Pick what to draw. The buttons above the chart select a ready-made group. Series with the "
                        "same unit share an axis; a different unit gets its own axis on the right. When there are more "
                        "rows than pixels, each pixel shows the lowest and highest value as a band.",
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
        "lv_loading": "Yükleniyor...", "lv_empty": "Soldan bir kayıt seçin.",
        "lv_nosel": "Yukarıdan hazır bir grup seçin ya da sağdan seri işaretleyin.",
        "lv_no_recs": "Bu klasörde kayıt yok.",
        "lv_col_min": "En düşük", "lv_col_avg": "Ort.", "lv_col_max": "En yüksek", "lv_col_cursor": "İmleç", "lv_col_pin": "İşaret",
        "lv_gap": "Boşluk {d}", "lv_abrupt": "Ani bitti", "lv_live": "canlı", "lv_prog": "programlar",
        "lv_rows": "{n} satır", "lv_row1": "1 satır", "lv_selected": "{n} seçili",
        "lv_range": "{a} - {b}   ({d})   {n} satır", "lv_delta": "İşaretler: {d}",
        "lv_hint": "Tekerlek: yakınlaştır   Sürükle: kaydır   Tıkla: işaretle   Çift tık: sıfırla",
        "lv_programs": "Programlar", "lv_prog_none": "Bu anda program verisi yok.",
        "lv_src_side": "işlem dosyası", "lv_src_top": "kaydın en çok çalışan programları",
        "lv_open_dir": "Klasörü aç", "lv_browse": "Dosya aç...", "lv_refresh": "Yenile",
        "lv_err": "Kayıt okunamadı: {e}", "lv_more": "+{n} tane daha", "lv_stride": "her {k}. satır gösteriliyor",
        "lv_gap_lbl": "Boşluk {d}", "lv_end_lbl": "Ani bitti", "lv_pick_file": "Kayıt (CSV)",
        "lv_no_data": "Bu aralıkta veri yok.", "lv_sort": "Sırala",
        "hp_lv_nav": "Tekerlek imlecin olduğu yeri yakınlaştırır, sürükleme görünümü kaydırır, tıklama bir işaret "
                     "bırakır (tekrar tıklayınca kalkar), çift tıklama kaydın tamamını gösterir. Shift + tekerlek, "
                     "ok tuşları ve + / - da çalışır.",
        "hp_lv_recs": "Kayıtların, en yenisi üstte. Kırmızı etiket kaydın ani bittiğini (çökme, donma ya da elektrik "
                      "kesintisi), sarı etiket arada satır eksik olduğunu gösterir. Yeşil: kayıt hâlâ yazılıyor.",
        "hp_lv_series": "Çizilecekleri seç. Grafiğin üstündeki düğmeler hazır bir grubu seçer. Birimi aynı olan seriler "
                        "aynı ekseni paylaşır; farklı birim sağda kendi eksenini alır. Satır sayısı piksel sayısından "
                        "çoksa her piksel en düşük ve en yüksek değeri bir bant olarak gösterir.",
    },
})

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

    def polyline(self, pts, col, width=1.6):
        if len(pts) < 2:
            return
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

    def polyline(self, pts, col, width=1.6):
        if len(pts) >= 2:
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
            self.F_TXT, _, self.F_NUM = api.fonts_now()
        except Exception:
            self.F_TXT, self.F_NUM = "Segoe UI", "Consolas"
        self.f9 = tkfont.Font(family=self.F_TXT, size=9)
        self.f10 = tkfont.Font(family=self.F_TXT, size=10)
        self.f10b = tkfont.Font(family=self.F_TXT, size=10, weight="bold")
        self.f9b = tkfont.Font(family=self.F_TXT, size=9, weight="bold")
        self.fnum9 = tkfont.Font(family=self.F_NUM, size=9)
        self.colors = series_colors(self.C)
        self.rec, self.sel, self.slot, self._keys, self.presets = None, [], {}, [], {}
        self.f0 = self.f1 = self.v0 = self.v1 = 0.0
        self.pins, self.hover_t, self.hover_i = [], None, None
        self._draw_job = self._load_job = self._ens_job = None
        self.paths, self.probes, self.cur_path = [], {}, None
        self.pq = queue.Queue()
        self.probe_gen = 0
        self.expanded, self.last_pick_key = set(), "temp"
        self.drag = None
        self.strip_off = 0
        self.picker_on = True
        self._pending_at = None
        self.msg = ""

        self.win = tk.Toplevel(api.root)
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
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<MouseWheel>", self._wheel_any)
        for k, fn in (("<Home>", self.reset_view), ("<Left>", lambda: self.pan_by(-0.2)),
                      ("<Right>", lambda: self.pan_by(0.2)), ("<plus>", lambda: self.zoom_by(0.7)),
                      ("<minus>", lambda: self.zoom_by(1 / 0.7)), ("<KP_Add>", lambda: self.zoom_by(0.7)),
                      ("<KP_Subtract>", lambda: self.zoom_by(1 / 0.7))):
            self.win.bind(k, lambda e, fn=fn: None if isinstance(self.win.focus_get(), tk.Entry) else fn())
        self.win.after(30, self._dark_title)
        self.reload_list()

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
        w, h = min(1400, mw - 40), min(860, mh - 70)
        self.win.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + max(10, (mh - h) // 3)}")
        self.win.minsize(min(1120, w), min(580, h))

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self.probe_gen += 1
        for j in (self._draw_job, self._load_job, self._ens_job):
            if j:
                try:
                    self.win.after_cancel(j)
                except tk.TclError:
                    pass
        try:
            self.tip.hide()
        except Exception:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if _STATE["win"] is self:
            _STATE["win"] = None

    # ------------------------------------------------------------ widgets
    def _btn(self, parent, text, cmd, primary=False, small=False):
        C = self.C
        base = C["ui"] if primary else C["track"]
        hover = C["ui_hover"] if primary else C["border"]
        b = tk.Button(parent, text=text, command=cmd, bg=base, fg=C["on_ui"] if primary else C["fg"],
                      activebackground=hover, activeforeground=C["on_ui"] if primary else C["fg"], relief="flat", bd=0,
                      padx=8 if small else 14, pady=3 if small else 5, cursor="hand2", highlightthickness=0,
                      font=(self.F_TXT, 9 if small else 10, "bold" if primary else "normal"), takefocus=0)
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=getattr(b, "base", base)))
        b.base = base
        return b

    def _entry(self, parent, var, ph):
        C = self.C
        e = tk.Entry(parent, textvariable=var, bg=C["track"], fg=C["fg"], insertbackground=C["fg"], relief="flat",
                     highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["ui"],
                     font=(self.F_TXT, 10), selectbackground=C["select"], selectforeground=C["fg"])
        hint = tk.Label(e, text=ph, bg=C["track"], fg=C["faint"], font=(self.F_TXT, 10), cursor="xterm")
        hint.bind("<Button-1>", lambda ev: e.focus_set())

        def upd(*a):
            if var.get():
                hint.place_forget()
            else:
                hint.place(x=4, rely=0.5, anchor="w")
        var.trace_add("write", upd)
        upd()
        e.bind("<Escape>", lambda ev: (var.set(""), "break")[1])
        return e

    def _build(self):
        C, win = self.C, self.win
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(0, weight=1)

        # ---- left: recordings
        left = tk.Frame(win, bg=C["bg"], width=262)
        left.grid(row=0, column=0, sticky="ns", padx=(14, 0), pady=14)
        left.pack_propagate(False)
        head = tk.Frame(left, bg=C["bg"])
        head.pack(fill="x")
        tk.Label(head, text=t("lv_recs"), bg=C["bg"], fg=C["fg"], font=(self.F_TXT, 12, "bold")).pack(side="left")
        self.rec_count = tk.Label(head, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9))
        self.rec_count.pack(side="left", padx=(8, 0), pady=(3, 0))
        helptexts.info_icon(head, "lv_recs", bg=C["bg"]).pack(side="right")
        self.v_rsearch = tk.StringVar()
        self.v_rsearch.trace_add("write", lambda *a: self.rebuild_rec_rows())
        self._entry(left, self.v_rsearch, t("lv_search")).pack(fill="x", pady=(8, 8), ipady=4)
        self.rlist = VList(left, C, 62, self._draw_rec, self._click_rec, bg=C["card"])
        self.rlist.pack(fill="both", expand=True)
        bar = tk.Frame(left, bg=C["bg"])
        bar.pack(fill="x", pady=(8, 0))
        self._btn(bar, t("lv_refresh"), self.reload_list, small=True).pack(side="left")
        self._btn(bar, t("lv_open_dir"), self.open_dir, small=True).pack(side="left", padx=(6, 0))
        self._btn(bar, t("lv_browse"), self.browse, small=True).pack(side="left", padx=(6, 0))

        # ---- centre: toolbar, chart, info line, stats strip
        mid = tk.Frame(win, bg=C["bg"])
        mid.grid(row=0, column=1, sticky="nsew", padx=14, pady=14)
        mid.grid_rowconfigure(1, weight=1)
        mid.grid_columnconfigure(0, weight=1)
        tb = tk.Frame(mid, bg=C["bg"])
        tb.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.preset_btns = {}
        for key in PRESET_KEYS:
            b = self._btn(tb, t("lv_p_" + key), lambda k=key: self.apply_preset(k), small=True)
            b.pack(side="left", padx=(0, 6))
            self.preset_btns[key] = b
        self._btn(tb, t("lv_clear"), self.clear_sel, small=True).pack(side="left", padx=(8, 0))
        self.series_btn = self._btn(tb, t("lv_series"), self.toggle_picker, small=True)
        self.series_btn.pack(side="right")
        self._btn(tb, t("lv_all"), self.show_all, small=True).pack(side="right", padx=(0, 6))

        self.cv = tk.Canvas(mid, bg=C["bg"], highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.grid(row=1, column=0, sticky="nsew")
        self.cv.bind("<Configure>", lambda e: self.request_draw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", self._leave)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Double-Button-1>", self._double)
        self.tip = ChartTip(self.cv)

        info = tk.Frame(mid, bg=C["bg"])
        info.grid(row=2, column=0, sticky="ew", pady=(8, 4))
        self.info_l = tk.Label(info, text="", bg=C["bg"], fg=C["name"], font=(self.F_TXT, 9), anchor="w")
        self.info_l.pack(side="left")
        helptexts.info_icon(info, "lv_nav", bg=C["bg"]).pack(side="right")
        self.strip = tk.Canvas(mid, height=96, bg=C["card"], highlightthickness=1, highlightbackground=C["border"],
                               bd=0)
        self.strip.grid(row=3, column=0, sticky="ew")
        self.strip.bind("<Configure>", lambda e: self.draw_strip())

        # ---- right: series picker + programs
        self.right = tk.Frame(win, bg=C["bg"], width=300)
        self.right.grid(row=0, column=2, sticky="ns", padx=(0, 14), pady=14)
        self.right.pack_propagate(False)
        ph = tk.Frame(self.right, bg=C["bg"])
        ph.pack(fill="x")
        tk.Label(ph, text=t("lv_series"), bg=C["bg"], fg=C["fg"], font=(self.F_TXT, 12, "bold")).pack(side="left")
        self.sel_count = tk.Label(ph, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9))
        self.sel_count.pack(side="left", padx=(8, 0), pady=(3, 0))
        helptexts.info_icon(ph, "lv_series", bg=C["bg"]).pack(side="right")
        self.v_ssearch = tk.StringVar()
        self.v_ssearch.trace_add("write", lambda *a: self.rebuild_pick_rows())
        self._entry(self.right, self.v_ssearch, t("lv_series_search")).pack(fill="x", pady=(8, 8), ipady=4)
        self.plist = VList(self.right, C, 26, self._draw_pick, self._click_pick, self._hover_pick, bg=C["card"])
        self.plist.pack(fill="both", expand=True)
        self.ptip = tooltip.Tooltip(self.plist.cv, self._pick_tip)

        self.prog = tk.Frame(self.right, bg=C["bg"], height=290)
        self.prog.pack_propagate(False)
        ph2 = tk.Frame(self.prog, bg=C["bg"])
        ph2.pack(fill="x", pady=(10, 4))
        tk.Label(ph2, text=t("lv_programs"), bg=C["bg"], fg=C["fg"], font=(self.F_TXT, 11, "bold")).pack(side="left")
        self.prog_time = tk.Label(ph2, text="", bg=C["bg"], fg=C["dim"], font=(self.F_TXT, 9))
        self.prog_time.pack(side="left", padx=(8, 0), pady=(2, 0))
        self.sort_bar = tk.Frame(self.prog, bg=C["bg"])
        self.sort_bar.pack(fill="x", pady=(0, 4))
        self.sort_metric, self.sort_btns = "CPU", {}
        for m in logdata.Recording.METRICS:
            b = tk.Label(self.sort_bar, text=m, bg=C["track"], fg=C["dim"], font=(self.F_TXT, 8), padx=7, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, m=m: self.set_sort(m))
            self.sort_btns[m] = b
        self.prog_rows, self.prog_src = [], None
        self.pglist = VList(self.prog, C, 38, self._draw_prog, bg=C["card"])
        self.pglist.pack(fill="both", expand=True)

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
            for lst in (self.rlist, self.plist, self.pglist):
                if w is lst or w is lst.cv:
                    lst.scroll(e.delta)
                    return
            w = getattr(w, "master", None)

    # ------------------------------------------------------------ recordings list
    def reload_list(self):
        self.paths = logdata.list_recordings(self.folder)
        self.rec_count.config(text=str(len(self.paths)) if self.paths else "")
        self.probe_gen += 1
        gen = self.probe_gen
        todo = list(self.paths)

        def work():
            for p in todo:
                if gen != self.probe_gen:
                    return
                self.pq.put((gen, p, logdata.probe(p)))
        threading.Thread(target=work, daemon=True).start()
        self.rebuild_rec_rows()
        self.win.after(60, self._poll_probe)
        try:
            if self.rec is not None and self.cur_path and os.path.getsize(self.cur_path) != self.rec.size:
                self.open_path(self.cur_path, keep_view=True)           # the recording is still being written
        except OSError:
            pass
        if self.autoload and self.rec is None and self.paths and self.cur_path is None:
            self.open_path(self.paths[0])

    def _poll_probe(self):
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
            self.rlist.redraw()
        if any(p not in self.probes for p in self.paths):
            self.win.after(120, self._poll_probe)

    def rebuild_rec_rows(self):
        q = (self.v_rsearch.get() or "").strip().lower()
        rows = []
        for p in self.paths:
            if q:
                d = self.probes.get(p, {})
                hay = os.path.basename(p).lower() + " " + (fmt_clock(d["start"], True) if d.get("start") else "")
                if q not in hay:
                    continue
            rows.append(p)
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

    def _draw_rec(self, cv, i, p, y, w, hot):
        C = self.C
        RH = self.rlist.row_h
        d = self.probes.get(p, {})
        if p == self.cur_path:
            cv.create_rectangle(0, y, w, y + RH, fill=C["select"], width=0)
            cv.create_rectangle(0, y, 3, y + RH, fill=C["ui"], width=0)
        elif hot:
            cv.create_rectangle(0, y, w, y + RH, fill=C["stripe"], width=0)
        cv.create_line(12, y + RH - 1, w - 12, y + RH - 1, fill=C["border"])
        cv.create_text(14, y + 12, text=fmt_clock(self._rec_time(p, d), True)[:16], anchor="w", fill=C["fg"],
                       font=self.f10b)
        if d.get("start") and d.get("end"):
            cv.create_text(w - 14, y + 12, text=crashscan.fmt_dur(d["end"] - d["start"]), anchor="e",
                           fill=C["name"], font=self.f9)
        sub = []
        try:
            sub.append(fmt_size(d.get("size") or os.path.getsize(p)))
        except OSError:
            pass
        if d.get("rows"):
            sub.append(rows_text(d["rows"]))
        cv.create_text(14, y + 31, text="  ·  ".join(sub) if sub else "...", anchor="w", fill=C["dim"],
                       font=self.f9)
        badges = []
        if d.get("abrupt"):
            badges.append((t("lv_abrupt"), C["red"]))
        if d.get("gaps"):
            n = len(d["gaps"])
            badges.append((t("lv_gap", d=f"{n}x" if n > 1 else crashscan.fmt_dur(d["gaps"][0][1] - d["gaps"][0][0])),
                           C["yellow"]))
        if d.get("live"):
            badges.append((t("lv_live"), C["green"]))
        if d.get("side"):
            badges.append((t("lv_prog"), C["accent_net"]))
        x = 14
        for text, col in badges:
            tw = self.f9.measure(text) + 12
            if x + tw > w - 10:
                break
            cv.create_rectangle(x, y + 42, x + tw, y + 56, fill=themes.mix(C["card"], col, 0.2), width=0)
            cv.create_text(x + tw / 2, y + 49, text=text, fill=col, font=(self.F_TXT, 8))
            x += tw + 5

    def _click_rec(self, i, p, e):
        self.open_path(p)

    def open_dir(self):
        try:
            os.makedirs(self.folder, exist_ok=True)
            os.startfile(self.folder)
        except OSError:
            pass

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
        self.rlist.redraw()
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
                self._load_job = self.win.after(40, poll)
                return
            if "rec" in box:
                self.set_recording(box["rec"])
            else:
                self.msg = t("lv_err", e=box.get("err", "?"))
                self.request_draw()
        self._load_job = self.win.after(30, poll)

    def set_recording(self, rec):
        if not self._keys and getattr(self.api, "cfg", None) is not None:
            self._keys = list(self.api.cfg.get("viewer_series") or [])
        keep = [rec.header.index(k) for k in self._keys if k in rec.header]
        self.presets = {k: [s.idx for s in logdata.preset(rec, k)] for k in PRESET_KEYS}
        self.rec = rec
        self.msg = ""
        self.sel, self.slot, self.pins = [], {}, []
        self.hover_t = self.hover_i = None
        self._leave()
        self.f0 = rec.start
        self.f1 = rec.end + max(rec.interval, (rec.end - rec.start) * 0.015) if len(rec) else rec.start + 1
        self.v0, self.v1 = self.f0, self.f1
        self.expanded = set()
        for k, b in self.preset_btns.items():
            b.config(state="normal" if self.presets[k] else "disabled")
        keep = [i for i in keep if i in rec.by_idx]
        if keep:
            self.set_selection(keep)
            self.expanded |= {rec.by_idx[i].cat for i in keep}
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
        if rec.path in self.rlist.rows:
            self.rlist.see(self.rlist.rows.index(rec.path))
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

    def apply_preset(self, key):
        """Adds the preset's series to the selection (earlier picks stay) and opens its picker groups."""
        idxs = self.presets.get(key) if self.rec is not None else None
        if not idxs:
            return
        self.last_pick_key = key
        cats = {self.rec.by_idx[i].cat for i in idxs}
        active = getattr(self, "active_presets", set())
        if key in active:
            # second click: the preset's whole group is switched off (incl. series added later with Show all)
            active.discard(key)
            self.expanded -= cats
            self.set_selection([i for i in self.sel if self.rec.by_idx[i].cat not in cats])
        else:
            active.add(key)
            self.expanded |= cats
            self.set_selection(list(self.sel) + [i for i in idxs if i not in self.sel])
        self.active_presets = active
        self.plist.set_rows(self.plist.rows, keep=False)

    def show_all(self):
        """Toggle: selects every series of the groups whose preset is switched on and shows the full time range;
        pressed again while all of them are shown, it switches them off. Does nothing when no preset is on."""
        if self.rec is None:
            return
        groups = {self.rec.by_idx[i].cat for k in getattr(self, "active_presets", set())
                  for i in (self.presets.get(k) or [])}
        if not groups:
            return
        idxs = [s.idx for s in self.rec.series if s.cat in groups]
        if idxs and set(idxs) <= set(self.sel):
            off = set(idxs)
            self.set_selection([i for i in self.sel if i not in off])
        else:
            self.set_selection(list(self.sel) + [i for i in idxs if i not in self.sel])
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
        self.active_presets = set()
        self.set_selection([])

    def _mark_presets(self):
        if self.rec is None:
            return
        C = self.C
        for k, b in self.preset_btns.items():
            ser = self.presets.get(k)
            on = bool(ser) and k in getattr(self, "active_presets", set())
            base = C["ui"] if on else C["track"]
            b.base = base
            b.config(bg=base, fg=C["on_ui"] if on else (C["fg"] if ser else C["faint"]),
                     activebackground=C["ui_hover"] if on else C["border"])

    # ------------------------------------------------------------ picker
    def rebuild_pick_rows(self):
        rec = self.rec
        rows = []
        q = (self.v_ssearch.get() or "").strip().lower()
        if rec is not None:
            groups = {}
            for s in rec.series:
                if q and q not in (s.key + " " + s.label).lower():
                    continue
                groups.setdefault(s.cat, []).append(s)
            for cat in logdata.CATS:
                lst = groups.get(cat)
                if not lst:
                    continue
                nsel = sum(1 for s in lst if s.idx in self.sel)
                open_ = cat in self.expanded or bool(q)
                rows.append(("grp", cat, len(lst), nsel, open_))
                if open_:
                    rows += [("ser", s) for s in lst]
        self.plist.set_rows(rows)
        self.sel_count.config(text=t("lv_selected", n=len(self.sel)) if self.sel else "")

    def _draw_pick(self, cv, i, row, y, w, hot):
        C = self.C
        if hot:
            cv.create_rectangle(0, y, w, y + 26, fill=C["stripe"], width=0)
        if row[0] == "grp":
            _, cat, n, nsel, open_ = row
            cx, cy = 14, y + 13
            pts = [cx - 3, cy - 4, cx + 3, cy, cx - 3, cy + 4] if not open_ else [cx - 4, cy - 2, cx + 4, cy - 2, cx, cy + 3]
            cv.create_polygon(*pts, fill=C["dim"], outline="")
            cv.create_text(28, cy, text=t("lv_cat_" + cat), anchor="w", fill=C["fg"], font=self.f10b)
            right = f"{nsel}/{n}" if nsel else str(n)
            cv.create_text(w - 10, cy, text=right, anchor="e", fill=C["ui"] if nsel else C["faint"], font=self.f9)
            return
        s = row[1]
        on = s.idx in self.sel
        cy = y + 13
        col = self.color_of(s.idx) if on else None
        cv.create_rectangle(16, cy - 7, 30, cy + 7, fill=col or C["track"], outline=col or C["faint"], width=1)
        if on:
            cv.create_line(19, cy, 22, cy + 3, 27, cy - 3, fill=C["bg"], width=2)
        maxw = w - 40 - 10
        name = fit_text(s.label, self.f10, maxw)
        cv.create_text(40, cy, text=name, anchor="w", fill=C["fg"] if on else C["name"], font=self.f10)

    def _click_pick(self, i, row, e):
        if row[0] == "grp":
            cat = row[1]
            if cat in self.expanded:
                self.expanded.discard(cat)
            else:
                self.expanded.add(cat)
            self.rebuild_pick_rows()
        else:
            self.toggle_series(row[1].idx)

    def _hover_pick(self, i):
        self.ptip._leave()
        if i is not None:
            self._tip_row = self.plist.rows[i]
            try:
                cv = self.plist.cv
                self.ptip._enter(types.SimpleNamespace(x_root=cv.winfo_pointerx(), y_root=cv.winfo_pointery()))
            except tk.TclError:
                pass

    def _pick_tip(self):
        row = getattr(self, "_tip_row", None)
        if not row or row[0] != "ser":
            return ""
        s = row[1]
        rows = [(s.name, s.unit, "head")]
        if s.section:
            rows.append((s.section, ""))
        return rows

    def toggle_picker(self):
        self.picker_on = not self.picker_on
        if self.picker_on:
            self.right.grid()
        else:
            self.right.grid_remove()
        self.request_draw()

    # ------------------------------------------------------------ programs panel
    def _update_prog_visibility(self):
        has = self.rec is not None and self.rec.has_programs()
        if has and not self.prog.winfo_ismapped():
            self.prog.pack(fill="x", side="bottom", before=self.plist)
        elif not has and self.prog.winfo_ismapped():
            self.prog.pack_forget()
        if has:
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
        if rec is None or i is None or not self.prog.winfo_ismapped():
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
        cv.create_text(12, y + 11, text=fit_text(name, self.f10, w - 100), anchor="w", fill=C["fg"], font=self.f10)
        if main is not None:
            cv.create_text(w - 12, y + 11, text=fmt(m, main), anchor="e", fill=C["fg"], font=self.f10b)
        parts = [f"{k} {fmt(k, v)}" for k, v in vals.items() if k != m and v is not None]
        cv.create_text(12, y + 26, text=fit_text("   ".join(parts), self.f9, w - 24), anchor="w", fill=C["dim"],
                       font=self.f9)
        if main and top > 0:
            cv.create_rectangle(12, y + 34, 12 + (w - 24) * min(1.0, main / top), y + 36, fill=C["ui"], width=0)
        cv.create_line(12, y + 37, w - 12, y + 37, fill=C["border"])

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
            self._draw_job = self.win.after_idle(self.draw)

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
                self._ens_job = self.win.after(40, poll)
        self._ens_job = self.win.after(40, poll)
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
            cv.create_rectangle(0, 0, W, H, fill=C["card"], outline=C["border"])
            text = self.msg or (t("lv_empty") if rec is None else t("lv_no_data"))
            cv.create_text(W / 2, H / 2, text=text, fill=C["dim"], font=(self.F_TXT, 11))
            self.box = None
            self.draw_strip()
            return
        sel = [rec.by_idx[i] for i in self.sel if i in rec.by_idx]
        missing = [s.idx for s in sel if s.idx not in rec.cols]
        if missing and not self.start_ensure(missing):
            sel = [s for s in sel if s.idx in rec.cols]
            loading = True
        else:
            loading = False
        self._sel_drawn = sel

        # ---- legend layout (rows of chips above the plot)
        legend, ly = [], 0
        x, row = 0, 0
        avail = W - 4
        for s in sel:
            label = s.label
            tw = self.f9.measure(fit_text(label, self.f9, 220)) + 28
            if x + tw > avail and x > 0:
                row += 1
                x = 0
            if row >= 3:
                break
            legend.append((s, x, row, tw))
            x += tw + 6
        shown = len(legend)
        leg_rows = (legend[-1][2] + 1) if legend else 0
        top = 8 + leg_rows * 20 + (18 if sel else 0)

        # ---- units / axes
        units = []
        for s in sel:
            if s.unit not in units:
                units.append(s.unit)
        extra = max(0, len(units) - 1)
        pl, pr = self.PAD_L, W - self.PAD_R - extra * self.AXIS_W
        pt, pb = top + 4, H - self.PAD_B
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

        if gfx.AVAILABLE and Image is not None:
            pw = PilPainter(cv, W, H, C["bg"])
        else:
            pw = CanvasPainter(cv, W, H, C["bg"], C["card"])
        pw.rect(0, 0, W, H, C["bg"])
        pw.rect(pl, pt, pr, pb, C["card"])

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
        for s in sel:
            rg = ranges.get(s.unit)
            if rg is None:
                continue
            col = self.color_of(s.idx)
            ymap = lambda v, rg=rg: self._ymap(v, rg, pt, pb)
            if dense:
                self._paint_dense(pw, rec, s, edges, col, ymap, pl, pt, pb)
            else:
                self._paint_sparse(pw, rec, s, i0, i1, col, ymap, x_of)
        pw.rect(0, pt - 2, pl, pb + 2, C["bg"])                       # lines of the neighbouring rows end at the frame
        pw.rect(pr + 1, pt - 2, W, pb + 2, C["bg"])
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
        # y axes
        for k, u in enumerate(units):
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
                x = pr + 10 + (k - 1) * self.AXIS_W
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
            y = 8 + row * 20 + 9
            col = self.color_of(s.idx)
            tag = f"lg{s.idx}"
            cv.create_rectangle(pl - 52 + lx, y - 5, pl - 52 + lx + 10, y + 5, fill=col, width=0, tags=tag)
            cv.create_text(pl - 52 + lx + 16, y, text=fit_text(s.label, self.f9, 220), anchor="w", fill=C["name"],
                           font=self.f9, tags=tag)
            cv.tag_bind(tag, "<Button-1>", lambda e, i=s.idx: self.toggle_series(i))
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor="crosshair"))
        if len(sel) > shown:
            cv.create_text(W - 6, 8 + (leg_rows - 1) * 20 + 9, text=t("lv_more", n=len(sel) - shown), anchor="e",
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

    def _paint_sparse(self, pw, rec, s, i0, i1, col, ymap, x_of):
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
        n = i1 - i0
        for sg in segs:
            if len(sg) >= 2:
                pw.polyline(sg, col, 1.7)
        if n <= max(12, int((self.box[2] - self.box[0]) / 9)):
            pw.dots([p for sg in segs for p in sg], 2.6, col)
        else:
            pw.dots([sg[0] for sg in segs if len(sg) == 1], 2.2, col)

    def _paint_dense(self, pw, rec, s, edges, col, ymap, pl, pt, pb):
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
                for a, b in zip(upper, lower):                    # min..max band: one translucent bar per pixel column
                    if b[1] - a[1] > 0.8:
                        pw.rect(a[0] - 0.5, a[1], a[0] + 0.5, b[1], col, 70)
                pw.polyline(mean, col, 1.7)
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
                cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill=col, outline=C["card"], width=2, tags="cross")
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

    # ------------------------------------------------------------ info line and stats strip
    def _update_info(self):
        rec = self.rec
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
            st.config(height=34)
            st.create_text(12, 17, text=t("lv_nosel") if rec is not None else t("lv_empty"), anchor="w",
                           fill=C["faint"], font=self.f9)
            return
        RH, HH = 20, 24
        nrows = len(sel)
        vis = min(nrows, 6)
        want = HH + vis * RH + 4
        if int(st.cget("height")) != want:
            st.config(height=want)
        self.strip_off = max(0, min(self.strip_off, nrows - vis))
        j0, j1 = bisect_left(rec.t, self.v0), bisect_right(rec.t, self.v1)
        ci = self.hover_i if self.hover_i is not None else (rec.nearest(self.pins[-1]) if self.pins else None)
        cw = 96
        heads = [t("lv_col_min"), t("lv_col_avg"), t("lv_col_max"),
                 t("lv_col_cursor") if self.hover_i is not None or not self.pins else t("lv_col_pin")]
        for k, h in enumerate(heads):
            st.create_text(W - 12 - cw * (3 - k), HH / 2 + 1, text=h, anchor="e", fill=C["dim"], font=self.f9b)
        st.create_line(10, HH, W - 10, HH, fill=C["border"])
        for r in range(vis):
            s = sel[self.strip_off + r]
            y = HH + r * RH + RH / 2 + 2
            if r % 2:
                st.create_rectangle(1, y - RH / 2, W, y + RH / 2, fill=C["stripe"], width=0)
            col = self.color_of(s.idx)
            st.create_rectangle(14, y - 4, 22, y + 4, fill=col, width=0)
            st.create_text(30, y, text=fit_text(s.label, self.f9, W - 30 - cw * 4 - 10), anchor="w", fill=C["fg"],
                           font=self.f9)
            stat = rec.stats(s.idx, j0, j1) if s.idx in rec.cols else None
            vals = [stat[0], stat[1], stat[2]] if stat else [None] * 3
            vals.append(rec.value(s.idx, ci) if ci is not None else None)
            for k, v in enumerate(vals):
                st.create_text(W - 12 - cw * (3 - k), y, text=fnum(v, s.unit) if v is not None else "-", anchor="e",
                               fill=C["fg"] if k == 3 else C["name"], font=self.fnum9)
        if nrows > vis:                                                # thin scroll bar
            span = vis * RH
            h = max(14, span * vis / nrows)
            y0 = HH + 2 + (span - h) * (self.strip_off / (nrows - vis))
            st.create_rectangle(W - 5, y0, W - 2, y0 + h, fill=C["border"], width=0)


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
