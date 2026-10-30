"""Sensor data sources ("backends"). Every source returns readings shaped like hwsm.read_all().

    hwinfo    HWiNFO64 shared memory: the widest coverage (needs HWiNFO, see README)
    lhm       LibreHardwareMonitor (open source): temperatures, fans, voltages, power (sens_lhm.py)
    builtin   no install, no admin: usage, clocks, RAM, network, disk, NVIDIA GPU (sens_builtin.py)
    auto      HWiNFO if its shared memory is there (or the panel starts HWiNFO itself), else LHM if it is
              reachable, else built-in; switches up to HWiNFO / LHM as soon as they appear

FPS for lhm / builtin comes from PresentMon when "presentmon_path" is set (presentmon.py, experimental).
"""
import os
import threading
import time

import config
import hwctl
import hwsm
from i18n import t

BACKENDS = ("auto", "hwinfo", "lhm", "builtin")
NAMES = {"hwinfo": "HWiNFO", "lhm": "LibreHardwareMonitor", "builtin": "Built-in"}
LHM_EXE = "LibreHardwareMonitor.exe"
PROBE_S = 5.0            # auto: how often to look for a better source
FALLBACK_S = 15.0        # auto: HWiNFO / LHM gone this long -> next source (HWiNFO managed by the panel: 90 s)


def norm(v):
    return config.norm_backend(v)


def display_name(name):
    return t("src_builtin") if name == "builtin" else NAMES.get(name, name)


def hwinfo_available():
    try:
        return hwsm.available()
    except Exception:
        return False


def lhm_url(cfg):
    import sens_lhm
    u = str(cfg.get("lhm_url") or "").strip()
    return sens_lhm.DEFAULT_URL if not u or u.lower() == "auto" else u


_exe_cache = {}


def why_no_hwinfo(cfg):
    """i18n key that says why HWiNFO does not deliver data (for the built-in source), "" when it does."""
    if hwinfo_available():
        return ""
    now = time.time()
    if now - _exe_cache.get("t", 0) > 60:                  # a registry scan: not every probe
        _exe_cache.update(t=now, exe=hwctl.detect_hwinfo(cfg.get("hwinfo_path", "auto")))
    if not os.path.isfile(_exe_cache["exe"] or ""):
        return "why_hw_none"
    if hwctl.proc_running():
        return "why_hw_noshm"
    if not hwctl.tasks_installed():
        return "why_hw_notasks"
    return "why_hw_manage_off" if not cfg.get("manage_hwinfo", True) else ""


def lhm_available(cfg, timeout=0.35):
    import sens_lhm
    return sens_lhm.web_reachable(lhm_url(cfg), timeout) or hwctl.proc_running(LHM_EXE)


class _Hwinfo:
    name = "hwinfo"

    @staticmethod
    def read_all():
        return hwsm.read_all()

    @staticmethod
    def note_texts():
        return []

    def close(self):
        pass


def _create(name, cfg):
    if name == "hwinfo":
        return _Hwinfo()
    if name == "lhm":
        import sens_lhm
        return sens_lhm.LhmSource(lhm_url(cfg))
    import sens_builtin
    return sens_builtin.BuiltinSource()


