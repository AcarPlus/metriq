"""Frame pacing locked to the display the panel is on.

Tk has no vsync: the animation loop used to run on a plain 16 ms timer, which beats against the display. On a 60 Hz
screen a frame every ~15.5 ms means about four frames a second land in the same refresh (one is never seen, the
motion jumps); on a 144 / 180 Hz screen the frames are held for 2 or 3 refreshes in turn. Both read as stutter.

Pacer gives the delay to the next frame so that frames come exactly every k refreshes (k = refresh // 60:
60 Hz -> 60 fps, 120 / 144 / 165 Hz -> 60 / 72 / 82 fps, 180 Hz -> 60 fps, 240 Hz -> 60 fps), each one shortly after
a vertical blank of that monitor: a background thread waits for the monitor's vblank (D3DKMTWaitForVerticalBlankEvent)
and only records its time, while something moves. Without vblank access the cadence still follows the monitor's
refresh rate (no lock to the blank). Windows only; elsewhere a plain 60 fps cadence.
"""
import ctypes
import os
import sys
import threading
import time

PHASE = float(os.environ.get("METRIQ_VSYNC_PHASE", "0.002"))   # s after the vblank a frame starts

if sys.platform == "win32":
    from ctypes import wintypes as W
    _u32 = ctypes.windll.user32
    _gdi = ctypes.windll.gdi32
    _u32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    _u32.MonitorFromWindow.restype = W.HMONITOR
    _gdi.CreateDCW.restype = W.HDC
    _gdi.CreateDCW.argtypes = [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p]
    _gdi.DeleteDC.argtypes = [W.HDC]

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                    ("szDevice", W.WCHAR * 32)]

    class _DEVMODEW(ctypes.Structure):
        _fields_ = [("dmDeviceName", W.WCHAR * 32), ("dmSpecVersion", W.WORD), ("dmDriverVersion", W.WORD),
                    ("dmSize", W.WORD), ("dmDriverExtra", W.WORD), ("dmFields", W.DWORD),
                    ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                    ("dmDisplayOrientation", W.DWORD), ("dmDisplayFixedOutput", W.DWORD),
                    ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", W.WCHAR * 32),
                    ("dmLogPixels", W.WORD), ("dmBitsPerPel", W.DWORD), ("dmPelsWidth", W.DWORD),
                    ("dmPelsHeight", W.DWORD), ("dmDisplayFlags", W.DWORD), ("dmDisplayFrequency", W.DWORD)]

    class _OPENADAPTERFROMHDC(ctypes.Structure):
        _fields_ = [("hDc", W.HDC), ("hAdapter", ctypes.c_uint), ("AdapterLuid", ctypes.c_ulonglong),
                    ("VidPnSourceId", ctypes.c_uint)]

    class _WAITFORVBLANK(ctypes.Structure):
        _fields_ = [("hAdapter", ctypes.c_uint), ("hDevice", ctypes.c_uint), ("VidPnSourceId", ctypes.c_uint)]

    class _CLOSEADAPTER(ctypes.Structure):
        _fields_ = [("hAdapter", ctypes.c_uint)]


    class _GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    class _OUTDESC(ctypes.Structure):
        _fields_ = [("DeviceName", W.WCHAR * 32), ("rc", W.RECT), ("att", W.BOOL), ("rot", ctypes.c_uint),
                    ("mon", W.HMONITOR)]

    def _vcall(obj, idx, restype, *argtypes):
        vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vt[idx])

    def _release(obj):
        if obj:
            try:
                _vcall(obj, 2, ctypes.c_ulong)(obj)
            except Exception:
                pass

    def _dxgi_output(dev):
        """IDXGIOutput of the monitor `dev` (its GDI device name), or None. Its WaitForVBlank waits for THAT monitor's
        refresh; D3DKMTWaitForVerticalBlankEvent answered with the primary monitor's blanks on a mixed 180 / 60 Hz
        setup, so frames could not be locked to a 60 Hz side monitor."""
        try:
            iid = _GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
            f = ctypes.c_void_p()
            if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(f)) != 0:
                return None
        except Exception:
            return None
        found = None
        try:
            i = 0
            while found is None:
                ad = ctypes.c_void_p()
                if _vcall(f, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(f, i, ctypes.byref(ad)):
                    break                                       # EnumAdapters1: no more adapters
                j = 0
                while found is None:
                    o = ctypes.c_void_p()
                    if _vcall(ad, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(ad, j, ctypes.byref(o)):
                        break                                   # EnumOutputs: no more outputs
                    d = _OUTDESC()
                    _vcall(o, 7, ctypes.c_long, ctypes.POINTER(_OUTDESC))(o, ctypes.byref(d))
                    if d.DeviceName == dev:
                        found = o
                    else:
                        _release(o)
                    j += 1
                _release(ad)
                i += 1
        except Exception:
            found = None
        _release(f)
        return found


def _monitor_of(hwnd):
    """(monitor handle, device name, refresh Hz) of the monitor that holds most of the window."""
    mon = _u32.MonitorFromWindow(hwnd, 2)                    # MONITOR_DEFAULTTONEAREST
    mi = _MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if not _u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return mon, None, 60.0
    dm = _DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    hz = 60.0
    if _u32.EnumDisplaySettingsW(mi.szDevice, -1, ctypes.byref(dm)) and dm.dmDisplayFrequency > 1:
        hz = float(dm.dmDisplayFrequency)
        if hz in (59.0, 119.0, 143.0, 239.0):                   # 59.94 Hz style modes are reported rounded down
            hz = (hz + 1) * 1000 / 1001
    return mon, mi.szDevice, hz


class Pacer:
    def __init__(self, hwnd=None):
        self.hwnd = hwnd
        self.hz = 60.0
        self.mon = None
        self.dev = None
        self._vb = None                 # perf_counter time of the last vblank seen by the thread
        self._vb_period = None          # measured refresh period (s)
        self._next = None               # time slot of the next frame
        self._want = threading.Event()  # set while frames run
        self._thread = None
        self._lock = threading.Lock()
        self._ok = sys.platform == "win32"
        self.refresh_monitor()

    # ------------------------------------------------------------ monitor
    def refresh_monitor(self):
        if not self._ok or not self.hwnd:
            return
        try:
            mon, dev, hz = _monitor_of(self.hwnd)
        except Exception:
            return
        if mon != self.mon or hz != self.hz:
            with self._lock:
                self.mon, self.dev, self.hz = mon, dev, hz
                self._vb, self._vb_period = None, None

    def info(self):
        """Diagnostics (METRIQ_FXPROF): refresh rate, frames per refresh, vblank lock."""
        vb = self._vb
        return "hz %.2f k %d period %.3f ms vblank %s" % (self.hz, self.frame_k(), self.refresh_period() * 1000,
                                                       "locked" if vb and time.perf_counter() - vb < 0.5 else "none")

    def refresh_period(self):
        return self._vb_period or 1.0 / self.hz

    def frame_k(self):
        return max(1, int((self.hz + 3) // 60))

    # ------------------------------------------------------------ frames
    def start(self):
        """An animation starts after a still period: look at the monitor again, start the vblank watch."""
        self.refresh_monitor()
        self._next = None
        if self._ok and self.dev:
            self._want.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="vsync", daemon=True)
                self._thread.start()

    def stop(self):
        self._want.clear()
        self._next = None

    def delay_ms(self, now=None):
        """Milliseconds from now to the next frame slot (call at the end of a frame)."""
        now = time.perf_counter() if now is None else now
        P = self.refresh_period()
        F = P * self.frame_k()
        nxt = (self._next + F) if self._next is not None else now + F
        vb = self._vb
        if vb is not None and now - vb < 0.5:
            n = round((nxt - vb - PHASE) / P)              # snap to the vblank grid of that monitor
            nxt = vb + n * P + PHASE
        while nxt < now + 0.0015:                          # a frame was missed: the next refresh, not a burst
            nxt += P
        self._next = nxt
        return max(1, int(round((nxt - now) * 1000)))

    # ------------------------------------------------------------ vblank thread
    def _blank(self, last, periods):
        """A vertical blank just happened: remember its time, measure the real refresh period."""
        t = time.perf_counter()
        if last is not None and self._want.is_set():
            d = t - last
            if 0.5 / self.hz < d < 1.5 / self.hz:            # a real refresh interval (not a pause)
                periods.append(d)
                if len(periods) >= 30:
                    periods.sort()
                    self._vb_period = periods[len(periods) // 2]
                    periods.clear()
        self._vb = t

    def _run(self):
        try:
            gdi = ctypes.windll.gdi32
            wait = gdi.D3DKMTWaitForVerticalBlankEvent
            open_ = gdi.D3DKMTOpenAdapterFromHdc
            close = gdi.D3DKMTCloseAdapter
        except Exception:
            self._ok = False
            return
        adapter, dev, out, out_wait = None, None, None, None
        last, periods = None, []
        try:
            while True:
                self._want.wait()
                if dev != self.dev:                        # first run, or the window moved to another monitor
                    if adapter is not None:
                        close(ctypes.byref(_CLOSEADAPTER(adapter.hAdapter)))
                        adapter = None
                    _release(out)
                    out, out_wait = None, None
                    dev = self.dev
                    out = _dxgi_output(dev)                # preferred: the blanks of exactly this monitor
                    if out:
                        out_wait = _vcall(out, 10, ctypes.c_long)
                        last, periods = None, []
                if out_wait is not None:
                    if out_wait(out) != 0:
                        _release(out)
                        out, out_wait, dev = None, None, None   # (re-opened on the next round)
                        time.sleep(0.05)
                        continue
                    self._blank(last, periods)
                    last = self._vb
                    if not self._want.is_set():
                        last = None
                    continue
                if adapter is None:
                    hdc = _gdi.CreateDCW(dev, dev, None, None)
                    if not hdc:
                        self._ok = False
                        return
                    oa = _OPENADAPTERFROMHDC()
                    oa.hDc = hdc
                    r = open_(ctypes.byref(oa))
                    _gdi.DeleteDC(hdc)
                    if r != 0:
                        self._ok = False
                        return
                    adapter = oa
                    last, periods = None, []
                wv = _WAITFORVBLANK(adapter.hAdapter, 0, adapter.VidPnSourceId)
                if wait(ctypes.byref(wv)) != 0:
                    self._ok = False
                    return
                self._blank(last, periods)
                last = self._vb
                if not self._want.is_set():
                    last = None
        except Exception:
            self._ok = False
        finally:
            _release(out)
            if adapter is not None:
                try:
                    close(ctypes.byref(_CLOSEADAPTER(adapter.hAdapter)))
                except Exception:
                    pass
