"""Settings page "Alerts" (alerts.py): on / off, Windows notification, sound, a test button and the rule list.
Hook: settings_ui calls add_tab(settings) for the page prepared in PAGES; read() gives the "alerts" config dict."""
import copy
import tkinter as tk

import alerts
import config
import i18n
import minimode
import uikit
from i18n import t

i18n.register({
    "en": {
        "al_tab": "Alerts", "al_card_general": "Alerts", "al_card_rules": "Rules",
        "al_enable": "Watch the rules", "al_enable_hint": "metriQ tells you when a value stays past its limit.",
        "al_notify": "Windows notification",
        "al_notify_hint": "An icon appears next to the clock for it. During a full-screen game Windows may hold "
                          "notifications back (Do not disturb); the sound still plays.",
        "al_sound": "Sound", "al_test": "Try it", "al_test_btn": "Send a test",
        "al_test_hint": "Uses the switches above, even before you save.",
        "al_rules_hint": "A rule notifies once. It can notify again after the value went back, at most every "
                         "{n} minutes. Rules watch the rows of your panel layout.",
        "al_add": "Add rule", "al_suggested": "Suggested rules", "al_no_rows": "The panel has no rows to watch yet.",
        "al_missing": "{name} (not in the panel)", "al_above": "> above", "al_below": "< below",
        "al_now": "at once", "al_for_s": "for {n} s", "al_max": "At most {n} rules.",
        "al_title": "metriQ alert", "al_msg": "{name}: {value} (limit {limit})",
        "al_test_text": "This is how an alert looks.",
        "hp_alerts": "Each rule watches one row of the panel, for example CPU temperature above 90 °C for 10 s. "
                     "When it happens you get a Windows notification and / or a sound, and a red notice stays "
                     "above the status bar while the value is past the limit. metriQ must be running (the panel "
                     "open, or hidden in the tray).",
    },
    "tr": {
        "al_tab": "Uyarılar", "al_card_general": "Uyarılar", "al_card_rules": "Kurallar",
        "al_enable": "Kuralları izle", "al_enable_hint": "Bir değer sınırını aşıp orada kalırsa metriQ haber verir.",
        "al_notify": "Windows bildirimi",
        "al_notify_hint": "Bunun için saatin yanında bir simge belirir. Tam ekran oyunda Windows bildirimleri "
                          "bekletebilir (Rahatsız etmeyin); ses yine de çalar.",
        "al_sound": "Ses", "al_test": "Dene", "al_test_btn": "Deneme gönder",
        "al_test_hint": "Kaydetmeden önce de yukarıdaki seçeneklerle çalışır.",
        "al_rules_hint": "Bir kural bir kez haber verir. Değer geri döndükten sonra yeniden haber verebilir, en sık "
                         "{n} dakikada bir. Kurallar panel düzenindeki satırları izler.",
        "al_add": "Kural ekle", "al_suggested": "Önerilen kurallar", "al_no_rows": "Panelde izlenecek satır yok.",
        "al_missing": "{name} (panelde yok)", "al_above": "> üstünde", "al_below": "< altında",
        "al_now": "hemen", "al_for_s": "{n} sn boyunca", "al_max": "En çok {n} kural.",
        "al_title": "metriQ uyarısı", "al_msg": "{name}: {value} (sınır {limit})",
        "al_test_text": "Uyarılar böyle görünür.",
        "hp_alerts": "Her kural paneldeki bir satırı izler; örneğin 10 sn boyunca CPU sıcaklığı 90 °C üstünde. "
                     "Olunca Windows bildirimi ve / veya ses gelir; değer sınırın ötesinde kaldıkça durum "
                     "çubuğunun üstünde kırmızı bir not durur. metriQ'nun çalışıyor olması gerekir (panel açık ya "
                     "da saatin yanında gizli).",
    },
})

PAGE = 9                                  # index in settings_ui.PAGES


def _float(s):
    return float(str(s).strip().replace(",", "."))


