// FanWriteTest: one short, guarded write test on ONE motherboard fan header (phase 1 of fan control).
//
//   FanWriteTest.exe <controlIndex> <percent> <seconds>      (needs administrator)
//
// Safety rules (hard-coded):
//   - only Super I/O (LPC) fan controls, never GPU;
//   - control index 0 (usually CPU_FAN) is refused;
//   - percent is clamped to 30..100, seconds to 3..20;
//   - the header is ALWAYS handed back to the BIOS / automatic control (SetDefault) in finally,
//     also on Ctrl+C / errors, and Computer.Close() restores the initial state as well;
//   - aborts early if any CPU temperature exceeds 85 °C.
// Prints one line per second: elapsed, duty set, measured RPM of the paired fan sensor, CPU temp.
using System;
using System.Globalization;
using System.Threading;
using LibreHardwareMonitor.Hardware;

namespace FanTool
{
    internal static class FanWriteTest
    {
        private static ISensor _control;

        private static int Main(string[] args)
        {
            CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
            Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
            if (args.Length < 3)
            {
                Console.WriteLine("usage: FanWriteTest.exe <controlIndex 1..> <percent 30..100> <seconds 3..20>");
                return 1;
            }
            int index = int.Parse(args[0]);
            float pct = Math.Max(30f, Math.Min(100f, float.Parse(args[1])));
            int secs = Math.Max(3, Math.Min(20, int.Parse(args[2])));
            if (index <= 0)
            {
                Console.WriteLine("refused: control index 0 (CPU fan) is never tested");
                return 2;
            }

            Computer computer = new Computer();
            computer.IsMotherboardEnabled = true;
            computer.IsCpuEnabled = true;
            Console.CancelKeyPress += delegate { Restore(); };
            int rc = 0;
            try
            {
                computer.Open();
                ISensor fan = null;
                ISensor cpuTemp = null;
                foreach (IHardware hw in computer.Hardware)
                {
                    hw.Update();
                    foreach (IHardware sub in hw.SubHardware)
                    {
                        sub.Update();
                        if (sub.HardwareType != HardwareType.SuperIO) continue;
                        foreach (ISensor s in sub.Sensors)
                        {
                            if (s.SensorType == SensorType.Control && s.Index == index) _control = s;
                            if (s.SensorType == SensorType.Fan && s.Index == index) fan = s;
                        }
                    }
                    if (hw.HardwareType == HardwareType.Cpu)
                        foreach (ISensor s in hw.Sensors)
                            if (s.SensorType == SensorType.Temperature && cpuTemp == null) cpuTemp = s;
                }
                if (_control == null || _control.Control == null)
                {
                    Console.WriteLine("control " + index + " not found or not controllable");
                    return 3;
                }
                Console.WriteLine("control : " + _control.Identifier + "  (" + _control.Name + ")");
                Console.WriteLine("fan     : " + (fan == null ? "-" : fan.Identifier + "  (" + fan.Name + ")"));
                Console.WriteLine("mode    : " + _control.Control.ControlMode + "  value " + _control.Value);

                for (int t = -2; t <= secs + 4; t++)
                {
                    if (t == 0)
                    {
                        _control.Control.SetSoftware(pct);
                        Console.WriteLine("-> SetSoftware(" + pct + ")");
                    }
                    if (t == secs)
                    {
                        Restore();
                        Console.WriteLine("-> SetDefault (back to BIOS / automatic)");
                    }
                    foreach (IHardware hw in computer.Hardware)
                    {
                        hw.Update();
                        foreach (IHardware sub in hw.SubHardware) sub.Update();
                    }
                    float temp = cpuTemp != null && cpuTemp.Value.HasValue ? cpuTemp.Value.Value : 0f;
                    Console.WriteLine(string.Format("t={0,3}s  ctrl={1,6}  rpm={2,6}  cpu={3,5:0.0}C",
                        t, _control.Value, fan != null ? fan.Value : null, temp));
                    if (temp > 85f)
                    {
                        Console.WriteLine("abort: CPU temperature above 85 C");
                        rc = 4;
                        break;
                    }
                    Thread.Sleep(1000);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("error: " + ex.GetType().Name + ": " + ex.Message);
                rc = 5;
            }
            finally
            {
                Restore();
                try { computer.Close(); } catch { }
            }
            return rc;
        }

        private static void Restore()
        {
            try
            {
                if (_control != null && _control.Control != null) _control.Control.SetDefault();
            }
            catch { }
        }
    }
}
