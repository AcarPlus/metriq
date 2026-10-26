"""Settings page "Performance": benchmark cards (CPU, RAM, SSD, GPU), run all, results, history, export.
Engine and scoring: perf_core.py. Hook: settings_ui calls add_tab(settings) for the page prepared in PAGES."""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import gfx
import helptexts
import i18n
import perf_core as pc
import uikit
from i18n import t
from uikit import px

PAGE = 6                                  # index in settings_ui.PAGES
META = {                                  # test -> (accent token, icon, title key)
    "cpu": ("accent_clock", "cpu"), "ram": ("accent_net", "ram"),
    "ssd": ("accent_fan", "ssd"), "gpu": ("accent_fps", "gpu"),
}
ORDER = ("cpu", "ram", "ssd", "gpu")
SCORE_MAX = 2000.0                        # right end of the comparison bars (the reference sits at 1000)

i18n.register({
    "en": {
        "pf_tab": "Performance", "pf_title": "Performance benchmark",
        "pf_intro": "Measures what this PC can do: CPU, memory, SSD and graphics card, together with temperatures, "
                    "clocks and power during the run. 1000 points = the reference PC of the metriQ developer. "
                    "Scores are comparable only within metriQ - they do not replace other benchmarks.",
        "pf_warn": "The test puts the PC under full load: fans get loud and the CPU / GPU get hot. Close other "
                   "programs. On a laptop, plug in the charger first.",
        "pf_run_all": "Run all", "pf_start": "Start", "pf_cancel": "Cancel", "pf_overall": "System score",
        "pf_overall_none": "Run the tests to get a score", "pf_pts": "points",
        "pf_partial": "partial: {n} of 4 tests", "pf_cmp": "comparable only within metriQ",
        "pf_confirm_title": "Start benchmark?",
        "pf_confirm": "{tests} will put your PC under full load for about {secs} seconds. Fans will get loud, "
                      "temperatures rise. On a laptop, plug in the charger.\n\nStart now?",
        "pf_ssd_free": "Free space on the temp drive: {gb:.0f} GB. The test writes a 2 GB temporary file (deleted afterwards).",
        "pf_ssd_low": "Only {gb:.1f} GB free on the temp drive: the SSD test will be skipped (needs {need} GB).",
        "pf_cpu_t": "CPU", "pf_ram_t": "Memory", "pf_ssd_t": "SSD", "pf_gpu_t": "Graphics card",
        "pf_cpu_d": "Integer and floating-point kernels, first on one thread, then on all threads (~60 s).",
        "pf_ram_d": "Read, write and copy bandwidth with large buffers plus random-access latency (~15 s).",
        "pf_ssd_d": "Sequential and random 4K read/write on a temporary file, cache bypassed (~30 s).",
        "pf_gpu_d": "Direct3D 11 compute shader: shader throughput and video memory bandwidth (~60 s).",
        "hp_bench_cpu": "Runs integer and floating-point loops in separate processes: one process for the "
                        "single-thread score, one per logical core for the multi-thread score. Records the highest "
                        "temperature, average clock, power and whether the CPU throttled.",
        "hp_bench_ram": "Reads, writes and copies large memory blocks with several threads to measure bandwidth, "
                        "then follows random addresses to estimate access latency (an estimate, not an exact "
                        "nanosecond figure).",
        "hp_bench_ssd": "Uses a temporary file in your Temp folder, written with caching disabled so the drive "
                        "itself is measured. Needs at least 4 GB free; the file is deleted afterwards.",
        "hp_bench_gpu": "Starts the small helper GpuBench.exe, which runs a Direct3D 11 compute shader on the "
                        "fastest graphics card (shader math and video memory bandwidth). Needs no driver add-ons.",
        "hp_bench_score": "Score = 1000 x your result / result of the reference PC, averaged geometrically over the "
                          "test's numbers. Bars show your result against that reference (the line at 1000).",
        "pf_running": "Running {name}", "pf_idle": "Ready", "pf_done": "Finished", "pf_cancelled": "Cancelled",
        "pf_no_result": "No result yet.", "pf_skipped": "Skipped: {why}", "pf_error": "Failed: {why}",
        "pf_single": "Single thread", "pf_multi": "All threads",
        "pf_m_single_int": "Integer, 1 thread", "pf_m_single_float": "Float, 1 thread",
        "pf_m_multi_int": "Integer, all threads", "pf_m_multi_float": "Float, all threads",
        "pf_m_read": "Read", "pf_m_write": "Write", "pf_m_copy": "Copy", "pf_m_latency": "Latency (lower is better)",
        "pf_m_seq_read": "Sequential read", "pf_m_seq_write": "Sequential write",
        "pf_m_rnd_read": "4K random read", "pf_m_rnd_write": "4K random write",
        "pf_m_gflops": "Shader math", "pf_m_bandwidth": "Memory bandwidth",
        "pf_temp": "max {v:.0f} °C", "pf_clock": "avg {v:.0f} MHz", "pf_power": "avg {v:.0f} W / max {m:.0f} W",
        "pf_throttle_no": "no throttling", "pf_throttle_yes": "throttled: {why}", "pf_eff": "{v:.1f} points/W",
        "pf_scaling": "{v:.1f}x scaling on {n} threads",
        "pf_hist": "History", "pf_hist_none": "No runs yet.", "pf_col_date": "Date", "pf_col_all": "Overall",
        "pf_save_csv": "Save CSV", "pf_copy": "Copy", "pf_copied": "Copied to the clipboard.",
        "pf_saved": "Saved: {p}", "pf_showing": "Showing the run of {d}", "pf_latest": "Latest",
        "pf_hist_hint": "Select a row to show that run in the cards above.",
        "pf_quick": "short test mode (METRIQ_BENCH_QUICK)",
    },
    "tr": {
        "pf_tab": "Performans", "pf_title": "Performans testi",
        "pf_intro": "Bu bilgisayarın neler yapabildiğini ölçer: işlemci, bellek, SSD ve ekran kartı; test sırasındaki "
                    "sıcaklık, saat hızı ve güç tüketimiyle birlikte. 1000 puan = metriQ geliştiricisinin referans "
                    "bilgisayarı. Puanlar yalnızca metriQ içinde karşılaştırılabilir; başka testlerin yerini tutmaz.",
        "pf_warn": "Test bilgisayarı tam yük altına sokar: fanlar gürültülü çalışır, işlemci ve ekran kartı ısınır. "
                   "Diğer programları kapat. Dizüstü bilgisayarda önce şarj cihazını tak.",
        "pf_run_all": "Hepsini çalıştır", "pf_start": "Başlat", "pf_cancel": "İptal", "pf_overall": "Sistem puanı",
        "pf_overall_none": "Puan için testleri çalıştır", "pf_pts": "puan",
        "pf_partial": "kısmi: 4 testten {n}'i", "pf_cmp": "yalnızca metriQ içinde karşılaştırılabilir",
        "pf_confirm_title": "Test başlatılsın mı?",
        "pf_confirm": "{tests}, bilgisayarını yaklaşık {secs} saniye tam yük altında çalıştıracak. Fanlar gürültülü "
                      "olur, sıcaklıklar yükselir. Dizüstü bilgisayarda şarj cihazını tak.\n\nŞimdi başlansın mı?",
        "pf_ssd_free": "Geçici disk (Temp) üzerinde boş alan: {gb:.0f} GB. Test 2 GB'lık geçici bir dosya yazar "
                       "(sonra silinir).",
        "pf_ssd_low": "Temp diskinde yalnızca {gb:.1f} GB boş alan var: SSD testi atlanacak (en az {need} GB gerekir).",
        "pf_cpu_t": "İşlemci", "pf_ram_t": "Bellek", "pf_ssd_t": "SSD", "pf_gpu_t": "Ekran kartı",
        "pf_cpu_d": "Tam sayı ve kayan noktalı çekirdekler; önce tek iş parçacığı, sonra tümü (~60 sn).",
        "pf_ram_d": "Büyük arabelleklerle okuma, yazma ve kopyalama hızı, ayrıca rastgele erişim gecikmesi (~15 sn).",
        "pf_ssd_d": "Geçici dosyada sıralı ve rastgele 4K okuma/yazma, önbellek devre dışı (~30 sn).",
        "pf_gpu_d": "Direct3D 11 compute shader: gölgelendirici işlem gücü ve video belleği bant genişliği (~60 sn).",
        "hp_bench_cpu": "Tam sayı ve kayan noktalı döngüleri ayrı süreçlerde çalıştırır: tek iş parçacığı puanı için "
                        "bir süreç, çok iş parçacığı puanı için her mantıksal çekirdekte bir süreç. En yüksek "
                        "sıcaklığı, ortalama saat hızını, gücü ve işlemcinin kısılıp kısılmadığını kaydeder.",
        "hp_bench_ram": "Büyük bellek bloklarını birkaç iş parçacığıyla okur, yazar ve kopyalar (bant genişliği); "
                        "sonra rastgele adresleri izleyerek erişim gecikmesini tahmin eder (kesin bir nanosaniye "
                        "değeri değil, tahmindir).",
        "hp_bench_ssd": "Temp klasöründe geçici bir dosya kullanır; sürücünün kendisi ölçülsün diye önbellek "
                        "kapalı yazılır. En az 4 GB boş alan gerekir; dosya sonra silinir.",
        "hp_bench_gpu": "GpuBench.exe adlı küçük yardımcıyı başlatır; en hızlı ekran kartında bir Direct3D 11 "
                        "compute shader çalıştırır (gölgelendirici matematiği ve video belleği bant genişliği). "
                        "Ek sürücü bileşeni gerekmez.",
        "hp_bench_score": "Puan = 1000 x senin sonucun / referans bilgisayarın sonucu; testin sayılarının geometrik "
                          "ortalamasıdır. Çubuklar sonucunu referansla (1000'deki çizgi) karşılaştırır.",
        "pf_running": "{name} çalışıyor", "pf_idle": "Hazır", "pf_done": "Bitti", "pf_cancelled": "İptal edildi",
        "pf_no_result": "Henüz sonuç yok.", "pf_skipped": "Atlandı: {why}", "pf_error": "Başarısız: {why}",
        "pf_single": "Tek iş parçacığı", "pf_multi": "Tüm iş parçacıkları",
        "pf_m_single_int": "Tam sayı, 1 iş parçacığı", "pf_m_single_float": "Kayan nokta, 1 iş parçacığı",
        "pf_m_multi_int": "Tam sayı, tüm iş parçacıkları", "pf_m_multi_float": "Kayan nokta, tüm iş parçacıkları",
        "pf_m_read": "Okuma", "pf_m_write": "Yazma", "pf_m_copy": "Kopyalama",
        "pf_m_latency": "Gecikme (düşük daha iyi)",
        "pf_m_seq_read": "Sıralı okuma", "pf_m_seq_write": "Sıralı yazma",
        "pf_m_rnd_read": "4K rastgele okuma", "pf_m_rnd_write": "4K rastgele yazma",
        "pf_m_gflops": "Gölgelendirici matematiği", "pf_m_bandwidth": "Bellek bant genişliği",
        "pf_temp": "en çok {v:.0f} °C", "pf_clock": "ort. {v:.0f} MHz", "pf_power": "ort. {v:.0f} W / en çok {m:.0f} W",
        "pf_throttle_no": "kısılma yok", "pf_throttle_yes": "kısıldı: {why}", "pf_eff": "{v:.1f} puan/W",
        "pf_scaling": "{n} iş parçacığında {v:.1f}x ölçekleme",
        "pf_hist": "Geçmiş", "pf_hist_none": "Henüz çalıştırma yok.", "pf_col_date": "Tarih", "pf_col_all": "Toplam",
        "pf_save_csv": "CSV kaydet", "pf_copy": "Kopyala", "pf_copied": "Panoya kopyalandı.",
        "pf_saved": "Kaydedildi: {p}", "pf_showing": "{d} tarihli çalıştırma gösteriliyor", "pf_latest": "Sonuncu",
        "pf_hist_hint": "Bir satırı seçersen o çalıştırma yukarıdaki kartlarda görünür.",
        "pf_quick": "kısa test modu (METRIQ_BENCH_QUICK)",
    },
})


