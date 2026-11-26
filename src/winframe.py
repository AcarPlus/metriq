"""Main window without the Windows caption, with every native behaviour kept - and NO Python window procedure.

The caption style (WS_CAPTION) is taken off the Tk main window; the resizable frame (WS_THICKFRAME), the system
menu and the minimise / maximise boxes stay, so Aero Snap, Win+arrow keys, the minimise / maximise animations, the
taskbar button, Alt+Tab, the system menu and the shadow keep working. What is left of the frame at the top is the
native resize border (a few px, only while not maximised); Windows 11 paints it in the caption colour, which
set_colors makes the panel's own top colour, so it reads as part of the panel.

Why no window procedure (an earlier version subclassed the window to answer WM_NCCALCSIZE): every Windows message
then passed through a Python ctypes callback, and Windows' modal move / size loops ran under it (and under the Tk
binding that started a move with SendMessage). Tk keeps serving its timers inside those loops, the nested Tcl ->
Python calls found tkinter's thread state in the wrong place and Python aborted (0xc0000409 in ucrtbase): metriQ
closed when its window was dragged. Here nothing of ours is on the stack during those loops:
* a move is started by POSTING WM_NCLBUTTONDOWN / HTCAPTION, so the loop runs from Tk's own message dispatch,
  exactly as for a normal title bar;
* the system menu is posted as well (WM_SYSMENU, what Windows itself sends for Alt+Space / right-click on a caption).
Tk tracks the real client area itself (coordinates stay right); leaving full screen, Tk puts the caption back, so
ensure() is called after every state change.

Windows only; never raises. Elsewhere, or when a call fails, the window keeps its normal frame (active False).
"""
import ctypes
import sys

WS_CAPTION = 0x00C00000
GWL_STYLE = -16
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSMENU = 0x0313
HTCAPTION, HTTOP = 2, 12
SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010     # + NOSIZE NOMOVE NOZORDER NOACTIVATE
SWP_FRAMECHANGED_MOVE = 0x0020 | 0x0004 | 0x0010                  # FRAMECHANGED + NOZORDER NOACTIVATE (moves, sizes)
WS_THICKFRAME = 0x00040000
DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR = 20, 34, 35

if sys.platform == "win32":
    from ctypes import wintypes as W
    _u32 = ctypes.WinDLL("user32")        # own instance: argtypes set here must not clash with other modules
    _u32.GetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
    _u32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _u32.IsZoomed.argtypes = [W.HWND]
    try:
        _dwm = ctypes.WinDLL("dwmapi")
    except OSError:
        _dwm = None


def _lparam(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _colorref(hex_):
    h = hex_.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return ctypes.c_uint(r | (g << 8) | (b << 16))                 # COLORREF 0x00BBGGRR


class Frameless:
    """Takes the caption away from the Tk main window `root` (Windows); see the module doc."""

    @classmethod
    def inactive(cls):
        """A stand-in with the normal Windows frame (active False), for code that runs before the real one exists."""
        f = cls.__new__(cls)
        f.root, f.active, f._colors = None, False, None
        return f

    def __init__(self, root):
        self.root = root
        self.active = False
        self._colors = None
        if sys.platform != "win32":
            return
        try:
            root.update_idletasks()
            self.active = True
            self.ensure()
        except Exception as ex:
            print("frameless window not available:", ex, file=sys.stderr)
            self.active = False

    @property
    def hwnd(self):
        """The window's frame handle, read when needed (Tk may give the window a new one)."""
        try:
            return int(self.root.wm_frame(), 16)
        except Exception:
            return None

    def ensure(self):
        """Caption off (again): at start and after every state change (Tk puts it back when full screen ends)."""
        if not self.active:
            return
        try:
            h = self.hwnd
            st = _u32.GetWindowLongPtrW(h, GWL_STYLE)
            if st & WS_CAPTION:
                _u32.SetWindowLongPtrW(h, GWL_STYLE, st & ~WS_CAPTION)
                _u32.SetWindowPos(h, None, 0, 0, 0, 0, SWP_FRAMECHANGED)
                if self._colors:
                    self.set_colors(*self._colors)
        except Exception:
            pass

    reframe = ensure

    def zoomed(self):
        try:
            return bool(_u32.IsZoomed(self.hwnd))
        except Exception:
            return False

    def fullscreen(self, on):
        """Full screen on the monitor the window is on: no frame at all, the whole monitor (taskbar included).
        Off: the resizable frame comes back (the caller restores the size / maximised state). Returns on."""
        if not self.active:
            return False
        try:
            h = self.hwnd
            st = _u32.GetWindowLongPtrW(h, GWL_STYLE)
            if on:
                rect = monitor_rect(h)
                if not rect:
                    return False
                self._fs_style = st
                _u32.SetWindowLongPtrW(h, GWL_STYLE, st & ~(WS_CAPTION | WS_THICKFRAME))
                x0, y0, x1, y1 = rect
                _u32.SetWindowPos(h, None, x0, y0, x1 - x0, y1 - y0, SWP_FRAMECHANGED_MOVE)
                return True
            back = getattr(self, "_fs_style", st | WS_THICKFRAME)
            _u32.SetWindowLongPtrW(h, GWL_STYLE, back & ~WS_CAPTION)
            _u32.SetWindowPos(h, None, 0, 0, 0, 0, SWP_FRAMECHANGED)
            return False
        except Exception:
            return False

    # ------------------------------------------------------------ native move loop / system menu (posted, see doc)
    def start_move(self, widget, e):
        """A press on the panel's title row: Windows' own move loop (Aero Snap, drag-to-top maximise, shake)."""
        if not self.active:
            return
        try:
            _u32.ReleaseCapture()
            _u32.PostMessageW(self.hwnd, WM_NCLBUTTONDOWN, HTCAPTION, _lparam(e.x_root, e.y_root))
        except Exception:
            pass

    def system_menu(self, x_root, y_root):
        """The window's system menu (restore, move, size, minimise, maximise, close) at the pointer."""
        if not self.active:
            return
        try:
            _u32.PostMessageW(self.hwnd, WM_SYSMENU, 0, _lparam(x_root, y_root))
        except Exception:
            pass

    # ------------------------------------------------------------ look
    def set_colors(self, dark, border_hex, top_hex=None):
        """Dark frame / system menu for dark themes, the Windows 11 border in the theme's colour, and the caption
        colour (the thin native resize border at the top) in the panel's top colour."""
        if not self.active or _dwm is None:
            return
        self._colors = (dark, border_hex, top_hex)
        try:
            h = W.HWND(self.hwnd)
            v = ctypes.c_int(1 if dark else 0)
            _dwm.DwmSetWindowAttribute(h, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
            c = _colorref(border_hex)
            _dwm.DwmSetWindowAttribute(h, DWMWA_BORDER_COLOR, ctypes.byref(c), 4)
            if top_hex:
                c2 = _colorref(top_hex)
                _dwm.DwmSetWindowAttribute(h, DWMWA_CAPTION_COLOR, ctypes.byref(c2), 4)
        except Exception:
            pass


def monitor_rect(hwnd):
    """(left, top, right, bottom) of the whole monitor that holds most of the window, or None."""
    if sys.platform != "win32":
        return None
    try:
        class _MI(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]
        _u32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        _u32.MonitorFromWindow.restype = W.HMONITOR
        _u32.GetMonitorInfoW.argtypes = [W.HMONITOR, ctypes.c_void_p]
        mi = _MI()
        mi.cbSize = ctypes.sizeof(mi)
        if not _u32.GetMonitorInfoW(_u32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None
