// metriQ GPU benchmark helper: Direct3D 11 compute shaders (FMA throughput + memory bandwidth), no dependencies.
// Plain COM through vtable slots (no SharpDX/Vortice), HLSL is compiled at run time with d3dcompiler_47.dll that
// ships with Windows. C# 5 compatible (the .NET Framework csc.exe). Build: gpu\build.ps1
//
//   GpuBench.exe [--seconds N] [--adapter I] [--list]
//   GpuBench.exe --stress [--adapter I] [--fault]   runs a self-checking compute load until it is killed; prints
//     {"stress":{"errors":0,"checks":123456789,"elapsed":12.3}} about once a second (--fault: corrupt on purpose, for tests)
//
// stdout is one JSON object per line:
//   {"progress":0.42,"phase":"fma"}  ...  {"result":{"adapter":"..","vram_mb":16303,"gflops":41234.5,"bandwidth_gbs":812.3}}
//   {"error":"message"}              (exit code 1)
using System;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

static class GpuBench
{
    // ---------------------------------------------------------------- native entry points
    [DllImport("d3d11.dll")]
    static extern int D3D11CreateDevice(IntPtr adapter, uint driverType, IntPtr software, uint flags, IntPtr levels,
        uint nLevels, uint sdk, out IntPtr device, out uint level, out IntPtr context);

    [DllImport("d3dcompiler_47.dll")]
    static extern int D3DCompile([MarshalAs(UnmanagedType.LPArray)] byte[] src, IntPtr size,
        [MarshalAs(UnmanagedType.LPStr)] string name, IntPtr defines, IntPtr include,
        [MarshalAs(UnmanagedType.LPStr)] string entry, [MarshalAs(UnmanagedType.LPStr)] string target,
        uint f1, uint f2, out IntPtr code, out IntPtr errors);

    [DllImport("winmm.dll")]
    static extern uint timeBeginPeriod(uint ms);

    [DllImport("dxgi.dll")]
    static extern int CreateDXGIFactory1(ref Guid riid, out IntPtr factory);

