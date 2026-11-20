"""Basic view: a short, plain-language panel built from the auto-detected layout.

    basicmode.view(layout.autodetect(rd, rids=True), rd)    # -> sections in config form (see layout.build_sections)
    basicmode.status_text(row, value)                       # "Normal" / "High but safe" / "Very hot" / "Low" / ... or ""
    basicmode.color(row, value, C)                          # green / amber / red for temperatures, neutral otherwise
    basicmode.value_text(row, value)                        # ("11,0 / 32", "GB") for RAM / VRAM, else None

Basic never touches the stored layout: the Advanced layout stays exactly as the user made it. Rows are picked by their
stable "rid" (the i18n key they were created from), never by their translated names.
"""
import i18n
import layout
from i18n import t

# (section title key, icon, rids shown in it); a section without any detected row is left out
SPEC = (("bs_cpu", "gauge", ("cpu_usage", "cpu_temp", "fan")),
        ("bs_gpu", "fps", ("gpu_load", "gpu_temp", "gpu_fan")),
        ("bs_mem", "gauge", ("ram", "vram")),
        ("bs_net", "net", ("download", "upload", "disk_read", "disk_write")),
        ("bs_game", "fps", ("fps",)),
        ("bs_bat", "bolt", ("bat_level", "bat_state", "bat_time")))

# which rows get a status word, and by which scale
STATUS = {"cpu_usage": "usage", "gpu_load": "usage", "ram": "usage", "vram": "usage",
          "cpu_temp": "temp", "gpu_temp": "temp"}
# Status levels use fixed, hardware-realistic absolute limits (not the colour scale of the row): normal gaming
# temperatures must never look alarming. (level 1 starts at the first number, level 2 at the second; degrees C)
TEMP_LIMITS = {"cpu_temp": (85.0, 95.0), "gpu_temp": (83.0, 90.0), "gpu_hot": (95.0, 105.0),
               "gpu_memtemp": (95.0, 105.0)}
USAGE_LIMITS = (0.40, 0.80)            # share of the capacity (0..100 % rows, RAM / VRAM of their total)
_WORDS = {"temp": ("stw_ok", "stw_warm", "stw_hot"), "usage": ("stw_low", "stw_mid", "stw_high")}


def _fan_pick(cands, rd):
    """The fastest system fan right now (the first one when there are no readings)."""
    if len(cands) < 2 or not rd:
        return cands[:1]
    best, best_v = cands[0], -1.0
    for d in cands:
        try:
            r = layout.Row.from_cfg(d).match(rd)
        except Exception:
            r = None
        if r and r["value"] > best_v:
            best, best_v = d, r["value"]
    return [best]


def view(cfg_sections, rd=None):
    """Curated sections (config form, current language) from the detected layout `cfg_sections`
    (layout.autodetect(rd, rids=True)). Names are plain words, every row carries its explanation and status scale."""
    by = {}
    for s in cfg_sections or []:
        for d in s.get("rows", []):
            if d.get("rid") and d.get("visible", True) is not False:
                by.setdefault(d["rid"], []).append(d)
    out = []
    for title_key, icon, rids in SPEC:
        rows = []
        for rid in rids:
            cands = by.get(rid)
            if not cands:
                continue
            for d in (_fan_pick(cands, rd) if rid == "fan" else cands[:1]):
                d = dict(d)
                d["name"] = t("bn_" + rid)
                d["help"] = "b_" + rid
                if rid in STATUS:
                    d["status"] = STATUS[rid]
                if rid in ("ram", "vram"):
                    d["bar"] = False          # "11,0 / 32 GB" needs the room of the bar column
                rows.append(d)
        if title_key == "bs_cpu" and rows and "cpu_temp" not in by:
            # no CPU temperature from this data source (built-in): a hint row that leads to the HWiNFO setting
            rows.insert(1, {"name": t("bn_cpu_temp_hw"), "label": "^$never$", "rid": "hint_cpu_temp",
                            "help": "b_cpu_temp_hw"})
        if rows:
            out.append({"title": t(title_key), "color": layout.ICON_COLORS[icon], "icon": icon, "rows": rows})
    return out


def level(row, v):
    """0 / 1 / 2 (calm .. most intense) of a value of a Basic status row, None for a row without status."""
    kind = getattr(row, "status", None)
    if kind == "temp":
        lim = TEMP_LIMITS.get(getattr(row, "rid", None))
        if not lim:
            return None
        return 0 if v < lim[0] else 1 if v < lim[1] else 2
    if kind == "usage":
        f = layout.frac(row, v)
        if f is None:
            return None
        return 0 if f < USAGE_LIMITS[0] else 1 if f < USAGE_LIMITS[1] else 2
    return None


def status_text(row, v):
    """Status word for a value of a Basic row ('' when the row has none)."""
    lv = level(row, v)
    return "" if lv is None else t(_WORDS[row.status][lv])


