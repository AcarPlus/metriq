"""System tray icon and single instance for the panel (all Win32 through ctypes).

    inst = tray.acquire_instance()      # named mutex; None when another panel of this data folder already runs
    tray.signal_existing()              # ... then ask that one to show its panel (the caller exits afterwards)
    tr = tray.Tray(ico_path)            # message window + icon on a thread of their own; the icon starts hidden
    tr.set_visible(True, tip, items)    # Tk thread: show the icon (True when Explorer accepted it)
    tr.set_info(tip, items)             # new tooltip / menu: items = [(command, text, checked)], command "-" = separator
    tr.balloon(title, text)             # one notification next to the icon
    tr.drain()                          # Tk thread, polled with after(): commands since the last call
    tr.close()                          # removes the icon (NIM_DELETE), ends the thread

The tray thread never touches Tk: a click, a menu choice or a "show yourself" signal from a second launch only puts a
command word ("show", "mini", "stoplog", "exit", "quit") into a queue, which the Tk thread collects (the same pattern as
the hotkey thread of minimode.py). The hidden message window is an ordinary top-level window (not message-only), so it
also receives the "TaskbarCreated" broadcast and the icon is added again when Explorer restarts.
"""
import ctypes
import hashlib
import os
import queue
import threading
import time
import traceback
from ctypes import wintypes

import config

# ---------------------------------------------------------------- constants
ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED = 183, 5
WM_NULL, WM_CLOSE, WM_DESTROY = 0x0000, 0x0010, 0x0002
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
WM_APP = 0x8000
WM_ICON, WM_SHOW, WM_SYNC, WM_BALLOON, WM_QUIT_TRAY = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4, WM_APP + 5
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN, TPM_RETURNCMD = 0x2, 0x20, 0x100
IMAGE_ICON, LR_LOADFROMFILE, SM_CXSMICON = 1, 0x10, 49
SMTO_ABORTIFHUNG = 0x2
ICON_ID = 1


def _suffix():
    """A separate data folder (METRIQ_DIR: tests, portable copies) is a separate instance."""
    d = config.env_dir()
    return "." + hashlib.sha1(os.path.normcase(os.path.abspath(d)).encode("utf-8", "replace")).hexdigest()[:8] if d else ""


def mutex_name():
    return "Local\\metriQ.panel" + _suffix()


def class_name():
    return "metriQ.PanelTray" + _suffix()


# ---------------------------------------------------------------- Win32
class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD), ("guidItem", _GUID),
                ("hBalloonIcon", wintypes.HANDLE)]


_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HANDLE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


_api = None