def add_tab(sw):
    """Fill the performance page of the SettingsWindow `sw`."""
    tab = sw.add_page(PAGE)
    PerfTab(sw, tab)
    return tab


# ---------------------------------------------------------------- small drawn parts
def _icon(kind, n, col, bubble):
    """Test icon on a round bubble (anti-aliased)."""
    n = max(16, int(n))

    def paint(d, k):
        c = n / 2
        gfx._disc(d, k, c, c, n / 2, bubble)
        w = max(1.4, n * 0.065)
        u = n / 100.0

        def L(x0, y0, x1, y1, ww=w):
            gfx._line(d, k, x0 * u, y0 * u, x1 * u, y1 * u, ww, col, True)
        if kind == "cpu":
            gfx.rrect(d, k, 31 * u, 31 * u, 69 * u, 69 * u, 6 * u, outline=col, w=w)
            gfx.rrect(d, k, 42 * u, 42 * u, 58 * u, 58 * u, 2 * u, fill=col)
            for p in (40, 60):
                L(p, 20, p, 31)
                L(p, 69, p, 80)
                L(20, p, 31, p)
                L(69, p, 80, p)
        elif kind == "ram":
            gfx.rrect(d, k, 20 * u, 36 * u, 80 * u, 64 * u, 4 * u, outline=col, w=w)
            for x in (30, 43, 56, 69):
                gfx.rrect(d, k, (x - 3.5) * u, 43 * u, (x + 3.5) * u, 57 * u, 1.5 * u, fill=col)
            for x in (30, 42, 54, 66, 76):
                L(x, 64, x, 72)
        elif kind == "ssd":
            gfx.rrect(d, k, 22 * u, 30 * u, 78 * u, 70 * u, 7 * u, outline=col, w=w)
            L(32, 58, 55, 58, w * 1.1)
            gfx._disc(d, k, 68 * u, 58 * u, 3.2 * u, col)
            L(32, 44, 46, 44, w * 0.9)
        else:                                                # gpu: card with a fan
            gfx.rrect(d, k, 17 * u, 31 * u, 83 * u, 66 * u, 6 * u, outline=col, w=w)
            gfx._arc(d, k, 50 * u, 48.5 * u, 11 * u, 0, 360, w, col)
            gfx._disc(d, k, 50 * u, 48.5 * u, 3 * u, col)
            L(26, 66, 26, 74)
            L(34, 66, 34, 74)
    return uikit._cached(("perfico", kind, n, col, bubble), n, n, paint)


