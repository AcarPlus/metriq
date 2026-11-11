"""Basic view: a short, plain-language panel built from the auto-detected layout.

    basicmode.view(layout.autodetect(rd, rids=True), rd)    # -> sections in config form (see layout.build_sections)
    basicmode.status_text(row, value)                       # "Normal" / "Warm" / "Very hot" / "Low" / ... or ""

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
# position within the row's green..red limits where the next word starts
_STEPS = {"temp": (0.60, 0.85, ("stw_ok", "stw_warm", "stw_hot")),
          "usage": (0.40, 0.80, ("stw_low", "stw_mid", "stw_high"))}


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
                rows.append(d)
        if rows:
            out.append({"title": t(title_key), "color": layout.ICON_COLORS[icon], "icon": icon, "rows": rows})
    return out


def status_text(row, v):
    """Status word for a value of a Basic row ('' when the row has none)."""
    sc = _STEPS.get(getattr(row, "status", None))
    f = layout.frac(row, v)
    if not sc or f is None:
        return ""
    return t(sc[2][0] if f < sc[0] else sc[2][1] if f < sc[1] else sc[2][2])


i18n.register({
    "en": {
        "bs_cpu": "PROCESSOR", "bs_gpu": "GRAPHICS CARD", "bs_mem": "MEMORY", "bs_net": "NETWORK & DISK",
        "bs_game": "GAMES", "bs_bat": "BATTERY",
        "bn_cpu_usage": "Processor usage", "bn_cpu_temp": "Processor temperature", "bn_fan": "Fan",
        "bn_gpu_load": "Graphics card usage", "bn_gpu_temp": "Graphics card temperature",
        "bn_gpu_fan": "Graphics card fan", "bn_ram": "Memory (RAM)", "bn_vram": "Graphics card memory",
        "bn_download": "Download", "bn_upload": "Upload", "bn_disk_read": "Disk read", "bn_disk_write": "Disk write",
        "bn_fps": "FPS", "bn_bat_level": "Battery", "bn_bat_state": "Battery state", "bn_bat_time": "Time left",
        "stw_ok": "Normal", "stw_warm": "Warm", "stw_hot": "Very hot", "stw_low": "Low", "stw_mid": "Medium",
        "stw_high": "High", "col_status": "STATUS",
        "ui_basic": "Basic", "ui_advanced": "Advanced",
        "ui_mode_hint": "Basic view is on. Switch to Advanced for all details (B).",
        "st_card_uimode": "View mode", "st_uimode": "Mode",
        "st_uimode_hint": "Basic shows a few easy-to-read rows. Advanced shows everything: all sensors, your own "
                          "layout, recording options and fan control.",
        "hp_uimodebtn": "Switches between Basic (a few easy rows) and Advanced (everything). You can also press B.",
        "hp_b_cpu_usage": "How busy your processor (CPU) is. Low means it is mostly resting; High is normal while "
                          "you run something heavy.",
        "hp_b_cpu_temp": "How hot your processor is. Normal is fine. If it stays Very hot for long, check that "
                         "your computer's air flow is not blocked.",
        "hp_b_fan": "How fast your main fan spins, in revolutions per minute (RPM). A faster fan cools more but is "
                    "louder.",
        "hp_b_gpu_load": "How hard your graphics card (GPU) is working. High is normal while gaming.",
        "hp_b_gpu_temp": "How hot your graphics card is. Normal is fine. If it stays Very hot for long, check the "
                         "air flow in your computer.",
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
        "bs_cpu": "İŞLEMCİ", "bs_gpu": "EKRAN KARTI", "bs_mem": "BELLEK", "bs_net": "AĞ VE DİSK",
        "bs_game": "OYUN", "bs_bat": "PİL",
        "bn_cpu_usage": "İşlemci kullanımı", "bn_cpu_temp": "İşlemci sıcaklığı", "bn_fan": "Fan",
        "bn_gpu_load": "Ekran kartı kullanımı", "bn_gpu_temp": "Ekran kartı sıcaklığı",
        "bn_gpu_fan": "Ekran kartı fanı", "bn_ram": "Bellek (RAM)", "bn_vram": "Ekran kartı belleği",
        "bn_download": "İndirme", "bn_upload": "Yükleme", "bn_disk_read": "Disk okuma", "bn_disk_write": "Disk yazma",
        "bn_fps": "FPS", "bn_bat_level": "Pil", "bn_bat_state": "Pil durumu", "bn_bat_time": "Kalan süre",
        "stw_ok": "Normal", "stw_warm": "Sıcak", "stw_hot": "Çok sıcak", "stw_low": "Düşük", "stw_mid": "Orta",
        "stw_high": "Yüksek", "col_status": "DURUM",
        "ui_basic": "Basit", "ui_advanced": "Gelişmiş",
        "ui_mode_hint": "Basit görünüm açık. Tüm ayrıntılar için Gelişmiş moda geç (B).",
        "st_card_uimode": "Görünüm modu", "st_uimode": "Mod",
        "st_uimode_hint": "Basit, birkaç anlaşılır satır gösterir. Gelişmiş her şeyi gösterir: tüm sensörler, kendi "
                          "düzeniniz, kayıt seçenekleri ve fan kontrolü.",
        "hp_uimodebtn": "Basit (birkaç kolay satır) ile Gelişmiş (her şey) arasında geçiş yapar. B tuşuna da "
                        "basabilirsiniz.",
        "hp_b_cpu_usage": "İşlemcinizin (CPU) ne kadar meşgul olduğu. Düşük, çoğunlukla boşta demektir; ağır bir "
                          "iş yaparken Yüksek olması normaldir.",
        "hp_b_cpu_temp": "İşlemcinizin ne kadar ısındığı. Normal iyidir. Uzun süre Çok sıcak kalırsa bilgisayarınızın "
                         "hava akışının tıkalı olmadığına bakın.",
        "hp_b_fan": "Ana fanınızın dakikadaki devir sayısı (RPM). Hızlı fan daha çok soğutur ama daha gürültülüdür.",
        "hp_b_gpu_load": "Ekran kartınızın (GPU) ne kadar çalıştığı. Oyun oynarken Yüksek olması normaldir.",
        "hp_b_gpu_temp": "Ekran kartınızın ne kadar ısındığı. Normal iyidir. Uzun süre Çok sıcak kalırsa "
                         "bilgisayarınızın hava akışına bakın.",
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
