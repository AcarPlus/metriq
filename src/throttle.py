"""Throttling indicator: HWiNFO's yes/no sensors -> one reading per CPU / GPU that says why it is held back.

HWiNFO publishes throttling as readings with the unit "Yes/No" and the value 0 / 1 ("Thermal Throttling (HTC)",
"Thermal Throttling (PROCHOT CPU)", "Power Limit Exceeded", NVIDIA "Performance Limit - Power" ...). derive()
combines the matching ones into "CPU Throttling" / "GPU Throttling" readings (sensor "metriQ: Throttling") whose
value is a bit mask of the reasons; fmt() turns it into text ("None" / "Thermal+Power"). Without HWiNFO (or without
such sensors) no reading is made and the layout has no throttling row.
"""
import re

import i18n

SENSOR = "metriQ: Throttling"
CPU_LABEL, GPU_LABEL = "CPU Throttling", "GPU Throttling"
THERMAL, POWER, PROCHOT = 1, 2, 4
NAMES = ((THERMAL, "thr_thermal"), (POWER, "thr_power"), (PROCHOT, "thr_prochot"))

i18n.register({
    "en": {
        "cpu_throttle": "CPU Throttling", "gpu_throttle": "GPU Throttling", "thr_none": "None",
        "thr_thermal": "Thermal", "thr_power": "Power", "thr_prochot": "PROCHOT",
        "thr_toast": "{who} throttling: {what}", "thr_cpu": "CPU", "thr_gpu": "GPU",
        "hp_throttle": "Shows why the processor / graphics card is being held back: None, or Thermal (too hot), "
                       "Power (power limit reached) or PROCHOT (an outside signal such as the laptop's embedded "
                       "controller slows the CPU). Needs HWiNFO as the data source; with LibreHardwareMonitor or "
                       "the built-in source there is no throttling information and this row does not appear.",
    },
    "tr": {
        "cpu_throttle": "CPU Kısılma", "gpu_throttle": "GPU Kısılma", "thr_none": "Yok",
        "thr_thermal": "Isıl", "thr_power": "Güç", "thr_prochot": "PROCHOT",
        "thr_toast": "{who} kısılıyor: {what}", "thr_cpu": "CPU", "thr_gpu": "GPU",
        "hp_throttle": "İşlemcinin / ekran kartının neden yavaşlatıldığını gösterir: Yok, ya da Isıl (çok sıcak), "
                       "Güç (güç sınırına ulaşıldı) veya PROCHOT (dizüstündeki gömülü denetleyici gibi dış bir "
                       "sinyal CPU'yu yavaşlatıyor). Veri kaynağı olarak HWiNFO gerekir; LibreHardwareMonitor ya da "
                       "yerleşik kaynakta kısılma bilgisi yoktur ve bu satır görünmez.",
    },
})

_YESNO = re.compile(r"^\s*yes\s*/\s*no\s*$", re.I)
# order matters: "Thermal Throttling (PROCHOT CPU)" is a PROCHOT
_KINDS = (
    (PROCHOT, re.compile(r"PROCHOT", re.I)),
    (THERMAL, re.compile(r"thermal (throttl|limit|event)|critical temp|\bHTC\b|performance limit\W+thermal", re.I)),
    (POWER, re.compile(r"power limit|performance limit\W+power|\bPL[1-4]\b|RAPL|PBM|electrical design|"
                       r"(PPT|TDC|EDC|current) limit", re.I)),
)
_CPU = re.compile(r"^CPU \[#0\]", re.I)
_GPU = re.compile(r"^(dGPU|GPU|iGPU) \[#(\d+)\]", re.I)
_GPU_ORDER = {"dgpu": 0, "gpu": 1, "igpu": 2}
_cache = {}


def _kind(sensor, label):
    k = (sensor, label)
    v = _cache.get(k)
    if v is None:
        v = 0
        for bit, rx in _KINDS:
            if rx.search(label):
                v = bit
                break
        if len(_cache) > 4000:
            _cache.clear()
        _cache[k] = v
    return v


def _flag(r):
    """True when the reading is a yes/no sensor (unit "Yes/No", or no unit and a 0 / 1 value)."""
    unit = r.get("unit") or ""
    return bool(_YESNO.match(unit)) or (unit == "" and r["value"] in (0.0, 1.0))


def _gpu_family(rd):
    """The GPU sensor family the panel's layout uses: dGPU first, then GPU, then iGPU (lowest number first)."""
    best = None
    for r in rd:
        m = _GPU.match(r["sensor"])
        if m:
            key = (_GPU_ORDER[m.group(1).lower()], int(m.group(2)))
            if best is None or key < best[0]:
                best = (key, m.group(0))
    return best[1] if best else None


def _reading(label, mask):
    v = float(mask)
    return dict(type=8, sensor=SENSOR, sensor_user=SENSOR, id=0, label=label, label_user=label, unit="", value=v,
                min=v, max=v, avg=v)


def derive(rd):
    """Readings of a HWiNFO snapshot -> [CPU / GPU throttling readings] (empty when there are no yes/no sensors)."""
    cpu = gpu = None
    fam = _gpu_family(rd)
    for r in rd:
        kind = _kind(r["sensor"], r["label"])
        if not kind or not _flag(r):
            continue
        s = r["sensor"]
        if _CPU.match(s):
            cpu = (cpu or 0) | (kind if r["value"] >= 0.5 else 0)
        elif fam and s.startswith(fam) and _GPU.match(s):
            gpu = (gpu or 0) | (kind if r["value"] >= 0.5 else 0)
    out = []
    if cpu is not None:
        out.append(_reading(CPU_LABEL, cpu))
    if gpu is not None:
        out.append(_reading(GPU_LABEL, gpu))
    return out


def reasons(mask):
    """Bit mask -> [translated reason names]."""
    m = int(round(mask))
    return [i18n.t(k) for bit, k in NAMES if m & bit]


def fmt(v):
    r = reasons(v)
    return "+".join(r) if r else i18n.t("thr_none")


def active(rd):
    """[(who, text)] of the throttling readings in `rd` whose value is not zero."""
    out = []
    for r in rd:
        if r["sensor"] == SENSOR and r["value"] >= 0.5:
            who = i18n.t("thr_cpu") if r["label"] == CPU_LABEL else i18n.t("thr_gpu")
            out.append((who, fmt(r["value"])))
    return out