class Bar(tk.Canvas):
    """Progress / comparison bar: pill track, pill fill, optional reference tick."""

    def __init__(self, parent, C, h=8, bg=None):
        super().__init__(parent, bg=bg or uikit._bg_of(parent), height=px(h + 2), highlightthickness=0, bd=0)
        self.C, self.h = C, px(h)
        self.frac, self.col, self.tick = 0.0, C["ui"], None
        self._refs = []
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, frac, col=None, tick=None):
        self.frac = max(0.0, min(1.0, frac))
        if col:
            self.col = col
        self.tick = tick
        self.draw()

    def draw(self):
        self.delete("all")
        self._refs = []
        w = self.winfo_width()
        if w < 8:
            return
        h, y = self.h, px(1)
        C = self.C
        fw = max(h, int(round((w * self.frac) / 3.0)) * 3) if self.frac > 0 else 0
        fw = min(fw, w)
        if gfx.AVAILABLE:
            tr = gfx.pill(w, h, C["track"])
            self._refs.append(tr)
            self.create_image(0, y, image=tr, anchor="nw")
            if fw:
                fi = gfx.pill(fw, h, self.col)
                self._refs.append(fi)
                self.create_image(0, y, image=fi, anchor="nw")
        else:
            self.create_rectangle(0, y, w, y + h, fill=C["track"], outline="")
            if fw:
                self.create_rectangle(0, y, fw, y + h, fill=self.col, outline="")
        if self.tick is not None:
            x = int(w * self.tick)
            self.create_line(x, 0, x, h + 2 * y, fill=C["dim"], width=max(1, px(1.5)))


