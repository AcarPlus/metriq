// FanService: metriQ fan control (Windows service "metriQFan", runs as LocalSystem).
//
// Drives motherboard (Super I/O) and GPU fan controls through LibreHardwareMonitorLib's generic IControl
// interface: whatever LHM can control is handled by the same code, everything else stays with BIOS / driver.
// The panel (normal user) talks to it over the named pipe \\.\pipe\metriQ.fan: one JSON line in, one out.
//
// Safety (hard-coded, the panel cannot switch it off):
//   - every value from the pipe is validated and clamped here;
//   - minimum duty per role: CPU 30 %, pump 60 %, unknown 30 %, GPU 30 % (case fans may stop);
//   - a CPU / GPU temperature at its limit drives every controlled fan to 100 % (until 8 C below);
//   - a curve whose temperature source cannot be read for 3 s goes to 100 %;
//   - stop / disable / service stop / shutdown / caught error: SetDefault (BIOS / driver control);
//   - LHM's SetDefault restores the state captured when it opened the chip. After a hard kill the chip is
//     still in manual mode, so a restarted service would "restore" the stuck value: fan_state.json marks the
//     headers taken over in this boot, and until the next reboot those are driven by a built-in safe curve
//     instead of being released.
//
// Language level: C# 5 (csc of .NET Framework 4.x): no interpolation, no ?., no expression-bodied members.
//
//   FanService.exe              started by the service control manager
//   FanService.exe --console    same engine in the foreground (administrator), Enter / Ctrl+C stops
//   FanService.exe --selftest   loads LHM and checks the JSON code, no hardware access

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using LibreHardwareMonitor.Hardware;

namespace MetriqFan
{
    internal static class Program
    {
        public const string ServiceName = "metriQFan";
        public const string PipeName = "metriQ.fan";
        public const string Version = "1";

        private static int Main(string[] args)
        {
            CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
            Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
            string mode = args.Length > 0 ? args[0] : "";
            if (mode == "--selftest") return SelfTest();
            if (mode == "--console") return RunConsole();
            if (Environment.UserInteractive)
            {
                Console.WriteLine("FanService.exe --console | --selftest   (normally started as the service " + ServiceName + ")");
                return 1;
            }
            ServiceBase.Run(new FanWinService());
            return 0;
        }

        private static int SelfTest()
        {
            Console.WriteLine("LHM : " + typeof(IHardware).Assembly.GetName().Version);
            Dictionary<string, object> cfg = Config.Validate(Json.Parse(
                "{\"enabled\":true,\"cpu_limit\":120,\"headers\":{\"/lpc/x/0/control/1\":{\"role\":\"pump\",\"mode\":\"fixed\",\"fixed\":10}," +
                "\"/lpc/x/0/control/2\":{\"mode\":\"curve\",\"curve\":{\"source\":\"max\",\"points\":[[70,90],[30,20],[50,40]]}}}}"));
            Console.WriteLine("cfg : " + Json.Write(cfg));
            Dictionary<string, object> h2 = (Dictionary<string, object>)((Dictionary<string, object>)cfg["headers"])["/lpc/x/0/control/2"];
            double[][] pts = Config.Points(h2);
            bool ok = (double)cfg["cpu_limit"] == 95 && pts[0][0] == 30 && Engine.Interp(pts, 40) == 30 && Engine.Interp(pts, 90) == 90;
            Console.WriteLine("PawnIO installed: " + Engine.PawnIoInstalled());
            Console.WriteLine(ok ? "selftest OK" : "selftest FAILED");
            return ok ? 0 : 2;
        }

