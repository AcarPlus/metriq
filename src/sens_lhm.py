"""LibreHardwareMonitor (open source, signed driver): CPU temperatures, fans, motherboard / VRM sensors, voltages, power.

Preferred: LHM's own web server (Options > Remote Web Server > Run, default port 8085), read from
http://127.0.0.1:8085/data.json. Fallback: the WMI namespace root\\LibreHardwareMonitor (only exists while LHM
runs and only in LHM versions that still provide it), read by one long-running hidden PowerShell process.

Readings are returned in the shape of hwsm.read_all(). Sensor names follow HWiNFO's families so the
auto-detection works the same: "CPU [#0]: <name>", "GPU [#0]: <name>", "System: <name>", "Network: <name>",
"Drive: <name>" (throughput) / "S.M.A.R.T.: <name>" (temperature, life), motherboard chips by their own name.
Labels are LHM's own ("Core (Tctl/Tdie)", "Package", "CPU Total" ...); layout.autodetect knows them.
"""
import json
import re
import subprocess
import threading
import time
import urllib.request

from sens_builtin import reading

DEFAULT_URL = "http://127.0.0.1:8085/data.json"
NOWIN = 0x08000000

# LHM sensor type (WMI SensorType / data.json "Type" / group node text) -> (HWiNFO type, unit for WMI values)
_TYPES = {
    "temperature": (1, "°C"), "voltage": (2, "V"), "fan": (3, "RPM"), "current": (4, "A"), "power": (5, "W"),
    "clock": (6, "MHz"), "load": (7, "%"), "control": (7, "%"), "level": (7, "%"), "data": (8, "GB"),
    "smalldata": (8, "MB"), "throughput": (8, "B/s"), "factor": (8, ""), "frequency": (8, "Hz"),
    "energy": (8, "mWh"), "noise": (8, "dBA"), "flow": (8, "L/h"), "humidity": (8, "%"),
    "conductivity": (8, "µS/cm"), "timespan": (8, "s"),
}
_GROUPS = {g: g for g in _TYPES}
_GROUPS.update({"temperatures": "temperature", "voltages": "voltage", "fans": "fan", "currents": "current",
                "powers": "power", "clocks": "clock", "controls": "control", "levels": "level",
                "factors": "factor", "frequencies": "frequency", "throughputs": "throughput"})
_SCALE = {"b/s": 1.0, "kb/s": 1024.0, "mb/s": 1048576.0, "gb/s": 1073741824.0,
          "kb": 1 / 1024.0, "mb": 1.0, "gb": 1024.0, "tb": 1048576.0}


def _kind(hw_id, image, name):
    """Hardware family from the LHM identifier ('/amdcpu/0'), the icon name or the hardware name."""
    s = (hw_id or "").lower()
    i = (image or "").lower()
    if re.match(r"^/(amdcpu|intelcpu|cpu)\b", s) or "cpu.png" in i:
        return "cpu"
    if re.match(r"^/(gpu|atigpu|nvidiagpu)", s) or re.search(r"(nvidia|ati|amd|intel)\.png", i):
        return "gpu"
    if re.match(r"^/(ram|memory)", s) or "ram.png" in i:
        return "dimm" if re.search(r"dimm|spd", s + " " + name.lower()) else "ram"
    if re.match(r"^/(hdd|nvme|ssd|storage)", s) or "hdd.png" in i:
        return "storage"
    if re.match(r"^/nic", s) or "nic.png" in i:
        return "nic"
    if re.match(r"^/battery", s) or "battery.png" in i:
        return "battery"
    return "board"            # mainboard, Super I/O, embedded controller, cooler, PSU ...


class _Namer:
    """Hardware -> HWiNFO-style sensor name (numbering CPUs and GPUs in the order LHM lists them)."""

    def __init__(self):
        self.n = {"cpu": 0, "gpu": 0}

    def __call__(self, kind, name, parent=None):
        if kind in self.n:
            k = self.n[kind]
            self.n[kind] += 1
            return f"{kind.upper()} [#{k}]: {name}"
        if kind == "ram":
            return f"System: {name}"
        if kind == "dimm":
            return f"DIMM: {name}"
        if kind == "nic":
            return f"Network: {name}"
        if kind == "battery":
            return f"Battery: {name}"
        if kind == "storage":
            return name                          # split per reading below
        return f"{parent} ({name})" if parent else name


def _num(s):
    """'45,3 °C' / '1.234 V' / 45.3 -> (value, unit). Culture-proof: LHM formats without thousands separators."""
    if isinstance(s, (int, float)):
        return float(s), None
    m = re.match(r"^\s*(-?[\d.,]+)\s*(.*?)\s*$", str(s or ""))
    if not m:
        return None, None
    n = m.group(1)
    if "," in n and "." in n:
        n = n.replace(",", "") if n.rfind(".") > n.rfind(",") else n.replace(".", "").replace(",", ".")
    else:
        n = n.replace(",", ".")
    try:
        return float(n), m.group(2)
    except ValueError:
        return None, None