def _win():
    """(kernel32, user32, shell32) with explicit prototypes (a 64-bit handle must not be squeezed into a C int)."""
    global _api
    if _api is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u = ctypes.WinDLL("user32", use_last_error=True)
        s = ctypes.WinDLL("shell32", use_last_error=True)
        hw, ui, i, ptr = wintypes.HWND, wintypes.UINT, ctypes.c_int, ctypes.c_void_p
        k.CreateMutexW.argtypes, k.CreateMutexW.restype = [ptr, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
        k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HANDLE
        u.RegisterClassW.argtypes, u.RegisterClassW.restype = [ctypes.POINTER(WNDCLASSW)], wintypes.ATOM
        u.UnregisterClassW.argtypes, u.UnregisterClassW.restype = [wintypes.LPCWSTR, wintypes.HANDLE], wintypes.BOOL
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, i, i, i, i,
                                      hw, wintypes.HANDLE, wintypes.HANDLE, ptr]
        u.CreateWindowExW.restype = hw
        u.DestroyWindow.argtypes, u.DestroyWindow.restype = [hw], wintypes.BOOL
        u.DefWindowProcW.argtypes = [hw, ui, wintypes.WPARAM, wintypes.LPARAM]
        u.DefWindowProcW.restype = _LRESULT
        u.GetMessageW.argtypes, u.GetMessageW.restype = [ctypes.POINTER(wintypes.MSG), hw, ui, ui], i
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes, u.DispatchMessageW.restype = [ctypes.POINTER(wintypes.MSG)], _LRESULT
        u.PostMessageW.argtypes, u.PostMessageW.restype = [hw, ui, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL
        u.PostQuitMessage.argtypes = [i]
        u.SendMessageTimeoutW.argtypes = [hw, ui, wintypes.WPARAM, wintypes.LPARAM, ui, ui,
                                          ctypes.POINTER(ctypes.c_size_t)]
        u.SendMessageTimeoutW.restype = _LRESULT
        u.RegisterWindowMessageW.argtypes, u.RegisterWindowMessageW.restype = [wintypes.LPCWSTR], ui
        u.CreatePopupMenu.restype = wintypes.HANDLE
        u.AppendMenuW.argtypes, u.AppendMenuW.restype = [wintypes.HANDLE, ui, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL
        u.SetMenuDefaultItem.argtypes, u.SetMenuDefaultItem.restype = [wintypes.HANDLE, ui, ui], wintypes.BOOL
        u.TrackPopupMenu.argtypes = [wintypes.HANDLE, ui, i, i, i, hw, ptr]
        u.TrackPopupMenu.restype = wintypes.BOOL
        u.DestroyMenu.argtypes, u.DestroyMenu.restype = [wintypes.HANDLE], wintypes.BOOL
        u.GetCursorPos.argtypes, u.GetCursorPos.restype = [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL
        u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = [hw], wintypes.BOOL
        u.FindWindowW.argtypes, u.FindWindowW.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], hw
        u.GetWindowThreadProcessId.argtypes, u.GetWindowThreadProcessId.restype = [hw, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD
        u.AllowSetForegroundWindow.argtypes, u.AllowSetForegroundWindow.restype = [wintypes.DWORD], wintypes.BOOL
        u.LoadImageW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ui, i, i, ui]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadIconW.argtypes, u.LoadIconW.restype = [wintypes.HANDLE, ctypes.c_void_p], wintypes.HANDLE
        u.DestroyIcon.argtypes, u.DestroyIcon.restype = [wintypes.HANDLE], wintypes.BOOL
        u.GetSystemMetrics.argtypes, u.GetSystemMetrics.restype = [i], i
        s.Shell_NotifyIconW.argtypes, s.Shell_NotifyIconW.restype = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)], wintypes.BOOL
        _api = (k, u, s)
    return _api


# ---------------------------------------------------------------- single instance
_mutex = None


def acquire_instance():
    """Creates the named mutex of this panel. True: this process is the only panel (it keeps the mutex until
    release_instance() / process end). False: another panel already runs (or its mutex cannot be opened)."""
    global _mutex
    k = _win()[0]
    ctypes.set_last_error(0)
    h = k.CreateMutexW(None, False, mutex_name())
    err = ctypes.get_last_error()
    if not h:
        return err != ERROR_ACCESS_DENIED          # access denied: it belongs to a panel of another privilege level
    if err == ERROR_ALREADY_EXISTS:
        k.CloseHandle(h)
        return False
    _mutex = h
    return True


def release_instance():
    """Lets go of the mutex at once (the process is about to end: a new launch must not be turned away)."""
    global _mutex
    h, _mutex = _mutex, None
    if h:
        _win()[0].CloseHandle(h)


def signal_existing(timeout=3.0):
    """Asks the running panel (found by the class name of its tray window) to show itself. The window can appear a
    moment after the mutex, so it is looked for up to `timeout` seconds. True when the message was delivered."""
    u = _win()[1]
    end = time.time() + timeout
    while True:
        hwnd = u.FindWindowW(class_name(), None)
        if hwnd:
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                u.AllowSetForegroundWindow(pid.value)       # this process was just started by the user: hand that right on
            return bool(u.PostMessageW(hwnd, WM_SHOW, 0, 0))
        if time.time() >= end:
            return False
        time.sleep(0.1)


