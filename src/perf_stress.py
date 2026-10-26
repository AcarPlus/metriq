"""Stress / stability test card of the Performance page (engine: perf_core.run_stress, runs in the benchmark process).

Loads CPU / RAM / GPU / SSD (or all of them) until the user presses Stop and CHECKS the results while doing so:
CPU workers repeat calculations with known answers, the RAM test writes and verifies patterns, the GPU helper computes
every value twice, the SSD test writes numbered blocks and reads them back. Any mismatch is an error and stops that
part. The run also stops by itself above a temperature limit or when the temperature sensors disappear."""
import time
import tkinter as tk
from tkinter import messagebox

import helptexts
import i18n
import perf_core as pc
from i18n import t
from perf_widgets import Chart
from uikit import px
import uikit

PARTS = ("cpu", "ram", "gpu", "ssd", "all")

i18n.register({
    "en": {
        "ps_title": "Stress / stability test",
        "ps_desc": "Keeps the chosen parts under full load until you press Stop and checks every result: wrong "
                   "calculations, memory or disk data errors and GPU errors mean the part is unstable. It also stops "
                   "by itself when a temperature limit is exceeded.",
        "ps_cpu": "CPU", "ps_ram": "RAM", "ps_gpu": "GPU", "ps_ssd": "SSD", "ps_all": "All",
        "ps_what": "Test", "ps_limits": "Stop automatically above",
        "ps_lim_cpu": "CPU", "ps_lim_gpu": "GPU", "ps_c": "°C",
        "ps_start": "Start stress test", "ps_stop": "Stop", "ps_stopping": "Stopping...",
        "ps_confirm_title": "Start stress test?",
        "ps_confirm": "The {what} test will put your PC under full load until you press Stop. Fans will get loud and "
                      "temperatures rise (automatic stop: CPU {cpu} °C, GPU {gpu} °C). Save your work and close other "
                      "programs. On a laptop, plug in the charger.\n\nStart now?",
        "ps_bad_limit": "The temperature limits must be between {lo} and {hi} °C.",
        "ps_running": "Running", "ps_elapsed": "Elapsed {t}",
        "ps_live_cpu": "CPU", "ps_live_ram": "RAM", "ps_live_gpu": "GPU", "ps_live_ssd": "SSD",
        "ps_temp": "{v:.0f} °C", "ps_hot": "hot spot {v:.0f} °C", "ps_clock": "{v:.0f} MHz", "ps_power": "{v:.0f} W",
        "ps_memclk": "memory clock {v:.0f} MHz", "ps_dimm": "DIMM {v}", "ps_cpupower": "CPU {v:.0f} W",
        "ps_read": "read {v:.0f} MB/s", "ps_write": "write {v:.0f} MB/s",
        "ps_thr_no": "no throttling", "ps_thr_yes": "THROTTLING", "ps_no_data": "no sensor data",
        "ps_checks": "{n} checks", "ps_errors": "errors: {e}", "ps_part_stopped": "stopped",
        "ps_err_banner": "ERROR in the {part} test: {msg}. That test was stopped - this part is not stable "
                         "(at these settings).",
        "ps_failed_start": "{part} test could not start: {msg}",
        "ps_note_notemp": "No temperature sensor is available (install HWiNFO for temperatures): the automatic "
                          "temperature stop is inactive - watch the temperatures yourself.",
        "ps_sum_title": "Result of the stress test",
        "ps_sum_dur": "Duration {t}",
        "ps_sum_ok": "Stable: no errors in {n} checks.",
        "ps_sum_bad": "NOT stable: {e} errors in {n} checks.",
        "ps_stop_user": "Stopped by you.",
        "ps_stop_temp": "Stopped automatically: {what} reached {v} °C (limit {lim} °C).",
        "ps_stop_sensors": "Stopped automatically: the temperature sensors stopped responding (is HWiNFO still "
                           "running?).",
        "ps_stop_failed": "Stopped: every selected test failed (see the errors).",
        "ps_stop_err": "The stress test process failed: {msg}",
        "ps_sum_cpu": "CPU: max {tmax}, clock {cmin}-{cmax}{drift}, power {pw}; {thr}",
        "ps_sum_ram": "RAM: {dimm}; memory clock {mc}; CPU power {pw}",
        "ps_sum_gpu": "GPU: max {tmax}{hot}, clock {cmin}-{cmax}{drift}, power {pw}; {thr}",
        "ps_sum_ssd": "SSD: drive {tmax}; read peak {rp}, write peak {wp}",
        "ps_drift": " ({v:+.1f} % over the run)",
        "ps_leg_clock": "clock", "ps_leg_temp": "temperature",
        "ps_chart_cap": "Clock and temperature over time (each line has its own scale)",
        "ps_n_none": "-",
        "hp_stress": "Runs a self-checking load until you press Stop. CPU: calculations with known answers (integer, "
                     "float, SHA-256, CRC); RAM: writes patterns into a few hundred MB and reads them back; GPU: every "
                     "shader value is computed twice and compared; SSD: numbered blocks are written and read back. A "
                     "single mismatch counts as an error and stops that test. The run stops by itself above the "
                     "temperature limit or when the sensors disappear.",
        "hp_stress_limit": "The test stops automatically when the CPU or GPU temperature goes above this value. "
                           "Needs a temperature source (HWiNFO gives the most complete data).",
    },
    "tr": {
        "ps_title": "Yük / kararlılık testi",
        "ps_desc": "Seçilen parçaları Durdur'a basana kadar tam yükte tutar ve her sonucu denetler: yanlış hesaplar, "
                   "bellek veya disk veri hataları ve GPU hataları parçanın kararsız olduğunu gösterir. Sıcaklık "
                   "sınırı aşılırsa test kendiliğinden durur.",
        "ps_cpu": "CPU", "ps_ram": "RAM", "ps_gpu": "GPU", "ps_ssd": "SSD", "ps_all": "Hepsi",
        "ps_what": "Test", "ps_limits": "Şu sıcaklığın üstünde otomatik durdur",
        "ps_lim_cpu": "CPU", "ps_lim_gpu": "GPU", "ps_c": "°C",
        "ps_start": "Yük testini başlat", "ps_stop": "Durdur", "ps_stopping": "Duruyor...",
        "ps_confirm_title": "Yük testi başlatılsın mı?",
        "ps_confirm": "{what} testi, sen Durdur'a basana kadar bilgisayarını tam yük altında çalıştıracak. Fanlar "
                      "gürültülü olur, sıcaklıklar yükselir (otomatik durdurma: CPU {cpu} °C, GPU {gpu} °C). "
                      "Çalışmanı kaydet, diğer programları kapat. Dizüstü bilgisayarda şarj cihazını tak."
                      "\n\nŞimdi başlansın mı?",
        "ps_bad_limit": "Sıcaklık sınırları {lo} ile {hi} °C arasında olmalı.",
        "ps_running": "Çalışıyor", "ps_elapsed": "Geçen süre {t}",
        "ps_live_cpu": "CPU", "ps_live_ram": "RAM", "ps_live_gpu": "GPU", "ps_live_ssd": "SSD",
        "ps_temp": "{v:.0f} °C", "ps_hot": "sıcak nokta {v:.0f} °C", "ps_clock": "{v:.0f} MHz", "ps_power": "{v:.0f} W",
        "ps_memclk": "bellek saati {v:.0f} MHz", "ps_dimm": "DIMM {v}", "ps_cpupower": "CPU {v:.0f} W",
        "ps_read": "okuma {v:.0f} MB/sn", "ps_write": "yazma {v:.0f} MB/sn",
        "ps_thr_no": "kısılma yok", "ps_thr_yes": "KISILIYOR", "ps_no_data": "sensör verisi yok",
        "ps_checks": "{n} denetim", "ps_errors": "hata: {e}", "ps_part_stopped": "durdu",
        "ps_err_banner": "{part} testinde HATA: {msg}. Bu test durduruldu - bu parça (bu ayarlarla) kararlı değil.",
        "ps_failed_start": "{part} testi başlatılamadı: {msg}",
        "ps_note_notemp": "Sıcaklık sensörü yok (sıcaklıklar için HWiNFO kur): otomatik sıcaklık durdurması devre "
                          "dışı - sıcaklıkları kendin izle.",
        "ps_sum_title": "Yük testi sonucu",
        "ps_sum_dur": "Süre {t}",
        "ps_sum_ok": "Kararlı: {n} denetimde hata yok.",
        "ps_sum_bad": "KARARSIZ: {n} denetimde {e} hata.",
        "ps_stop_user": "Sen durdurdun.",
        "ps_stop_temp": "Otomatik durduruldu: {what} {v} °C'ye ulaştı (sınır {lim} °C).",
        "ps_stop_sensors": "Otomatik durduruldu: sıcaklık sensörleri yanıt vermeyi kesti (HWiNFO hâlâ çalışıyor mu?).",
        "ps_stop_failed": "Durduruldu: seçilen testlerin hepsi başarısız oldu (hatalara bak).",
        "ps_stop_err": "Yük testi işlemi başarısız oldu: {msg}",
        "ps_sum_cpu": "CPU: en çok {tmax}, saat {cmin}-{cmax}{drift}, güç {pw}; {thr}",
        "ps_sum_ram": "RAM: {dimm}; bellek saati {mc}; CPU gücü {pw}",
        "ps_sum_gpu": "GPU: en çok {tmax}{hot}, saat {cmin}-{cmax}{drift}, güç {pw}; {thr}",
        "ps_sum_ssd": "SSD: sürücü {tmax}; okuma tepesi {rp}, yazma tepesi {wp}",
        "ps_drift": " (test boyunca {v:+.1f} %)",
        "ps_leg_clock": "saat", "ps_leg_temp": "sıcaklık",
        "ps_chart_cap": "Zamana göre saat hızı ve sıcaklık (her çizginin kendi ölçeği var)",
        "ps_n_none": "-",
        "hp_stress": "Kendini denetleyen bir yükü sen Durdur'a basana kadar çalıştırır. CPU: bilinen cevaplı hesaplar "
                     "(tam sayı, kayan nokta, SHA-256, CRC); RAM: birkaç yüz MB'a desen yazar ve geri okur; GPU: her "
                     "gölgelendirici değeri iki kez hesaplanıp karşılaştırılır; SSD: numaralı bloklar yazılıp geri "
                     "okunur. Tek bir uyuşmazlık hata sayılır ve o testi durdurur. Sıcaklık sınırı aşılırsa ya da "
                     "sensörler kaybolursa test kendiliğinden durur.",
        "hp_stress_limit": "CPU veya GPU sıcaklığı bu değerin üstüne çıkarsa test kendiliğinden durur. Bir sıcaklık "
                           "kaynağı gerekir (en eksiksiz veriyi HWiNFO verir).",
    },
})