        private static int RunConsole()
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
            {
                Console.WriteLine("needs administrator");
                return 3;
            }
            Log.ToConsole = true;
            Engine engine = new Engine();
            Console.CancelKeyPress += delegate(object s, ConsoleCancelEventArgs e) { e.Cancel = true; engine.Stop(); Environment.Exit(0); };
            engine.Start();
            Console.WriteLine("running (pipe \\\\.\\pipe\\" + PipeName + "), Enter stops");
            Console.ReadLine();
            engine.Stop();
            return 0;
        }
    }

    internal sealed class FanWinService : ServiceBase
    {
        private Engine _engine;

        public FanWinService()
        {
            ServiceName = Program.ServiceName;
            CanStop = true;
            CanShutdown = true;
            AutoLog = false;
        }

        protected override void OnStart(string[] args)
        {
            _engine = new Engine();
            _engine.Start();
        }

        protected override void OnStop()
        {
            if (_engine != null) _engine.Stop();
        }

        protected override void OnShutdown()
        {
            if (_engine != null) _engine.Stop();
        }
    }

    // ------------------------------------------------------------------------------------------------ engine

    internal sealed class Ctl
    {
        public string Id;
        public string Kind;          // "board" (Super I/O) | "gpu" | "other"
        public string HwName;
        public ISensor Control;
        public ISensor Fan;          // paired RPM sensor (same hardware, same index), may be null
        public bool Taken;           // we called SetSoftware and not yet SetDefault
        public double Last = -1;     // last duty written
        public DateTime LastWrite;
        public double Filtered = double.NaN;   // curve input temperature after hysteresis
        public int Lost;             // ticks the curve source was unreadable
        public int Stall;            // ticks with 0 RPM at a duty that should spin the fan
        public string Alarm = "";
        public string Reason = "";   // why the current duty was chosen
        public double OverridePct = -1;
        public DateTime OverrideUntil;
    }

    internal sealed class Engine
    {
        private const int TickMs = 1000;
        private const double RampDownPerTick = 2.0;
        private const double Hysteresis = 2.0;
        private static readonly double[][] SafeCurve =
        {
            new double[] { 30, 35 }, new double[] { 50, 45 }, new double[] { 65, 65 }, new double[] { 75, 85 }, new double[] { 82, 100 }
        };

        private readonly object _lock = new object();
        private readonly ManualResetEvent _stop = new ManualResetEvent(false);
        private Thread _worker;
        private PipeServer _pipe;
        private Computer _pc;
        private readonly List<Ctl> _ctls = new List<Ctl>();
        private readonly List<ISensor> _temps = new List<ISensor>();
        private readonly List<IHardware> _hw = new List<IHardware>();
        private Dictionary<string, object> _cfg;
        private DateTime _lastStatusAsk = DateTime.MinValue;
        private string _status = "{\"ok\":true,\"state\":\"starting\"}";
        private string _openError = "";
        private DateTime _openRetry = DateTime.MinValue;
        private bool _panic;
        private string _panicWhy = "";
        private double? _cpuT, _gpuT;
        private HashSet<string> _safeIds = new HashSet<string>();
        private long _bootUnix;

        public void Start()
        {
            Store.EnsureDir();
            _cfg = Config.Load();
            _bootUnix = BootUnix();
            _safeIds = Store.LoadDirty(_bootUnix);
            if (_safeIds.Count > 0) Log.Write("previous run did not hand " + _safeIds.Count + " header(s) back cleanly: safe curve until reboot");
            AppDomain.CurrentDomain.UnhandledException += delegate { ReleaseAllQuiet(); };
            _worker = new Thread(Loop);
            _worker.IsBackground = true;
            _worker.Name = "fan-loop";
            _worker.Start();
            _pipe = new PipeServer(this);
            _pipe.Start();
            Log.Write("started (enabled=" + Config.Bool(_cfg, "enabled") + ")");
        }

        public void Stop()
        {
            _stop.Set();
            if (_pipe != null) _pipe.Stop();
            if (_worker != null) _worker.Join(5000);
            lock (_lock)
            {
                CloseHardware();
            }
            Log.Write("stopped");
        }

        private void Loop()
        {
            while (!_stop.WaitOne(TickMs))
            {
                lock (_lock)
                {
                    try
                    {
                        Tick();
                    }
                    catch (Exception ex)
                    {
                        Log.Write("tick error: " + ex.GetType().Name + ": " + ex.Message);
                        ReleaseAllQuiet();
                    }
                }
            }
        }

        // -------------------------------------------------------------------------------------- per tick

        private void Tick()
        {
            bool enabled = Config.Bool(_cfg, "enabled");
            bool overrides = false;
            foreach (Ctl c in _ctls) if (c.OverridePct >= 0) overrides = true;
            bool need = enabled || _safeIds.Count > 0 || overrides || (DateTime.UtcNow - _lastStatusAsk).TotalSeconds < 60;
            if (need && _pc == null && DateTime.UtcNow >= _openRetry) OpenHardware();
            if (!need && _pc != null) CloseHardware();
            if (_pc != null)
            {
                foreach (IHardware h in _hw) h.Update();
                ReadTemps();
                Control(enabled);
            }
            _status = BuildStatus(enabled);
        }

        private void OpenHardware()
        {
            try
            {
                Computer pc = new Computer();
                pc.IsMotherboardEnabled = true;
                pc.IsCpuEnabled = true;
                pc.IsGpuEnabled = true;
                pc.Open();
                _pc = pc;
                _ctls.Clear();
                _temps.Clear();
                _hw.Clear();
                foreach (IHardware hw in pc.Hardware)
                {
                    _hw.Add(hw);
                    hw.Update();
                    Collect(hw);
                    foreach (IHardware sub in hw.SubHardware)
                    {
                        _hw.Add(sub);
                        sub.Update();
                        Collect(sub);
                    }
                }
                _openError = "";
                Log.Write("hardware opened: " + _ctls.Count + " control(s)");
            }
            catch (Exception ex)
            {
                _openError = ex.GetType().Name + ": " + ex.Message;
                _openRetry = DateTime.UtcNow.AddSeconds(30);
                Log.Write("open failed: " + _openError);
                CloseHardware();
            }
        }

        private void Collect(IHardware hw)
        {
            string kind = hw.HardwareType == HardwareType.SuperIO ? "board"
                : (hw.HardwareType == HardwareType.GpuNvidia || hw.HardwareType == HardwareType.GpuAmd || hw.HardwareType == HardwareType.GpuIntel) ? "gpu"
                : "other";
            foreach (ISensor s in hw.Sensors)
            {
                if (s.SensorType == SensorType.Temperature) _temps.Add(s);
                if (s.SensorType != SensorType.Control || s.Control == null) continue;
                Ctl c = new Ctl();
                c.Id = s.Identifier.ToString();
                c.Kind = kind;
                c.HwName = hw.Name;
                c.Control = s;
                foreach (ISensor f in hw.Sensors)
                    if (f.SensorType == SensorType.Fan && f.Index == s.Index) c.Fan = f;
                _ctls.Add(c);
            }
        }

        private void CloseHardware()
        {
            // service stop with unrestorable headers: leave them at a duty that is safe until the reboot
            foreach (Ctl c in _ctls)
                if (c.Taken && Unrestorable(c))
                {
                    try
                    {
                        c.Control.Control.SetSoftware((float)Math.Max(c.Last, 60));
                        Log.Write("left " + c.Id + " at " + Math.Max(c.Last, 60) + " % until reboot");
                    }
                    catch { }
                }
            ReleaseAllQuiet();
            if (_pc != null)
            {
                try { _pc.Close(); } catch { }
            }
            _pc = null;
            _ctls.Clear();
            _temps.Clear();
            _hw.Clear();
        }

        private void ReadTemps()
        {
            _cpuT = null;
            _gpuT = null;
            double best = -1;
            int bestRank = 99;
            string[] prefer = { "core (tctl/tdie)", "cpu package", "package", "tctl/tdie", "core (tctl)", "tdie" };
            foreach (ISensor s in _temps)
            {
                double? v = Val(s);
                if (v == null) continue;
                HardwareType t = s.Hardware.HardwareType;
                string n = s.Name.ToLowerInvariant();
                if (t == HardwareType.Cpu && n.IndexOf("distance") < 0)
                {
                    int rank = Array.IndexOf(prefer, n);
                    if (rank < 0) rank = 50;
                    if (rank < bestRank || (rank == bestRank && v.Value > best)) { bestRank = rank; best = v.Value; }
                }
                else if ((t == HardwareType.GpuNvidia || t == HardwareType.GpuAmd || t == HardwareType.GpuIntel) && n == "gpu core")
                {
                    if (_gpuT == null || v.Value > _gpuT.Value) _gpuT = v.Value;
                }
            }
            if (best >= 0) _cpuT = best;
            if (_gpuT == null)
                foreach (ISensor s in _temps)
                {
                    HardwareType t = s.Hardware.HardwareType;
                    double? v = Val(s);
                    if (v != null && (t == HardwareType.GpuNvidia || t == HardwareType.GpuAmd || t == HardwareType.GpuIntel)
                        && s.Name.ToLowerInvariant().IndexOf("hot") < 0 && (_gpuT == null || v.Value > _gpuT.Value))
                        _gpuT = v.Value;
                }
        }

        private static double? Val(ISensor s)
        {
            if (s == null || !s.Value.HasValue) return null;
            float v = s.Value.Value;
            if (float.IsNaN(v) || float.IsInfinity(v)) return null;
            return v;
        }

        private double? SourceTemp(string src)
        {
            if (src == "cpu") return _cpuT;
            if (src == "gpu") return _gpuT;
            if (src == "max")
            {
                if (_cpuT == null) return _gpuT;
                if (_gpuT == null) return _cpuT;
                return Math.Max(_cpuT.Value, _gpuT.Value);
            }
            foreach (ISensor s in _temps)
                if (s.Identifier.ToString() == src)
                {
                    double? v = Val(s);
                    return v != null && v.Value > 0 && v.Value < 130 ? v : null;
                }
            return null;
        }

        private void Control(bool enabled)
        {
            double cpuLim = Config.Num(_cfg, "cpu_limit");
            double gpuLim = Config.Num(_cfg, "gpu_limit");
            bool hot = (_cpuT != null && _cpuT.Value >= cpuLim) || (_gpuT != null && _gpuT.Value >= gpuLim);
            bool cool = (_cpuT == null || _cpuT.Value < cpuLim - 8) && (_gpuT == null || _gpuT.Value < gpuLim - 8);
            if (hot && !_panic)
            {
                _panic = true;
                _panicWhy = "CPU " + Fmt(_cpuT) + " C / GPU " + Fmt(_gpuT) + " C";
                Log.Write("temperature limit reached (" + _panicWhy + "): all controlled fans 100 %");
            }
            else if (_panic && cool)
            {
                _panic = false;
                _panicWhy = "";
                Log.Write("temperature back below the limit");
            }

            Dictionary<string, object> headers = (Dictionary<string, object>)_cfg["headers"];
            DateTime now = DateTime.UtcNow;
            foreach (Ctl c in _ctls)
            {
                object o;
                Dictionary<string, object> h = headers.TryGetValue(c.Id, out o) ? (Dictionary<string, object>)o : null;
                string role = h != null ? (string)h["role"] : Config.DefaultRole(c.Kind);
                string mode = h != null && enabled ? (string)h["mode"] : "bios";
                if (role == "none") mode = "bios";
                bool safe = c.Kind == "board" && _safeIds.Contains(c.Id);
                bool ovr = c.OverridePct >= 0 && now < c.OverrideUntil;
                if (c.OverridePct >= 0 && !ovr) c.OverridePct = -1;

                if (mode == "bios" && !safe && !ovr)
                {
                    Release(c);
                    c.Reason = "bios";
                    continue;
                }

                double target;
                string why;
                bool immediate = false;
                if (_panic)
                {
                    target = 100; why = "limit"; immediate = true;
                }
                else if (ovr)
                {
                    target = c.OverridePct; why = "override"; immediate = true;
                }
                else if (mode == "fixed")
                {
                    target = Config.Num(h, "fixed"); why = "fixed";
                }
                else
                {
                    double[][] pts = mode == "curve" ? Config.Points(h) : SafeCurve;
                    string src = mode == "curve" ? (string)((Dictionary<string, object>)h["curve"])["source"] : "max";
                    double? t = SourceTemp(src);
                    if (t == null)
                    {
                        c.Lost++;
                        if (c.Lost >= 3) { target = 100; why = "no sensor"; immediate = true; }
                        else { target = c.Last >= 0 ? c.Last : 100; why = "waiting sensor"; }
                    }
                    else
                    {
                        c.Lost = 0;
                        double tv = t.Value;
                        if (double.IsNaN(c.Filtered) || tv > c.Filtered) c.Filtered = tv;
                        else if (c.Filtered - tv > Hysteresis) c.Filtered = tv + Hysteresis;
                        target = Interp(pts, c.Filtered);
                        why = mode == "curve" ? "curve" : "safe";
                    }
                }
                target = Math.Max(Config.MinDuty(role, c.Kind), Math.Min(100, target));
                if (!immediate && c.Last >= 0 && target < c.Last - RampDownPerTick) target = c.Last - RampDownPerTick;
                target = Math.Round(target, 1);
                Apply(c, target, now);
                c.Reason = why;

                // stall: CPU / pump header at a duty that must spin it, but 0 RPM for 5 s
                double? rpm = Val(c.Fan);
                if ((role == "cpu" || role == "pump") && c.Fan != null && target >= 40 && rpm != null && rpm.Value < 1)
                {
                    c.Stall++;
                    if (c.Stall == 5)
                    {
                        c.Alarm = "stall";
                        Log.Write("stall: " + c.Id + " 0 RPM at " + target + " %");
                    }
                }
                else
                {
                    c.Stall = 0;
                    c.Alarm = "";
                }
            }
            SaveDirty();
        }

        private void Apply(Ctl c, double pct, DateTime now)
        {
            if (c.Taken && Math.Abs(pct - c.Last) < 0.5 && (now - c.LastWrite).TotalSeconds < 5) return;
            c.Control.Control.SetSoftware((float)pct);
            c.Taken = true;
            c.Last = pct;
            c.LastWrite = now;
        }

        private bool Unrestorable(Ctl c)
        {
            return c.Kind == "board" && _safeIds.Contains(c.Id);
        }

        private void Release(Ctl c)
        {
            if (!c.Taken) return;
            // LHM's "default" of a header left in manual mode by a killed run is that stuck value: keep driving it
            if (Unrestorable(c)) return;
            try
            {
                c.Control.Control.SetDefault();
                c.Taken = false;
                c.Last = -1;
                c.Filtered = double.NaN;
            }
            catch (Exception ex)
            {
                Log.Write("SetDefault failed for " + c.Id + ": " + ex.Message);
            }
        }

        private void ReleaseAllQuiet()
        {
            foreach (Ctl c in _ctls)
            {
                try { Release(c); } catch { }
            }
            SaveDirty();
        }

        // headers in software mode during this boot that a normal SetDefault could not hand back after a hard kill
        private void SaveDirty()
        {
            HashSet<string> ids = new HashSet<string>(_safeIds);
            foreach (Ctl c in _ctls) if (c.Taken && c.Kind == "board") ids.Add(c.Id);
            Store.SaveDirty(_bootUnix, ids);
        }

        public static double Interp(double[][] pts, double t)
        {
            if (t <= pts[0][0]) return pts[0][1];
            for (int i = 1; i < pts.Length; i++)
                if (t <= pts[i][0])
                {
                    double a = pts[i - 1][0], b = pts[i][0];
                    return b - a < 0.01 ? pts[i][1] : pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (t - a) / (b - a);
                }
            return pts[pts.Length - 1][1];
        }

        private static string Fmt(double? v)
        {
            return v == null ? "-" : v.Value.ToString("0.0", CultureInfo.InvariantCulture);
        }

        // ------------------------------------------------------------------------------------- status / pipe

        private string BuildStatus(bool enabled)
        {
            Dictionary<string, object> st = new Dictionary<string, object>();
            st["ok"] = true;
            st["version"] = Program.Version;
            st["state"] = _pc != null ? "open" : (_openError != "" ? "error" : "idle");
            st["error"] = _openError;
            st["enabled"] = enabled;
            st["pawnio"] = PawnIoInstalled();
            st["safe_mode"] = _safeIds.Count > 0;
            st["panic"] = _panic ? _panicWhy : "";
            st["cpu"] = _cpuT;
            st["gpu"] = _gpuT;
            List<object> list = new List<object>();
            foreach (Ctl c in _ctls)
            {
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["id"] = c.Id;
                d["name"] = c.Control.Name;
                d["hw"] = c.HwName;
                d["kind"] = c.Kind;
                d["index"] = c.Control.Index;
                d["duty"] = Val(c.Control);
                d["rpm"] = Val(c.Fan);
                d["target"] = c.Taken ? (object)c.Last : null;
                d["software"] = c.Taken;
                d["reason"] = c.Reason;
                d["alarm"] = c.Alarm;
                d["safe"] = _safeIds.Contains(c.Id);
                d["override"] = c.OverridePct >= 0 ? Math.Max(0, (c.OverrideUntil - DateTime.UtcNow).TotalSeconds) : 0;
                list.Add(d);
            }
            st["headers"] = list;
            List<object> temps = new List<object>();
            foreach (ISensor s in _temps)
            {
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["id"] = s.Identifier.ToString();
                d["name"] = s.Name;
                d["hw"] = s.Hardware.Name;
                d["value"] = Val(s);
                temps.Add(d);
            }
            st["temps"] = temps;
            return Json.Write(st);
        }

        public string Handle(string line)
        {
            Dictionary<string, object> req;
            try
            {
                req = Json.Parse(line);
            }
            catch
            {
                return Error("bad json");
            }
            object o;
            string cmd = req.TryGetValue("cmd", out o) ? o as string : null;
            lock (_lock)
            {
                if (cmd == "ping") return "{\"ok\":true,\"version\":\"" + Program.Version + "\"}";
                if (cmd == "status")
                {
                    _lastStatusAsk = DateTime.UtcNow;
                    return _status;
                }
                if (cmd == "get_config") return Ok("config", _cfg);
                if (cmd == "set_config")
                {
                    if (!req.TryGetValue("config", out o) || !(o is Dictionary<string, object>)) return Error("config missing");
                    bool was = Config.Bool(_cfg, "enabled");
                    Dictionary<string, object> cfg = Config.Validate((Dictionary<string, object>)o);
                    cfg["enabled"] = was;                          // switched only with "enable"
                    _cfg = cfg;
                    Config.Save(_cfg);
                    return Ok("config", _cfg);
                }
                if (cmd == "enable")
                {
                    bool on = req.TryGetValue("on", out o) && o is bool && (bool)o;
                    _cfg["enabled"] = on;
                    Config.Save(_cfg);
                    if (!on) ReleaseAllQuiet();
                    Log.Write(on ? "enabled" : "disabled");
                    return Ok("enabled", on);
                }
                if (cmd == "stop")
                {
                    _cfg["enabled"] = false;
                    Config.Save(_cfg);
                    foreach (Ctl c in _ctls) c.OverridePct = -1;
                    ReleaseAllQuiet();
                    Log.Write("stop requested");
                    return Ok("enabled", false);
                }
                if (cmd == "override")
                {
                    // short, self-expiring duty for identification / calibration (clamped to the role minimum)
                    string id = req.TryGetValue("id", out o) ? o as string : null;
                    double pct = req.TryGetValue("pct", out o) ? Config.ToNum(o, -1) : -1;
                    double secs = req.TryGetValue("seconds", out o) ? Config.ToNum(o, 0) : 0;
                    foreach (Ctl c in _ctls)
                        if (c.Id == id)
                        {
                            if (pct < 0)
                            {
                                c.OverridePct = -1;
                                return Ok("override", 0);
                            }
                            c.OverridePct = Math.Max(0, Math.Min(100, pct));
                            c.OverrideUntil = DateTime.UtcNow.AddSeconds(Math.Max(1, Math.Min(30, secs)));
                            _lastStatusAsk = DateTime.UtcNow;
                            return Ok("override", c.OverridePct);
                        }
                    return Error(_pc == null ? "hardware not open (ask status first)" : "unknown control");
                }
            }
            return Error("unknown command");
        }

        private static string Ok(string key, object value)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["ok"] = true;
            d[key] = value;
            return Json.Write(d);
        }

        private static string Error(string msg)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["ok"] = false;
            d["error"] = msg;
            return Json.Write(d);
        }

        public static bool PawnIoInstalled()
        {
            try
            {
                using (Microsoft.Win32.RegistryKey k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\PawnIO"))
                    return k != null;
            }
            catch
            {
                return false;
            }
        }

        [DllImport("kernel32.dll")]
        private static extern ulong GetTickCount64();

        // boot time in unix seconds, rounded to 10 s (the clock moves a little between calls)
        private static long BootUnix()
        {
            long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            long boot = now - (long)(GetTickCount64() / 1000);
            return boot / 10 * 10;
        }
    }

    // ------------------------------------------------------------------------------------------------ config

    internal static class Config
    {
        private static readonly string[] Roles = { "unknown", "case", "cpu", "pump", "gpu", "none" };
        private static readonly string[] Modes = { "bios", "fixed", "curve" };

        public static string DefaultRole(string kind)
        {
            return kind == "gpu" ? "gpu" : "unknown";
        }

        public static double MinDuty(string role, string kind)
        {
            if (role == "pump") return 60;
            if (role == "case") return kind == "gpu" ? 30 : 0;
            return 30;                                   // cpu, gpu, unknown
        }

        public static Dictionary<string, object> Defaults()
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["version"] = 1;
            d["enabled"] = false;
            d["cpu_limit"] = 90.0;
            d["gpu_limit"] = 85.0;
            d["headers"] = new Dictionary<string, object>();
            return d;
        }

        public static Dictionary<string, object> Load()
        {
            try
            {
                if (File.Exists(Store.ConfigPath)) return Validate(Json.Parse(File.ReadAllText(Store.ConfigPath, Encoding.UTF8)));
            }
            catch (Exception ex)
            {
                Log.Write("config unreadable, defaults used: " + ex.Message);
            }
            return Defaults();
        }

        public static void Save(Dictionary<string, object> cfg)
        {
            Store.WriteProtected(Store.ConfigPath, Json.Write(cfg));
        }

        // anything from the pipe or the file goes through here: unknown keys dropped, numbers clamped
        public static Dictionary<string, object> Validate(Dictionary<string, object> src)
        {
            Dictionary<string, object> d = Defaults();
            object o;
            if (src.TryGetValue("enabled", out o) && o is bool) d["enabled"] = (bool)o;
            if (src.TryGetValue("cpu_limit", out o)) d["cpu_limit"] = Clamp(ToNum(o, 90), 60, 95);
            if (src.TryGetValue("gpu_limit", out o)) d["gpu_limit"] = Clamp(ToNum(o, 85), 60, 90);
            Dictionary<string, object> outH = (Dictionary<string, object>)d["headers"];
            if (src.TryGetValue("headers", out o) && o is Dictionary<string, object>)
                foreach (KeyValuePair<string, object> kv in (Dictionary<string, object>)o)
                {
                    if (outH.Count >= 32 || kv.Key.Length > 200 || !(kv.Value is Dictionary<string, object>)) continue;
                    Dictionary<string, object> h = (Dictionary<string, object>)kv.Value;
                    Dictionary<string, object> r = new Dictionary<string, object>();
                    string name = h.TryGetValue("name", out o) ? o as string : null;
                    r["name"] = name == null ? "" : (name.Length > 40 ? name.Substring(0, 40) : name);
                    r["role"] = Pick(h, "role", Roles);
                    r["mode"] = Pick(h, "mode", Modes);
                    r["fixed"] = Clamp(h.TryGetValue("fixed", out o) ? ToNum(o, 50) : 50, 0, 100);
                    Dictionary<string, object> curve = new Dictionary<string, object>();
                    Dictionary<string, object> cin = h.TryGetValue("curve", out o) ? o as Dictionary<string, object> : null;
                    string srcName = cin != null && cin.TryGetValue("source", out o) ? o as string : null;
                    curve["source"] = srcName == "cpu" || srcName == "gpu" || srcName == "max" || (srcName != null && srcName.StartsWith("/") && srcName.Length < 200)
                        ? srcName : "max";
                    List<object> pts = new List<object>();
                    object[] raw = cin != null && cin.TryGetValue("points", out o) ? ToArray(o) : null;
                    if (raw != null)
                        foreach (object p in raw)
                        {
                            object[] pa = ToArray(p);
                            if (pa == null || pa.Length != 2 || pts.Count >= 8) continue;
                            pts.Add(new object[] { Clamp(ToNum(pa[0], 50), 0, 110), Clamp(ToNum(pa[1], 50), 0, 100) });
                        }
                    pts.Sort(delegate(object a, object b) { return ((double)((object[])a)[0]).CompareTo((double)((object[])b)[0]); });
                    if (pts.Count < 2)
                    {
                        pts.Clear();
                        pts.Add(new object[] { 40.0, 35.0 });
                        pts.Add(new object[] { 80.0, 100.0 });
                    }
                    curve["points"] = pts;
                    r["curve"] = curve;
                    outH[kv.Key] = r;
                }
            return d;
        }

        public static double[][] Points(Dictionary<string, object> h)
        {
            List<object> pts = (List<object>)((Dictionary<string, object>)h["curve"])["points"];
            double[][] r = new double[pts.Count][];
            for (int i = 0; i < pts.Count; i++)
            {
                object[] p = (object[])pts[i];
                r[i] = new double[] { (double)p[0], (double)p[1] };
            }
            return r;
        }

        private static string Pick(Dictionary<string, object> h, string key, string[] allowed)
        {
            object o;
            string v = h.TryGetValue(key, out o) ? o as string : null;
            return Array.IndexOf(allowed, v) >= 0 ? v : allowed[0];
        }

        public static object[] ToArray(object o)
        {
            if (o is object[]) return (object[])o;
            System.Collections.ArrayList al = o as System.Collections.ArrayList;
            if (al != null) return al.ToArray();
            List<object> l = o as List<object>;
            return l != null ? l.ToArray() : null;
        }

        public static double ToNum(object o, double dflt)
        {
            try
            {
                if (o == null || o is bool || o is string) return dflt;
                double v = Convert.ToDouble(o, CultureInfo.InvariantCulture);
                return double.IsNaN(v) || double.IsInfinity(v) ? dflt : v;
            }
            catch
            {
                return dflt;
            }
        }

        private static double Clamp(double v, double lo, double hi)
        {
            return Math.Max(lo, Math.Min(hi, v));
        }

        public static bool Bool(Dictionary<string, object> d, string key)
        {
            object o;
            return d.TryGetValue(key, out o) && o is bool && (bool)o;
        }

        public static double Num(Dictionary<string, object> d, string key)
        {
            object o;
            return d.TryGetValue(key, out o) ? ToNum(o, 0) : 0;
        }
    }

    // ------------------------------------------------------------------------------------------------ files

    internal static class Store
    {
        public static readonly string Dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "metriQ");
        public static readonly string ConfigPath = Path.Combine(Dir, "fan.json");
        public static readonly string DirtyPath = Path.Combine(Dir, "fan_state.json");
        public static readonly string LogPath = Path.Combine(Dir, "fan_service.log");
        private static string _lastDirty;

        public static void EnsureDir()
        {
            try { Directory.CreateDirectory(Dir); } catch { }
        }

        // SYSTEM / administrators write, users read (the panel reads, only the service writes)
        public static void WriteProtected(string path, string text)
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            FileSecurity fs = new FileSecurity();
            fs.SetAccessRuleProtection(true, false);
            fs.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
            fs.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
            fs.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null), FileSystemRights.Read, AccessControlType.Allow));
            try { File.SetAccessControl(tmp, fs); } catch { }
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
        }

        public static HashSet<string> LoadDirty(long boot)
        {
            HashSet<string> ids = new HashSet<string>();
            try
            {
                if (!File.Exists(DirtyPath)) return ids;
                Dictionary<string, object> d = Json.Parse(File.ReadAllText(DirtyPath, Encoding.UTF8));
                object o;
                long b = d.TryGetValue("boot", out o) ? (long)Config.ToNum(o, 0) : 0;
                if (Math.Abs(b - boot) > 120) return ids;          // rebooted since: BIOS set the chip up again
                object[] arr = d.TryGetValue("ids", out o) ? Config.ToArray(o) : null;
                if (arr != null)
                    foreach (object x in arr)
                        if (x is string) ids.Add((string)x);
            }
            catch { }
            return ids;
        }

        public static void SaveDirty(long boot, HashSet<string> ids)
        {
            try
            {
                if (ids.Count == 0)
                {
                    if (_lastDirty != "" && File.Exists(DirtyPath)) File.Delete(DirtyPath);
                    _lastDirty = "";
                    return;
                }
                List<string> l = new List<string>(ids);
                l.Sort(StringComparer.Ordinal);
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["boot"] = boot;
                d["ids"] = l;
                string text = Json.Write(d);
                if (text == _lastDirty) return;
                WriteProtected(DirtyPath, text);
                _lastDirty = text;
            }
            catch { }
        }
    }

    internal static class Log
    {
        public static bool ToConsole;
        private static readonly object Gate = new object();

        public static void Write(string msg)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + "  " + msg;
            if (ToConsole) Console.WriteLine(line);
            lock (Gate)
            {
                try
                {
                    FileInfo fi = new FileInfo(Store.LogPath);
                    if (fi.Exists && fi.Length > 256 * 1024)
                    {
                        string old = Store.LogPath + ".1";
                        if (File.Exists(old)) File.Delete(old);
                        File.Move(Store.LogPath, old);
                    }
                    File.AppendAllText(Store.LogPath, line + "\r\n", Encoding.UTF8);
                }
                catch { }
            }
        }
    }

    internal static class Json
    {
        private static JavaScriptSerializer Ser()
        {
            JavaScriptSerializer s = new JavaScriptSerializer();
            s.MaxJsonLength = 1 << 20;
            s.RecursionLimit = 16;
            return s;
        }

        public static Dictionary<string, object> Parse(string text)
        {
            Dictionary<string, object> d = Ser().DeserializeObject(text) as Dictionary<string, object>;
            if (d == null) throw new FormatException("not a JSON object");
            return d;
        }

        public static string Write(object o)
        {
            return Ser().Serialize(o);
        }
    }

    // ------------------------------------------------------------------------------------------------ pipe

    // \\.\pipe\metriQ.fan: local users read / write, network clients denied. One JSON line per connection.
    internal sealed class PipeServer
    {
        private const int MaxInstances = 4;
        private const int MaxLine = 256 * 1024;
        private readonly Engine _engine;
        private volatile bool _stopping;
        private Thread _thread;
        private NamedPipeServerStream _waiting;

        public PipeServer(Engine engine)
        {
            _engine = engine;
        }

        public void Start()
        {
            _thread = new Thread(Accept);
            _thread.IsBackground = true;
            _thread.Name = "fan-pipe";
            _thread.Start();
        }

        public void Stop()
        {
            _stopping = true;
            NamedPipeServerStream w = _waiting;
            if (w != null)
            {
                try { w.Dispose(); } catch { }
            }
        }

        private static PipeSecurity Security()
        {
            PipeSecurity ps = new PipeSecurity();
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null), PipeAccessRights.ReadWrite, AccessControlType.Allow));
            return ps;
        }

        private void Accept()
        {
            while (!_stopping)
            {
                NamedPipeServerStream p = null;
                try
                {
                    p = new NamedPipeServerStream(Program.PipeName, PipeDirection.InOut, MaxInstances, PipeTransmissionMode.Byte,
                        PipeOptions.None, 4096, 4096, Security());
                    _waiting = p;
                    p.WaitForConnection();
                    _waiting = null;
                    NamedPipeServerStream conn = p;
                    p = null;
                    Thread t = new Thread(delegate() { Serve(conn); });
                    t.IsBackground = true;
                    t.Start();
                }
                catch (Exception ex)
                {
                    _waiting = null;
                    if (p != null) { try { p.Dispose(); } catch { } }
                    if (_stopping) break;
                    Log.Write("pipe: " + ex.GetType().Name + ": " + ex.Message);
                    Thread.Sleep(1000);              // e.g. all instances busy
                }
            }
        }

        private void Serve(NamedPipeServerStream p)
        {
            // a client that never finishes its line is dropped after 5 s
            using (Timer kill = new Timer(delegate { try { p.Dispose(); } catch { } }, null, 5000, Timeout.Infinite))
            {
                try
                {
                    MemoryStream buf = new MemoryStream();
                    byte[] chunk = new byte[4096];
                    while (buf.Length < MaxLine)
                    {
                        int n = p.Read(chunk, 0, chunk.Length);
                        if (n <= 0) break;
                        int nl = Array.IndexOf(chunk, (byte)'\n', 0, n);
                        buf.Write(chunk, 0, nl >= 0 ? nl : n);
                        if (nl >= 0) break;
                    }
                    string reply = _engine.Handle(Encoding.UTF8.GetString(buf.ToArray()));
                    byte[] outb = Encoding.UTF8.GetBytes(reply + "\n");
                    p.Write(outb, 0, outb.Length);
                    p.Flush();
                    p.WaitForPipeDrain();
                }
                catch { }
                finally
                {
                    try { p.Dispose(); } catch { }
                }
            }
        }
    }
}