class Metrics(tk.Frame):
    """Rows: name | value | comparison bar against the reference (tick at 1000 points)."""

    def __init__(self, parent, sw, bg, acc):
        super().__init__(parent, bg=bg)
        self.sw, self.bg, self.acc = sw, bg, acc
        self.grid_columnconfigure(0, minsize=px(200))
        self.grid_columnconfigure(1, minsize=px(190))
        self.grid_columnconfigure(2, weight=1)
        self.rows = []

    def show(self, test, res):
        for w in self.winfo_children():
            w.destroy()
        sw, bg = self.sw, self.bg
        order = [k for k in pc.REF[test] if k in res["metrics"]]
        for i, name in enumerate(order):
            v = res["metrics"][name]
            sc = pc.metric_score(test, name, v)
            txt = pc.fmt_value(name, v) + " " + pc.UNITS.get(name, "")
            if name in ("rnd_read", "rnd_write"):
                txt += "  (%s IOPS)" % pc.fmt_value(name + "_iops", res["metrics"].get(name + "_iops"))
            sw.label(self, t("pf_m_" + name), size=9, bg=bg).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=3)
            sw.label(self, txt, size=9, bg=bg).grid(row=i, column=1, sticky="w", padx=(0, 12))
            bar = Bar(self, sw.C, h=7, bg=bg)
            bar.grid(row=i, column=2, sticky="ew", pady=3)
            bar.set(sc / SCORE_MAX, self.acc, 1000.0 / SCORE_MAX)
            sw.label(self, "%.0f" % sc, size=9, bg=bg, dim=True).grid(row=i, column=3, sticky="e", padx=(10, 0))