def _norm(rtype, unit, kind, vals):
    """Fixed units per reading (LHM scales throughput / data units with the value): °F -> °C, throughput ->
    KB/s (network) or MB/s (drives), data -> MB."""
    u = (unit or "").strip()
    ul = u.lower()
    if rtype == 1 and ul in ("°f", "f"):
        return "°C", [None if v is None else (v - 32) / 1.8 for v in vals]
    if ul.endswith("/s") and ul in _SCALE:
        to = "MB/s" if kind == "storage" else "KB/s"
        k = _SCALE[ul] / _SCALE[to.lower()]
        return to, [None if v is None else v * k for v in vals]
    if ul in ("kb", "mb", "gb", "tb"):
        return "MB", [None if v is None else v * _SCALE[ul] for v in vals]
    return u, vals


def _storage_sensor(name, rtype, label):
    if rtype == 1 or re.search(r"life|wear|spare|used space|health", label, re.I):
        return f"S.M.A.R.T.: {name}"
    return f"Drive: {name}"


def _make(rtype, kind, sensor, label, unit, v, vmin, vmax):
    unit, (v, vmin, vmax) = _norm(rtype, unit, kind, [v, vmin, vmax])
    if v is None:
        return None
    if kind == "storage":
        sensor = _storage_sensor(sensor, rtype, label)
    r = reading(rtype, sensor, label, unit, v)
    r["min"] = vmin if vmin is not None else v
    r["max"] = vmax if vmax is not None else v
    return r


# ================================================================== web server (data.json)
def parse_data_json(root):
    """LHM data.json tree -> readings."""
    out = []
    namer = _Namer()

    def is_group(node):
        return str(node.get("Text", "")).strip().lower() in _GROUPS

    def walk(node, hw, depth):
        kids = node.get("Children") or []
        if not kids:
            return
        for ch in kids:
            sub = ch.get("Children") or []
            if sub and is_group(ch):
                gtype = _GROUPS[str(ch["Text"]).strip().lower()]
                for leaf in sub:
                    if leaf.get("Children"):
                        continue
                    lt = str(leaf.get("Type") or gtype).lower()
                    rtype = _TYPES.get(lt, _TYPES[gtype])[0]
                    raw = leaf.get("RawValue")
                    v, unit = _num(leaf.get("Value"))
                    if isinstance(raw, (int, float)):
                        v = float(raw)
                    vmin, _ = _num(leaf.get("Min"))
                    vmax, _ = _num(leaf.get("Max"))
                    if hw is None:
                        continue
                    r = _make(rtype, hw["kind"], hw["sensor"], str(leaf.get("Text", "")).strip(), unit, v, vmin, vmax)
                    if r:
                        out.append(r)
            elif sub and depth >= 1:
                # a hardware node (depth 1 = the computer node itself is skipped)
                hid = ""
                for leaf in _leaves(ch):
                    sid = leaf.get("SensorId") or ""
                    if sid:
                        hid = sid
                        break
                name = str(ch.get("Text", "")).strip() or "?"
                kind = _kind(hid, ch.get("ImageURL"), name)
                parent = hw["name"] if hw and hw["kind"] == "board" and kind == "board" else None
                walk(ch, {"kind": kind, "name": name, "sensor": namer(kind, name, parent)}, depth + 1)
            elif sub:
                walk(ch, hw, depth + 1)

    walk(root, None, 0)
    return out


def _leaves(node):
    for ch in node.get("Children") or []:
        if ch.get("Children"):
            yield from _leaves(ch)
        else:
            yield ch


class _Web:
    def __init__(self, url):
        self.url = url
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a system proxy

    def fetch(self, timeout=0.6):
        with self.op.open(self.url, timeout=timeout) as f:
            return json.loads(f.read().decode("utf-8-sig", "replace"))


def web_reachable(url=DEFAULT_URL, timeout=0.35):
    try:
        import socket
        from urllib.parse import urlparse
        u = urlparse(url)
        # a closed port is the common case: find out with a bare connect (no HTTP / TLS set-up per probe)
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80)),
                                      timeout):
            pass
    except Exception:
        return False
    try:
        root = _Web(url).fetch(timeout)
        return isinstance(root, dict) and "Children" in root
    except Exception:
        return False