# ---------------------------------------------------------------- the tray icon
class Tray:
    def __init__(self, ico_path):
        self.ico = ico_path
        self.cmds = queue.SimpleQueue()
        self.hwnd = 0
        self.state = None                   # None until the window exists, then "ok" or "fail"
        self.added = False                  # the icon is in the notification area right now (tray thread writes)
        self.results = []                   # (operation, success) of the last Shell_NotifyIcon calls, for diagnostics
        self._lock = threading.Lock()
        self._want, self._tip, self._items, self._info = False, "", [], None
        self._icon = None
        self._wm_taskbar = 0
        self._ready = threading.Event()
        self._th = threading.Thread(target=self._run, name="tray", daemon=True)
        self._th.start()
        self._ready.wait(3.0)

    # ------------------------------------------------------------ Tk thread API
    @property
    def ok(self):
        return self.state == "ok" and bool(self.hwnd)

    def drain(self):
        out = []
        try:
            while True:
                out.append(self.cmds.get_nowait())
        except queue.Empty:
            return out

    def set_info(self, tip, items):
        with self._lock:
            self._tip, self._items = tip, list(items)
        self._post(WM_SYNC)

    def set_visible(self, visible, tip=None, items=None):
        """Show / remove the icon. Returns True when the icon is in place (or gone, for visible=False)."""
        with self._lock:
            self._want = bool(visible)
            if tip is not None:
                self._tip = tip
            if items is not None:
                self._items = list(items)
        if not self.ok:
            return False
        u = _win()[1]
        res = ctypes.c_size_t(0)
        # synchronous (the tray thread is idle in GetMessage): the caller needs to know before it hides the panel
        if not u.SendMessageTimeoutW(self.hwnd, WM_SYNC, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(res)):
            return False
        return self.added if visible else not self.added

    def balloon(self, title, text):
        with self._lock:
            self._info = (title, text)
        self._post(WM_BALLOON)

    def _post(self, msg):
        if self.ok:
            _win()[1].PostMessageW(self.hwnd, msg, 0, 0)

    def close(self):
        """NIM_DELETE + end of the thread (the icon of a killed process would stay in the area until the mouse
        moves over it)."""
        if self.ok:
            u = _win()[1]
            res = ctypes.c_size_t(0)
            u.SendMessageTimeoutW(self.hwnd, WM_QUIT_TRAY, 0, 0, SMTO_ABORTIFHUNG, 1500, ctypes.byref(res))
        self._th.join(1.5)

    # ------------------------------------------------------------ tray thread
    def _run(self):
        k, u, s = _win()
        cls = class_name()
        wc = None
        try:
            self._proc = _WNDPROC(self._wndproc)            # kept alive by the instance
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, cls
            if not u.RegisterClassW(ctypes.byref(wc)):
                raise OSError("RegisterClassW failed (%d)" % ctypes.get_last_error())
            self._wm_taskbar = u.RegisterWindowMessageW("TaskbarCreated")
            self._icon = self._load_icon()
            hwnd = u.CreateWindowExW(0, cls, "metriQ tray", 0, 0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                raise OSError("CreateWindowExW failed (%d)" % ctypes.get_last_error())
            self.hwnd = hwnd
            self.state = "ok"
        except Exception:
            traceback.print_exc()
            self.state = "fail"
        finally:
            self._ready.set()
        if self.state != "ok":
            return
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:      # 0 = WM_QUIT, -1 = error
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        finally:
            self.hwnd = 0
            if self._icon:
                u.DestroyIcon(self._icon)
            u.UnregisterClassW(cls, hinst)

    def _load_icon(self):
        u = _win()[1]
        n = u.GetSystemMetrics(SM_CXSMICON) or 16
        h = u.LoadImageW(None, self.ico, IMAGE_ICON, n, n, LR_LOADFROMFILE) if os.path.isfile(self.ico) else None
        return h or u.LoadIconW(None, 32512)                              # IDI_APPLICATION

    def _wndproc(self, hwnd, msg, wp, lp):
        u = _win()[1]
        try:
            if msg == WM_ICON:
                ev = lp & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.cmds.put("show")
                elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu(hwnd)
                return 0
            if msg == WM_SHOW:                  # a second launch asks this panel to show itself
                self.cmds.put("show")
                return 0
            if msg == WM_SYNC:
                self._sync()
                return 0
            if msg == WM_BALLOON:
                self._show_balloon()
                return 0
            if msg == WM_CLOSE:                 # taskkill / Setup: leave like any other exit, no questions asked
                self.cmds.put("quit")
                return 0
            if msg == WM_QUIT_TRAY:
                self._notify(NIM_DELETE)
                self.added = False
                u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            if self._wm_taskbar and msg == self._wm_taskbar:      # Explorer (re)started: the icons are gone
                with self._lock:
                    want = self._want
                if want:
                    self._notify(NIM_DELETE)                        # (an icon that is still there would make the add fail)
                    self.added = False
                    self._sync()
                return 0
        except Exception:
            traceback.print_exc()
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    def _data(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID, nid.uFlags = self.hwnd, ICON_ID, flags
        return nid

    def _notify(self, op, tip=None, info=None):
        """One Shell_NotifyIconW call; the result is remembered in self.results."""
        s = _win()[2]
        flags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid = self._data(flags if op != NIM_DELETE else 0)
        if op != NIM_DELETE:
            nid.uCallbackMessage, nid.hIcon = WM_ICON, self._icon
            nid.szTip = (tip or "")[:127]
        if info:
            nid.uFlags |= NIF_INFO
            nid.szInfoTitle, nid.szInfo, nid.dwInfoFlags = info[0][:63], info[1][:255], NIIF_INFO
        ok = bool(s.Shell_NotifyIconW(op, ctypes.byref(nid)))
        self.results = (self.results + [({NIM_ADD: "add", NIM_MODIFY: "modify", NIM_DELETE: "delete"}[op], ok)])[-20:]
        return ok

    def _sync(self):
        """Bring the icon in line with what the Tk thread asked for (add / update the tooltip / remove)."""
        with self._lock:
            want, tip = self._want, self._tip
        if want and not self.added:
            self.added = self._notify(NIM_ADD, tip)
        elif want:
            if not self._notify(NIM_MODIFY, tip):
                self.added = self._notify(NIM_ADD, tip)            # the icon was lost: add it again
        elif self.added:
            self._notify(NIM_DELETE)
            self.added = False

    def _show_balloon(self):
        with self._lock:
            info, tip = self._info, self._tip
        if info and self.added:
            self._notify(NIM_MODIFY, tip, info)

    def _menu(self, hwnd):
        k, u, s = _win()
        with self._lock:
            items = list(self._items)
        if not items:
            return
        hm = u.CreatePopupMenu()
        if not hm:
            return
        try:
            for i, (cmd, text, checked) in enumerate(items, 1):
                if cmd == "-":
                    u.AppendMenuW(hm, MF_SEPARATOR, 0, None)
                else:
                    u.AppendMenuW(hm, MF_STRING | (MF_CHECKED if checked else 0), i, text)
            u.SetMenuDefaultItem(hm, 1, 0)                          # the first entry (show) in bold
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            u.SetForegroundWindow(hwnd)                             # else the menu does not close on a click elsewhere
            sel = u.TrackPopupMenu(hm, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, pt.x, pt.y, 0, hwnd, None)
            u.PostMessageW(hwnd, WM_NULL, 0, 0)
            if sel and 1 <= sel <= len(items):
                self.cmds.put(items[sel - 1][0])
        finally:
            u.DestroyMenu(hm)