# ---------------------------------------------------------------- the page
class PerfTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p = sw, sw.C, p
        self.q = queue.Queue()
        self.thread = None
        self.ctl = None
        self.cancel_all = False
        self.sensors = pc.SensorLog()
        self.live = {}                      # test -> result shown
        self.run_results = {}               # results of the run in progress
        self.shown_run = None
        self.cards = {}
        self.hist = pc.load_history()
        self.last_pump = 0.0
        self._build()
        self._show_run(self.hist[-1] if self.hist else None)
        p.bind("<Destroy>", self._destroyed, add="+")

    # ------------------------------------------------------------ build
    def _build(self):
        sw, C, p = self.sw, self.C, self.p
        top = sw.card(p, t("pf_title"))
        bg = top.fillc
        intro = sw.label(top.body, t("pf_intro"), dim=True, size=9, bg=bg)
        uikit.autowrap(intro)
        sw.full(top, intro, pady=(0, 6), divider=False)
        warn = sw.label(top.body, t("pf_warn"), size=9, bg=bg)
        warn.config(fg=C["orange"])
        uikit.autowrap(warn)
        sw.full(top, warn, pady=(0, 8), divider=False)
        bar = tk.Frame(top.body, bg=bg)
        sw.full(top, bar, pady=(2, 4), divider=False)
        self.btn_all = sw.button(bar, t("pf_run_all"), self.run_all, primary=True, bg=bg)
        self.btn_all.pack(side="left")
        self.btn_cancel = sw.button(bar, t("pf_cancel"), self.cancel, bg=bg)
        self.status = sw.label(bar, t("pf_idle"), dim=True, size=9, bg=bg)
        self.status.pack(side="left", padx=(14, 0))
        sc = tk.Frame(bar, bg=bg)
        sc.pack(side="right")
        self.lbl_overall = tk.Label(sc, text="-", bg=bg, fg=C["ui"], font=(sw.F_NUM, 26, "bold"), anchor="e")
        self.lbl_overall.pack(anchor="e")
        sub = tk.Frame(sc, bg=bg)
        sub.pack(anchor="e")
        self.lbl_sub = sw.label(sub, t("pf_overall"), dim=True, size=9, bg=bg)
        self.lbl_sub.pack(side="left")
        helptexts.info_icon(sub, "bench_score", bg=bg).pack(side="left", padx=(6, 0))
        self.lbl_cmp = sw.label(top.body, t("pf_cmp"), dim=True, size=8, bg=bg)
        self.lbl_cmp.config(justify="right", anchor="e")
        sw.full(top, self.lbl_cmp, pady=(0, 0), divider=False, sticky="e")
        if pc.quick():
            q = sw.label(top.body, t("pf_quick"), size=8, bg=bg)
            q.config(fg=C["yellow"])
            sw.full(top, q, pady=(2, 0), divider=False, sticky="w")

        for test in ORDER:
            self._test_card(test)
        self._hist_card()

    def _test_card(self, test):
        sw, C, p = self.sw, self.C, self.p
        tok, ico = META[test]
        acc = C[tok]
        card = sw.card(p, t("pf_%s_t" % test), acc=acc)
        bg = card.fillc
        if gfx.AVAILABLE:                                # own icon in front of the title
            im = _icon(ico, uikit.px(32), acc, uikit.mix(bg, acc, 0.16))
            first = card.hdr.pack_slaves()
            for w in first:
                if isinstance(w, tk.Label) and w.cget("image"):
                    w.destroy()
            lb = tk.Label(card.hdr, image=im, bg=bg, bd=0)
            lb.image = im
            lb.pack(side="left", padx=(0, px(10)), before=card.hdr.pack_slaves()[0])
        d = tk.Frame(card.body, bg=bg)
        sw.full(card, d, pady=(0, 6), divider=False)
        dl = sw.label(d, t("pf_%s_d" % test), dim=True, size=9, bg=bg)
        dl.pack(side="left", fill="x", expand=True)
        uikit.autowrap(dl)
        helptexts.info_icon(d, "bench_" + test, bg=bg).pack(side="left", padx=(8, 0), anchor="n")
        note = None
        if test == "ssd":
            free = pc.free_space_gb()
            if free < pc.MIN_FREE_GB:
                note = (t("pf_ssd_low", gb=free, need=pc.MIN_FREE_GB), C["orange"])
            else:
                note = (t("pf_ssd_free", gb=free), C["dim"])
        if note:
            nl = sw.label(card.body, note[0], size=9, bg=bg)
            nl.config(fg=note[1])
            uikit.autowrap(nl)
            sw.full(card, nl, pady=(0, 6), divider=False)
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 4), divider=False)
        b = sw.button(row, t("pf_start"), lambda tt=test: self.run([tt]), bg=bg)
        b.pack(side="left")
        st = sw.label(row, "", dim=True, size=9, bg=bg)
        st.pack(side="left", padx=(12, 0))
        pb = Bar(card.body, C, h=8, bg=bg)
        pb.set(0, acc)
        pb.grid(row=card.slot(False), column=0, columnspan=2, sticky="ew", pady=(0, 4))
        pb.grid_remove()
        res = tk.Frame(card.body, bg=bg)
        sw.full(card, res, pady=(4, 0), divider=False)
        self.cards[test] = dict(card=card, bg=bg, acc=acc, btn=b, status=st, bar=pb, res=res, bar_shown=False)
        self._render_result(test, None)

    def _hist_card(self):
        sw, C, p = self.sw, self.C, self.p
        from tkinter import ttk
        card = sw.card(p, t("pf_hist"))
        bg = card.fillc
        self.hist_card = card
        box = uikit.Card(card.body, C, fill=C["bg"], pad=(6, 6, 6, 6), radius=8, bg=bg)
        sw.full(card, box, pady=(0, 6), divider=False)
        cols = ("date", "all", "cpu", "ram", "ssd", "gpu")
        self.tree = ttk.Treeview(box.body, columns=cols, show="headings", height=4, style="HP.Treeview",
                                 selectmode="browse")
        heads = {"date": t("pf_col_date"), "all": t("pf_col_all"), "cpu": "CPU", "ram": "RAM", "ssd": "SSD",
                 "gpu": "GPU"}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=150 if c == "date" else 70, anchor="w" if c == "date" else "e",
                             stretch=(c == "date"))
        sb = uikit.Scrollbar(box.body, C, self.tree.yview, bg=C["bg"])
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y", padx=(4, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._pick())
        self.tree.bind("<MouseWheel>", lambda e: (self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                                                  "break")[1])
        hint = sw.label(card.body, t("pf_hist_hint"), dim=True, size=9, bg=bg)
        sw.full(card, hint, pady=(0, 6), divider=False)
        bar = tk.Frame(card.body, bg=bg)
        sw.full(card, bar, pady=(0, 2), divider=False)
        sw.button(bar, t("pf_save_csv"), self.save_csv, bg=bg).pack(side="left")
        sw.button(bar, t("pf_copy"), self.copy, bg=bg).pack(side="left", padx=(8, 0))
        sw.button(bar, t("pf_latest"), self.show_latest, bg=bg).pack(side="left", padx=(8, 0))
        self.hist_msg = sw.label(bar, "", dim=True, size=9, bg=bg)
        self.hist_msg.pack(side="left", padx=(12, 0))
        self._fill_tree()

    # ------------------------------------------------------------ history / display
    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, run in reversed(list(enumerate(self.hist))):
            rs = run.get("results", {})

            def s(k):
                return "%.0f" % rs[k]["score"] if k in rs and rs[k].get("score") else "-"
            self.tree.insert("", "end", iid=str(i), values=(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(run.get("ts", 0))) + (" *" if run.get("quick") else ""),
                "%.0f" % run.get("overall", 0), s("cpu"), s("ram"), s("ssd"), s("gpu")))

    def _pick(self):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            run = self.hist[int(sel[0])]
        except (ValueError, IndexError):
            return
        self._show_run(run)
        self.hist_msg.config(text=t("pf_showing", d=time.strftime("%Y-%m-%d %H:%M", time.localtime(run["ts"]))))

    def show_latest(self):
        if self.busy():
            return
        self.tree.selection_remove(self.tree.selection())
        self.hist_msg.config(text="")
        self._show_run(self.hist[-1] if self.hist else None)

    def _show_run(self, run):
        self.shown_run = run
        rs = (run or {}).get("results", {})
        for test in ORDER:
            self._render_result(test, rs.get(test))
        self._set_overall(rs)

    def _set_overall(self, rs):
        good = {k: v for k, v in rs.items() if v and v.get("score")}
        if not good:
            self.lbl_overall.config(text="-")
            self.lbl_sub.config(text=t("pf_overall_none"))
            return
        self.lbl_overall.config(text="%.0f" % pc.overall(good))
        sub = t("pf_overall") + (" · " + t("pf_partial", n=len(good)) if len(good) < 4 else "")
        self.lbl_sub.config(text=sub)

    def _render_result(self, test, res):
        c = self.cards[test]
        sw, bg, acc = self.sw, c["bg"], c["acc"]
        for w in c["res"].winfo_children():
            w.destroy()
        if not res:
            sw.label(c["res"], t("pf_no_result"), dim=True, size=9, bg=bg).pack(anchor="w")
            return
        if res.get("skipped"):
            lb = sw.label(c["res"], t("pf_skipped", why=res["skipped"]), size=9, bg=bg)
            lb.config(fg=self.C["orange"])
            lb.pack(anchor="w")
            return
        head = tk.Frame(c["res"], bg=bg)
        head.pack(fill="x")
        tk.Label(head, text="%.0f" % res["score"], bg=bg, fg=acc, font=(sw.F_NUM, 22, "bold")).pack(side="left")
        sw.label(head, t("pf_pts"), dim=True, size=9, bg=bg).pack(side="left", padx=(6, 0), pady=(8, 0))
        if res.get("efficiency"):
            sw.label(head, t("pf_eff", v=res["efficiency"]), dim=True, size=9, bg=bg).pack(side="right", pady=(8, 0))
        extra = None
        if test == "cpu" and res.get("scaling"):
            extra = t("pf_scaling", v=res["scaling"], n=res.get("threads", "?"))
        if test == "gpu" and res.get("adapter"):
            extra = res["adapter"]
        if extra:
            sw.label(c["res"], extra, dim=True, size=9, bg=bg).pack(anchor="w")
        m = Metrics(c["res"], sw, bg, acc)
        m.pack(fill="x", pady=(6, 4))
        m.show(test, res)
        s = res.get("sensors") or {}
        pre = "gpu" if test == "gpu" else "cpu"
        bits = []
        if s.get(pre + "_temp_max") is not None:
            bits.append(t("pf_temp", v=s[pre + "_temp_max"]))
        if s.get(pre + "_clock_avg"):
            bits.append(t("pf_clock", v=s[pre + "_clock_avg"]))
        if s.get(pre + "_power_avg"):
            bits.append(t("pf_power", v=s[pre + "_power_avg"], m=s.get(pre + "_power_max") or 0))
        if s.get("throttled") is True:
            bits.append(t("pf_throttle_yes", why=", ".join(s.get("throttle_notes") or []) or "?"))
        elif s.get("throttled") is False:
            bits.append(t("pf_throttle_no"))
        if bits:
            lb = sw.label(c["res"], "  ·  ".join(bits), dim=True, size=9, bg=bg)
            uikit.autowrap(lb)
            lb.pack(anchor="w", fill="x")
        if res.get("cached"):
            lb = sw.label(c["res"], "(!) cache", size=9, bg=bg)
            lb.config(fg=self.C["orange"])
            lb.pack(anchor="w")

    # ------------------------------------------------------------ running
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def run_all(self):
        self.run(list(ORDER))

    def run(self, tests):
        if self.busy():
            return
        secs = {"cpu": 60, "ram": 15, "ssd": 30, "gpu": 60}
        total = int(sum(secs[x] for x in tests) * (0.1 if pc.quick() else 1))
        names = ", ".join(t("pf_%s_t" % x) for x in tests)
        if not messagebox.askokcancel(t("pf_confirm_title"), t("pf_confirm", tests=names, secs=max(total, 5)),
                                      parent=self.sw.win):
            return
        self.cancel_all = False
        self.run_results = {}
        self.show_latest_lock = True
        for test in ORDER:
            c = self.cards[test]
            c["bar"].set(0, c["acc"])
            c["status"].config(text="")
            if test in tests:
                self._bar_show(test, True)
                self._render_result(test, None)
        self.btn_all.config(state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0), after=self.btn_all)
        for c in self.cards.values():
            c["btn"].config(state="disabled")
        self.last_pump = 0.0
        self.sensors = pc.SensorLog()
        self.thread = threading.Thread(target=self._work, args=(list(tests),), daemon=True)
        self.thread.start()
        self.sw.win.after(100, self._poll)

    def _bar_show(self, test, on):
        c = self.cards[test]
        if on and not c["bar_shown"]:
            c["bar"].grid()
            c["bar_shown"] = True
        elif not on and c["bar_shown"]:
            c["bar"].grid_remove()
            c["bar_shown"] = False

    def _work(self, tests):
        for test in tests:
            if self.cancel_all:
                break
            ctl = pc.Ctl(progress=lambda f, tx, tt=test: self.q.put(("p", tt, f, tx)), sensors=self.sensors)
            self.ctl = ctl
            self.q.put(("start", test))
            try:
                res = pc.RUNNERS[test](ctl)
                self.q.put(("done", test, res))
            except pc.Cancelled:
                self.q.put(("cancel", test))
                break
            except Exception as ex:
                self.q.put(("err", test, "%s: %s" % (type(ex).__name__, ex)))
        self.q.put(("end",))

    def cancel(self):
        self.cancel_all = True
        if self.ctl:
            self.ctl.cancel()

    def _poll(self):
        try:
            if not self.p.winfo_exists():
                return
        except tk.TclError:
            return
        ended = False
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "start":
                    c = self.cards[m[1]]
                    c["status"].config(text=t("pf_running", name=t("pf_%s_t" % m[1])))
                    self.status.config(text=t("pf_running", name=t("pf_%s_t" % m[1])))
                elif kind == "p":
                    self.cards[m[1]]["bar"].set(m[2])
                elif kind == "done":
                    res = m[2]
                    c = self.cards[m[1]]
                    c["bar"].set(1.0 if not res.get("skipped") else 0.0)
                    c["status"].config(text=t("pf_done") if not res.get("skipped") else "")
                    self.run_results[m[1]] = res
                    self._render_result(m[1], res)
                elif kind == "cancel":
                    self.cards[m[1]]["status"].config(text=t("pf_cancelled"))
                    self.cards[m[1]]["bar"].set(0)
                elif kind == "err":
                    c = self.cards[m[1]]
                    c["status"].config(text="")
                    self._render_result(m[1], {"skipped": m[2]})
                elif kind == "end":
                    ended = True
        except queue.Empty:
            pass
        now = time.time()
        if now - self.last_pump >= 1.0 and not ended:
            self.last_pump = now
            try:
                self.sensors.add(self.sw.api.readings())
            except Exception:
                pass
        if ended:
            self._finished()
        else:
            self.sw.win.after(100, self._poll)

    def _finished(self):
        self.btn_cancel.pack_forget()
        self.btn_all.config(state="normal")
        for c in self.cards.values():
            c["btn"].config(state="normal")
        for test in ORDER:
            self._bar_show(test, False)
        good = {k: v for k, v in self.run_results.items() if v.get("score")}
        if good:
            run = pc.new_run(good)
            try:
                self.hist = pc.save_run(run)
            except OSError:
                self.hist.append(run)
            self._fill_tree()
            self._set_overall(good)
            self.status.config(text=t("pf_done"))
        else:
            self.status.config(text=t("pf_cancelled") if self.cancel_all else t("pf_idle"))
        self.thread = None

    def _destroyed(self, e=None):
        if e is not None and e.widget is not self.p:
            return
        self.cancel()

    # ------------------------------------------------------------ export
    def _current_run(self):
        if self.shown_run:
            return self.shown_run
        return self.hist[-1] if self.hist else None

    def save_csv(self):
        run = self._current_run()
        if not run:
            return
        name = "metriq-benchmark-%s.csv" % time.strftime("%Y%m%d-%H%M", time.localtime(run["ts"]))
        path = filedialog.asksaveasfilename(parent=self.sw.win, defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(pc.run_to_csv(run))
            self.hist_msg.config(text=t("pf_saved", p=os.path.basename(path)))
        except OSError as ex:
            self.hist_msg.config(text=str(ex))

    def copy(self):
        run = self._current_run()
        if not run:
            return
        self.sw.win.clipboard_clear()
        self.sw.win.clipboard_append(pc.run_to_csv(run))
        self.hist_msg.config(text=t("pf_copied"))