# ================================================================== WMI (via one hidden PowerShell process)
_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
while ($null -ne ($l = [Console]::In.ReadLine())) {
  try {
    $h = @(Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Hardware |
           Select-Object Identifier, Name, HardwareType, Parent)
    $s = @(Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor |
           Select-Object Identifier, Name, SensorType, Parent, Value, Min, Max, Index)
    [Console]::Out.WriteLine((@{ok = $true; hw = $h; s = $s} | ConvertTo-Json -Compress -Depth 3))
  } catch {
    [Console]::Out.WriteLine((@{ok = $false; err = "$($_.Exception.Message)"} | ConvertTo-Json -Compress))
  }
}
"""

_HWTYPE = {"cpu": "cpu", "gpunvidia": "gpu", "gpuamd": "gpu", "gpuati": "gpu", "gpuintel": "gpu", "memory": "ram",
           "storage": "storage", "hdd": "storage", "network": "nic", "battery": "battery"}


def parse_wmi(data):
    hws = {h.get("Identifier"): h for h in data.get("hw") or []}
    namer = _Namer()
    names = {}
    for hid, h in hws.items():           # keep LHM's order; nested chips get the board name in front
        kind = _HWTYPE.get(str(h.get("HardwareType", "")).lower(), "board")
        if kind == "ram" and re.search(r"dimm|spd", f"{hid} {h.get('Name', '')}", re.I):
            kind = "dimm"
        par = hws.get(h.get("Parent"))
        parent = par.get("Name") if par and kind == "board" else None
        names[hid] = (kind, namer(kind, str(h.get("Name") or "?"), parent))
    out = []
    order = {k: i for i, k in enumerate(_TYPES)}
    sens = sorted(data.get("s") or [], key=lambda s: (list(hws).index(s.get("Parent")) if s.get("Parent") in hws
                                                      else 999, order.get(str(s.get("SensorType")).lower(), 99),
                                                      s.get("Index") or 0))
    for s in sens:
        kind, sensor = names.get(s.get("Parent"), ("board", str(s.get("Parent") or "?")))
        rtype, unit = _TYPES.get(str(s.get("SensorType", "")).lower(), (8, ""))
        v = s.get("Value")
        if not isinstance(v, (int, float)):
            continue
        r = _make(rtype, kind, sensor, str(s.get("Name") or "?"), unit, float(v),
                  s.get("Min") if isinstance(s.get("Min"), (int, float)) else None,
                  s.get("Max") if isinstance(s.get("Max"), (int, float)) else None)
        if r:
            out.append(r)
    return out


class _Wmi:
    """Background thread: asks the PowerShell helper for a snapshot about once a second."""

    def __init__(self):
        self.data, self.stamp, self.err = None, 0.0, ""
        self.stop = False
        self.p = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-Command", _PS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, creationflags=NOWIN, text=True, encoding="utf-8",
                                  errors="replace", bufsize=1)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop and self.p.poll() is None:
            try:
                self.p.stdin.write("x\n")
                self.p.stdin.flush()
                line = self.p.stdout.readline()
                if not line:
                    break
                d = json.loads(line)
                if d.get("ok"):
                    self.data, self.stamp, self.err = d, time.time(), ""
                else:
                    self.err = d.get("err") or "WMI error"
            except Exception as ex:
                self.err = str(ex)
                break
            time.sleep(1.0)

    def snapshot(self):
        if self.data and time.time() - self.stamp < 6:
            return self.data
        raise RuntimeError(self.err or "LibreHardwareMonitor WMI not available")

    def close(self):
        self.stop = True
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass


# ================================================================== the source
class LhmSource:
    """Polls LHM in a background thread (a refused localhost connection takes ~2 s on Windows: never block the
    panel). read_all() returns the last snapshot, or RuntimeError when there is none newer than 5 s."""
    name = "lhm"
    POLL_S = 1.0

    def __init__(self, url=None, use_wmi=True):
        self.web = _Web(url or DEFAULT_URL)
        self.use_wmi = use_wmi
        self.wmi = None
        self.via = ""                  # "web" | "wmi" | ""
        self.err = ""
        self.data, self.stamp = None, 0.0
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        next_web = 0.0
        while not self.stop.is_set():
            t0 = time.time()
            ok = False
            if self.via != "wmi" or t0 >= next_web:
                try:
                    rd = parse_data_json(self.web.fetch(timeout=3.0))
                    self.data, self.stamp, self.via, self.err, ok = rd, time.time(), "web", "", True
                    if self.wmi:
                        self.wmi.close()
                        self.wmi = None
                except Exception as ex:
                    self.err = str(ex)
                    next_web = time.time() + 10       # while WMI works, retry the web server every 10 s only
            if not ok and self.use_wmi and not self.stop.is_set():
                try:
                    if self.wmi is None or self.wmi.p.poll() is not None:
                        self.wmi = _Wmi()
                    rd = parse_wmi(self.wmi.snapshot())
                    self.data, self.stamp, self.via = rd, time.time(), "wmi"
                except Exception as ex:
                    self.err = f"{self.err}; WMI: {ex}"
            self.stop.wait(max(0.1, self.POLL_S - (time.time() - t0)))

    def read_all(self):
        if self.data is not None and time.time() - self.stamp < 5.0:
            return list(self.data)
        raise RuntimeError(f"LibreHardwareMonitor not reachable ({self.err or 'no data yet'})")

    def note_texts(self):
        return []

    def close(self):
        self.stop.set()
        if self.wmi:
            self.wmi.close()
            self.wmi = None
