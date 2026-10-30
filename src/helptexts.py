"""Help texts for first-time users, and the little "i" marker that shows one as a hover balloon.

    helptexts.info_icon(parent, "interval")      # Label with a themed (i) image; hovering explains the setting
    helptexts.text("hdr_min")                    # the text itself (for tooltips on the panel canvas)

Texts live in i18n under "hp_<key>" (Turkish and English); keep them short, friendly and free of jargon.
"""
import tkinter as tk

import gfx
import i18n
import themes
from i18n import t
from tooltip import Tooltip

SIZE = 14           # marker diameter in pixels at 100 % scaling

i18n.register({
    "en": {
        # ---- settings: Appearance
        "hp_columns": "How many columns the cards are arranged in. Automatic picks what fits your screen best.",
        "hp_monitor": "The screen the panel opens on. Automatic prefers a second, upright (portrait) screen, "
                      "otherwise your main one.",
        "hp_window_mode": "Maximized fills the screen but keeps the window frame. Full screen hides everything "
                          "else. Window is a normal window you can move and resize.",
        "hp_topmost": "Keeps the panel above other windows, so it stays visible while you work or play.",
        "hp_updates": "Looks for a newer metriQ about once a day. Nothing is installed without asking you first.",
        # ---- settings: Logging
        "hp_interval": "How often the panel reads the sensors and writes a line to the CSV, in seconds. "
                       "Smaller values give finer detail but bigger files.",
        "hp_logging": "While this is on, the readings are saved to a CSV file you can open in Excel. "
                      "You can also press L on the panel.",
        "hp_logall": "Panel data saves only the rows you see on the panel. All data saves every sensor the data "
                     "source offers: many more columns and bigger files.",
        "hp_logdir": "Where the CSV files are saved. Leave it empty to use the default folder.",
        "hp_delim": "The character that separates the columns in the CSV. Automatic follows your Windows "
                    "regional settings, so Excel opens the file correctly.",
        "hp_decimal": "The symbol used for decimals in the CSV (comma or dot). Automatic follows your Windows "
                      "regional settings, so Excel reads the numbers correctly.",
        "hp_top": "Adds columns to the CSV with the programs that use the most CPU, GPU, RAM, VRAM or disk at "
                  "each moment. 0 turns a column off.",
        "hp_detail": "Also saves a second CSV with one line per program at every sample, so you can see exactly "
                     "who was busy. It grows quickly.",
        "hp_filter": "Choose which programs go into that second file. By default every program is included.",
        "hp_maxmb": "When the CSV reaches this size, the panel starts a new file. 0 means no limit.",
        "hp_keep": "CSV files made by the panel that are older than this many days are deleted when it starts. "
                   "0 keeps everything.",
        "hp_est": "A rough guess of the disk space recording uses with the current settings.",
        # ---- settings: Data source
        "hp_backend": "Where the numbers come from. HWiNFO has the widest coverage (temperatures, fans, voltages). "
                      "LibreHardwareMonitor is a free alternative. Built-in needs no extra program but has no CPU "
                      "temperatures or fans. Automatic tries them in that order.",
        "hp_lhm": "The address of LibreHardwareMonitor's web server. Leave it on auto unless you changed its port.",
        "hp_pm": "Optional: PresentMon lets the panel show FPS when HWiNFO is not the data source. "
                 "Leave it empty if you do not need FPS.",
        "hp_hwpath": "Where HWiNFO64.EXE is installed. Automatic usually finds it by itself.",
        "hp_manage": "The panel opens HWiNFO when it starts and closes it when you close the panel. "
                     "Needs the one-time setup (install.ps1).",
        "hp_sync": "Sets HWiNFO's own reading speed to your recording interval, so both run in step. "
                   "Needs the one-time setup (install.ps1).",
        # ---- settings: Layout
        "hp_layout": "Choose which rows appear on the panel, and edit their names, order and colour limits. "
                     "Changes apply with Apply or Save. Reset brings back the automatic layout.",
        "hp_lay_show": "Untick to hide a row from the panel. It is then left out of the Panel data CSV too.",
        "hp_lay_limits": "The value shown fully green and the value shown fully red; colours blend in between. "
                         "Leave both empty for automatic limits. Swap them when low is bad (like FPS).",
        "hp_lay_bar": "Shows a small bar next to the value for this row.",
        # ---- settings: Theme
        "hp_th_radius": "How round the corners of the cards are.",
        "hp_th_spacing": "Space between the rows: lower packs more rows in, higher makes the panel airier.",
        "hp_th_fonts": "Pick the typefaces used on the panel. (automatic) lets metriQ choose a good one.",
        "hp_th_grad": "The five colours that values pass through from good (1) to bad (5), "
                      "for example green to red as a temperature climbs.",
        "hp_th_mode": "Smooth fades the colour gradually. Stepped uses a few flat colour bands that change at the "
                      "thresholds below.",
        "hp_th_auto": "Uses 4 equal bands between each row's green and red limits. Untick to set your own thresholds.",
        "hp_th_steps": "Where the colour changes, as a percent of each row's green-to-red range. "
                       "2 to 4 rising numbers (like 50, 75, 90) give 3 to 5 bands.",
        # ---- settings: Diagnostics
        "hp_scan": "Checks your recordings for ones that stopped suddenly or have gaps, which can point to a "
                   "freeze, crash or power loss, and compares them with the Windows event log.",
        # ---- panel
        "hp_hdr_now": "The value right now.",
        "hp_hdr_min": "The lowest value since the panel started, since you last pressed reset (R), "
                      "or since recording started.",
        "hp_hdr_max": "The highest value since the panel started, since you last pressed reset (R), "
                      "or since recording started.",
        "hp_hdr_avg": "The average since the panel started, since you last pressed reset (R), "
                      "or since recording started.",
        "hp_logbtn": "Click to start or stop recording to a CSV file (or press L). Green means the readings "
                     "are being saved.",
        "hp_modebtn": "Chooses what is saved: Panel data is only the rows you see on the panel, All data is every "
                      "sensor. Click to switch.",
        "hp_resetbtn": "Starts MIN, MAX and AVG over from now (or press R). Recording is not affected.",
        "hp_viewbtn": "Opens the recording viewer (or press V).",
        "hp_gearbtn": "Opens the settings (or press S).",
        "hp_interval_lbl": "Seconds between readings and CSV lines. You can also change it in Settings > Logging.",
        "hp_bg": "Normally closing the panel (X) ends metriQ. Tick a situation here and, while it is active, closing "
                 "the panel only hides the window: recording and mini mode keep going, and a metriQ icon appears "
                 "next to the clock. Click it to bring the panel back; right-click it for Exit. Launching metriQ "
                 "again also shows the hidden panel.",
        # ---- settings: Mini mode
        "hp_mini": "A small see-through strip with a few readings that stays on top of your game. It works only "
                   "over games in windowed or borderless (fullscreen windowed) mode: a game in exclusive "
                   "fullscreen hides it. The strip cannot be clicked, so the mouse and keys always reach the game. "
                   "metriQ never touches the game itself.",
        "hp_mini_hotkey": "Shows or hides the strip from anywhere, even inside a game (the strip itself cannot be "
                          "clicked). On the panel you can also press M. If another program already uses the key "
                          "combination, metriQ tells you; pick a different one.",
        "hp_mini_bg": "Plate puts the readings on a card in the theme colour, which is the easiest to read. "
                      "Transparent shows only the text, which can be hard to read on bright scenes.",
        "hp_mini_metrics": "Rows of your panel layout (up to 8). The colours follow the same green-to-red limits "
                           "as on the panel. Leave it as it is to get FPS, CPU / GPU temperature, usage and power "
                           "where they exist.",
    },
    "tr": {
        "hp_columns": "Kartların kaç sütunda dizileceği. Otomatik, ekranınıza en uygun olanı seçer.",
        "hp_monitor": "Panelin açılacağı ekran. Otomatik, varsa ikinci bir dikey ekranı, yoksa ana ekranınızı seçer.",
        "hp_window_mode": "Büyütülmüş ekranı kaplar ama pencere çerçevesi kalır. Tam ekran her şeyi gizler. "
                          "Pencere, taşıyıp yeniden boyutlandırabileceğiniz normal bir penceredir.",
        "hp_topmost": "Paneli diğer pencerelerin üstünde tutar; çalışırken ya da oyun oynarken görünür kalır.",
        "hp_updates": "Günde bir kez daha yeni bir metriQ olup olmadığına bakar. Sormadan hiçbir şey kurulmaz.",
        "hp_interval": "Panelin sensörleri ne sıklıkla okuyup CSV'ye bir satır yazdığı (saniye). "
                       "Küçük değer daha ince ayrıntı verir ama dosyalar büyür.",
        "hp_logging": "Bu açıkken okumalar, Excel'de açabileceğiniz bir CSV dosyasına kaydedilir. "
                      "Panelde L tuşuna da basabilirsiniz.",
        "hp_logall": "Paneldeki veriler, yalnızca panelde gördüğünüz satırları kaydeder. Tüm veriler, veri kaynağının "
                     "sunduğu bütün sensörleri kaydeder: çok daha fazla sütun ve daha büyük dosyalar.",
        "hp_logdir": "CSV dosyalarının kaydedileceği yer. Varsayılan klasör için boş bırakın.",
        "hp_delim": "CSV'de sütunları ayıran karakter. Otomatik, Windows bölge ayarlarınızı izler; "
                    "böylece Excel dosyayı doğru açar.",
        "hp_decimal": "CSV'de ondalık için kullanılan işaret (virgül ya da nokta). Otomatik, Windows bölge "
                      "ayarlarınızı izler; böylece Excel sayıları doğru okur.",
        "hp_top": "CSV'ye, her an en çok CPU, GPU, RAM, VRAM ya da disk kullanan programların sütunlarını ekler. "
                  "0 o sütunu kapatır.",
        "hp_detail": "Her örnekte program başına bir satır içeren ikinci bir CSV de kaydeder; kimin yoğun "
                     "olduğunu tam görürsünüz. Hızla büyür.",
        "hp_filter": "İkinci dosyaya hangi programların gireceğini seçin. Varsayılan olarak hepsi girer.",
        "hp_maxmb": "CSV bu boyuta ulaşınca panel yeni bir dosya başlatır. 0 sınırsız demektir.",
        "hp_keep": "Panelin oluşturduğu CSV dosyaları, bu kadar günden eskiyse panel açılırken silinir. "
                   "0 hepsini saklar.",
        "hp_est": "Mevcut ayarlarla kaydın diskte ne kadar yer tutacağının kaba bir tahmini.",
        "hp_backend": "Sayıların nereden geldiği. HWiNFO en geniş kapsamı sunar (sıcaklık, fan, voltaj). "
                      "LibreHardwareMonitor ücretsiz bir alternatiftir. Yerleşik kaynak ek program istemez ama "
                      "CPU sıcaklığı ve fan göstermez. Otomatik bunları bu sırayla dener.",
        "hp_lhm": "LibreHardwareMonitor'ın web sunucusunun adresi. Portunu değiştirmediyseniz auto kalsın.",
        "hp_pm": "İsteğe bağlı: PresentMon, veri kaynağı HWiNFO değilken panelde FPS göstermeyi sağlar. "
                 "FPS gerekmiyorsa boş bırakın.",
        "hp_hwpath": "HWiNFO64.EXE'nin kurulu olduğu yer. Otomatik genelde kendiliğinden bulur.",
        "hp_manage": "Panel açılırken HWiNFO'yu açar, panel kapanınca onu da kapatır. "
                     "Tek seferlik kurulum gerekir (install.ps1).",
        "hp_sync": "HWiNFO'nun kendi okuma hızını kayıt aralığınıza eşitler; ikisi aynı tempoda çalışır. "
                   "Tek seferlik kurulum gerekir (install.ps1).",
        "hp_layout": "Panelde hangi satırların görüneceğini seçin; adlarını, sıralarını ve renk sınırlarını "
                     "düzenleyin. Değişiklikler Uygula veya Kaydet ile geçerli olur. Sıfırla, otomatik düzeni geri getirir.",
        "hp_lay_show": "Bir satırı panelden gizlemek için işareti kaldırın. Paneldeki veriler CSV'sinden de çıkar.",
        "hp_lay_limits": "Tam yeşil görünen değer ve tam kırmızı görünen değer; arası renk geçişidir. "
                         "Otomatik sınırlar için ikisini de boş bırakın. Düşük değer kötüyse (FPS gibi) yer değiştirin.",
        "hp_lay_bar": "Bu satırda değerin yanında küçük bir çubuk gösterir.",
        "hp_th_radius": "Kart köşelerinin ne kadar yuvarlak olduğu.",
        "hp_th_spacing": "Satırlar arasındaki boşluk: düşük değer daha fazla satır sığdırır, yüksek değer paneli ferahlatır.",
        "hp_th_fonts": "Panelde kullanılan yazı tiplerini seçin. (otomatik) iyi bir tanesini metriQ'nun seçmesine bırakır.",
        "hp_th_grad": "Değerlerin iyiden (1) kötüye (5) geçerken uğradığı beş renk; örneğin sıcaklık "
                      "yükseldikçe yeşilden kırmızıya.",
        "hp_th_mode": "Yumuşak, rengi yavaş yavaş değiştirir. Kademeli, aşağıdaki eşiklerde değişen birkaç düz renk bandı kullanır.",
        "hp_th_auto": "Her satırın yeşil ve kırmızı sınırı arasında 4 eşit bant kullanır. Kendi eşiklerinizi "
                      "girmek için işareti kaldırın.",
        "hp_th_steps": "Rengin değiştiği yerler; her satırın yeşilden kırmızıya aralığının yüzdesi olarak. "
                       "2-4 artan sayı (50, 75, 90 gibi) 3-5 bant verir.",
        "hp_scan": "Kayıtlarda ani biten ya da boşluk içerenleri arar; bunlar donma, çökme veya elektrik kesintisine "
                   "işaret edebilir. Sonuçları Windows olay günlüğüyle karşılaştırır.",
        "hp_hdr_now": "Şu anki değer.",
        "hp_hdr_min": "Panel açıldığından, en son sıfırla'ya (R) bastığınızdan ya da kayıt başladığından beri "
                      "görülen en düşük değer.",
        "hp_hdr_max": "Panel açıldığından, en son sıfırla'ya (R) bastığınızdan ya da kayıt başladığından beri "
                      "görülen en yüksek değer.",
        "hp_hdr_avg": "Panel açıldığından, en son sıfırla'ya (R) bastığınızdan ya da kayıt başladığından beri "
                      "ortalama.",
        "hp_logbtn": "CSV dosyasına kaydı başlatmak ya da durdurmak için tıklayın (veya L'ye basın). Yeşil, "
                     "okumaların kaydedildiği anlamına gelir.",
        "hp_modebtn": "Neyin kaydedileceğini seçer: Paneldeki veriler, yalnızca panelde gördüğünüz satırlar; Tüm veriler, "
                      "her sensör. Değiştirmek için tıklayın.",
        "hp_resetbtn": "MİN, MAKS ve ORT değerlerini şu andan başlatır (veya R'ye basın). Kayıt etkilenmez.",
        "hp_viewbtn": "Kayıt görüntüleyiciyi açar (veya V'ye basın).",
        "hp_gearbtn": "Ayarları açar (veya S'ye basın).",
        "hp_interval_lbl": "Okumalar ve CSV satırları arasındaki saniye. Ayarlar > Kayıt'tan da değiştirebilirsiniz.",
        "hp_bg": "Normalde paneli kapatmak (X) metriQ'yu sonlandırır. Buradan bir durum işaretlerseniz, o durum "
                 "sürerken paneli kapatmak yalnızca pencereyi gizler: kayıt ve mini mod devam eder, saatin yanında "
                 "bir metriQ simgesi belirir. Paneli geri getirmek için simgeye tıklayın; Çıkış için sağ tıklayın. "
                 "metriQ'yu yeniden başlatmak da gizli paneli açar.",
        "hp_mini": "Oyunun üstünde duran, birkaç değer gösteren küçük, yarı saydam bir şerit. Yalnızca pencereli "
                   "ya da kenarlıksız (tam ekran pencere) çalışan oyunların üstünde görünür: özel tam ekran "
                   "(exclusive fullscreen) oyunlar şeridi gizler. Şerit tıklanamaz; fare ve tuşlar hep oyuna "
                   "gider. metriQ oyuna hiçbir şekilde müdahale etmez.",
        "hp_mini_hotkey": "Şeridi her yerden, oyunun içinden bile gösterir ya da gizler (şeride tıklanamaz). "
                          "Panelde M tuşuna da basabilirsiniz. Tuş birleşimini başka bir program kullanıyorsa "
                          "metriQ haber verir; başka bir tane seçin.",
        "hp_mini_bg": "Zemin, değerleri tema renginde bir kartın üstüne koyar; en rahat okunan budur. Saydam "
                      "yalnızca yazıyı gösterir; parlak sahnelerde okunması zor olabilir.",
        "hp_mini_metrics": "Panel düzeninizdeki satırlar (en çok 8). Renkler, paneldeki aynı yeşilden kırmızıya "
                           "sınırları izler. Dokunmazsanız, varsa FPS, CPU / GPU sıcaklığı, kullanımı ve gücü gelir.",
    },
})