class Manager:
    """The active source. hwinfo_managed() tells whether the panel starts HWiNFO itself (install.ps1 tasks):
    then auto chooses HWiNFO even before its shared memory exists."""

    def __init__(self, cfg, hwinfo_managed=lambda: False):
        self.cfg = cfg
        self.managed = hwinfo_managed
        self.src = None
        self.active = None
        self.gen = 0                 # +1 whenever the source changes (the panel then re-detects its layout)
        self.err = ""
        self.down_since = None
        self.probe_at = 0.0
        self.pm = None
        self.pm_path = None
        self.avail = {"hwinfo": False, "lhm": False}       # auto: filled by the probe thread
        self.why = ""                # i18n key: why HWiNFO is not the source while the built-in one is (probe thread)
        self.closed = threading.Event()
        self.select()
        threading.Thread(target=self._probe_loop, daemon=True).start()

    def _probe_loop(self):
        """auto: look for HWiNFO / LHM in the background (an LHM probe can take seconds when it is not running)."""
        while not self.closed.is_set():
            slow = False
            if self.setting() == "auto":
                try:
                    hw = hwinfo_available()
                    # HWiNFO is up and delivering: LibreHardwareMonitor is only a fallback, so do not look for it
                    # (a probe is a socket connect + a process listing)
                    healthy = hw and self.active == "hwinfo" and not self.down_since
                    self.avail = {"hwinfo": hw, "lhm": self.avail["lhm"] if healthy else lhm_available(self.cfg)}
                    slow = healthy
                except Exception:
                    pass
            try:
                self.why = why_no_hwinfo(self.cfg) if self.active == "builtin" else ""
            except Exception:
                self.why = ""
            self.closed.wait(PROBE_S * 3 if slow else PROBE_S)

    def setting(self):
        return norm(self.cfg.get("backend"))

    def resolve(self):
        s = self.setting()
        if s != "auto":
            return s
        if hwinfo_available() or self.managed():
            return "hwinfo"
        if lhm_available(self.cfg):
            return "lhm"
        return "builtin"

    def select(self, name=None):
        """Switch to `name` (default: what the setting resolves to). True when the source changed."""
        name = name or self.resolve()
        if self.src is not None and name == self.active:
            return False
        self.close_source()
        try:
            self.src = _create(name, self.cfg)
        except Exception as ex:          # never leave the panel without a source
            self.err = f"{display_name(name)}: {ex}"
            name = "builtin"
            self.src = _create(name, self.cfg)
        self.active = name
        self.gen += 1
        self.down_since = None
        self.probe_at = time.time() + PROBE_S
        self._presentmon()
        return True

    def reconfigure(self):
        """After a settings change (backend, lhm_url, presentmon_path)."""
        if self.active == "lhm" and self.setting() in ("lhm", "auto"):
            url = lhm_url(self.cfg)
            if getattr(self.src.web, "url", url) != url:
                self.close_source()
                self.active = None
        changed = self.select()
        self._presentmon()
        return changed

    def _presentmon(self):
        path = str(self.cfg.get("presentmon_path") or "").strip()
        path = os.path.expandvars(path) if path else ""
        want = path if path and self.active != "hwinfo" else ""
        if want == (self.pm_path or ""):
            return
        if self.pm:
            self.pm.close()
        self.pm, self.pm_path = None, want or None
        if want:
            import presentmon
            self.pm = presentmon.PresentMon(want)

    def read_all(self):
        """Readings of the active source; RuntimeError while it has no data (like hwsm.read_all)."""
        now = time.time()
        if self.setting() == "auto" and now >= self.probe_at:
            self.probe_at = now + PROBE_S
            self._auto_switch(now)
        try:
            rd = self.src.read_all()
        except Exception:
            if self.down_since is None:
                self.down_since = now
            raise
        self.down_since = None
        if self.pm:
            self.pm.read(rd.append)
        for i, r in enumerate(rd):
            r["id"] = i
        return rd

    def _auto_switch(self, now):
        av = self.avail
        if self.active != "hwinfo" and av["hwinfo"]:
            self.select("hwinfo")
        elif self.active == "builtin" and av["lhm"]:
            self.select("lhm")
        elif self.active in ("hwinfo", "lhm") and self.down_since:
            wait = 90.0 if self.active == "hwinfo" and self.managed() else FALLBACK_S
            if now - self.down_since > wait:
                self.select("lhm" if self.active == "hwinfo" and av["lhm"] else "builtin")

    def notes(self):
        """Localised lines: which data the active source cannot provide, and why."""
        out = []
        if self.why and self.active == "builtin":
            out.append(t(self.why))
        if self.err:
            out.append(self.err)
        try:
            out += self.src.note_texts()
        except Exception:
            pass
        if self.active != "hwinfo":
            if self.pm and self.pm.err:
                out.append(t("na_pm_err", err=self.pm.err))
            elif not self.pm:
                out.append(t("na_fps"))
        return out

    def via(self):
        return getattr(self.src, "via", "") if self.active == "lhm" and self.down_since is None else ""

    def close_source(self):
        if self.src is not None:
            try:
                self.src.close()
            except Exception:
                pass
        self.src = None

    def close(self):
        self.closed.set()
        self.close_source()
        if self.pm:
            self.pm.close()
            self.pm = None


def read_once(cfg, name=None):
    """For the command line tools: readings of one source (default: the configured one). RuntimeError if none."""
    c = dict(cfg)
    if name:
        c["backend"] = norm(name)
    m = Manager(c)
    try:
        if m.active == "builtin":
            m.read_all()            # rates (network, disk, clocks) need two samples
            time.sleep(1.0)
        elif m.active == "lhm":     # first snapshot comes from a background thread
            end = time.time() + 8
            while m.src.data is None and time.time() < end:
                time.sleep(0.1)
        return m.active, m.read_all()
    finally:
        m.close()


def cli_backend(argv):
    """'--backend NAME' from a tool's command line, or None."""
    if "--backend" in argv:
        i = argv.index("--backend")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None
