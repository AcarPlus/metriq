// FanProbe - READ-ONLY fan / control sensor discovery via LibreHardwareMonitorLib.
//
// Safety contract: this program NEVER writes to hardware. It does not call
// IControl.SetSoftware / SetDefault or any other setter. It only reads
// properties. Computer is opened with Motherboard/GPU/CPU/Controller enabled
// (Storage and everything else disabled), read once, then Closed.
//
// Language level: C# 5 (csc from .NET Framework 4.x) - no interpolation, no ?., no =>.
//
// Usage:
//   FanProbe.exe --selftest            load LHM assembly, print version, do NOT open Computer
//   FanProbe.exe [--out <path>]        (run as administrator) discovery JSON to stdout and file

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Principal;
using System.Text;
using LibreHardwareMonitor.Hardware;

namespace FanProbe
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            string outPath = null;
            bool selftest = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--selftest") selftest = true;
                else if (args[i] == "--out" && i + 1 < args.Length) { outPath = args[i + 1]; i++; }
                else if (args[i] == "-h" || args[i] == "--help")
                {
                    Console.WriteLine("FanProbe.exe --selftest | [--out <path>]   (discovery needs administrator)");
                    return 0;
                }
            }

            try
            {
                if (selftest) return SelfTest();
                return Discover(outPath);
            }
            catch (Exception ex)
            {
                // Never crash: report and exit 0 as specified.
                Console.Error.WriteLine("FanProbe fatal: " + ex);
                return 0;
            }
        }

        // ---------------------------------------------------------------- selftest

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static Assembly LhmAssembly()
        {
            return typeof(IHardware).Assembly;   // loads LibreHardwareMonitorLib, no Computer created
        }

        private static int SelfTest()
        {
            Assembly asm = LhmAssembly();
            Console.WriteLine("LHM assembly: " + asm.FullName);
            Console.WriteLine("LHM version : " + LhmVersion(asm));
            Console.WriteLine("LHM location: " + asm.Location);
            int bad = 0;
            foreach (AssemblyName an in asm.GetReferencedAssemblies())
            {
                try
                {
                    Assembly.Load(an);
                }
                catch (Exception ex)
                {
                    bad++;
                    Console.WriteLine("  reference NOT loadable: " + an.FullName + " -> " + ex.GetType().Name + ": " + ex.Message);
                }
            }
            Console.WriteLine(bad == 0 ? "All referenced assemblies load OK." : bad + " referenced assembly(ies) failed to load.");
            Console.WriteLine("Selftest done (Computer was NOT opened, no hardware access).");
            return 0;
        }

        private static string LhmVersion(Assembly asm)
        {
            string info = null;
            try
            {
                object[] a = asm.GetCustomAttributes(typeof(AssemblyInformationalVersionAttribute), false);
                if (a.Length > 0) info = ((AssemblyInformationalVersionAttribute)a[0]).InformationalVersion;
            }
            catch (Exception) { }
            string file = null;
            try { file = FileVersionInfo.GetVersionInfo(asm.Location).FileVersion; } catch (Exception) { }
            return "assembly " + asm.GetName().Version + ", file " + (file ?? "?") + ", informational " + (info ?? "?");
        }

        // ---------------------------------------------------------------- discovery

        private static bool IsAdmin()
        {
            try
            {
                WindowsPrincipal p = new WindowsPrincipal(WindowsIdentity.GetCurrent());
                return p.IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch (Exception) { return false; }
        }

        private static int Discover(string outPath)
        {
            JsonWriter w = new JsonWriter();
            List<PairInfo> pairs = new List<PairInfo>();
            List<string> errors = new List<string>();

            w.BeginObject();
            w.Prop("tool", "FanProbe (read-only)");
            w.Prop("timeUtc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));
            w.Prop("isAdmin", IsAdmin());
            w.Prop("lhmVersion", LhmVersion(LhmAssembly()));

            w.Name("hardware");
            w.BeginArray();
            ReadAllHardware(w, pairs, errors);
            w.EndArray();

            w.Name("pairing");
            w.BeginArray();
            foreach (PairInfo p in pairs) WritePair(w, p);
            w.EndArray();

            w.Name("errors");
            w.BeginArray();
            foreach (string e in errors) w.Value(e);
            w.EndArray();

            w.EndObject();

            string json = w.ToString();
            Console.WriteLine(json);
            if (!string.IsNullOrEmpty(outPath))
            {
                try
                {
                    string dir = Path.GetDirectoryName(Path.GetFullPath(outPath));
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    File.WriteAllText(outPath, json, new UTF8Encoding(false));
                    Console.Error.WriteLine("Wrote " + outPath);
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Could not write --out file: " + ex.Message);
                }
            }
            return 0;
        }

        private static void ReadAllHardware(JsonWriter w, List<PairInfo> pairs, List<string> errors)
        {
            Computer computer = null;
            try
            {
                computer = new Computer();
                computer.IsMotherboardEnabled = true;
                computer.IsGpuEnabled = true;
                computer.IsCpuEnabled = true;
                computer.IsControllerEnabled = true;
                computer.IsStorageEnabled = false;
                computer.IsMemoryEnabled = false;
                computer.IsNetworkEnabled = false;
                computer.IsBatteryEnabled = false;
                computer.IsPsuEnabled = false;
                try
                {
                    computer.Open();
                }
                catch (Exception ex)
                {
                    errors.Add("Computer.Open failed: " + ex.GetType().Name + ": " + ex.Message);
                }

                foreach (IHardware hw in computer.Hardware)
                {
                    try { DumpHardware(w, hw, pairs, errors); }
                    catch (Exception ex)
                    {
                        errors.Add("Hardware dump failed: " + ex.GetType().Name + ": " + ex.Message);
                    }
                }
            }
            catch (Exception ex)
            {
                errors.Add("Computer failure: " + ex.GetType().Name + ": " + ex.Message);
            }
            finally
            {
                if (computer != null)
                {
                    try { computer.Close(); }
                    catch (Exception ex) { errors.Add("Computer.Close failed: " + ex.Message); }
                }
            }
        }

        private static void DumpHardware(JsonWriter w, IHardware hw, List<PairInfo> pairs, List<string> errors)
        {
            string hid = SafeStr(delegate { return hw.Identifier.ToString(); });
            w.BeginObject();
            w.Prop("hardwareType", SafeStr(delegate { return hw.HardwareType.ToString(); }));
            w.Prop("name", SafeStr(delegate { return hw.Name; }));
            w.Prop("identifier", hid);

            try { hw.Update(); }
            catch (Exception ex)
            {
                string m = "Update failed for " + hid + ": " + ex.GetType().Name + ": " + ex.Message;
                errors.Add(m);
                w.Prop("error", m);
            }

            List<SensorInfo> fans = new List<SensorInfo>();
            List<SensorInfo> controls = new List<SensorInfo>();

            w.Name("sensors");
            w.BeginArray();
            try
            {
                foreach (ISensor s in hw.Sensors)
                {
                    SensorType st = s.SensorType;
                    if (st != SensorType.Fan && st != SensorType.Control && st != SensorType.Temperature) continue;
                    try
                    {
                        SensorInfo si = DumpSensor(w, s, errors);
                        if (st == SensorType.Fan) fans.Add(si);
                        else if (st == SensorType.Control) controls.Add(si);
                    }
                    catch (Exception ex)
                    {
                        errors.Add("Sensor failed in " + hid + ": " + ex.GetType().Name + ": " + ex.Message);
                    }
                }
            }
            catch (Exception ex)
            {
                errors.Add("Sensors enumeration failed for " + hid + ": " + ex.GetType().Name + ": " + ex.Message);
            }
            w.EndArray();

            foreach (SensorInfo c in controls)
            {
                PairInfo p = new PairInfo();
                p.HardwareId = hid;
                p.Control = c;
                foreach (SensorInfo f in fans)
                {
                    if (f.Index == c.Index) { p.Fan = f; break; }
                }
                pairs.Add(p);
            }

            w.Name("subHardware");
            w.BeginArray();
            try
            {
                foreach (IHardware sub in hw.SubHardware)
                {
                    try { DumpHardware(w, sub, pairs, errors); }
                    catch (Exception ex)
                    {
                        errors.Add("SubHardware failed under " + hid + ": " + ex.GetType().Name + ": " + ex.Message);
                    }
                }
            }
            catch (Exception ex)
            {
                errors.Add("SubHardware enumeration failed for " + hid + ": " + ex.GetType().Name + ": " + ex.Message);
            }
            w.EndArray();

            w.EndObject();
        }

        private static SensorInfo DumpSensor(JsonWriter w, ISensor s, List<string> errors)
        {
            SensorInfo si = new SensorInfo();
            si.Name = s.Name;
            si.Identifier = s.Identifier.ToString();
            si.Index = s.Index;

            w.BeginObject();
            w.Prop("sensorType", s.SensorType.ToString());
            w.Prop("name", si.Name);
            w.Prop("identifier", si.Identifier);
            w.Prop("index", s.Index);
            w.Prop("value", s.Value);
            w.Prop("min", s.Min);
            w.Prop("max", s.Max);

            if (s.SensorType == SensorType.Control)
            {
                // READ-ONLY: only property getters of IControl are touched.
                IControl ctl = s.Control;
                w.Name("control");
                w.BeginObject();
                w.Prop("exists", ctl != null);
                if (ctl != null)
                {
                    try { w.Prop("controlMode", ctl.ControlMode.ToString()); }
                    catch (Exception ex) { w.Prop("controlMode", "ERROR: " + ex.Message); errors.Add("ControlMode " + si.Identifier + ": " + ex.Message); }
                    try { w.Prop("softwareValue", ctl.SoftwareValue); }
                    catch (Exception ex) { w.PropNull("softwareValue"); errors.Add("SoftwareValue " + si.Identifier + ": " + ex.Message); }
                    try { w.Prop("minSoftwareValue", ctl.MinSoftwareValue); }
                    catch (Exception ex) { w.PropNull("minSoftwareValue"); errors.Add("MinSoftwareValue " + si.Identifier + ": " + ex.Message); }
                    try { w.Prop("maxSoftwareValue", ctl.MaxSoftwareValue); }
                    catch (Exception ex) { w.PropNull("maxSoftwareValue"); errors.Add("MaxSoftwareValue " + si.Identifier + ": " + ex.Message); }
                }
                w.EndObject();
            }
            w.EndObject();
            return si;
        }

        private static void WritePair(JsonWriter w, PairInfo p)
        {
            w.BeginObject();
            w.Prop("hardwareIdentifier", p.HardwareId);
            w.Prop("index", p.Control.Index);
            w.Prop("controlName", p.Control.Name);
            w.Prop("controlIdentifier", p.Control.Identifier);
            if (p.Fan != null)
            {
                w.Prop("fanName", p.Fan.Name);
                w.Prop("fanIdentifier", p.Fan.Identifier);
                w.Prop("matchedBy", "same hardware + same sensor index (LHM convention); heuristic, verify manually");
            }
            else
            {
                w.PropNull("fanName");
                w.PropNull("fanIdentifier");
                w.Prop("matchedBy", "none (no Fan sensor with same index on this hardware)");
            }
            w.EndObject();
        }

        private delegate string StrGetter();

        private static string SafeStr(StrGetter g)
        {
            try { return g(); }
            catch (Exception ex) { return "ERROR: " + ex.Message; }
        }

        private class SensorInfo
        {
            public string Name;
            public string Identifier;
            public int Index;
        }

        private class PairInfo
        {
            public string HardwareId;
            public SensorInfo Control;
            public SensorInfo Fan;
        }
    }

    // Minimal JSON writer (indented, culture-invariant, NaN/Infinity -> null).
    internal sealed class JsonWriter
    {
        private readonly StringBuilder _sb = new StringBuilder();
        private readonly Stack<bool> _first = new Stack<bool>();   // true = next item is first in container
        private bool _afterName;

        private void Indent()
        {
            _sb.Append('\n');
            _sb.Append(' ', _first.Count * 2);
        }

        private void Sep()
        {
            if (_afterName) { _afterName = false; return; }
            if (_first.Count == 0) return;
            if (_first.Peek()) { _first.Pop(); _first.Push(false); }
            else _sb.Append(',');
            Indent();
        }

        public void BeginObject() { Sep(); _sb.Append('{'); _first.Push(true); }
        public void EndObject() { CloseContainer('}'); }
        public void BeginArray() { Sep(); _sb.Append('['); _first.Push(true); }
        public void EndArray() { CloseContainer(']'); }

        private void CloseContainer(char c)
        {
            bool wasEmpty = _first.Pop();
            if (!wasEmpty) Indent();
            _sb.Append(c);
        }

        public void Name(string n)
        {
            Sep();
            _sb.Append(Quote(n));
            _sb.Append(": ");
            _afterName = true;
        }

        public void Value(string v) { Sep(); _sb.Append(v == null ? "null" : Quote(v)); }
        public void Value(bool v) { Sep(); _sb.Append(v ? "true" : "false"); }
        public void Value(int v) { Sep(); _sb.Append(v.ToString(CultureInfo.InvariantCulture)); }
        public void Value(float? v)
        {
            Sep();
            if (!v.HasValue || float.IsNaN(v.Value) || float.IsInfinity(v.Value)) _sb.Append("null");
            else _sb.Append(v.Value.ToString("R", CultureInfo.InvariantCulture));
        }

        public void Prop(string n, string v) { Name(n); Value(v); }
        public void Prop(string n, bool v) { Name(n); Value(v); }
        public void Prop(string n, int v) { Name(n); Value(v); }
        public void Prop(string n, float? v) { Name(n); Value(v); }
        public void PropNull(string n) { Name(n); Sep(); _sb.Append("null"); }

        private static string Quote(string s)
        {
            StringBuilder b = new StringBuilder(s.Length + 2);
            b.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': b.Append("\\\""); break;
                    case '\\': b.Append("\\\\"); break;
                    case '\n': b.Append("\\n"); break;
                    case '\r': b.Append("\\r"); break;
                    case '\t': b.Append("\\t"); break;
                    default:
                        if (c < 0x20) b.Append("\\u").Append(((int)c).ToString("x4"));
                        else b.Append(c);
                        break;
                }
            }
            b.Append('"');
            return b.ToString();
        }

        public override string ToString() { return _sb.ToString(); }
    }
}