def color(row, v, C):
    """Colour of a Basic row's value and status word, both from the same level: temperatures green / amber / red,
    everything else (usage included, which is never alarming) neutral."""
    if getattr(row, "status", None) == "temp":
        lv = level(row, v)
        if lv is not None:
            return C[("green", "yellow", "red")[lv]]
    return C["fg"]


def value_text(row, v):
    """RAM / VRAM in Basic: ("11,0 / 32", "GB") instead of raw MB; None for every other row."""
    if getattr(row, "rid", None) not in ("ram", "vram") or not row.limits:
        return None
    total = max(row.limits) / 1024.0
    used = f"{v / 1024.0:.1f}"
    tot = f"{total:.0f}" if abs(total - round(total)) < 0.05 else f"{total:.1f}"
    if i18n.decimal_comma():
        used, tot = used.replace(".", ","), tot.replace(".", ",")
    return f"{used} / {tot}", "GB"


i18n.register({
    "en": {
        "bn_cpu_temp_hw": "CPU temperature needs HWiNFO",
        "ui_mode_deferred": "The view changed. The Settings pages switch when the running test ends.",
        "hp_b_cpu_temp_hw": "The built-in data source cannot read the processor temperature; HWiNFO (free) can. Click to open the HWiNFO settings (this switches to the Advanced view).",
        "bs_cpu": "PROCESSOR", "bs_gpu": "GRAPHICS CARD", "bs_mem": "MEMORY", "bs_net": "NETWORK & DISK",
        "bs_game": "GAMES", "bs_bat": "BATTERY",
        "bn_cpu_usage": "Processor usage", "bn_cpu_temp": "Processor temperature", "bn_fan": "Fan",
        "bn_gpu_load": "Graphics card usage", "bn_gpu_temp": "Graphics card temperature",
        "bn_gpu_fan": "Graphics card fan", "bn_ram": "Memory (RAM)", "bn_vram": "Graphics card memory",
        "bn_download": "Download", "bn_upload": "Upload", "bn_disk_read": "Disk read", "bn_disk_write": "Disk write",
        "bn_fps": "FPS", "bn_bat_level": "Battery", "bn_bat_state": "Battery state", "bn_bat_time": "Time left",
        "stw_ok": "Normal", "stw_warm": "High but safe", "stw_hot": "Very hot", "stw_low": "Low", "stw_mid": "Medium",
        "stw_high": "Busy", "col_status": "STATUS",
        "ui_basic": "Basic", "ui_advanced": "Advanced",
        "ui_mode_hint": "Basic view is on. Switch to Advanced for all details (B).",
        "st_card_uimode": "View mode", "st_uimode": "Mode",
        "st_uimode_hint": "Basic shows a few easy-to-read rows. Advanced shows everything: all sensors, your own "
                          "layout, recording options and fan control.",
        "hp_uimodebtn": "Switches between Basic (a few easy rows) and Advanced (everything). You can also press B.",
        "hp_b_cpu_usage": "How busy your processor (CPU) is. Low means it is mostly resting; Busy is normal while you run something heavy. Usage is never a warning.",
        "hp_b_cpu_temp": "How hot your processor is. Normal and High but safe are both fine: processors are built to run warm under load. Only if it stays Very hot for long, check your computer's air flow.",
        "hp_b_fan": "How fast your main fan spins, in revolutions per minute (RPM). A faster fan cools more but is "
                    "louder.",
        "hp_b_gpu_load": "How hard your graphics card (GPU) is working. Busy is normal while gaming.",
        "hp_b_gpu_temp": "How hot your graphics card is. Normal and High but safe are both fine: graphics cards are built to run warm while gaming. Only if it stays Very hot for long, check the air flow in your computer.",
        "hp_b_gpu_fan": "How fast the fan on your graphics card spins. At rest it may stop completely, which is "
                        "normal.",
        "hp_b_ram": "How much of your computer's main memory (RAM) is in use. If it is nearly full, the computer "
                    "can feel slow.",
        "hp_b_vram": "How much of the graphics card's own memory is in use. Games and video editing use a lot of it.",
        "hp_b_download": "How fast data is coming in from the internet right now.",
        "hp_b_upload": "How fast data is going out to the internet right now.",
        "hp_b_disk_read": "How fast your main disk is reading data right now.",
        "hp_b_disk_write": "How fast your main disk is saving data right now.",
        "hp_b_fps": "Frames per second: how smoothly the running game is drawn. Higher is smoother; about 60 feels "
                    "smooth.",
        "hp_b_bat_level": "How much charge your battery has left.",
        "hp_b_bat_state": "Whether the battery is charging, discharging or full.",
        "hp_b_bat_time": "About how long the battery lasts at the current use. Only shown while on battery power.",
    },
    "tr": {
        "bn_cpu_temp_hw": "İşlemci sıcaklığı için HWiNFO gerekir",
        "ui_mode_deferred": "Görünüm değişti. Ayar sayfaları, çalışan test bitince geçecek.",
        "hp_b_cpu_temp_hw": "Yerleşik veri kaynağı işlemci sıcaklığını okuyamaz; HWiNFO (ücretsiz) okuyabilir. HWiNFO ayarlarını açmak için tıklayın (Gelişmiş görünüme geçer).",
        "bs_cpu": "İŞLEMCİ", "bs_gpu": "EKRAN KARTI", "bs_mem": "BELLEK", "bs_net": "AĞ VE DİSK",
        "bs_game": "OYUN", "bs_bat": "PİL",
        "bn_cpu_usage": "İşlemci kullanımı", "bn_cpu_temp": "İşlemci sıcaklığı", "bn_fan": "Fan",
        "bn_gpu_load": "Ekran kartı kullanımı", "bn_gpu_temp": "Ekran kartı sıcaklığı",
        "bn_gpu_fan": "Ekran kartı fanı", "bn_ram": "Bellek (RAM)", "bn_vram": "Ekran kartı belleği",
        "bn_download": "İndirme", "bn_upload": "Yükleme", "bn_disk_read": "Disk okuma", "bn_disk_write": "Disk yazma",
        "bn_fps": "FPS", "bn_bat_level": "Pil", "bn_bat_state": "Pil durumu", "bn_bat_time": "Kalan süre",
        "stw_ok": "Normal", "stw_warm": "Yüksek ama güvenli", "stw_hot": "Çok sıcak", "stw_low": "Düşük", "stw_mid": "Orta",
        "stw_high": "Yoğun", "col_status": "DURUM",
        "ui_basic": "Basit", "ui_advanced": "Gelişmiş",
        "ui_mode_hint": "Basit görünüm açık. Tüm ayrıntılar için Gelişmiş moda geç (B).",
        "st_card_uimode": "Görünüm modu", "st_uimode": "Mod",
        "st_uimode_hint": "Basit, birkaç anlaşılır satır gösterir. Gelişmiş her şeyi gösterir: tüm sensörler, kendi "
                          "düzeniniz, kayıt seçenekleri ve fan kontrolü.",
        "hp_uimodebtn": "Basit (birkaç kolay satır) ile Gelişmiş (her şey) arasında geçiş yapar. B tuşuna da "
                        "basabilirsiniz.",
        "hp_b_cpu_usage": "İşlemcinizin (CPU) ne kadar meşgul olduğu. Düşük, çoğunlukla boşta demektir; ağır bir iş yaparken Yoğun olması normaldir. Kullanım hiçbir zaman bir uyarı değildir.",
        "hp_b_cpu_temp": "İşlemcinizin ne kadar ısındığı. Normal ve Yüksek ama güvenli ikisi de sorun değildir: işlemciler yük altında sıcak çalışacak şekilde tasarlanır. Yalnızca uzun süre Çok sıcak kalırsa bilgisayarınızın hava akışına bakın.",
        "hp_b_fan": "Ana fanınızın dakikadaki devir sayısı (RPM). Hızlı fan daha çok soğutur ama daha gürültülüdür.",
        "hp_b_gpu_load": "Ekran kartınızın (GPU) ne kadar çalıştığı. Oyun oynarken Yoğun olması normaldir.",
        "hp_b_gpu_temp": "Ekran kartınızın ne kadar ısındığı. Normal ve Yüksek ama güvenli ikisi de sorun değildir: ekran kartları oyunda sıcak çalışacak şekilde tasarlanır. Yalnızca uzun süre Çok sıcak kalırsa bilgisayarınızın hava akışına bakın.",
        "hp_b_gpu_fan": "Ekran kartındaki fanın hızı. Boştayken tamamen durabilir, bu normaldir.",
        "hp_b_ram": "Bilgisayarınızın ana belleğinin (RAM) ne kadarının kullanıldığı. Neredeyse doluysa bilgisayar "
                    "yavaşlayabilir.",
        "hp_b_vram": "Ekran kartının kendi belleğinin ne kadarının kullanıldığı. Oyunlar ve video düzenleme çok "
                     "kullanır.",
        "hp_b_download": "İnternetten şu anda ne kadar hızlı veri geldiği.",
        "hp_b_upload": "İnternete şu anda ne kadar hızlı veri gönderildiği.",
        "hp_b_disk_read": "Ana diskinizin şu anda ne kadar hızlı veri okuduğu.",
        "hp_b_disk_write": "Ana diskinizin şu anda ne kadar hızlı veri yazdığı.",
        "hp_b_fps": "Saniyedeki kare sayısı: çalışan oyunun ne kadar akıcı çizildiği. Yüksek olması daha akıcıdır; "
                    "60 civarı akıcı hissettirir.",
        "hp_b_bat_level": "Pilinizde ne kadar şarj kaldığı.",
        "hp_b_bat_state": "Pilin şarj olup olmadığı, boşalıp boşalmadığı ya da dolu olduğu.",
        "hp_b_bat_time": "Şu anki kullanımla pilin yaklaşık ne kadar dayanacağı. Yalnızca pilden çalışırken görünür.",
    },
})