def fmt_time(secs):
    s = int(max(0, secs))
    return "%02d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else "%02d:%02d" % (s // 60, s % 60)


def fmt_n(n):
    n = float(n or 0)
    for lim, suf in ((1e9, " G"), (1e6, " M"), (1e4, " k")):
        if n >= lim:
            return "%.1f%s" % (n / lim, suf)
    return "%d" % n


def _v(x, key, unit=""):
    return t(key, v=x) if x is not None else t("ps_n_none")


class StressCard:
    def __init__(self, tab):
        self.tab = tab
        self.sw, self.C = tab.sw, tab.C
        self.running = False
        self.stopping = False
        self.stop_at = 0.0
        self.last_live = None
        self.job_parts = []

    # ------------------------------------------------------------ build
    def build(self):
        sw, C, p = self.sw, self.C, self.tab.p
        card = sw.card(p, t("ps_title"), acc=C.get("accent_power"))
        self.card = card
        bg = card.fillc
        d = tk.Frame(card.body, bg=bg)
        sw.full(card, d, pady=(0, 8), divider=False)
        dl = sw.label(d, t("ps_desc"), dim=True, size=9, bg=bg)
        dl.pack(side="left", fill="x", expand=True)
        uikit.autowrap(dl)
        helptexts.info_icon(d, "stress", bg=bg).pack(side="left", padx=(8, 0), anchor="n")

        row = tk.Frame(card.body, bg=bg)
        sw.full(card, row, pady=(0, 8), divider=False)
        sw.label(row, t("ps_what"), bg=bg).pack(side="left", padx=(0, 10))
        self.seg = uikit.Segmented(row, C, [t("ps_" + x) for x in PARTS], 0, None, (sw.F_TXT, 10), bg=bg)
        self.seg.pack(side="left")

        lim = tk.Frame(card.body, bg=bg)
        sw.full(card, lim, pady=(0, 8), divider=False)
        sw.label(lim, t("ps_limits"), bg=bg).pack(side="left")
        helptexts.info_icon(lim, "stress_limit", bg=bg).pack(side="left", padx=(6, 12))
        self.v_cpu, self.v_gpu = tk.StringVar(value=str(pc.CPU_LIMIT_DEFAULT)), tk.StringVar(value=str(pc.GPU_LIMIT_DEFAULT))
        for lbl, var in (("ps_lim_cpu", self.v_cpu), ("ps_lim_gpu", self.v_gpu)):
            sw.label(lim, t(lbl), dim=True, size=9, bg=bg).pack(side="left", padx=(0, 4))
            e = sw.entry(lim, var, 4, bg=bg, justify="center")
            e.pack(side="left")
            sw.label(lim, t("ps_c"), dim=True, size=9, bg=bg).pack(side="left", padx=(3, 14))

        bar = tk.Frame(card.body, bg=bg)
        sw.full(card, bar, pady=(0, 4), divider=False)
        self.btn_start = sw.button(bar, t("ps_start"), self.start, primary=True, bg=bg)
        self.btn_start.pack(side="left")
        self.btn_stop = sw.button(bar, t("ps_stop"), self.stop, bg=bg)
        self.status = sw.label(bar, "", dim=True, size=9, bg=bg)
        self.status.pack(side="left", padx=(14, 0))

        self.live = tk.Frame(card.body, bg=bg)
        self.lbl_el = tk.Label(self.live, text="", bg=bg, fg=C["fg"], font=(sw.F_NUM, 20, "bold"), anchor="w")
        self.lbl_el.pack(anchor="w")
        self.lines = {}
        for part in pc.STRESS_PARTS:
            lb = sw.label(self.live, "", size=9, bg=bg)
            uikit.autowrap(lb)
            self.lines[part] = lb
        self.lbl_banner = sw.label(self.live, "", bold=True, size=10, bg=bg)
        self.lbl_banner.config(fg=C["red"])
        uikit.autowrap(self.lbl_banner)
        self.lbl_note = sw.label(self.live, "", size=9, bg=bg)
        self.lbl_note.config(fg=C["orange"])
        uikit.autowrap(self.lbl_note)
        self.live_row = None
        self.summary = tk.Frame(card.body, bg=bg)
        self.sum_row = None
        self.bg = bg

    # ------------------------------------------------------------ start / stop
    def _limits(self):
        try:
            c, g = int(float(self.v_cpu.get())), int(float(self.v_gpu.get()))
        except ValueError:
            return None
        if not (pc.LIMIT_MIN <= c <= pc.LIMIT_MAX and pc.LIMIT_MIN <= g <= pc.LIMIT_MAX):
            return None
        return c, g

    def start(self):
        tab = self.tab
        if tab.busy():
            return
        lim = self._limits()
        if lim is None:
            messagebox.showwarning(t("ps_confirm_title"), t("ps_bad_limit", lo=pc.LIMIT_MIN, hi=pc.LIMIT_MAX),
                                   parent=self.sw.win)
            return
        sel = PARTS[self.seg.index]
        parts = list(pc.STRESS_PARTS) if sel == "all" else [sel]
        if not messagebox.askokcancel(t("ps_confirm_title"), t("ps_confirm", what=t("ps_" + sel), cpu=lim[0], gpu=lim[1]),
                                      parent=self.sw.win):
            return
        self.job_parts = parts
        self.running, self.stopping = True, False
        self.last_live = None
        self._clear()
        self.status.config(text=t("ps_running"))
        self.btn_stop.pack(side="left", padx=(8, 0), after=self.btn_start)
        self.btn_stop.config(state="normal")
        self.live_row = self.sw.full(self.card, self.live, pady=(6, 0), divider=False) if self.live_row is None else \
            self.live_row
        self.live.grid()
        self.lbl_el.config(text=t("ps_elapsed", t=fmt_time(0)))
        for part in pc.STRESS_PARTS:
            lb = self.lines[part]
            if part in parts:
                lb.config(text=t("ps_live_" + part) + "  ...", fg=self.C["fg"])
                lb.pack(anchor="w", fill="x", pady=(2, 0))
            else:
                lb.pack_forget()
        self.lbl_banner.pack_forget()
        self.lbl_note.pack_forget()
        self.tab.start_job(list(parts), "stress", {"parts": parts, "cpu_limit": lim[0], "gpu_limit": lim[1]})

    def stop(self):
        if not self.running or self.stopping:
            return
        self.stopping = True
        self.stop_at = time.time()
        self.btn_stop.config(state="disabled")
        self.status.config(text=t("ps_stopping"))
        self.tab.job.cancel()

    def _clear(self):
        for w in self.summary.winfo_children():
            w.destroy()
        if self.sum_row is not None:
            self.summary.grid_remove()

    # ------------------------------------------------------------ messages from the benchmark process
    def on_live(self, m):
        self.last_live = m
        el = m.get("elapsed", 0)
        self.lbl_el.config(text=t("ps_elapsed", t=fmt_time(el)))
        s = m.get("sample") or {}
        parts, done, failed = m.get("parts", {}), m.get("done", {}), m.get("failed", {})
        for part in self.job_parts:
            st = parts.get(part) or done.get(part) or {"checks": 0, "errors": 0}
            bits = [self._sensor_bits(part, s)]
            bits.append(t("ps_checks", n=fmt_n(st["checks"])))
            bad = st["errors"] > 0 or part in failed
            bits.append(t("ps_errors", e=fmt_n(st["errors"])) if st["errors"] else t("ps_errors", e=0))
            if part in done or part in failed:
                bits.append(t("ps_part_stopped"))
            self.lines[part].config(text=t("ps_live_" + part) + "   " + "  ·  ".join(b for b in bits if b),
                                    fg=self.C["red"] if bad else self.C["fg"])
        errs = [(p, failed[p]) for p in self.job_parts if p in failed]
        if errs:
            self.lbl_banner.config(text="\n".join(t("ps_err_banner", part=t("ps_live_" + p), msg=msg) for p, msg in errs))
            if not self.lbl_banner.winfo_ismapped():
                self.lbl_banner.pack(anchor="w", fill="x", pady=(6, 0))
        notes = m.get("notes") or []
        if notes:
            self.lbl_note.config(text=t("ps_note_notemp"))
            if not self.lbl_note.winfo_ismapped():
                self.lbl_note.pack(anchor="w", fill="x", pady=(4, 0))

    def _sensor_bits(self, part, s):
        out = []
        if part == "cpu":
            out += [_v(s.get("cpu_temp"), "ps_temp"), _v(s.get("cpu_clock"), "ps_clock"), _v(s.get("cpu_power"), "ps_power"),
                    t("ps_thr_yes") if s.get("throttle_cpu") else t("ps_thr_no")]
        elif part == "gpu":
            out += [_v(s.get("gpu_temp"), "ps_temp")]
            if s.get("gpu_hot") is not None:
                out.append(t("ps_hot", v=s["gpu_hot"]))
            out += [_v(s.get("gpu_clock"), "ps_clock"), _v(s.get("gpu_power"), "ps_power"),
                    t("ps_thr_yes") if s.get("throttle_gpu") else t("ps_thr_no")]
        elif part == "ram":
            if s.get("dimm_temps"):
                out.append(t("ps_dimm", v="/".join("%.0f" % x for x in s["dimm_temps"]) + " °C"))
            if s.get("ram_clock") is not None:
                out.append(t("ps_memclk", v=s["ram_clock"]))
            if s.get("cpu_power") is not None:
                out.append(t("ps_cpupower", v=s["cpu_power"]))
        elif part == "ssd":
            if s.get("ssd_temp") is not None:
                out.append(_v(s["ssd_temp"], "ps_temp"))
            if s.get("drive_read") is not None:
                out.append(t("ps_read", v=s["drive_read"]))
            if s.get("drive_write") is not None:
                out.append(t("ps_write", v=s["drive_write"]))
        return "  ·  ".join(x for x in out if x)

    def on_error(self, msg):
        self.lbl_banner.config(text=t("ps_stop_err", msg=msg))
        self.lbl_banner.pack(anchor="w", fill="x", pady=(6, 0))

    def on_done(self, res):
        self.show_summary(res)

    def on_end(self):
        self.running = self.stopping = False
        self.btn_stop.pack_forget()
        self.status.config(text="")

    def poll_tick(self):
        """Called by the page's poll loop: kill the process when the graceful stop takes too long."""
        if self.stopping and time.time() - self.stop_at > 15 and self.tab.job:
            self.tab.job.kill()

    # ------------------------------------------------------------ summary
    def stop_text(self, res):
        st = res.get("stop") or {}
        r, info = st.get("reason"), st.get("info", "")
        if r == "temp":
            what, lim = info.split("|") if "|" in info else (info, "?")
            what_name, val = (what.split() + ["?"])[:2]
            return t("ps_stop_temp", what=what_name, v=val, lim=lim)
        if r == "sensors":
            return t("ps_stop_sensors")
        if r == "failed":
            return t("ps_stop_failed")
        return t("ps_stop_user")

    def show_summary(self, res):
        sw, C, bg = self.sw, self.C, self.bg
        self._clear()
        if self.live_row is not None:
            self.live.grid_remove()
        if self.sum_row is None:
            self.sum_row = sw.full(self.card, self.summary, pady=(10, 0), divider=True)
        self.summary.grid()
        tk.Frame(self.summary, bg=bg, height=1).pack()
        sw.label(self.summary, t("ps_sum_title"), bold=True, bg=bg).pack(anchor="w")
        checks = sum(res.get("checks", {}).values())
        errs = sum(res.get("errors", {}).values())
        bad = errs > 0 or bool(res.get("failed"))
        if res.get("failed") and not errs:
            errs = len(res["failed"])
        verdict = sw.label(self.summary, t("ps_sum_bad", e=fmt_n(errs), n=fmt_n(checks)) if bad else
                           t("ps_sum_ok", n=fmt_n(checks)), bold=True, bg=bg)
        verdict.config(fg=C["red"] if bad else C["green"])
        uikit.autowrap(verdict)
        verdict.pack(anchor="w", fill="x", pady=(2, 0))
        for p, msg in (res.get("failed") or {}).items():
            lb = sw.label(self.summary, "%s: %s" % (t("ps_live_" + p), msg), size=9, bg=bg)
            lb.config(fg=C["red"])
            uikit.autowrap(lb)
            lb.pack(anchor="w", fill="x")
        lb = sw.label(self.summary, "%s  ·  %s" % (t("ps_sum_dur", t=fmt_time(res.get("duration", 0))),
                                                    self.stop_text(res)), dim=True, size=9, bg=bg)
        uikit.autowrap(lb)
        lb.pack(anchor="w", fill="x", pady=(2, 4))
        sens = res.get("sensors") or {}
        drift = res.get("clock_drift") or {}
        for part in res.get("parts", []):
            s = sens.get(part) or {}
            txt = self._sum_line(part, s, drift.get(part), res.get("errors", {}).get(part, 0),
                                 res.get("checks", {}).get(part, 0))
            if txt:
                lb = sw.label(self.summary, txt, size=9, bg=bg)
                uikit.autowrap(lb)
                lb.pack(anchor="w", fill="x", pady=1)
        ser = res.get("series") or {}
        lines = []
        for key, col, txt in (("cpu_clock", C["accent_clock"], "CPU " + t("ps_leg_clock")),
                              ("cpu_temp", C["orange"], "CPU " + t("ps_leg_temp")),
                              ("gpu_clock", C["accent_fps"], "GPU " + t("ps_leg_clock")),
                              ("gpu_temp", C["red"], "GPU " + t("ps_leg_temp"))):
            part = key.split("_")[0]
            vals = ser.get(key)
            if part in res.get("parts", []) and vals and any(x is not None for x in vals):
                lines.append(dict(color=col, values=vals, span=0.3 if "temp" in key else 0.12, name=txt))
        if lines and len(ser.get("t", [])) >= 3:
            sw.label(self.summary, t("ps_chart_cap"), dim=True, size=8, bg=bg).pack(anchor="w", pady=(6, 2))
            Chart(self.summary, C, lines, xs=ser["t"], height=90, bg=bg).pack(fill="x")
            leg = tk.Frame(self.summary, bg=bg)
            leg.pack(anchor="w", pady=(2, 0))
            for ln in lines:
                tk.Label(leg, text="●", fg=ln["color"], bg=bg, font=(sw.F_TXT, 9)).pack(side="left")
                sw.label(leg, ln["name"], dim=True, size=8, bg=bg).pack(side="left", padx=(2, 12))
        notes = res.get("notes") or []
        if notes:
            lb = sw.label(self.summary, t("ps_note_notemp"), size=9, bg=bg)
            lb.config(fg=C["orange"])
            uikit.autowrap(lb)
            lb.pack(anchor="w", fill="x", pady=(4, 0))

    def _sum_line(self, part, s, drift, errs, checks):
        def val(x, key):
            return t(key, v=x) if x is not None else t("ps_n_none")
        d = t("ps_drift", v=drift) if drift is not None else ""
        thr = t("ps_thr_yes") + " (%s)" % ", ".join(s.get("throttle_notes") or []) if s.get("throttled") else \
            (t("ps_thr_no") if s.get("throttled") is False else t("ps_no_data"))
        if part == "cpu":
            return t("ps_sum_cpu", tmax=val(s.get("temp_max"), "ps_temp"), cmin=val(s.get("clock_min"), "ps_clock"),
                     cmax=val(s.get("clock_max"), "ps_clock"), drift=d, pw=val(s.get("power_avg"), "ps_power"), thr=thr)
        if part == "gpu":
            hot = ", " + t("ps_hot", v=s["hot_max"]) if s.get("hot_max") is not None else ""
            return t("ps_sum_gpu", tmax=val(s.get("temp_max"), "ps_temp"), hot=hot,
                     cmin=val(s.get("clock_min"), "ps_clock"), cmax=val(s.get("clock_max"), "ps_clock"), drift=d,
                     pw=val(s.get("power_avg"), "ps_power"), thr=thr)
        if part == "ram":
            dimm = ", ".join("%s %.0f °C" % (n, v) for n, v in zip(s.get("dimm_names") or [], s.get("dimm_max") or [])) \
                or t("ps_no_data")
            return t("ps_sum_ram", dimm=dimm, mc=val(s.get("clock_avg"), "ps_clock"),
                     pw=val(s.get("power_avg"), "ps_power"))
        if part == "ssd":
            return t("ps_sum_ssd", tmax=val(s.get("temp_max"), "ps_temp"),
                     rp=("%.0f MB/s" % s["read_peak"]) if s.get("read_peak") is not None else t("ps_n_none"),
                     wp=("%.0f MB/s" % s["write_peak"]) if s.get("write_peak") is not None else t("ps_n_none"))
        return ""
