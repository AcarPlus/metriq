"""UI strings (English / Turkish). Use ``t("key")``; the language is chosen once with ``set_language``."""
import ctypes

_STR = {
    "en": {
        # column headers
        "col_now": "NOW", "col_min": "MIN", "col_max": "MAX", "col_avg": "AVG",
        # default section titles
        "sec_clock": "CLOCK / USAGE", "sec_power": "POWER", "sec_temp": "TEMPERATURES", "sec_fan": "FANS",
        "sec_net": "NETWORK / DISK", "sec_fps": "FPS",
        # default row names
        "clock": "Clock", "gpu": "GPU", "cpu_usage": "CPU Total Usage", "cpu_maxthread": "CPU Busiest Thread",
        "gpu_clock": "GPU Clock", "gpu_load": "GPU Core Load", "vram": "VRAM Used", "ram": "RAM Used",
        "cpu_pkg_power": "CPU Package Power", "cpu_core_power": "CPU Core Power", "cpu_soc_power": "CPU SoC Power",
        "cpu_core_volt": "CPU Core Voltage", "gpu_power": "GPU Power", "gpu_volt": "GPU Core Voltage",
        "cpu_temp": "CPU Temperature", "cpu_ccd": "CPU CCD{n}", "cpu_iod": "CPU IOD Hotspot",
        "cpu_core_max": "CPU Hottest Core", "gpu_temp": "GPU", "gpu_hot": "GPU Hot Spot",
        "gpu_memtemp": "VRAM (Mem Junction)", "gpu_fan": "GPU Fan", "dimm": "RAM DIMM {n}", "mb": "Motherboard {n}",
        "download": "Download", "upload": "Upload", "disk_read": "Disk Read", "disk_write": "Disk Write",
        "fps": "FPS", "fps_1": "FPS 1% Low", "fps_01": "FPS 0.1% Low", "drive": "Drive",
        # status bar
        "log_on": "RECORDING ON", "log_off": "RECORDING OFF", "sw_required": "Required", "sw_all": "All data",
        "interval_lbl": "interval (s)", "reset_done": "MIN / MAX / AVG reset", "conn_ok": "HWiNFO connected", "conn_wait": "waiting for HWiNFO…",
        "frozen": "data paused, {src} reconnecting…",
        "log_err": "recording failed: the CSV file cannot be written",
        "applying": "setting HWiNFO polling interval to {ms} ms · values held…",
        "mode_all": "all data ({n} columns)", "mode_req": "required data ({n} columns)",
        "file_wait": "waiting", "file_hint": "press L to record",
        "hint_not_running": "HWiNFO is not running: start HWiNFO64 in sensors mode",
        "hint_no_shm": "HWiNFO is running but shared memory is off: Settings > Shared Memory Support",
        "hint_empty": "Waiting for sensor data…",
        "hint_readonly": "read-only mode (run install.ps1 to let the panel start/stop HWiNFO)",
        "csv_time": "Time",
        # settings window
        "st_title": "Settings",
        "st_tab_look": "Appearance", "st_tab_log": "Logging", "st_tab_hw": "Data source", "st_tab_layout": "Layout",
        "st_save": "Save", "st_cancel": "Cancel", "st_apply": "Apply", "st_applied": "Applied.",
        "st_invalid": "Check the fields marked in red.", "st_write_err": "config.json could not be written.",
        "st_auto": "Automatic", "st_language": "Language", "st_columns": "Columns", "st_monitor": "Monitor",
        "st_mon_primary": "primary", "st_mon_portrait": "portrait", "st_window_mode": "Window mode",
        "st_wm_maximized": "Maximized", "st_wm_fullscreen": "Full screen", "st_wm_window": "Window",
        "st_check_updates": "Check for updates automatically",
        "upd_available": "Update available: v{v}",
        "upd_confirm": "HWiNFO Panel v{v} is available (this is v{cur}).\n\nDownload and install it now? The panel closes and starts again after the update.",
        "upd_confirm_src": "HWiNFO Panel v{v} is available (this is v{cur}).\n\nOpen the download page?",
        "upd_downloading": "Downloading update… {p}%",
        "upd_failed": "Update failed",
        "st_topmost": "Always on top", "st_scale": "Row / font scale (%)", "st_scale_hint": "80 - 150, rows never exceed the available height",
        "st_interval": "Interval (s)", "st_interval_hint": "0.5 - 30", "st_logging": "Recording on (L)",
        "st_data": "Recorded data", "st_logdir": "Log folder", "st_logdir_hint": "empty = default folder",
        "st_browse": "Browse…", "st_openfolder": "Open folder", "st_delim": "CSV delimiter",
        "st_decimal": "CSV decimal symbol", "st_tab": "Tab",
        "st_hwpath": "HWiNFO64.EXE", "st_detected": "Detected", "st_notfound": "not found",
        "st_manage": "Start HWiNFO with the panel and stop it when the panel closes",
        "st_sync": "Set HWiNFO's polling interval to the logging interval",
        "st_tasks_ok": "Scheduled tasks: installed (the panel can start / stop HWiNFO without a UAC prompt).",
        "st_tasks_no": "Scheduled tasks: not installed. Run install.ps1 (see README) so the panel can start / stop HWiNFO and set its interval; until then these two options have no effect.",
        "st_lay_show": "Show", "st_lay_name": "Name", "st_lay_green": "Green at", "st_lay_red": "Red at",
        "st_lay_bar": "Bar", "st_lay_title": "Title", "st_lay_color": "Colour",
        "st_lay_reset": "Reset to auto layout", "st_lay_add": "Add sensor", "st_lay_open": "Open config.json",
        "st_lay_reset_q": "Go back to the automatic layout? The custom layout is removed from config.json when you save.",
        "st_lay_empty": "No layout yet: waiting for sensor data.", "st_lay_auto_note": "Automatic layout (editing anything turns it into your own layout).",
        "st_open_err": "Could not open it.",
        "st_add_title": "Add sensor", "st_search": "Search", "st_col_sensor": "Sensor", "st_col_label": "Reading",
        "st_col_unit": "Unit", "st_col_value": "Value", "st_add_to": "Add to section", "st_add_btn": "Add",
        "st_add_none": "No sensor data available.", "st_custom_section": "Custom", "st_close": "Close",
        # data sources
        "src_builtin": "Built-in (Windows)", "conn_lhm": "LibreHardwareMonitor connected",
        "conn_lhm_wmi": "LibreHardwareMonitor connected (WMI)", "conn_builtin": "built-in sensors",
        "conn_builtin_short": "no CPU temperatures / fans", "conn_wait_lhm": "waiting for LibreHardwareMonitor…",
        "hint_lhm": "LibreHardwareMonitor not reachable: start it and enable Options > Remote Web Server > Run (port 8085)",
        "na_driver": "CPU / motherboard temperatures, fans, voltages and CPU power need a signed kernel driver: choose HWiNFO or LibreHardwareMonitor as data source.",
        "na_nvml": "NVIDIA GPU temperature, clock, power and fan are not available: NVML ({err}).",
        "na_gpu_other": "GPU: load and VRAM only (temperature, clock and power need an NVIDIA GPU (NVML), HWiNFO or LibreHardwareMonitor).",
        "na_pdh": "Windows performance counters unavailable ({err}): no core, network or disk data. Install psutil (pip install psutil) or repair the counters (lodctr /r as admin).",
        "na_pdh_psutil": "Windows performance counters unavailable ({err}): using psutil (no core clocks).",
        "na_fps": "FPS: optional, needs PresentMon (Settings > Data source).",
        "na_pm_err": "FPS: PresentMon error: {err} (it needs admin rights or the 'Performance Log Users' group).",
        "st_backend": "Data source", "st_be_auto": "Automatic (HWiNFO → LibreHardwareMonitor → built-in)",
        "st_be_hwinfo": "HWiNFO (widest coverage)", "st_be_lhm": "LibreHardwareMonitor (open source)",
        "st_be_builtin": "Built-in (no install, no temperatures / fans)", "st_active": "Active now: {name}",
        "st_avail": "Available", "st_av_hw": "HWiNFO shared memory", "st_av_lhm": "LibreHardwareMonitor",
        "st_av_builtin": "Built-in", "st_av_yes": "yes", "st_av_no": "no", "st_av_always": "always",
        "st_missing": "Not available with the active source", "st_refresh": "Refresh",
        "st_lhm_url": "LHM web server", "st_lhm_hint": "auto = http://127.0.0.1:8085/data.json · in LibreHardwareMonitor: Options > Remote Web Server > Run",
        "st_pm_path": "PresentMon (FPS)", "st_pm_hint": "optional, experimental: PresentMon console exe, FPS for LHM / built-in · empty = off",
        "st_hw_only": "HWiNFO only: these options are used only while HWiNFO is the active data source.",
    },
    "tr": {
        "col_now": "ŞİMDİ", "col_min": "MİN", "col_max": "MAKS", "col_avg": "ORT",
        "sec_clock": "SAAT / KULLANIM", "sec_power": "GÜÇ", "sec_temp": "SICAKLIK", "sec_fan": "FAN",
        "sec_net": "AĞ / DİSK", "sec_fps": "FPS",
        "clock": "Saat", "gpu": "GPU", "cpu_usage": "CPU Toplam Kullanım", "cpu_maxthread": "CPU En Yüklü Thread",
        "gpu_clock": "GPU Saati", "gpu_load": "GPU Çekirdek Yükü", "vram": "VRAM Kullanılan", "ram": "RAM Kullanılan",
        "cpu_pkg_power": "CPU Paket Gücü", "cpu_core_power": "CPU Core Gücü", "cpu_soc_power": "CPU SoC Gücü",
        "cpu_core_volt": "CPU Core Voltajı", "gpu_power": "GPU Toplam Güç", "gpu_volt": "GPU Çekirdek Voltajı",
        "cpu_temp": "CPU Sıcaklığı", "cpu_ccd": "CPU CCD{n}", "cpu_iod": "CPU IOD Hotspot",
        "cpu_core_max": "CPU En Sıcak Çekirdek", "gpu_temp": "GPU", "gpu_hot": "GPU Hot Spot",
        "gpu_memtemp": "VRAM (Mem Junction)", "gpu_fan": "GPU Fan", "dimm": "RAM DIMM {n}", "mb": "Anakart {n}",
        "download": "Download", "upload": "Upload", "disk_read": "Disk Okuma", "disk_write": "Disk Yazma",
        "fps": "FPS", "fps_1": "FPS 1% Low", "fps_01": "FPS 0.1% Low", "drive": "Disk",
        "log_on": "KAYIT AÇIK", "log_off": "KAYIT KAPALI", "sw_required": "Gerekli", "sw_all": "Tüm veriler",
        "interval_lbl": "aralık (sn)", "reset_done": "MİN / MAKS / ORT sıfırlandı", "conn_ok": "HWiNFO bağlı", "conn_wait": "HWiNFO bekleniyor…",
        "frozen": "veri akışı duraklatıldı, {src} yeniden bağlanıyor…",
        "log_err": "kayıt başarısız: CSV dosyası yazılamıyor",
        "applying": "HWiNFO tarama aralığı {ms} ms yapılıyor · değerler bekletiliyor…",
        "mode_all": "tüm veriler ({n} sütun)", "mode_req": "gerekli veriler ({n} sütun)",
        "file_wait": "bekleniyor", "file_hint": "kayıt için L",
        "hint_not_running": "HWiNFO çalışmıyor: HWiNFO64'ü sensör modunda açın",
        "hint_no_shm": "HWiNFO çalışıyor ama paylaşılan bellek kapalı: Ayarlar > Shared Memory Support",
        "hint_empty": "Sensör verisi bekleniyor…",
        "hint_readonly": "salt okunur mod (HWiNFO'yu panelin açıp kapatması için install.ps1 çalıştırın)",
        "csv_time": "Zaman",
        "st_title": "Ayarlar",
        "st_tab_look": "Görünüm", "st_tab_log": "Kayıt", "st_tab_hw": "Veri kaynağı", "st_tab_layout": "Düzen",
        "st_save": "Kaydet", "st_cancel": "İptal", "st_apply": "Uygula", "st_applied": "Uygulandı.",
        "st_invalid": "Kırmızı işaretli alanları kontrol edin.", "st_write_err": "config.json yazılamadı.",
        "st_auto": "Otomatik", "st_language": "Dil", "st_columns": "Sütun sayısı", "st_monitor": "Monitör",
        "st_mon_primary": "ana", "st_mon_portrait": "dikey", "st_window_mode": "Pencere modu",
        "st_wm_maximized": "Büyütülmüş", "st_wm_fullscreen": "Tam ekran", "st_wm_window": "Pencere",
        "st_check_updates": "Güncellemeleri otomatik denetle",
        "upd_available": "Güncelleme var: v{v}",
        "upd_confirm": "HWiNFO Panel v{v} yayınlandı (şu an v{cur}).\n\nŞimdi indirilip kurulsun mu? Panel kapanır ve güncellemeden sonra yeniden açılır.",
        "upd_confirm_src": "HWiNFO Panel v{v} yayınlandı (şu an v{cur}).\n\nİndirme sayfası açılsın mı?",
        "upd_downloading": "Güncelleme indiriliyor… %{p}",
        "upd_failed": "Güncelleme başarısız",
        "st_topmost": "Her zaman üstte", "st_scale": "Satır / yazı ölçeği (%)", "st_scale_hint": "80 - 150, satırlar kullanılabilir yüksekliği aşmaz",
        "st_interval": "Aralık (sn)", "st_interval_hint": "0,5 - 30", "st_logging": "Kayıt açık (L)",
        "st_data": "Kaydedilen veri", "st_logdir": "Kayıt klasörü", "st_logdir_hint": "boş = varsayılan klasör",
        "st_browse": "Gözat…", "st_openfolder": "Klasörü aç", "st_delim": "CSV ayırıcı",
        "st_decimal": "CSV ondalık işareti", "st_tab": "Sekme",
        "st_hwpath": "HWiNFO64.EXE", "st_detected": "Bulunan", "st_notfound": "bulunamadı",
        "st_manage": "HWiNFO'yu panel ile aç, panel kapanınca kapat",
        "st_sync": "HWiNFO tarama aralığını kayıt aralığıyla eşitle",
        "st_tasks_ok": "Zamanlanmış görevler: kurulu (panel HWiNFO'yu UAC sormadan açıp kapatabilir).",
        "st_tasks_no": "Zamanlanmış görevler: kurulu değil. Panelin HWiNFO'yu açıp kapatması ve aralığını ayarlaması için install.ps1 çalıştırın (README); o zamana kadar bu iki seçeneğin etkisi yoktur.",
        "st_lay_show": "Göster", "st_lay_name": "Ad", "st_lay_green": "Yeşil", "st_lay_red": "Kırmızı",
        "st_lay_bar": "Çubuk", "st_lay_title": "Başlık", "st_lay_color": "Renk",
        "st_lay_reset": "Otomatik düzene sıfırla", "st_lay_add": "Sensör ekle", "st_lay_open": "config.json'u aç",
        "st_lay_reset_q": "Otomatik düzene dönülsün mü? Kaydettiğinizde özel düzen config.json'dan kaldırılır.",
        "st_lay_empty": "Henüz düzen yok: sensör verisi bekleniyor.", "st_lay_auto_note": "Otomatik düzen (bir şeyi değiştirirseniz kendi düzeniniz olur).",
        "st_open_err": "Açılamadı.",
        "st_add_title": "Sensör ekle", "st_search": "Ara", "st_col_sensor": "Sensör", "st_col_label": "Okuma",
        "st_col_unit": "Birim", "st_col_value": "Değer", "st_add_to": "Eklenecek bölüm", "st_add_btn": "Ekle",
        "st_add_none": "Sensör verisi yok.", "st_custom_section": "Özel", "st_close": "Kapat",
        "src_builtin": "Yerleşik (Windows)", "conn_lhm": "LibreHardwareMonitor bağlı",
        "conn_lhm_wmi": "LibreHardwareMonitor bağlı (WMI)", "conn_builtin": "yerleşik sensörler",
        "conn_builtin_short": "CPU sıcaklığı / fan yok", "conn_wait_lhm": "LibreHardwareMonitor bekleniyor…",
        "hint_lhm": "LibreHardwareMonitor'a ulaşılamıyor: açın ve Options > Remote Web Server > Run seçin (port 8085)",
        "na_driver": "CPU / anakart sıcaklıkları, fanlar, voltajlar ve CPU gücü imzalı bir çekirdek sürücüsü gerektirir: veri kaynağı olarak HWiNFO veya LibreHardwareMonitor seçin.",
        "na_nvml": "NVIDIA GPU sıcaklığı, saati, gücü ve fanı okunamıyor: NVML ({err}).",
        "na_gpu_other": "GPU: yalnızca yük ve VRAM (sıcaklık, saat ve güç için NVIDIA GPU (NVML), HWiNFO veya LibreHardwareMonitor gerekir).",
        "na_pdh": "Windows performans sayaçları kullanılamıyor ({err}): çekirdek, ağ ve disk verisi yok. psutil kurun (pip install psutil) veya sayaçları onarın (yönetici olarak lodctr /r).",
        "na_pdh_psutil": "Windows performans sayaçları kullanılamıyor ({err}): psutil kullanılıyor (çekirdek saatleri yok).",
        "na_fps": "FPS: isteğe bağlı, PresentMon gerekir (Ayarlar > Veri kaynağı).",
        "na_pm_err": "FPS: PresentMon hatası: {err} (yönetici hakkı veya 'Performance Log Users' grubu gerekir).",
        "st_backend": "Veri kaynağı", "st_be_auto": "Otomatik (HWiNFO → LibreHardwareMonitor → yerleşik)",
        "st_be_hwinfo": "HWiNFO (en geniş kapsam)", "st_be_lhm": "LibreHardwareMonitor (açık kaynak)",
        "st_be_builtin": "Yerleşik (kurulum yok, sıcaklık / fan yok)", "st_active": "Şu an etkin: {name}",
        "st_avail": "Kullanılabilir", "st_av_hw": "HWiNFO paylaşılan bellek", "st_av_lhm": "LibreHardwareMonitor",
        "st_av_builtin": "Yerleşik", "st_av_yes": "evet", "st_av_no": "hayır", "st_av_always": "her zaman",
        "st_missing": "Etkin kaynakta olmayan veriler", "st_refresh": "Yenile",
        "st_lhm_url": "LHM web sunucusu", "st_lhm_hint": "auto = http://127.0.0.1:8085/data.json · LibreHardwareMonitor'da: Options > Remote Web Server > Run",
        "st_pm_path": "PresentMon (FPS)", "st_pm_hint": "isteğe bağlı, deneysel: PresentMon konsol exe'si, LHM / yerleşik için FPS · boş = kapalı",
        "st_hw_only": "Yalnızca HWiNFO: bu seçenekler sadece veri kaynağı HWiNFO iken kullanılır.",
    },
}

_lang = "en"


def windows_language():
    """'tr' if the Windows display language is Turkish, otherwise 'en'."""
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "tr" if (lid & 0x3FF) == 0x1F else "en"
    except Exception:
        return "en"


def set_language(code):
    """code: 'auto' | 'tr' | 'en'. Returns the language actually used."""
    global _lang
    code = (code or "auto").lower()
    _lang = windows_language() if code == "auto" else (code if code in _STR else "en")
    return _lang


def language():
    return _lang


def register(strings):
    """Feature modules add their own texts here instead of editing _STR: register({"tr": {...}, "en": {...}})."""
    for lang, d in strings.items():
        _STR.setdefault(lang, {}).update(d)


def t(key, **kw):
    s = _STR.get(_lang, _STR["en"]).get(key) or _STR["en"].get(key) or key
    return s.format(**kw) if kw else s
