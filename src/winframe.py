"""Frameless main window that keeps every native Windows behaviour.

The Windows caption (title bar) is taken away by answering WM_NCCALCSIZE ourselves: the client area grows over the
caption, the left / right / bottom resize borders stay the standard (invisible on Windows 10 / 11) ones. The window
style is untouched, so Aero Snap, Win+arrow keys, the minimise / maximise animations, the taskbar button, Alt+Tab,
the system menu (Alt+Space) and the drop shadow all keep working. The panel draws its own title bar (titlebar.py)
and hands its mouse presses to the native move / size loop (start_move / start_size).

Everything here is Windows only and never raises: on any other platform, or when a call fails, the window simply
keeps its normal frame (Frameless.active is False).
"""
import ctypes
import sys

WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_LBUTTONUP = 0x0202
WM_SYSCOMMAND = 0x0112
WM_NCDESTROY = 0x0082
HTCAPTION, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 2, 10, 11, 12, 13, 14
WS_CAPTION = 0x00C00000
GWL_STYLE, GWLP_WNDPROC = -16, -4
SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010     # + NOSIZE NOMOVE NOZORDER NOACTIVATE
SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 33, 92
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_BORDER_COLOR = 20, 34

if sys.platform == "win32":
    from ctypes import wintypes as W
    _u32 = ctypes.WinDLL("user32")        # own instance: argtypes set here must not clash with other modules
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    _u32.CallWindowProcW.argtypes = [ctypes.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _u32.CallWindowProcW.restype = LRESULT
    _u32.SetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int, ctypes.c_void_p]
    _u32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _u32.GetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
    _u32.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _u32.SendMessageW.restype = LRESULT
    _u32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _u32.GetSystemMenu.argtypes = [W.HWND, W.BOOL]
    _u32.GetSystemMenu.restype = W.HMENU
    _u32.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p]
    _u32.TrackPopupMenu.restype = W.BOOL
    _u32.IsZoomed.argtypes = [W.HWND]

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", W.RECT * 3), ("lppos", ctypes.c_void_p)]


def _lparam(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _metric(idx, dpi):
    try:
        return _u32.GetSystemMetricsForDpi(idx, dpi)
    except Exception:
        return _u32.GetSystemMetrics(idx)


class Frameless:
    """Takes the caption away from the Tk main window `root` (Windows); see the module doc."""

    @classmethod
    def inactive(cls):
        """A stand-in with the normal Windows frame (active False), for code that runs before the real one exists."""
        f = cls.__new__(cls)
        f.root, f.active, f.hwnd, f._prev, f._cb = None, False, None, None, None
        return f

    def __init__(self, root):
        self.root = root
        self.active = False
        self.hwnd = None
        self._prev = None
        self._cb = None
        if sys.platform != "win32":
            return
        try:
            root.update_idletasks()
            self.hwnd = int(root.wm_frame(), 16)
            self._cb = WNDPROC(self._proc)                 # kept on self: the callback must outlive the window
            self._prev = _u32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, ctypes.cast(self._cb, ctypes.c_void_p))
            if not self._prev:
                self._cb = None
                return
            self.active = True
            self.reframe()
        except Exception as ex:
            print("frameless window not available:", ex, file=sys.stderr)
            self.active = False

    # ------------------------------------------------------------ window procedure
    def _proc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_NCCALCSIZE and wp:
                p = ctypes.cast(lp, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                top0 = p.rgrc[0].top
                res = _u32.CallWindowProcW(self._prev, hwnd, msg, wp, lp)
                if _u32.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_CAPTION == WS_CAPTION:
                    # the caption becomes client area; left / right / bottom keep their standard resize borders.
                    # Maximised, Windows pushes the frame off the screen edge: only that part stays cut off.
                    p.rgrc[0].top = top0 + (self.frame_y() if _u32.IsZoomed(hwnd) else 0)
                return res
            if msg == WM_NCDESTROY:
                prev = self._prev
                _u32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, prev)
                self.active = False
                return _u32.CallWindowProcW(prev, hwnd, msg, wp, lp)
        except Exception:
            pass
        return _u32.CallWindowProcW(self._prev, hwnd, msg, wp, lp)

    def frame_y(self):
        """Height of the (off-screen) top frame of a maximised window, in physical pixels."""
        try:
            dpi = _u32.GetDpiForWindow(self.hwnd)
        except Exception:
            dpi = 96
        return _metric(SM_CYSIZEFRAME, dpi) + _metric(SM_CXPADDEDBORDER, dpi)

    def reframe(self):
        """Let Windows recalculate the frame (after a style change, e.g. leaving full screen)."""
        if self.active:
            try:
                _u32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED)
            except Exception:
                pass

    # ------------------------------------------------------------ native move / size loops
    def zoomed(self):
        try:
            return bool(_u32.IsZoomed(self.hwnd))
        except Exception:
            return False

    def _nc_press(self, ht, widget, e):
        """Hand a mouse press on our title bar to Windows' own move / size loop (snap, shake and the maximise-on-
        drag-to-top all come with it). The loop swallows the button release: Tk gets one posted afterwards so it
        does not keep the pointer grabbed by the title bar."""
        if not self.active:
            return
        try:
            _u32.ReleaseCapture()
            _u32.SendMessageW(self.hwnd, WM_NCLBUTTONDOWN, ht, _lparam(e.x_root, e.y_root))
            _u32.PostMessageW(widget.winfo_id(), WM_LBUTTONUP, 0, _lparam(e.x, e.y))
        except Exception:
            pass

    def start_move(self, widget, e):
        self._nc_press(HTCAPTION, widget, e)

    def start_size(self, edge, widget, e):
        """edge: 'top', 'topleft' or 'topright' (the other edges are the window's own resize borders)."""
        self._nc_press({"top": HTTOP, "topleft": HTTOPLEFT, "topright": HTTOPRIGHT}[edge], widget, e)

    def system_menu(self, x_root, y_root):
        """The window's system menu (restore, move, size, minimise, maximise, close) at the pointer."""
        if not self.active:
            return
        try:
            menu = _u32.GetSystemMenu(self.hwnd, False)
            cmd = _u32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, int(x_root), int(y_root), 0,
                                      self.hwnd, None)
            if cmd:
                _u32.PostMessageW(self.hwnd, WM_SYSCOMMAND, cmd, 0)
        except Exception:
            pass

    # ------------------------------------------------------------ look
    def set_colors(self, dark, border_hex):
        """Dark system menu / frame for dark themes, and the Windows 11 window border in the theme's colour."""
        if not self.active:
            return
        try:
            dwm = ctypes.windll.dwmapi
            v = ctypes.c_int(1 if dark else 0)
            dwm.DwmSetWindowAttribute(W.HWND(self.hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
            h = border_hex.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            c = ctypes.c_uint(r | (g << 8) | (b << 16))                  # COLORREF 0x00BBGGRR
            dwm.DwmSetWindowAttribute(W.HWND(self.hwnd), DWMWA_BORDER_COLOR, ctypes.byref(c), 4)
        except Exception:
            pass