def text(key):
    return t("hp_" + key)


def _scale(w):
    try:
        return max(1.0, w.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def info_icon(parent, key, bg=None, size=SIZE):
    """Small "i" in a ring (dim, accent on hover) that explains hp_<key> in a balloon when hovered."""
    P = themes.palette()
    bg = bg or parent.cget("bg")
    dia = int(round(size * _scale(parent)))
    lbl = tk.Label(parent, bg=bg, bd=0, highlightthickness=0, cursor="question_arrow", takefocus=0)
    ok = False
    if gfx.AVAILABLE:
        try:
            dim, hot = gfx.info(dia, P["dim"]), gfx.info(dia, themes.accent("gauge"))
            lbl.config(image=dim)
            lbl.image, lbl._imgs = dim, (dim, hot)           # keep both alive
            lbl.bind("<Enter>", lambda e: lbl.config(image=hot), add="+")
            lbl.bind("<Leave>", lambda e: lbl.config(image=dim), add="+")
            ok = True
        except Exception:
            ok = False
    if not ok:
        lbl.config(text="ⓘ", fg=P["dim"], font=("Segoe UI Symbol", max(9, int(dia * 0.75))))
        lbl.bind("<Enter>", lambda e: lbl.config(fg=themes.accent("gauge")), add="+")
        lbl.bind("<Leave>", lambda e: lbl.config(fg=P["dim"]), add="+")
    lbl.tip = Tooltip(lbl, lambda: text(key))
    return lbl
