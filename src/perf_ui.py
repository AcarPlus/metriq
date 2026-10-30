"""Settings page "Performance": benchmark cards (CPU, RAM, SSD, GPU), Quick / Detailed mode, run all, results, history,
export and the stress / stability test. Engine and scoring: perf_core.py (runs in a separate process, this page only
polls its message queue ~7 times a second). Hook: settings_ui calls add_tab(settings) for the page prepared in PAGES."""
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import config
import gfx
import helptexts
import i18n
import perf_core as pc
import perf_stress
import uikit
from i18n import t
from perf_widgets import Bar, Chart
from uikit import px

PAGE = 6                                  # index in settings_ui.PAGES
META = {                                  # test -> (accent token, icon, title key)
    "cpu": ("accent_clock", "cpu"), "ram": ("accent_net", "ram"),
    "ssd": ("accent_fan", "ssd"), "gpu": ("accent_fps", "gpu"),
}
ORDER = ("cpu", "ram", "ssd", "gpu")
SCORE_MAX = 2000.0                        # right end of the comparison bars
POLL_MS = 140                             # message queue polling (~7 Hz)

i18n.register({
    "en": {
        "pf_tab": "Performance", "pf_title": "Performance benchmark",
        "pf_intro": "Measures what this PC can do: CPU, memory, SSD and graphics card, together with temperatures, "
                    "clocks and power during the run. Scores are comparable only within metriQ - they do not replace "
                    "other benchmarks.",
        "pf_warn": "The test puts the PC under full load: fans get loud and the CPU / GPU get hot. Close other "
                   "programs. On a laptop, plug in the charger first.",
        "pf_mode": "Mode", "pf_mode_quick": "Quick", "pf_mode_detailed": "Detailed",
        "pf_mode_hint_quick": "Quick: one pass of every test, about {t} for all four tests.",
        "pf_mode_hint_detailed": "Detailed: about 3x longer - every test is repeated (mean and variation), plus a "
                                 "sustained-load section, memory block sizes, SSD queue depths and a longer GPU run. "
                                 "About {t} for all four tests.",
        "pf_dur": "Quick ~{q}, Detailed ~{d}.",
        "pf_s": "{n} s", "pf_min": "{n} min",
        "pf_run_all": "Run all", "pf_start": "Start", "pf_cancel": "Cancel", "pf_overall": "System score",
        "pf_overall_none": "Run the tests to get a score", "pf_pts": "points",
        "pf_partial": "partial: {n} of 4 tests", "pf_cmp": "comparable only within metriQ",
        "pf_confirm_title": "Start benchmark?",
        "pf_confirm": "{tests} ({mode}) will put your PC under full load for about {secs}. Fans will get loud, "
                      "temperatures rise. On a laptop, plug in the charger.\n\nStart now?",
        "pf_ssd_free": "Free space on {drive}: {gb:.0f} GB. The test writes a {size:g} GB temporary file "
                       "(deleted afterwards).",
        "pf_ssd_low": "Only {gb:.1f} GB free on {drive}: the SSD test will be skipped (needs {need} GB).",
        "pf_ssd_drive": "Drive", "pf_ssd_auto": "{drive} · Temp folder",
        "pf_ssd_hdd": "This drive is a hard disk (HDD) or behind a slow bus: about 100-250 MB/s is normal there.",
        "pf_cpu_t": "CPU", "pf_ram_t": "Memory", "pf_ssd_t": "SSD", "pf_gpu_t": "Graphics card",
        "pf_cpu_d": "Integer and floating-point kernels, first on one thread, then on all threads. Detailed adds "
                    "repeated runs (median) and a 90 s sustained-load section.",
        "pf_ram_d": "Read, write and copy bandwidth with large buffers plus random-access latency. Detailed adds "
                    "repeated runs and a bandwidth curve over block sizes (cache to DRAM).",
        "pf_ssd_d": "Sequential 1 MB read/write with 8 requests in flight and random 4K read/write on a temporary "
                    "file, cache bypassed. Detailed uses a larger file, repeated runs and more queue depths.",
        "pf_gpu_d": "Direct3D 11 compute shader: shader throughput and video memory bandwidth. Detailed runs three "
                    "times in a row and shows how clock and temperature hold up.",
        "hp_bench_cpu": "Runs integer and floating-point loops in separate processes: one process for the "
                        "single-thread score, one per logical core for the multi-thread score. Records the highest "
                        "temperature, average clock, power and whether the CPU throttled. Detailed mode repeats the "
                        "runs (median and variation) and keeps all cores busy for 90 s to see whether clock, "
                        "temperature and speed stay stable.",
        "hp_bench_ram": "Reads, writes and copies large memory blocks with several threads to measure bandwidth, "
                        "then follows random addresses to estimate access latency (an estimate, not an exact "
                        "nanosecond figure). Records the DIMM temperatures (SPD hub), memory clock and CPU package "
                        "power. Detailed mode also measures bandwidth for small and large blocks, which shows the "
                        "cache levels and where main memory begins.",
        "hp_bench_ssd": "Uses a temporary file in your Temp folder (or on the drive picked below), written with "
                        "caching disabled so the drive "
                        "itself is measured. Needs at least 4 GB free; the file is deleted afterwards. Records the "
                        "drive temperature and the peak read / write rate. Detailed mode uses a larger file and "
                        "also tests several queue depths.",
        "hp_bench_gpu": "Starts the small helper GpuBench.exe, which runs a Direct3D 11 compute shader on the "
                        "fastest graphics card (shader math and video memory bandwidth). Needs no driver add-ons. "
                        "Records GPU temperature, hot spot, clock and power. Detailed mode runs it three times.",
        "hp_bench_score": "Higher is better. The score combines the measurements of a test into one number "
                          "(geometric mean); the overall score combines the test scores. Scores are comparable only "
                          "within metriQ - not with other benchmark programs.",
        "hp_bench_mode": "Quick measures every test once. Detailed takes about 3 to 4 times longer: it repeats the "
                         "measurements and shows the average with its variation (± %), adds a sustained-load "
                         "section (clock and temperature over time), memory block sizes, SSD queue depths and a "
                         "longer GPU run. History entries remember the mode.",
        "pf_running": "Running {name}", "pf_idle": "Ready", "pf_done": "Finished", "pf_cancelled": "Cancelled",
        "pf_cancelling": "Cancelling...",
        "pf_no_result": "No result yet.", "pf_skipped": "Skipped: {why}", "pf_error": "Failed: {why}",
        "pf_single": "Single thread", "pf_multi": "All threads",
        "pf_m_single_int": "Integer, 1 thread", "pf_m_single_float": "Float, 1 thread",
        "pf_m_multi_int": "Integer, all threads", "pf_m_multi_float": "Float, all threads",
        "pf_m_read": "Read", "pf_m_write": "Write", "pf_m_copy": "Copy", "pf_m_latency": "Latency (lower is better)",
        "pf_m_seq_read": "Sequential read", "pf_m_seq_write": "Sequential write",
        "pf_m_rnd_read": "4K random read", "pf_m_rnd_write": "4K random write",
        "pf_m_gflops": "Shader math", "pf_m_bandwidth": "Memory bandwidth",
        "pf_x_seq_read_q8": "Sequential read, queue depth 8", "pf_x_seq_write_q8": "Sequential write, queue depth 8",
        "pf_x_seq_read_q1": "Sequential read, queue depth 1", "pf_x_seq_write_q1": "Sequential write, queue depth 1",
        "pf_x_rnd_read_q4": "4K random read, queue depth 4", "pf_x_rnd_read_q32": "4K random read, queue depth 32",
        "pf_pm": "±{v:.1f} %",
        "pf_temp": "max {v:.0f} °C", "pf_clock": "avg {v:.0f} MHz", "pf_power": "avg {v:.0f} W / max {m:.0f} W",
        "pf_hot": "hot spot max {v:.0f} °C",
        "pf_dimm": "DIMM {names}: max {temps} °C", "pf_memclk": "memory clock avg {v:.0f} MHz",
        "pf_cpupower": "CPU package power avg {v:.0f} W / max {m:.0f} W",
        "pf_drive_temp": "drive max {v:.0f} °C", "pf_peak_r": "read peak {v:.0f} MB/s",
        "pf_peak_w": "write peak {v:.0f} MB/s",
        "pf_throttle_no": "no throttling", "pf_throttle_yes": "throttled: {why}",
        "pf_scaling": "{v:.1f}x scaling on {n} threads",
        "pf_live": "now: {s}",
        "pf_sustain": "Sustained load ({secs:.0f} s)",
        "pf_sus_sum": "Clock {cmin:.0f}-{cmax:.0f} MHz ({cd:+.1f} % first to last fifth), temperature {t0}{t1}, "
                      "max {tm}{perf}. {thr}",
        "pf_sus_perf": ", speed {p:+.1f} %",
        "pf_sus_temp": "{a:.0f} to {b:.0f} °C",
        "pf_leg_clock": "clock", "pf_leg_temp": "temperature", "pf_leg_perf": "speed",
        "pf_curve": "Read bandwidth by block size (cache to main memory)",
        "pf_curve_sum": "Fastest: {fast:.0f} GB/s at {fsz}, main memory: {slow:.0f} GB/s at {ssz}",
        "pf_extra": "More tests", "pf_runs": "{n} runs",
        "pf_hist": "History", "pf_hist_none": "No runs yet.", "pf_col_date": "Date", "pf_col_all": "Overall",
        "pf_col_mode": "Mode",
        "pf_save_csv": "Save CSV", "pf_copy": "Copy", "pf_copied": "Copied to the clipboard.",
        "pf_saved": "Saved: {p}", "pf_showing": "Showing the run of {d}", "pf_latest": "Latest",
        "pf_hist_hint": "Select a row to show that run in the cards above.",
        "pf_quick": "short test mode (METRIQ_BENCH_QUICK)",
    },
    "tr": {
        "pf_tab": "Performans", "pf_title": "Performans testi",
        "pf_intro": "Bu bilgisayarın neler yapabildiğini ölçer: işlemci, bellek, SSD ve ekran kartı; test sırasındaki "
                    "sıcaklık, saat hızı ve güç tüketimiyle birlikte. Puanlar yalnızca metriQ içinde "
                    "karşılaştırılabilir; başka testlerin yerini tutmaz.",
        "pf_warn": "Test bilgisayarı tam yük altına sokar: fanlar gürültülü çalışır, işlemci ve ekran kartı ısınır. "
                   "Diğer programları kapat. Dizüstü bilgisayarda önce şarj cihazını tak.",
        "pf_mode": "Mod", "pf_mode_quick": "Hızlı", "pf_mode_detailed": "Detaylı",
        "pf_mode_hint_quick": "Hızlı: her testin tek turu, dört test için yaklaşık {t}.",
        "pf_mode_hint_detailed": "Detaylı: yaklaşık 3 kat uzun - her test tekrarlanır (ortalama ve değişim), ayrıca "
                                 "sürekli yük bölümü, bellek blok boyutları, SSD kuyruk derinlikleri ve daha uzun "
                                 "bir GPU testi eklenir. Dört test için yaklaşık {t}.",
        "pf_dur": "Hızlı ~{q}, Detaylı ~{d}.",
        "pf_s": "{n} sn", "pf_min": "{n} dk",
        "pf_run_all": "Hepsini çalıştır", "pf_start": "Başlat", "pf_cancel": "İptal", "pf_overall": "Sistem puanı",
        "pf_overall_none": "Puan için testleri çalıştır", "pf_pts": "puan",
        "pf_partial": "kısmi: 4 testten {n}'i", "pf_cmp": "yalnızca metriQ içinde karşılaştırılabilir",
        "pf_confirm_title": "Test başlatılsın mı?",
        "pf_confirm": "{tests} ({mode}), bilgisayarını yaklaşık {secs} tam yük altında çalıştıracak. Fanlar gürültülü "
                      "olur, sıcaklıklar yükselir. Dizüstü bilgisayarda şarj cihazını tak.\n\nŞimdi başlansın mı?",
        "pf_ssd_free": "{drive} üzerinde boş alan: {gb:.0f} GB. Test {size:g} GB'lık geçici bir dosya "
                       "yazar (sonra silinir).",
        "pf_ssd_low": "{drive} üzerinde yalnızca {gb:.1f} GB boş alan var: SSD testi atlanacak (en az {need} GB "
                      "gerekir).",
        "pf_ssd_drive": "Sürücü", "pf_ssd_auto": "{drive} · Temp klasörü",
        "pf_ssd_hdd": "Bu sürücü sabit disk (HDD) ya da yavaş bir veri yolunda: orada 100-250 MB/s normaldir.",
        "pf_cpu_t": "İşlemci", "pf_ram_t": "Bellek", "pf_ssd_t": "SSD", "pf_gpu_t": "Ekran kartı",
        "pf_cpu_d": "Tam sayı ve kayan noktalı çekirdekler; önce tek iş parçacığı, sonra tümü. Detaylı mod tekrarlı "
                    "çalıştırma (medyan) ve 90 sn'lik sürekli yük bölümü ekler.",
        "pf_ram_d": "Büyük arabelleklerle okuma, yazma ve kopyalama hızı, ayrıca rastgele erişim gecikmesi. Detaylı "
                    "mod tekrarlı çalıştırma ve blok boyutuna göre bant genişliği eğrisi (önbellekten DRAM'e) ekler.",
        "pf_ssd_d": "Geçici dosyada 8 istek kuyrukta 1 MB sıralı ve 4K rastgele okuma/yazma, önbellek devre dışı. "
                    "Detaylı mod daha büyük dosya, tekrarlı çalıştırma ve daha fazla kuyruk derinliği kullanır.",
        "pf_gpu_d": "Direct3D 11 compute shader: gölgelendirici işlem gücü ve video belleği bant genişliği. Detaylı "
                    "mod üç kez art arda çalışır ve saat hızı ile sıcaklığın nasıl tuttuğunu gösterir.",
        "hp_bench_cpu": "Tam sayı ve kayan noktalı döngüleri ayrı süreçlerde çalıştırır: tek iş parçacığı puanı için "
                        "bir süreç, çok iş parçacığı puanı için her mantıksal çekirdekte bir süreç. En yüksek "
                        "sıcaklığı, ortalama saat hızını, gücü ve işlemcinin kısılıp kısılmadığını kaydeder. Detaylı "
                        "mod çalıştırmaları tekrarlar (medyan ve değişim) ve saat hızı, sıcaklık ve hızın sabit "
                        "kalıp kalmadığını görmek için tüm çekirdekleri 90 sn meşgul tutar.",
        "hp_bench_ram": "Büyük bellek bloklarını birkaç iş parçacığıyla okur, yazar ve kopyalar (bant genişliği); "
                        "sonra rastgele adresleri izleyerek erişim gecikmesini tahmin eder (kesin bir nanosaniye "
                        "değeri değil, tahmindir). DIMM sıcaklıklarını (SPD hub), bellek saatini ve CPU paket gücünü "
                        "kaydeder. Detaylı mod küçük ve büyük bloklarla da bant genişliği ölçer; bu, önbellek "
                        "katmanlarını ve ana belleğin nerede başladığını gösterir.",
        "hp_bench_ssd": "Temp klasöründe (ya da aşağıda seçilen sürücüde) geçici bir dosya kullanır; sürücünün "
                        "kendisi ölçülsün diye önbellek "
                        "kapalı yazılır. En az 4 GB boş alan gerekir; dosya sonra silinir. Sürücü sıcaklığını ve "
                        "en yüksek okuma / yazma hızını kaydeder. Detaylı mod daha büyük dosya kullanır ve birkaç "
                        "kuyruk derinliğini de dener.",
        "hp_bench_gpu": "GpuBench.exe adlı küçük yardımcıyı başlatır; en hızlı ekran kartında bir Direct3D 11 "
                        "compute shader çalıştırır (gölgelendirici matematiği ve video belleği bant genişliği). "
                        "Ek sürücü bileşeni gerekmez. GPU sıcaklığını, sıcak noktayı, saat hızını ve gücü kaydeder. "
                        "Detaylı mod üç kez çalıştırır.",
        "hp_bench_score": "Yüksek daha iyidir. Puan, bir testin ölçümlerini tek bir sayıda birleştirir (geometrik "
                          "ortalama); sistem puanı test puanlarını birleştirir. Puanlar yalnızca metriQ içinde "
                          "karşılaştırılabilir - başka test programlarıyla değil.",
        "hp_bench_mode": "Hızlı, her testi bir kez ölçer. Detaylı yaklaşık 3-4 kat uzun sürer: ölçümleri tekrarlar, "
                         "ortalamayı değişimiyle (± %) gösterir; sürekli yük bölümü (zamana göre saat ve sıcaklık), "
                         "bellek blok boyutları, SSD kuyruk derinlikleri ve daha uzun GPU testi ekler. Geçmiş "
                         "kayıtları modu hatırlar.",
        "pf_running": "{name} çalışıyor", "pf_idle": "Hazır", "pf_done": "Bitti", "pf_cancelled": "İptal edildi",
        "pf_cancelling": "İptal ediliyor...",
        "pf_no_result": "Henüz sonuç yok.", "pf_skipped": "Atlandı: {why}", "pf_error": "Başarısız: {why}",
        "pf_single": "Tek iş parçacığı", "pf_multi": "Tüm iş parçacıkları",
        "pf_m_single_int": "Tam sayı, 1 iş parçacığı", "pf_m_single_float": "Kayan nokta, 1 iş parçacığı",
        "pf_m_multi_int": "Tam sayı, tüm iş parçacıkları", "pf_m_multi_float": "Kayan nokta, tüm iş parçacıkları",
        "pf_m_read": "Okuma", "pf_m_write": "Yazma", "pf_m_copy": "Kopyalama",
        "pf_m_latency": "Gecikme (düşük daha iyi)",
        "pf_m_seq_read": "Sıralı okuma", "pf_m_seq_write": "Sıralı yazma",
        "pf_m_rnd_read": "4K rastgele okuma", "pf_m_rnd_write": "4K rastgele yazma",
        "pf_m_gflops": "Gölgelendirici matematiği", "pf_m_bandwidth": "Bellek bant genişliği",
        "pf_x_seq_read_q8": "Sıralı okuma, kuyruk derinliği 8", "pf_x_seq_write_q8": "Sıralı yazma, kuyruk derinliği 8",
        "pf_x_seq_read_q1": "Sıralı okuma, kuyruk derinliği 1", "pf_x_seq_write_q1": "Sıralı yazma, kuyruk derinliği 1",
        "pf_x_rnd_read_q4": "4K rastgele okuma, kuyruk derinliği 4",
        "pf_x_rnd_read_q32": "4K rastgele okuma, kuyruk derinliği 32",
        "pf_pm": "±{v:.1f} %",
        "pf_temp": "en çok {v:.0f} °C", "pf_clock": "ort. {v:.0f} MHz", "pf_power": "ort. {v:.0f} W / en çok {m:.0f} W",
        "pf_hot": "sıcak nokta en çok {v:.0f} °C",
        "pf_dimm": "DIMM {names}: en çok {temps} °C", "pf_memclk": "bellek saati ort. {v:.0f} MHz",
        "pf_cpupower": "CPU paket gücü ort. {v:.0f} W / en çok {m:.0f} W",
        "pf_drive_temp": "sürücü en çok {v:.0f} °C", "pf_peak_r": "okuma tepesi {v:.0f} MB/sn",
        "pf_peak_w": "yazma tepesi {v:.0f} MB/sn",
        "pf_throttle_no": "kısılma yok", "pf_throttle_yes": "kısıldı: {why}",
        "pf_scaling": "{n} iş parçacığında {v:.1f}x ölçekleme",
        "pf_live": "şimdi: {s}",
        "pf_sustain": "Sürekli yük ({secs:.0f} sn)",
        "pf_sus_sum": "Saat {cmin:.0f}-{cmax:.0f} MHz (ilk ve son beşte bir arası {cd:+.1f} %), sıcaklık {t0}{t1}, "
                      "en çok {tm}{perf}. {thr}",
        "pf_sus_perf": ", hız {p:+.1f} %",
        "pf_sus_temp": "{a:.0f} ile {b:.0f} °C",
        "pf_leg_clock": "saat", "pf_leg_temp": "sıcaklık", "pf_leg_perf": "hız",
        "pf_curve": "Blok boyutuna göre okuma bant genişliği (önbellekten ana belleğe)",
        "pf_curve_sum": "En hızlı: {fsz} blokta {fast:.0f} GB/sn, ana bellek: {ssz} blokta {slow:.0f} GB/sn",
        "pf_extra": "Ek testler", "pf_runs": "{n} çalıştırma",
        "pf_hist": "Geçmiş", "pf_hist_none": "Henüz çalıştırma yok.", "pf_col_date": "Tarih", "pf_col_all": "Toplam",
        "pf_col_mode": "Mod",
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


def fmt_secs(n):
    n = int(round(n))
    return t("pf_s", n=n) if n < 90 else t("pf_min", n=max(1, round(n / 60.0)))


def fmt_size(b):
    return "%d KB" % (b >> 10) if b < (1 << 20) else "%d MB" % (b >> 20)


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


class Metrics(tk.Frame):
    """Rows: name | value (+- variation) | comparison bar against the best bar position."""

    def __init__(self, parent, sw, bg, acc):
        super().__init__(parent, bg=bg)
        self.sw, self.bg, self.acc = sw, bg, acc
        self.grid_columnconfigure(0, minsize=px(200))
        self.grid_columnconfigure(1, minsize=px(210))
        self.grid_columnconfigure(2, weight=1)

    def show(self, test, res):
        for w in self.winfo_children():
            w.destroy()
        sw, bg = self.sw, self.bg
        order = [k for k in pc.REF[test] if k in res["metrics"]]
        spread = res.get("spread") or {}
        for i, name in enumerate(order):
            v = res["metrics"][name]
            sc = pc.metric_score(test, name, v)
            txt = pc.fmt_value(name, v) + " " + pc.UNITS.get(name, "")
            if spread.get(name) is not None:
                txt += "  " + t("pf_pm", v=spread[name])
            if name in ("rnd_read", "rnd_write"):
                txt += "  (%s IOPS)" % pc.fmt_value(name + "_iops", res["metrics"].get(name + "_iops"))
            sw.label(self, t("pf_m_" + name), size=9, bg=bg).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=3)
            sw.label(self, txt, size=9, bg=bg).grid(row=i, column=1, sticky="w", padx=(0, 12))
            bar = Bar(self, sw.C, h=7, bg=bg)
            bar.grid(row=i, column=2, sticky="ew", pady=3)
            bar.set(sc / SCORE_MAX, self.acc)
            sw.label(self, "%.0f" % sc, size=9, bg=bg, dim=True).grid(row=i, column=3, sticky="e", padx=(10, 0))


# ---------------------------------------------------------------- the page
class PerfTab:
    def __init__(self, sw, p):
        self.sw, self.C, self.p = sw, sw.C, p
        self.job = None
        self.job_kind = "bench"
        self.job_mode = "quick"
        self.cancel_all = False
        self.cancel_at = 0.0
        self.mode_idx = 0
        self.run_results = {}               # results of the run in progress
        self.shown_run = None
        self.cards = {}
        self.hist = pc.load_history()
        self.stress = perf_stress.StressCard(self)
        self._build()
        self._show_run(self.hist[-1] if self.hist else None)
        p.bind("<Destroy>", self._destroyed, add="+")

    @property
    def mode(self):
        return pc.MODES[self.mode_idx]

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
        mrow = tk.Frame(top.body, bg=bg)
        sw.full(top, mrow, pady=(0, 2), divider=False)
        sw.label(mrow, t("pf_mode"), bg=bg).pack(side="left", padx=(0, 6))
        helptexts.info_icon(mrow, "bench_mode", bg=bg).pack(side="left", padx=(0, 10))
        self.seg_mode = uikit.Segmented(mrow, C, [t("pf_mode_quick"), t("pf_mode_detailed")], 0, self._mode_changed,
                                        (sw.F_TXT, 10), bg=bg)
        self.seg_mode.pack(side="left")
        self.mode_hint = sw.label(top.body, "", dim=True, size=9, bg=bg)
        uikit.autowrap(self.mode_hint)
        sw.full(top, self.mode_hint, pady=(2, 8), divider=False)
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
        self.stress.build()
        self._hist_card()
        self._mode_changed(0)

    def _total_secs(self, tests, mode):
        k = 0.1 if pc.quick() else 1.0
        return sum(pc.DURATION[mode][x] for x in tests) * k

    def _mode_changed(self, i):
        self.mode_idx = i
        total = fmt_secs(max(5, self._total_secs(ORDER, self.mode)))
        self.mode_hint.config(text=t("pf_mode_hint_" + self.mode, t=total))
        self._ssd_note()

    def _ssd_note(self):
        lb = getattr(self, "ssd_note", None)
        if lb is None:
            return
        d = pc.ssd_tmp_dir(self.sw.api.cfg.get("bench_ssd_drive", "auto"))
        free = pc.free_space_gb(d)
        drive = os.path.splitdrive(d)[0] or d
        if free < pc.MIN_FREE_GB:
            lb.config(text=t("pf_ssd_low", gb=free, need=pc.MIN_FREE_GB, drive=drive), fg=self.C["orange"])
        else:
            lb.config(text=t("pf_ssd_free", gb=free, size=pc.ssd_file_gb(self.mode, free), drive=drive),
                      fg=self.C["dim"])

    def _ssd_picker(self, parent, bg):
        """Drive the SSD test runs on: the Temp folder's drive (default) or another local fixed drive."""
        tmp_drive = os.path.splitdrive(pc.ssd_tmp_dir())[0]
        drives = [d for d in pc.fixed_drives() if d.upper() != tmp_drive.upper()]
        if not drives:
            return
        keys = ["auto"] + drives
        names = [t("pf_ssd_auto", drive=pc.drive_label(tmp_drive))] + [pc.drive_label(d) for d in drives]
        cur = str(self.sw.api.cfg.get("bench_ssd_drive", "auto")).upper()
        idx = next((i for i, k in enumerate(keys) if k.upper() == cur), 0)
        row = tk.Frame(parent, bg=bg)
        self.sw.label(row, t("pf_ssd_drive"), size=9, bg=bg).pack(side="left", padx=(0, 8))
        cb = self.sw.combo(row, names, idx, width=min(44, max(len(n) for n in names) + 2))
        cb.pack(side="left")

        def picked(e=None):
            self.sw.api.cfg["bench_ssd_drive"] = keys[max(0, cb.current())]
            config.save_keys(self.sw.api.cfg, "bench_ssd_drive")
            self._ssd_note()
        cb.bind("<<ComboboxSelected>>", picked)
        return row

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
        dur = t("pf_dur", q=fmt_secs(pc.DURATION["quick"][test]), d=fmt_secs(pc.DURATION["detailed"][test]))
        dl = sw.label(d, t("pf_%s_d" % test) + " " + dur, dim=True, size=9, bg=bg)
        dl.pack(side="left", fill="x", expand=True)
        uikit.autowrap(dl)
        helptexts.info_icon(d, "bench_" + test, bg=bg).pack(side="left", padx=(8, 0), anchor="n")
        if test == "ssd":
            nl = sw.label(card.body, "", size=9, bg=bg)
            uikit.autowrap(nl)
            sw.full(card, nl, pady=(0, 6), divider=False)
            self.ssd_note = nl
            pick = self._ssd_picker(card.body, bg)
            if pick is not None:
                sw.full(card, pick, pady=(0, 6), divider=False)
        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 4), divider=False)
        b = sw.button(row, t("pf_start"), lambda tt=test: self.run([tt]), bg=bg)
        b.pack(side="left")
        st = sw.label(row, "", dim=True, size=9, bg=bg)
        st.pack(side="left", padx=(12, 0))
        live = sw.label(card.body, "", dim=True, size=9, bg=bg)
        uikit.autowrap(live)
        live_row = sw.full(card, live, pady=(0, 2), divider=False)
        live_row.grid_remove()
        pb = Bar(card.body, C, h=8, bg=bg)
        pb.set(0, acc)
        pb.grid(row=card.slot(False), column=0, columnspan=2, sticky="ew", pady=(0, 4))
        pb.grid_remove()
        res = tk.Frame(card.body, bg=bg)
        sw.full(card, res, pady=(4, 0), divider=False)
        self.cards[test] = dict(card=card, bg=bg, acc=acc, btn=b, status=st, bar=pb, res=res, bar_shown=False,
                                live=live, live_shown=False)
        self._render_result(test, None)

    def _hist_card(self):
        sw, C, p = self.sw, self.C, self.p
        from tkinter import ttk
        card = sw.card(p, t("pf_hist"))
        bg = card.fillc
        self.hist_card = card
        box = uikit.Card(card.body, C, fill=C["bg"], pad=(6, 6, 6, 6), radius=8, bg=bg)
        sw.full(card, box, pady=(0, 6), divider=False)
        cols = ("date", "mode", "all", "cpu", "ram", "ssd", "gpu")
        self.tree = ttk.Treeview(box.body, columns=cols, show="headings", height=4, style="HP.Treeview",
                                 selectmode="browse")
        heads = {"date": t("pf_col_date"), "mode": t("pf_col_mode"), "all": t("pf_col_all"), "cpu": "CPU",
                 "ram": "RAM", "ssd": "SSD", "gpu": "GPU"}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=140 if c == "date" else (80 if c == "mode" else 66),
                             anchor="w" if c in ("date", "mode") else "e", stretch=(c == "date"))
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
                t("pf_mode_" + (run.get("mode") if run.get("mode") in pc.MODES else "quick")),
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

    def _sensor_bits(self, test, res):
        s = pc.sens_view(test, res.get("sensors"))
        bits = []
        if test == "ram":
            if s.get("dimm_max"):
                bits.append(t("pf_dimm", names=", ".join(s.get("dimm_names") or []),
                              temps="/".join("%.0f" % x for x in s["dimm_max"])))
            if s.get("clock_avg"):
                bits.append(t("pf_memclk", v=s["clock_avg"]))
            if s.get("power_avg"):
                bits.append(t("pf_cpupower", v=s["power_avg"], m=s.get("power_max") or 0))
        elif test == "ssd":
            if s.get("temp_max") is not None:
                bits.append(t("pf_drive_temp", v=s["temp_max"]))
            if s.get("read_peak"):
                bits.append(t("pf_peak_r", v=s["read_peak"]))
            if s.get("write_peak"):
                bits.append(t("pf_peak_w", v=s["write_peak"]))
        else:
            if s.get("temp_max") is not None:
                bits.append(t("pf_temp", v=s["temp_max"]))
            if s.get("hot_max") is not None:
                bits.append(t("pf_hot", v=s["hot_max"]))
            if s.get("clock_avg"):
                bits.append(t("pf_clock", v=s["clock_avg"]))
            if s.get("power_avg"):
                bits.append(t("pf_power", v=s["power_avg"], m=s.get("power_max") or 0))
            if s.get("throttled") is True:
                bits.append(t("pf_throttle_yes", why=", ".join(s.get("throttle_notes") or []) or "?"))
            elif s.get("throttled") is False:
                bits.append(t("pf_throttle_no"))
        return bits

    def _legend(self, parent, items, bg):
        leg = tk.Frame(parent, bg=bg)
        for col, txt in items:
            tk.Label(leg, text="●", fg=col, bg=bg, font=(self.sw.F_TXT, 9)).pack(side="left")
            self.sw.label(leg, txt, dim=True, size=8, bg=bg).pack(side="left", padx=(2, 12))
        return leg

    def _render_sustain(self, test, res, parent, bg):
        sus = res.get("sustain")
        if not sus:
            return
        sw, C = self.sw, self.C
        ser = sus.get("series") or {}
        pre = "gpu" if test == "gpu" else "cpu"
        lines, leg = [], []
        for key, col, name, span in ((pre + "_clock", C["accent_clock"], t("pf_leg_clock"), 0.12),
                                     (pre + "_temp", C["orange"], t("pf_leg_temp"), 0.3),
                                     ("perf", C["accent_net"], t("pf_leg_perf"), 0.12)):
            vals = ser.get(key)
            if vals and any(x is not None for x in vals):
                lines.append(dict(color=col, values=vals, span=span))
                leg.append((col, name))
        if len(ser.get("t", [])) < 3 or not lines:
            return
        sw.label(parent, t("pf_sustain", secs=sus.get("secs", 0)), bold=True, size=9, bg=bg).pack(anchor="w", pady=(8, 2))
        Chart(parent, C, lines, xs=ser["t"], height=90, bg=bg).pack(fill="x")
        self._legend(parent, leg, bg).pack(anchor="w", pady=(2, 0))
        t0, t1 = sus.get("temp_start"), sus.get("temp_end")
        tt = t("pf_sus_temp", a=t0, b=t1) if t0 is not None and t1 is not None else "-"
        thr = t("pf_throttle_yes", why=", ".join(sus.get("notes") or []) or "?") if sus.get("throttled") else \
            (t("pf_throttle_no") if sus.get("throttled") is False else "")
        cd = sus.get("clock_drift")
        txt = t("pf_sus_sum", cmin=sus.get("clock_min") or 0, cmax=sus.get("clock_max") or 0,
                cd=cd if cd is not None else 0.0, t0=tt, t1="",
                tm=("%.0f °C" % sus["temp_max"]) if sus.get("temp_max") is not None else "-",
                perf=t("pf_sus_perf", p=sus["perf_drift"]) if sus.get("perf_drift") is not None else "", thr=thr)
        lb = sw.label(parent, txt, dim=True, size=9, bg=bg)
        uikit.autowrap(lb)
        lb.pack(anchor="w", fill="x", pady=(2, 0))

    def _render_curve(self, res, parent, bg):
        curve = res.get("curve")
        if not curve or len(curve) < 2:
            return
        sw, C = self.sw, self.C
        sw.label(parent, t("pf_curve"), bold=True, size=9, bg=bg).pack(anchor="w", pady=(8, 2))
        vals = [c[1] for c in curve]
        Chart(parent, C, [dict(color=C["accent_net"], values=vals, span=0.5, dots=True,
                               values_text=["%.0f" % v for v in vals])],
              xlabels=[fmt_size(c[0]) for c in curve], height=110, bg=bg).pack(fill="x")
        fast = max(curve, key=lambda c: c[1])
        slow = curve[-1]
        lb = sw.label(parent, t("pf_curve_sum", fast=fast[1], fsz=fmt_size(fast[0]), slow=slow[1], ssz=fmt_size(slow[0])),
                      dim=True, size=9, bg=bg)
        uikit.autowrap(lb)
        lb.pack(anchor="w", fill="x", pady=(2, 0))

    def _render_extra(self, res, parent, bg):
        ex = res.get("extra")
        if not ex:
            return
        sw = self.sw
        sw.label(parent, t("pf_extra"), bold=True, size=9, bg=bg).pack(anchor="w", pady=(8, 2))
        fr = tk.Frame(parent, bg=bg)
        fr.pack(fill="x")
        fr.grid_columnconfigure(0, minsize=px(240))
        for i, (name, d) in enumerate(ex.items()):
            sw.label(fr, t("pf_x_" + name), size=9, bg=bg).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 12))
            txt = "%s MB/s" % pc.fmt_value(name, d["v"])
            if d.get("iops") and d["v"] < 1000:
                txt += "  (%.0f IOPS)" % d["iops"]
            sw.label(fr, txt, size=9, bg=bg).grid(row=i, column=1, sticky="w")

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
        mode = res.get("mode") if res.get("mode") in pc.MODES else "quick"
        sw.label(head, t("pf_mode_" + mode), dim=True, size=9, bg=bg).pack(side="right", pady=(8, 0))
        extra = None
        if test == "cpu" and res.get("scaling"):
            extra = t("pf_scaling", v=res["scaling"], n=res.get("threads", "?"))
        if test == "gpu" and res.get("adapter"):
            extra = res["adapter"]
        if test == "ssd" and (res.get("drive_label") or res.get("drive")):
            extra = res.get("drive_label") or res["drive"]
        if extra:
            sw.label(c["res"], extra, dim=True, size=9, bg=bg).pack(anchor="w")
        m = Metrics(c["res"], sw, bg, acc)
        m.pack(fill="x", pady=(6, 4))
        m.show(test, res)
        bits = self._sensor_bits(test, res)
        if bits:
            lb = sw.label(c["res"], "  ·  ".join(bits), dim=True, size=9, bg=bg)
            uikit.autowrap(lb)
            lb.pack(anchor="w", fill="x")
        if test == "ssd":
            self._render_extra(res, c["res"], bg)
        if test == "ram":
            self._render_curve(res, c["res"], bg)
        if test in ("cpu", "gpu"):
            self._render_sustain(test, res, c["res"], bg)
        if res.get("cached"):
            lb = sw.label(c["res"], "(!) cache", size=9, bg=bg)
            lb.config(fg=self.C["orange"])
            lb.pack(anchor="w")
        if test == "ssd" and (res.get("hdd") or res.get("bus") in ("USB", "SD", "MMC")):
            lb = sw.label(c["res"], t("pf_ssd_hdd"), size=9, bg=bg)
            lb.config(fg=self.C["orange"])
            uikit.autowrap(lb)
            lb.pack(anchor="w", fill="x")

    # ------------------------------------------------------------ running
    def busy(self):
        return self.job is not None

    def _set_busy(self, on):
        st = "disabled" if on else "normal"
        self.btn_all.config(state=st)
        for c in self.cards.values():
            c["btn"].config(state=st)
        self.stress.btn_start.config(state=st)

    def run_all(self):
        self.run(list(ORDER))

    def run(self, tests):
        if self.busy():
            return
        mode = self.mode
        total = max(5, self._total_secs(tests, mode))
        names = ", ".join(t("pf_%s_t" % x) for x in tests)
        if not messagebox.askokcancel(t("pf_confirm_title"),
                                      t("pf_confirm", tests=names, mode=t("pf_mode_" + mode), secs=fmt_secs(total)),
                                      parent=self.sw.win):
            return
        self.cancel_all = False
        self.run_results = {}
        self.job_mode = mode
        for test in ORDER:
            c = self.cards[test]
            c["bar"].set(0, c["acc"])
            c["status"].config(text="")
            self._live_show(test, False)
            if test in tests:
                self._bar_show(test, True)
                self._render_result(test, None)
        self.btn_cancel.pack(side="left", padx=(8, 0), after=self.btn_all)
        self.btn_cancel.config(state="normal")
        self.start_job(list(tests), mode)

    def start_job(self, tests, mode, stress=None):
        """Start the benchmark process (never blocks: the process is spawned from a helper thread)."""
        cfg = {k: self.sw.api.cfg.get(k) for k in ("backend", "lhm_url", "bench_ssd_drive")}
        self.job_kind = "stress" if mode == "stress" else "bench"
        self.job = pc.BenchJob(tests, mode, cfg, stress)
        self._set_busy(True)
        self.sw.win.after(POLL_MS, self._poll)

    def _bar_show(self, test, on):
        c = self.cards[test]
        if on and not c["bar_shown"]:
            c["bar"].grid()
            c["bar_shown"] = True
        elif not on and c["bar_shown"]:
            c["bar"].grid_remove()
            c["bar_shown"] = False

    def _live_show(self, test, on, text=""):
        c = self.cards[test]
        if on:
            c["live"].config(text=text)
            if not c["live_shown"]:
                c["live"].grid()
                c["live_shown"] = True
        elif c["live_shown"]:
            c["live"].grid_remove()
            c["live_shown"] = False

    def cancel(self):
        if not self.job:
            return
        self.cancel_all = True
        self.cancel_at = time.time()
        self.btn_cancel.config(state="disabled")
        self.status.config(text=t("pf_cancelling"))
        self.job.cancel()

    def _live_text(self, test, s):
        """One line of live sensor values for the test that is running."""
        bits = []
        if test == "cpu":
            if s.get("cpu_temp") is not None:
                bits.append("%.0f °C" % s["cpu_temp"])
            if s.get("cpu_clock"):
                bits.append("%.0f MHz" % s["cpu_clock"])
            if s.get("cpu_power") is not None:
                bits.append("%.0f W" % s["cpu_power"])
            if s.get("throttle_cpu"):
                bits.append(t("pf_throttle_yes", why=s.get("throttle_cpu_name") or "?"))
        elif test == "ram":
            if s.get("dimm_temps"):
                bits.append("DIMM " + "/".join("%.0f" % x for x in s["dimm_temps"]) + " °C")
            if s.get("ram_clock"):
                bits.append("%.0f MHz" % s["ram_clock"])
            if s.get("cpu_power") is not None:
                bits.append("CPU %.0f W" % s["cpu_power"])
        elif test == "ssd":
            if s.get("ssd_temp") is not None:
                bits.append("%.0f °C" % s["ssd_temp"])
            if s.get("drive_read") is not None:
                bits.append("R %.0f MB/s" % s["drive_read"])
            if s.get("drive_write") is not None:
                bits.append("W %.0f MB/s" % s["drive_write"])
        elif test == "gpu":
            if s.get("gpu_temp") is not None:
                bits.append("%.0f °C" % s["gpu_temp"])
            if s.get("gpu_hot") is not None:
                bits.append("hot %.0f °C" % s["gpu_hot"])
            if s.get("gpu_clock"):
                bits.append("%.0f MHz" % s["gpu_clock"])
            if s.get("gpu_power") is not None:
                bits.append("%.0f W" % s["gpu_power"])
            if s.get("throttle_gpu"):
                bits.append(t("pf_throttle_yes", why=s.get("throttle_gpu_name") or "?"))
        return t("pf_live", s="  ·  ".join(bits)) if bits else ""

    def _handle(self, m):
        kind = m[0]
        if kind == "start":
            if m[1] in self.cards:
                name = t("pf_running", name=t("pf_%s_t" % m[1]))
                self.cards[m[1]]["status"].config(text=name)
                self.status.config(text=name)
        elif kind == "p":
            c = self.cards.get(m[1])
            if c:
                c["bar"].set(m[2])
        elif kind == "s":
            if self.job_kind == "bench" and m[1] in self.cards and not self.cancel_all:
                txt = self._live_text(m[1], m[2])
                if txt:
                    self._live_show(m[1], True, txt)
        elif kind == "live":
            self.stress.on_live(m[1])
        elif kind == "done":
            if m[1] == "stress":
                self.stress.on_done(m[2])
                return
            res = m[2]
            c = self.cards[m[1]]
            c["bar"].set(1.0 if not res.get("skipped") else 0.0)
            c["status"].config(text=t("pf_done") if not res.get("skipped") else "")
            self._live_show(m[1], False)
            self.run_results[m[1]] = res
            self._render_result(m[1], res)
        elif kind == "cancel":
            if m[1] in self.cards:
                self.cards[m[1]]["status"].config(text=t("pf_cancelled"))
                self.cards[m[1]]["bar"].set(0)
                self._live_show(m[1], False)
        elif kind == "err":
            if m[1] == "stress" or (not m[1] and self.job_kind == "stress"):
                self.stress.on_error(m[2])
            elif m[1] in self.cards:
                self.cards[m[1]]["status"].config(text="")
                self._live_show(m[1], False)
                self._render_result(m[1], {"skipped": m[2]})
            else:
                self.status.config(text=t("pf_error", why=m[2]))

    def _poll(self):
        job = self.job
        if job is None:
            return
        try:
            if not self.p.winfo_exists():
                job.kill()
                return
        except tk.TclError:
            return
        for m in job.poll():
            self._handle(m)
        self.stress.poll_tick()
        if self.cancel_all and time.time() - self.cancel_at > 12 and not job.ended:
            job.kill()
        if job.ended:
            self._finished()
        else:
            self.sw.win.after(POLL_MS, self._poll)

    def _finished(self):
        kind = self.job_kind
        job = self.job
        self.job = None
        if job is not None:
            job.join(0)                    # the process has already sent "end": never wait for it on the Tk thread
        self.btn_cancel.pack_forget()
        self._set_busy(False)
        for test in ORDER:
            self._bar_show(test, False)
            self._live_show(test, False)
        if kind == "stress":
            self.stress.on_end()
            return
        good = {k: v for k, v in self.run_results.items() if v.get("score")}
        if good:
            run = pc.new_run(good, mode=self.job_mode)
            try:
                self.hist = pc.save_run(run)
            except OSError:
                self.hist.append(run)
            self._fill_tree()
            self._set_overall(good)
            self.status.config(text=t("pf_done"))
        else:
            self.status.config(text=t("pf_cancelled") if self.cancel_all else t("pf_idle"))

    def _destroyed(self, e=None):
        if e is not None and e.widget is not self.p:
            return
        if self.job is not None:
            self.job.kill()

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