    // ---------------------------------------------------------------- COM through vtable slots
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate uint FRelease(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate IntPtr FBlobPtr(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate IntPtr FBlobSize(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FEnumAdapters1(IntPtr self, uint i, out IntPtr adapter);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FGetDesc1(IntPtr self, IntPtr desc);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FCreateBuffer(IntPtr dev, ref BufDesc d, IntPtr init, out IntPtr buf);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FCreateSrv(IntPtr dev, IntPtr res, ref SrvDesc d, out IntPtr view);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FCreateUav(IntPtr dev, IntPtr res, ref UavDesc d, out IntPtr view);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FCreateCs(IntPtr dev, IntPtr code, IntPtr size, IntPtr link, out IntPtr cs);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FCreateQuery(IntPtr dev, ref QueryDesc d, out IntPtr q);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FEnd(IntPtr ctx, IntPtr asyncObj);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FGetData(IntPtr ctx, IntPtr asyncObj, IntPtr data, uint size, uint flags);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FDispatch(IntPtr ctx, uint x, uint y, uint z);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FUpdate(IntPtr ctx, IntPtr res, uint sub, IntPtr box, IntPtr src, uint rowPitch, uint depthPitch);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FMap(IntPtr ctx, IntPtr res, uint sub, uint type, uint flags, IntPtr mapped);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FUnmap(IntPtr ctx, IntPtr res, uint sub);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FCopyRes(IntPtr ctx, IntPtr dst, IntPtr src);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FSetShader(IntPtr ctx, IntPtr cs, IntPtr inst, uint n);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FSetUavs(IntPtr ctx, uint start, uint n, IntPtr[] views, IntPtr counts);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void FSetViews(IntPtr ctx, uint start, uint n, IntPtr[] views);

    [StructLayout(LayoutKind.Sequential)] struct BufDesc { public uint ByteWidth, Usage, BindFlags, CpuAccess, Misc, Stride; }
    [StructLayout(LayoutKind.Sequential)] struct SrvDesc { public uint Format, Dim, First, Num, Pad0, Pad1; }
    [StructLayout(LayoutKind.Sequential)] struct UavDesc { public uint Format, Dim, First, Num, Flags; }
    [StructLayout(LayoutKind.Sequential)] struct QueryDesc { public uint Query, Misc; }

    static T Slot<T>(IntPtr obj, int index) where T : class
    {
        IntPtr vt = Marshal.ReadIntPtr(obj);
        IntPtr fn = Marshal.ReadIntPtr(vt, index * IntPtr.Size);
        return (T)(object)Marshal.GetDelegateForFunctionPointer(fn, typeof(T));
    }

    static void Release(IntPtr o) { if (o != IntPtr.Zero) Slot<FRelease>(o, 2)(o); }

    static void Check(int hr, string what)
    {
        if (hr < 0) throw new Exception(what + " failed (0x" + hr.ToString("X8") + ")");
    }

    // ---------------------------------------------------------------- shaders
    // Pairs of chains that multiply each other (a = a * b + k): a sequence of these cannot be folded algebraically. The
    // DXBC carries "refactoringAllowed", so a driver may legally merge a chain like a = a * m + k (uniform m, k) into
    // fewer instructions, and some do - the GFLOPS then came out several times too high on those GPUs only.
    // Every value settles near k (~1e-4): no overflow, no denormals.
    const string FmaSrc = @"
cbuffer P : register(b0) { uint iters; uint u1; uint u2; uint u3; float4 m; float4 k; };
RWStructuredBuffer<float4> o : register(u0);
[numthreads(256,1,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    float s = id.x * 1e-7f;
    float4 a = m - s, b = m * 0.9f - s, c = m * 0.8f - s, d = m * 0.7f - s;
    float4 e = m * 0.6f - s, f = m * 0.5f - s, g = m * 0.4f - s, h = m * 0.3f - s;
    for (uint i = 0; i < iters; i++)
    {
        a = a * b + k; c = c * d + k; e = e * f + k; g = g * h + k;
        b = b * a + k; d = d * c + k; f = f * e + k; h = h * g + k;
        a = a * b + k; c = c * d + k; e = e * f + k; g = g * h + k;
        b = b * a + k; d = d * c + k; f = f * e + k; h = h * g + k;
    }
    o[id.x] = a + b + c + d + e + f + g + h;
}";
    // Self-checking variant: every chain is computed twice (the second copy with constants the compiler cannot fold),
    // a mismatch bumps a counter. A healthy GPU gives identical bits, an unstable overclock does not.
    const string VerifySrc = @"
cbuffer P : register(b0) { uint iters; uint zbits; uint u2; uint u3; float4 m; float4 k; };
RWStructuredBuffer<float4> o : register(u0);
RWStructuredBuffer<uint> err : register(u1);
[numthreads(256,1,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    float z = asfloat(zbits);
    float4 m2 = m + z, k2 = k + z;
    float s = id.x * 1e-7f;
    float4 a = float4(s + 0.1f, s + 0.2f, s + 0.3f, s + 0.4f);
    float4 b = a + 0.5f, c = a + 1.5f, d = a + 2.5f;
    float4 a2 = a, b2 = b, c2 = c, d2 = d;
    for (uint i = 0; i < iters; i++)
    {
        a = a * m + k; b = b * m + k; c = c * m + k; d = d * m + k;
        a2 = a2 * m2 + k2; b2 = b2 * m2 + k2; c2 = c2 * m2 + k2; d2 = d2 * m2 + k2;
        a = a * m + k; b = b * m + k; c = c * m + k; d = d * m + k;
        a2 = a2 * m2 + k2; b2 = b2 * m2 + k2; c2 = c2 * m2 + k2; d2 = d2 * m2 + k2;
    }
    if (any(a != a2) || any(b != b2) || any(c != c2) || any(d != d2))
    {
        uint old;
        InterlockedAdd(err[0], 1, old);
    }
    o[id.x] = a + b + c + d;
}";
    const double FlopsPerThreadIter = 16 * 4 * 2;         // 16 float4 mad per iteration x 4 lanes x (mul + add)

    // One pass per dispatch: every thread group copies 1024 consecutive float4, each load / store fully coalesced.
    // (An older version looped several passes inside one dispatch over the same dst; a compiler may drop all but the
    // last pass as dead stores, which inflated the bandwidth on some GPUs.)
    const string CopySrc = @"
StructuredBuffer<float4> src : register(t0);
RWStructuredBuffer<float4> dst : register(u0);
[numthreads(256,1,1)]
void main(uint3 g : SV_GroupID, uint3 t : SV_GroupThreadID)
{
    uint i = g.x * 1024 + t.x;
    float4 a = src[i], b = src[i + 256], c = src[i + 512], d = src[i + 768];
    dst[i] = a + 1.0f; dst[i + 256] = b + 1.0f; dst[i + 512] = c + 1.0f; dst[i + 768] = d + 1.0f;
}";

    const uint Groups = 4096;                               // x 256 threads = ~1M threads per dispatch

    static IntPtr dev, ctx, query, tsDisjoint, tsStart, tsEnd; static uint groupsNow = Groups;
    static FEnd fBegin, fEnd; static FGetData fGetData; static FDispatch fDispatch; static FUpdate fUpdate;
    static FSetShader fSetShader; static FSetUavs fSetUavs; static FSetViews fSetSrvs, fSetCbs;
    static Stopwatch clock = Stopwatch.StartNew();
    static double lastProgress = -1, totalSecs, doneSecs;

    static void Json(string s) { Console.Out.WriteLine(s); Console.Out.Flush(); }

    static string Num(double v) { return v.ToString("0.###", CultureInfo.InvariantCulture); }

    static string Esc(string s)
    {
        StringBuilder b = new StringBuilder();
        foreach (char c in s)
        {
            if (c == '"' || c == '\\') b.Append('\\').Append(c);
            else if (c < 32) b.Append(' ');
            else b.Append(c);
        }
        return b.ToString();
    }

    static IntPtr Compile(string src, string target)
    {
        byte[] bytes = Encoding.ASCII.GetBytes(src);
        IntPtr code, err;
        int hr = D3DCompile(bytes, (IntPtr)bytes.Length, "bench", IntPtr.Zero, IntPtr.Zero, "main", target, 1 << 15, 0, out code, out err);
        if (hr < 0)
        {
            string msg = "";
            if (err != IntPtr.Zero)
                msg = Marshal.PtrToStringAnsi(Slot<FBlobPtr>(err, 3)(err), (int)Slot<FBlobSize>(err, 4)(err));
            throw new Exception("HLSL compile failed: " + msg);
        }
        Release(err);
        return code;
    }

    static IntPtr MakeShader(string src)
    {
        IntPtr blob = Compile(src, "cs_5_0");
        IntPtr cs;
        Check(Slot<FCreateCs>(dev, 18)(dev, Slot<FBlobPtr>(blob, 3)(blob), Slot<FBlobSize>(blob, 4)(blob), IntPtr.Zero, out cs), "CreateComputeShader");
        Release(blob);
        return cs;
    }

    static IntPtr MakeBuffer(uint bytes, uint bind, uint misc, uint stride)
    {
        BufDesc d = new BufDesc(); d.ByteWidth = bytes; d.BindFlags = bind; d.Misc = misc; d.Stride = stride;
        IntPtr b;
        Check(Slot<FCreateBuffer>(dev, 3)(dev, ref d, IntPtr.Zero, out b), "CreateBuffer");
        return b;
    }

    static IntPtr MakeUav(IntPtr buf, uint elems)
    {
        UavDesc d = new UavDesc(); d.Format = 0; d.Dim = 1; d.First = 0; d.Num = elems;
        IntPtr v;
        Check(Slot<FCreateUav>(dev, 8)(dev, buf, ref d, out v), "CreateUnorderedAccessView");
        return v;
    }

    static IntPtr MakeSrv(IntPtr buf, uint elems)
    {
        SrvDesc d = new SrvDesc(); d.Format = 0; d.Dim = 1; d.First = 0; d.Num = elems;
        IntPtr v;
        Check(Slot<FCreateSrv>(dev, 7)(dev, buf, ref d, out v), "CreateShaderResourceView");
        return v;
    }

    static void SetConstants(IntPtr cb, uint[] u, float[] f)
    {
        IntPtr p = Marshal.AllocHGlobal(48);
        for (int i = 0; i < 4; i++) Marshal.WriteInt32(p, i * 4, (int)u[i]);
        for (int i = 0; i < 8; i++) Marshal.WriteInt32(p, 16 + i * 4, BitConverter.ToInt32(BitConverter.GetBytes(f[i]), 0));
        fUpdate(ctx, cb, 0, IntPtr.Zero, p, 0, 0);
        Marshal.FreeHGlobal(p);
    }

    // Polls an async object until its data is there (S_OK); GetData with flags 0 also flushes the command buffer.
    static void Wait(IntPtr obj, IntPtr data, uint size)
    {
        while (true)
        {
            int hr = fGetData(ctx, obj, data, size, 0);
            if (hr == 0) return;
            if (hr < 0) throw new Exception("GPU device lost (0x" + hr.ToString("X8") + ")");
            Thread.Sleep(0);
        }
    }

    // Submits `batch` dispatches, waits for the GPU and returns the seconds the GPU spent on them. GPU timestamps, not
    // the wall clock: some drivers finish the wait only on the next Windows timer tick (15.6 ms), which made every
    // batch look up to that much longer - the score of those GPUs came out 15-40 % too low. Wall clock only when the
    // timestamps are unusable (disjoint, e.g. a clock change in between).
    static double RunBatch(int batch)
    {
        double t0 = clock.Elapsed.TotalSeconds;
        fBegin(ctx, tsDisjoint);
        fEnd(ctx, tsStart);
        for (int i = 0; i < batch; i++) fDispatch(ctx, groupsNow, 1, 1);
        fEnd(ctx, tsEnd);
        fEnd(ctx, tsDisjoint);
        fEnd(ctx, query);
        IntPtr buf = Marshal.AllocHGlobal(16);
        try
        {
            Wait(query, buf, 4);
            double wall = clock.Elapsed.TotalSeconds - t0;
            Wait(tsDisjoint, buf, 16);
            long freq = Marshal.ReadInt64(buf, 0);
            bool disjoint = Marshal.ReadInt32(buf, 8) != 0;
            Wait(tsStart, buf, 8);
            long a = Marshal.ReadInt64(buf, 0);
            Wait(tsEnd, buf, 8);
            long b = Marshal.ReadInt64(buf, 0);
            if (disjoint || freq <= 0 || b <= a) return wall;
            return Math.Min(wall, (double)(b - a) / freq);
        }
        finally { Marshal.FreeHGlobal(buf); }
    }

    static void Progress(string phase, double inPhase)
    {
        double f = (doneSecs + inPhase) / totalSecs;
        if (f - lastProgress >= 0.01)
        {
            lastProgress = f;
            Json("{\"progress\":" + Num(Math.Min(1.0, f)) + ",\"phase\":\"" + phase + "\"}");
        }
    }

    // Runs `secs` of batches (each ~120 ms); returns work units per second.
    static double Measure(string phase, double secs, double workPerDispatch)
    {
        RunBatch(1);                                         // warm-up
        double t = RunBatch(1);
        int batch = Math.Max(1, Math.Min(2000, (int)(0.12 / Math.Max(t, 1e-5))));
        double total = 0, work = 0, start = clock.Elapsed.TotalSeconds;
        while (clock.Elapsed.TotalSeconds - start < secs)
        {
            double dt = RunBatch(batch);
            total += dt; work += batch * workPerDispatch;
            Progress(phase, clock.Elapsed.TotalSeconds - start);
        }
        doneSecs += secs;
        return work / total;
    }

    // Runs the self-checking kernel until the parent kills the process.
    static int Stress(IntPtr cb, bool fault)
    {
        uint outElems = Groups * 256;
        IntPtr cs = MakeShader(VerifySrc);
        IntPtr outBuf = MakeBuffer(outElems * 16, 0x80, 0x40, 16);
        IntPtr outUav = MakeUav(outBuf, outElems);
        IntPtr errBuf = MakeBuffer(64, 0x80, 0x40, 4);
        IntPtr errUav = MakeUav(errBuf, 16);
        BufDesc sd = new BufDesc(); sd.ByteWidth = 64; sd.Usage = 3; sd.CpuAccess = 0x20000;
        IntPtr staging;
        Check(Slot<FCreateBuffer>(dev, 3)(dev, ref sd, IntPtr.Zero, out staging), "CreateBuffer(staging)");
        FMap fMap = Slot<FMap>(ctx, 14); FUnmap fUnmap = Slot<FUnmap>(ctx, 15); FCopyRes fCopy = Slot<FCopyRes>(ctx, 47);
        float[] fc = new float[] { 0.9999f, 0.9998f, 0.9997f, 0.9996f, 0.0001f, 0.0002f, 0.0003f, 0.0004f };
        uint zbits = fault ? (uint)BitConverter.ToInt32(BitConverter.GetBytes(1e-3f), 0) : 0u;
        fSetShader(ctx, cs, IntPtr.Zero, 0);
        fSetUavs(ctx, 0, 2, new IntPtr[] { outUav, errUav }, IntPtr.Zero);
        fSetCbs(ctx, 0, 1, new IntPtr[] { cb });
        uint iters = 64;
        for (int round = 0; round < 10; round++)
        {
            SetConstants(cb, new uint[] { iters, zbits, 0, 0 }, fc);
            RunBatch(1);
            double t = RunBatch(1);
            if (t > 0.006) break;
            iters = (uint)Math.Min(1 << 20, Math.Max(iters * 2L, (long)(iters * 0.008 / Math.Max(t, 1e-5))));
        }
        SetConstants(cb, new uint[] { iters, zbits, 0, 0 }, fc);
        double one = RunBatch(1);
        int batch = Math.Max(1, Math.Min(2000, (int)(0.12 / Math.Max(one, 1e-5))));
        IntPtr mapped = Marshal.AllocHGlobal(16);
        double start = clock.Elapsed.TotalSeconds, lastRep = 0; double checks = 0;
        while (true)
        {
            RunBatch(batch);
            checks += (double)batch * outElems;
            double now = clock.Elapsed.TotalSeconds;
            if (now - lastRep >= 1.0)
            {
                lastRep = now;
                fCopy(ctx, staging, errBuf);
                Check(fMap(ctx, staging, 0, 1, 0, mapped), "Map");
                uint errs = (uint)Marshal.ReadInt32(Marshal.ReadIntPtr(mapped), 0);
                fUnmap(ctx, staging, 0);
                Json("{\"stress\":{\"errors\":" + errs + ",\"checks\":" + Num(checks) + ",\"elapsed\":" + Num(now - start) + "}}");
            }
        }
    }

    static int Main(string[] args)
    {
        try { return Run(args); }
        catch (Exception ex)
        {
            Json("{\"error\":\"" + Esc(ex.Message) + "\"}");
            return 1;
        }
    }

    static int Run(string[] args)
    {
        double seconds = 60; int want = -1; bool list = false, stress = false, fault = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--seconds" && i + 1 < args.Length) seconds = double.Parse(args[++i], CultureInfo.InvariantCulture);
            else if (args[i] == "--adapter" && i + 1 < args.Length) want = int.Parse(args[++i]);
            else if (args[i] == "--list") list = true;
            else if (args[i] == "--stress") stress = true;
            else if (args[i] == "--fault") fault = true;
        }
        if (seconds < 2) seconds = 2;
        timeBeginPeriod(1);                                  // shorter waits for the GPU between batches

        // ---- pick the adapter with the most dedicated video memory (the discrete GPU over an integrated one, which has
        // little or none), never a software (WARP / Basic Render) or remote adapter
        Guid iidFactory = new Guid("770aae78-f26f-4dba-a829-253c83d1b387");
        IntPtr factory;
        Check(CreateDXGIFactory1(ref iidFactory, out factory), "CreateDXGIFactory1");
        IntPtr desc = Marshal.AllocHGlobal(512);
        IntPtr best = IntPtr.Zero; string bestName = ""; long bestMem = -1; int idx = 0; uint bestVendor = 0;
        for (uint i = 0; ; i++)
        {
            IntPtr ad;
            if (Slot<FEnumAdapters1>(factory, 12)(factory, i, out ad) < 0) break;
            Slot<FGetDesc1>(ad, 10)(ad, desc);
            string name = Marshal.PtrToStringUni(desc, 128);
            int z = name.IndexOf('\0'); if (z >= 0) name = name.Substring(0, z);
            uint vendor = (uint)Marshal.ReadInt32(desc, 256);
            long mem = Marshal.ReadInt64(desc, 272);
            uint flags = (uint)Marshal.ReadInt32(desc, 304);
            bool soft = (flags & 3) != 0 || vendor == 0x1414;   // DXGI_ADAPTER_FLAG_REMOTE | _SOFTWARE, Microsoft
            if (list) Json("{\"adapter\":" + i + ",\"name\":\"" + Esc(name) + "\",\"vendor\":" + vendor + ",\"vram_mb\":" + (mem >> 20) + ",\"software\":" + (soft ? "true" : "false") + "}");
            bool pick = (want >= 0) ? (i == want) : (!soft && mem > bestMem);
            if (pick) { Release(best); best = ad; bestName = name; bestMem = mem; idx = (int)i; bestVendor = vendor; }
            else Release(ad);
        }
        Release(factory);
        if (list) return 0;
        if (best == IntPtr.Zero) throw new Exception("no GPU found");

        uint level;
        Check(D3D11CreateDevice(best, 0, IntPtr.Zero, 0, IntPtr.Zero, 0, 7, out dev, out level, out ctx), "D3D11CreateDevice");
        if (level < 0xB000) throw new Exception("Direct3D feature level 11_0 is required");
        fBegin = Slot<FEnd>(ctx, 27); fEnd = Slot<FEnd>(ctx, 28); fGetData = Slot<FGetData>(ctx, 29); fDispatch = Slot<FDispatch>(ctx, 41);
        fUpdate = Slot<FUpdate>(ctx, 48); fSetShader = Slot<FSetShader>(ctx, 69); fSetUavs = Slot<FSetUavs>(ctx, 68);
        fSetSrvs = Slot<FSetViews>(ctx, 67); fSetCbs = Slot<FSetViews>(ctx, 71);
        QueryDesc qd = new QueryDesc(); qd.Query = 0;        // D3D11_QUERY_EVENT
        Check(Slot<FCreateQuery>(dev, 24)(dev, ref qd, out query), "CreateQuery");
        qd.Query = 3;                                        // D3D11_QUERY_TIMESTAMP_DISJOINT
        Check(Slot<FCreateQuery>(dev, 24)(dev, ref qd, out tsDisjoint), "CreateQuery(disjoint)");
        qd.Query = 2;                                        // D3D11_QUERY_TIMESTAMP
        Check(Slot<FCreateQuery>(dev, 24)(dev, ref qd, out tsStart), "CreateQuery(timestamp)");
        Check(Slot<FCreateQuery>(dev, 24)(dev, ref qd, out tsEnd), "CreateQuery(timestamp)");

        IntPtr cb = MakeBuffer(48, 4, 0, 0);
        if (stress) return Stress(cb, fault);

        // ---- FMA throughput
        totalSecs = seconds; doneSecs = 0;
        double fmaSecs = seconds * 0.55, bwSecs = seconds - fmaSecs;
        IntPtr csFma = MakeShader(FmaSrc);
        uint outElems = Groups * 256;
        IntPtr outBuf = MakeBuffer(outElems * 16, 0x80, 0x40, 16);
        IntPtr outUav = MakeUav(outBuf, outElems);
        float[] fc = new float[] { 0.9999f, 0.9998f, 0.9997f, 0.9996f, 0.0001f, 0.0002f, 0.0003f, 0.0004f };
        // calibrate iterations so that one dispatch takes about 8 ms (far below the 2 s driver timeout)
        uint iters = 64;
        fSetShader(ctx, csFma, IntPtr.Zero, 0);
        fSetUavs(ctx, 0, 1, new IntPtr[] { outUav }, IntPtr.Zero);
        fSetCbs(ctx, 0, 1, new IntPtr[] { cb });
        for (int round = 0; round < 10; round++)
        {
            SetConstants(cb, new uint[] { iters, 0, 0, 0 }, fc);
            RunBatch(1);
            double t = RunBatch(1);
            if (t > 0.006) break;
            iters = (uint)Math.Min(1 << 20, Math.Max(iters * 2L, (long)(iters * 0.008 / Math.Max(t, 1e-5))));
        }
        SetConstants(cb, new uint[] { iters, 0, 0, 0 }, fc);
        double flopsPerDispatch = (double)outElems * iters * FlopsPerThreadIter;
        double flops = Measure("fma", fmaSecs, flopsPerDispatch);

        // ---- memory bandwidth (copy kernel, read + write traffic)
        long bytes = Math.Min(256L << 20, Math.Max(32L << 20, bestMem / 8));
        uint elems = Groups * 256 * 4;                           // power of two, >= 64 MB
        while ((long)elems * 32 <= bytes) elems *= 2;
        groupsNow = elems / 1024;                            // one pass: every group moves 1024 float4
        IntPtr src = MakeBuffer(elems * 16, 0x88, 0x40, 16);
        IntPtr dst = MakeBuffer(elems * 16, 0x88, 0x40, 16);
        IntPtr srcSrv = MakeSrv(src, elems), dstUav = MakeUav(dst, elems);
        IntPtr csCopy = MakeShader(CopySrc);
        fSetShader(ctx, csCopy, IntPtr.Zero, 0);
        fSetUavs(ctx, 0, 1, new IntPtr[] { IntPtr.Zero }, IntPtr.Zero);
        fSetSrvs(ctx, 0, 1, new IntPtr[] { srcSrv });
        fSetUavs(ctx, 0, 1, new IntPtr[] { dstUav }, IntPtr.Zero);
        double bw = Measure("bandwidth", bwSecs, 2.0 * elems * 16);

        Json("{\"result\":{\"adapter\":\"" + Esc(bestName) + "\",\"adapter_index\":" + idx + ",\"vendor\":" + bestVendor + ",\"vram_mb\":" + (bestMem >> 20) +
             ",\"gflops\":" + Num(flops / 1e9) + ",\"bandwidth_gbs\":" + Num(bw / 1e9) + ",\"seconds\":" + Num(seconds) + "}}");
        return 0;
    }
}