class AlertsTab:
    def __init__(self, sw, p):
        self.sw, self.C = sw, sw.C
        conf = config.norm_alerts(sw.api.cfg.get("alerts"))
        self.conf = conf

        card = sw.card(p, t("al_card_general"), "temp")
        self.v_enabled = tk.BooleanVar(value=conf["enabled"])
        sw.row(card, t("al_enable"), sw.switch(card.body, self.v_enabled), t("al_enable_hint"), help="alerts")
        self.v_notify = tk.BooleanVar(value=conf["notify"])
        sw.row(card, t("al_notify"), sw.switch(card.body, self.v_notify), t("al_notify_hint"))
        self.v_sound = tk.BooleanVar(value=conf["sound"])
        sw.row(card, t("al_sound"), sw.switch(card.body, self.v_sound))
        sw.row(card, t("al_test"), sw.button(card.body, t("al_test_btn"), self.test), t("al_test_hint"))

        card = sw.card(p, t("al_card_rules"), "gauge")
        self.card = card
        bg = card.fillc
        h = sw.label(card.body, t("al_rules_hint", n=alerts.REARM_S // 60), dim=True, size=9, bg=bg)
        sw.full(card, h, pady=(0, 6), divider=False)
        uikit.autowrap(h)
        self.rows = [r for r in sw.api.mini_rows() if alerts.usable(r)]
        self.keys = [minimode.metric_key(r) for r in self.rows]
        self.auto0 = alerts.suggested_rules(self.rows)
        start = self.auto0 if conf["rules"] == "auto" else conf["rules"]
        self.start = [self._clean(r) for r in start]
        self.box = tk.Frame(card.body, bg=bg)
        sw.full(card, self.box, pady=(2, 4), divider=False)
        self.box.grid_columnconfigure(0, weight=1)
        btns = tk.Frame(card.body, bg=bg)
        self.b_add = sw.button(btns, "+ " + t("al_add"), self.add)
        self.b_add.pack(side="left")
        sw.button(btns, t("al_suggested"), self.suggested).pack(side="left", padx=(8, 0))
        sw.full(card, btns, pady=(6, 4), divider=False, sticky="w")
        self.lines = []
        if not self.rows:
            sw.full(card, sw.label(card.body, t("al_no_rows"), dim=True, size=9, bg=bg), divider=False)
        self._build(self.start)

    # ------------------------------------------------------------ rule lines
    @staticmethod
    def _clean(r):
        return {"metric": r["metric"], "name": r.get("name", ""), "op": r.get("op", ">"),
                "value": float(r.get("value", 0)), "secs": int(r.get("secs", 10))}

    def _build(self, rules):
        for ln in self.lines:
            ln["dead"] = True
        for w in self.box.winfo_children():
            w.destroy()
        self.lines = []
        for r in rules:
            self._line(r)
        self._buttons()

    def _buttons(self):
        try:
            state = len(self.lines) < config.ALERT_MAX_RULES and bool(self.rows)
            self.b_add.configure(state="normal" if state else "disabled")
        except tk.TclError:
            pass

    def _line(self, rule):
        sw, C = self.sw, self.C
        bg = self.card.fillc
        fr = tk.Frame(self.box, bg=bg)
        fr.grid(row=len(self.lines), column=0, sticky="ew", pady=4)
        names = [r.name for r in self.rows]
        keys = list(self.keys)
        if rule["metric"] in keys:
            idx = keys.index(rule["metric"])
        else:                                   # the row is not in the panel layout any more: keep it, say so
            keys.append(rule["metric"])
            names.append(t("al_missing", name=rule.get("name") or "?"))
            idx = len(keys) - 1
        cb_m = sw.combo(fr, names, idx, 22)
        cb_m.pack(side="left")
        cb_op = sw.combo(fr, [t("al_above"), t("al_below")], 0 if rule["op"] == ">" else 1, 8)
        cb_op.pack(side="left", padx=(8, 0))
        v = tk.StringVar(value=alerts.fmt_limit(rule["value"]))
        e = sw.entry(fr, v, 6, justify="right")
        e.pack(side="left", padx=(8, 0))
        unit = tk.Label(fr, text="", bg=bg, fg=C["dim"], font=(sw.F_TXT, 10), width=4, anchor="w")
        unit.pack(side="left", padx=(4, 0))
        secs = list(alerts.SECS)
        if rule["secs"] not in secs:
            secs.append(rule["secs"])
        cb_s = sw.combo(fr, [t("al_now") if s == 0 else t("al_for_s", n=s) for s in secs], secs.index(rule["secs"]), 13)
        cb_s.pack(side="left", padx=(8, 0))
        ln = {"frame": fr, "keys": keys, "cb_m": cb_m, "cb_op": cb_op, "v": v, "e": e, "secs": secs, "cb_s": cb_s,
              "unit": unit, "name": rule.get("name", ""), "dead": False}
        x = sw.small_btn(fr, "✕", lambda ln=ln: self.remove(ln))
        x.pack(side="left", padx=(8, 0))

        def ok():
            if ln["dead"]:
                return True
            val = _float(v.get())
            return val == val and abs(val) < 1e9
        rc = sw.add_check(sw.checks, PAGE, [e], ok)
        v.trace_add("write", lambda *a: rc())
        cb_m.bind("<<ComboboxSelected>>", lambda ev, ln=ln: self._unit(ln), add="+")
        self._unit(ln)
        self.lines.append(ln)

    def _unit(self, ln):
        i = ln["cb_m"].current()
        row = self.rows[i] if 0 <= i < len(self.rows) else None
        try:
            ln["unit"].config(text=(row.unit_eff or row.unit) if row is not None else "")
        except tk.TclError:
            pass

    def add(self):
        if not self.rows or len(self.lines) >= config.ALERT_MAX_RULES:
            self.sw.say(t("al_max", n=config.ALERT_MAX_RULES), error=True)
            return
        used = {self._read_line(ln)["metric"] for ln in self.lines}
        row = next((r for r in self.rows if minimode.metric_key(r) not in used), self.rows[0])
        self._line({"metric": minimode.metric_key(row), "name": row.name, "op": ">", "value": 90.0, "secs": 10})
        self._buttons()
        self.sw._size_scrolls()

    def remove(self, ln):
        rules = [self._read_line(x) for x in self.lines if x is not ln]
        self._build(rules)
        self.sw._size_scrolls()

    def suggested(self):
        self._build([self._clean(r) for r in self.auto0])
        self.sw._size_scrolls()

    def _read_line(self, ln):
        i = max(0, ln["cb_m"].current())
        key = ln["keys"][i]
        name = self.rows[i].name if i < len(self.rows) else ln["name"]
        try:
            val = round(_float(ln["v"].get()), 2)
        except ValueError:
            val = ln["v"].get()
        return {"metric": key, "name": name, "op": ">" if ln["cb_op"].current() == 0 else "<", "value": val,
                "secs": ln["secs"][max(0, ln["cb_s"].current())]}

    # ------------------------------------------------------------
    def test(self):
        fn = getattr(self.sw.api, "alert_test", None)
        if fn:
            fn(bool(self.v_notify.get()), bool(self.v_sound.get()))

    def read(self):
        """The "alerts" config dict as the page shows it (an untouched suggested list stays "auto")."""
        rules = [self._read_line(ln) for ln in self.lines]
        same_auto = self.conf["rules"] == "auto" and self._sig(rules) == self._sig(self.auto0)
        d = {"enabled": bool(self.v_enabled.get()), "notify": bool(self.v_notify.get()),
             "sound": bool(self.v_sound.get()), "rules": "auto" if same_auto else rules}
        return config.norm_alerts(d) if all(isinstance(r["value"], float) for r in rules) else d

    @staticmethod
    def _sig(rules):
        return [(r["metric"], r["op"], float(r["value"]) if isinstance(r["value"], (int, float)) else r["value"],
                 int(r["secs"])) for r in rules]


def initial(cfg):
    return copy.deepcopy(config.norm_alerts(cfg.get("alerts")))


def add_tab(sw):
    tab = sw.add_page(PAGE)
    sw.alert_tab = AlertsTab(sw, tab)
    return tab
