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
        "log_on": "RECORDING ON", "log_off": "RECORDING OFF", "sw_required": "Panel data", "sw_all": "All data",
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
        "upd_confirm": "metriQ v{v} is available (this is v{cur}).\n\nDownload and install it now? The panel closes and starts again after the update.",
        "upd_confirm_src": "metriQ v{v} is available (this is v{cur}).\n\nOpen the download page?",
        "upd_downloading": "Downloading update… {p}%",
        "upd_failed": "Update failed",
        "st_topmost": "Always on top",
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
        "na_gpu_other": "GPU: load and VRAM only (temperature, clock and power need an NVIDIA GPU (NVML), an AMD GPU (ADL), HWiNFO or LibreHardwareMonitor).",
        "na_adl": "AMD GPU temperature, clock, power and fan are not available: ADL ({err}).",
        "why_hw_none": "Built-in source: temperatures and power need HWiNFO, and HWiNFO64 is not installed. Install it from hwinfo.com, then run the metriQ Setup again.",
        "why_hw_notasks": "Built-in source: HWiNFO is installed but not running, and the panel cannot start it (its scheduled tasks are missing). Run the metriQ Setup again, or start HWiNFO64 yourself.",
        "why_hw_manage_off": "Built-in source: HWiNFO is not running (starting HWiNFO with the panel is switched off). Start HWiNFO64 or switch the option on.",
        "why_hw_noshm": "Built-in source: HWiNFO is running but its shared memory is off. In HWiNFO: Settings > Shared Memory Support (the free version switches it off after 12 hours: restart HWiNFO).",
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
        # ---- settings: Fan page (fan_ui.py)
        "fn_tab": "Fan", "fn_title": "Fan control",
        "fn_missing": "Fan control only works in the installed version (Setup). The portable version has no fan service.",
        "fn_stopped": "The fan service (metriQFan) is not running. Until it starts, the BIOS controls the fans. "
                      "You can start it from the Windows Services list and try again.",
        "fn_starting": "The fan service is starting or stopping. Try again in a few seconds.",
        "fn_unreach": "The fan service does not answer. Wait a moment and try again; if it keeps failing, restart "
                      "the computer.",
        "fn_retry": "Try again", "fn_checking": "Connecting to the fan service...",
        "fn_pawnio": "Motherboard fans need the PawnIO driver (GPU fans work without it).",
        "fn_pawnio_btn": "Get PawnIO",
        "fn_risk_title": "Read this first",
        "fn_risk_intro": "Wrong fan settings can overheat the computer and damage hardware. This feature comes "
                         "without any warranty; you use it at your own risk.",
        "fn_risk_rules": "Safety rules",
        "fn_risk_1": "Every role has a minimum speed: pump at least 60 %, CPU / GPU / unknown fans at least 30 %. The "
                     "pump and the CPU fan are never lowered below their minimum.",
        "fn_risk_2": "If the CPU or GPU temperature limit is exceeded, all fans go to 100 %.",
        "fn_risk_3": "If a temperature sensor is lost, the fans that depend on it go to 100 %.",
        "fn_risk_4": "Pressing STOP or switching the control off gives the fans back to the BIOS.",
        "fn_risk_ok": "I understand the risks", "fn_continue": "Continue",
        "fn_master": "Fan control", "fn_stop": "STOP", "fn_cpu": "CPU", "fn_gpu": "GPU",
        "fn_panic": "Temperature limit exceeded: all fans at 100 %",
        "fn_safe": "A fan could not return to the BIOS cleanly; the safe curve is used. Restarting the computer "
                   "gives control back to the BIOS.",
        "fn_hw_error": "Hardware could not be opened: {e}", "fn_hw_idle": "Opening the hardware...",
        "fn_lost": "The fan service is not reachable.",
        "fn_save_err": "Settings could not be saved: the fan service is not reachable.",
        "fn_enable_err": "Fan control could not be changed: the fan service is not reachable.",
        "fn_stop_err": "The STOP command did not reach the service! The fans may still be under software control.",
        "fn_profiles": "Profiles", "fn_p_silent": "Silent", "fn_p_balanced": "Balanced", "fn_p_perf": "Performance",
        "fn_profiles_hint": "Applies a ready-made curve to every fan except pumps and empty headers; pumps run at a "
                            "fixed 100 %. You can fine-tune each fan afterwards.",
        "fn_limits": "Temperature limits", "fn_cpu_limit": "CPU limit (°C)", "fn_gpu_limit": "GPU limit (°C)",
        "fn_limit_hint": "At this temperature all fans go to 100 % (CPU 60-95 °C, GPU 60-90 °C).",
        "fn_headers": "Fan headers",
        "fn_headers_hint": "Each card is one fan header; all fans of a GPU share one card. Switch Identify on to "
                           "find out which fan in the case it is.",
        "fn_gpu_fans": "{n} fans", "fn_fan_n": "Fan {n}", "fn_pct": "{d} %",
        "fn_gpu_all": "Mode and curve apply to all fans of this GPU together.",
        "fn_chip_none": "Empty header", "fn_chip_nofan": "No fan detected", "fn_show": "Show", "fn_hide": "Hide",
        "fn_sub_on": "metriQ is driving the fans", "fn_sub_off": "The BIOS is driving the fans",
        "fn_no_headers": "No fan headers found.",
        "fn_role": "Role", "fn_mode": "Mode",
        "fn_role_unknown": "Unknown", "fn_role_case": "Case fan", "fn_role_cpu": "CPU fan", "fn_role_pump": "Pump",
        "fn_role_gpu": "GPU", "fn_role_none": "Empty header",
        "fn_mode_bios": "BIOS", "fn_mode_fixed": "Fixed", "fn_mode_curve": "Curve",
        "fn_min": "at least {n} %", "fn_min0": "no minimum",
        "fn_rpm": "{v} RPM", "fn_no_rpm": "- RPM", "fn_duty": "now {d} %", "fn_target": " (target {g} %)",
        "fn_r_bios": "BIOS controls it", "fn_r_fixed": "fixed speed", "fn_r_curve": "following the curve",
        "fn_r_safe": "safe curve", "fn_r_limit": "temperature limit: 100 %", "fn_r_override": "identify",
        "fn_r_nosensor": "sensor lost: 100 %", "fn_r_waiting": "waiting for the sensor",
        "fn_stall": "Fan is not spinning!",
        "fn_diag": "Identify", "fn_diag_on": "Identify on · {t}", "fn_diag_fail": "Could not start.",
        "fn_diag_lost": "Identify stopped: the fan service did not answer.",
        "fn_fixed": "Fixed speed", "fn_source": "Temperature source", "fn_src_cpu": "CPU", "fn_src_gpu": "GPU",
        "fn_src_max": "Highest (CPU / GPU)",
        "fn_curve_hint": "Drag the points. Double-click empty space to add a point, double-click a point to remove "
                         "it (2-8 points). The red area is below this role's minimum.",
        "fn_bios_note": "The BIOS controls this fan; metriQ leaves it alone.",
        "fn_none_note": "Empty header: it always stays under BIOS control.",
        "hp_fan_master": "While on, metriQ drives the fans with the curves you set. While off, the BIOS controls "
                         "all fans.",
        "hp_fan_stop": "Gives every fan back to the BIOS at once and switches fan control off.",
        "hp_fan_profiles": "Quick starting points. Each profile sets a temperature curve on all fans except pumps "
                           "and empty headers.",
        "hp_fan_limits": "If the CPU or GPU reaches this temperature, all fans go to 100 %.",
        "hp_fan_role": "The role sets the lowest speed allowed: pump 60 %, CPU / GPU / unknown 30 %, case fan no "
                       "minimum. An empty header has no fan and always stays under BIOS control.",
        "hp_fan_curve": "Sets the fan speed by temperature. Drag the points; double-click empty space to add one, "
                        "double-click a point to remove it (2-8 points). The red area is below the role's minimum: "
                        "the fan never goes lower. The vertical line is the current temperature, the dot the "
                        "current speed.",
        "hp_fan_diag": "A switch: while on, the fan runs at 100 % with no time limit, so you can look into the case "
                       "and see which fan it is (on a GPU card, all fans of that GPU). The service accepts only 30 "
                       "seconds at a time, so metriQ renews it every 10 seconds. It turns off when you press it "
                       "again, leave this page, close Settings or press STOP.",
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
        "log_on": "KAYIT AÇIK", "log_off": "KAYIT KAPALI", "sw_required": "Paneldeki veriler", "sw_all": "Tüm veriler",
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
        "upd_confirm": "metriQ v{v} yayınlandı (şu an v{cur}).\n\nŞimdi indirilip kurulsun mu? Panel kapanır ve güncellemeden sonra yeniden açılır.",
        "upd_confirm_src": "metriQ v{v} yayınlandı (şu an v{cur}).\n\nİndirme sayfası açılsın mı?",
        "upd_downloading": "Güncelleme indiriliyor… %{p}",
        "upd_failed": "Güncelleme başarısız",
        "st_topmost": "Her zaman üstte",
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
        "na_gpu_other": "GPU: yalnızca yük ve VRAM (sıcaklık, saat ve güç için NVIDIA GPU (NVML), AMD GPU (ADL), HWiNFO veya LibreHardwareMonitor gerekir).",
        "na_adl": "AMD GPU sıcaklığı, saati, gücü ve fanı okunamıyor: ADL ({err}).",
        "why_hw_none": "Yerleşik kaynak: sıcaklık ve güç için HWiNFO gerekli, ama HWiNFO64 kurulu değil. hwinfo.com'dan kurun, sonra metriQ kurulumunu yeniden çalıştırın.",
        "why_hw_notasks": "Yerleşik kaynak: HWiNFO kurulu ama çalışmıyor ve panel onu başlatamıyor (zamanlanmış görevler yok). metriQ kurulumunu yeniden çalıştırın ya da HWiNFO64'ü kendiniz açın.",
        "why_hw_manage_off": "Yerleşik kaynak: HWiNFO çalışmıyor (HWiNFO'yu panelle başlatma seçeneği kapalı). HWiNFO64'ü açın ya da seçeneği açın.",
        "why_hw_noshm": "Yerleşik kaynak: HWiNFO çalışıyor ama paylaşılan belleği kapalı. HWiNFO'da: Settings > Shared Memory Support (ücretsiz sürüm bunu 12 saat sonra kapatır: HWiNFO'yu yeniden başlatın).",
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
        # ---- ayarlar: Fan sayfası (fan_ui.py)
        "fn_tab": "Fan", "fn_title": "Fan kontrolü",
        "fn_missing": "Fan kontrolü yalnızca kurulu sürümde (Setup) çalışır. Taşınabilir sürümde fan hizmeti "
                      "bulunmaz.",
        "fn_stopped": "Fan hizmeti (metriQFan) çalışmıyor. Hizmet başlayana kadar fanları BIOS yönetir. Hizmeti "
                      "Windows'un Hizmetler ekranından başlatıp yeniden deneyebilirsin.",
        "fn_starting": "Fan hizmeti başlıyor ya da duruyor. Birkaç saniye sonra yeniden dene.",
        "fn_unreach": "Fan hizmeti yanıt vermiyor. Biraz bekleyip yeniden dene; sorun sürerse bilgisayarı yeniden "
                      "başlat.",
        "fn_retry": "Yeniden dene", "fn_checking": "Fan hizmetine bağlanılıyor…",
        "fn_pawnio": "Anakart fanları için PawnIO sürücüsü gerekli (GPU fanları onsuz da çalışır).",
        "fn_pawnio_btn": "PawnIO'yu indir",
        "fn_risk_title": "Önce bunu oku",
        "fn_risk_intro": "Fan hızlarını yanlış ayarlamak bilgisayarın aşırı ısınmasına ve donanımın zarar "
                         "görmesine yol açabilir. Bu özellik hiçbir garanti olmadan sunulur; kullanım sorumluluğu "
                         "sana aittir.",
        "fn_risk_rules": "Güvenlik kuralları",
        "fn_risk_1": "Her rolün bir alt hız sınırı var: pompa en az %60, CPU / GPU / bilinmeyen fanlar en az %30. "
                     "Pompa ve CPU fanı bu sınırın altına asla indirilmez.",
        "fn_risk_2": "CPU ya da GPU sıcaklık sınırı aşılırsa tüm fanlar %100'e çıkar.",
        "fn_risk_3": "Bir sıcaklık sensörü okunamaz olursa o sensöre bağlı fan %100'e çıkar.",
        "fn_risk_4": "DURDUR'a basınca ya da kontrolü kapatınca fanlar BIOS'a döner.",
        "fn_risk_ok": "Riskleri anladım", "fn_continue": "Devam",
        "fn_master": "Fan kontrolü", "fn_stop": "DURDUR", "fn_cpu": "CPU", "fn_gpu": "GPU",
        "fn_panic": "Sıcaklık sınırı aşıldı: tüm fanlar %100",
        "fn_safe": "Bir fan BIOS'a temiz dönemedi; güvenli eğri kullanılıyor, bilgisayarı yeniden başlatınca BIOS "
                   "devralır.",
        "fn_hw_error": "Donanım açılamadı: {e}", "fn_hw_idle": "Donanım açılıyor…",
        "fn_lost": "Fan hizmetine ulaşılamıyor.",
        "fn_save_err": "Ayarlar kaydedilemedi: fan hizmetine ulaşılamadı.",
        "fn_enable_err": "Fan kontrolü değiştirilemedi: fan hizmetine ulaşılamadı.",
        "fn_stop_err": "DURDUR komutu hizmete ulaşmadı! Fanlar hâlâ yazılım denetiminde olabilir.",
        "fn_profiles": "Profiller", "fn_p_silent": "Sessiz", "fn_p_balanced": "Dengeli",
        "fn_p_perf": "Performans",
        "fn_profiles_hint": "Pompa ve boş başlıklar dışındaki her fana hazır bir eğri uygular; pompa %100 sabit "
                            "döner. Sonra fanları tek tek ayarlayabilirsin.",
        "fn_limits": "Sıcaklık sınırları", "fn_cpu_limit": "CPU sınırı (°C)", "fn_gpu_limit": "GPU sınırı (°C)",
        "fn_limit_hint": "Bu sıcaklıkta tüm fanlar %100'e çıkar (CPU 60-95 °C, GPU 60-90 °C).",
        "fn_headers": "Fan başlıkları",
        "fn_headers_hint": "Her kart bir fan başlığıdır; bir GPU'nun fanları tek kartta toplanır. Kasadaki hangi "
                           "fan olduğunu anlamak için Tanı'yı aç.",
        "fn_gpu_fans": "{n} fan", "fn_fan_n": "Fan {n}", "fn_pct": "%{d}",
        "fn_gpu_all": "Mod ve eğri bu GPU'nun tüm fanlarına birlikte uygulanır.",
        "fn_chip_none": "Boş başlık", "fn_chip_nofan": "Fan algılanmadı", "fn_show": "Göster", "fn_hide": "Gizle",
        "fn_sub_on": "Fanları metriQ yönetiyor", "fn_sub_off": "Fanları BIOS yönetiyor",
        "fn_no_headers": "Fan başlığı bulunamadı.",
        "fn_role": "Rol", "fn_mode": "Mod",
        "fn_role_unknown": "Bilinmiyor", "fn_role_case": "Kasa fanı", "fn_role_cpu": "CPU fanı",
        "fn_role_pump": "Pompa", "fn_role_gpu": "GPU", "fn_role_none": "Boş başlık",
        "fn_mode_bios": "BIOS", "fn_mode_fixed": "Sabit", "fn_mode_curve": "Eğri",
        "fn_min": "en az %{n}", "fn_min0": "alt sınır yok",
        "fn_rpm": "{v} RPM", "fn_no_rpm": "- RPM", "fn_duty": "şimdi %{d}", "fn_target": " (hedef %{g})",
        "fn_r_bios": "BIOS yönetiyor", "fn_r_fixed": "sabit hız", "fn_r_curve": "eğriye göre",
        "fn_r_safe": "güvenli eğri", "fn_r_limit": "sıcaklık sınırı: %100", "fn_r_override": "tanı",
        "fn_r_nosensor": "sensör kayıp: %100", "fn_r_waiting": "sensör bekleniyor",
        "fn_stall": "Fan dönmüyor!",
        "fn_diag": "Tanı", "fn_diag_on": "Tanı açık · {t}", "fn_diag_fail": "Başlatılamadı.",
        "fn_diag_lost": "Tanı durdu: fan hizmeti yanıt vermedi.",
        "fn_fixed": "Sabit hız", "fn_source": "Sıcaklık kaynağı", "fn_src_cpu": "CPU", "fn_src_gpu": "GPU",
        "fn_src_max": "En yüksek (CPU / GPU)",
        "fn_curve_hint": "Noktaları sürükle. Boş yere çift tıklayınca nokta eklenir, noktaya çift tıklayınca "
                         "silinir (2-8 nokta). Kırmızı alan bu rolün alt sınırının altıdır.",
        "fn_bios_note": "Bu fanı BIOS yönetir; metriQ dokunmaz.",
        "fn_none_note": "Boş başlık: her zaman BIOS'un denetiminde kalır.",
        "hp_fan_master": "Açıkken metriQ fanları senin belirlediğin eğrilere göre yönetir. Kapalıyken tüm fanları "
                         "BIOS yönetir.",
        "hp_fan_stop": "Tüm fanları hemen BIOS'a geri verir ve fan kontrolünü kapatır.",
        "hp_fan_profiles": "Hızlı başlangıç noktaları. Her profil, pompa ve boş başlıklar dışındaki tüm fanlara bir "
                           "sıcaklık eğrisi uygular.",
        "hp_fan_limits": "CPU ya da GPU bu sıcaklığa ulaşırsa tüm fanlar %100'e çıkar.",
        "hp_fan_role": "Rol, izin verilen en düşük hızı belirler: pompa %60, CPU / GPU / bilinmeyen %30, kasa "
                       "fanında alt sınır yok. Boş başlıkta fan takılı değildir ve her zaman BIOS'un denetiminde "
                       "kalır.",
        "hp_fan_curve": "Fan hızını sıcaklığa göre belirler. Noktaları sürükle; boş yere çift tıklayınca nokta "
                        "eklenir, noktaya çift tıklayınca silinir (2-8 nokta). Kırmızı alan rolün alt sınırının "
                        "altıdır: fan bunun altına inmez. Dikey çizgi şimdiki sıcaklığı, nokta şimdiki hızı "
                        "gösterir.",
        "hp_fan_diag": "Bir anahtar: açıkken fan süre sınırı olmadan %100 hızla döner; kasaya bakıp hangi fan "
                       "olduğunu görebilirsin (GPU kartında o GPU'nun tüm fanları döner). Hizmet her seferinde en "
                       "çok 30 saniyeye izin verdiği için metriQ bunu 10 saniyede bir yeniler. Tekrar basınca, bu "
                       "sayfadan çıkınca, Ayarlar'ı kapatınca ya da DURDUR'a basınca kapanır.",
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
