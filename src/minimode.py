"""In-game mini mode: a small semi-transparent strip with a few readings that stays on top of windowed / borderless games.

    mm = MiniMode(api, notify)        # api: the panel's API object (root, cfg, C, fonts_now, mini_rows), notify(text)
    mm.update(rows, readings, live)   # every panel tick: the values come from the panel's own rows (match / fmt_value /
                                      # value_color), only widgets whose text or colour changed are touched
    mm.toggle() / mm.show() / mm.hide() / mm.reconfigure(old_conf) / mm.shutdown()

Nothing is injected into any game: this is an ordinary borderless window of the panel's own process. It is click-through
(WS_EX_TRANSPARENT), never takes the focus (WS_EX_NOACTIVATE), has no taskbar / Alt+Tab entry (WS_EX_TOOLWINDOW) and is
re-asserted as topmost every 2 s because games push overlays down. A game in exclusive fullscreen hides it (Windows
draws such a game above everything); windowed and borderless modes work. Because the strip cannot be clicked, a global
hotkey (RegisterHotKey on a thread of its own, which only counts presses for the Tk thread) toggles it.
"""
import ctypes
import queue
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

import config
import i18n
import layout
from i18n import t

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x20, 0x80, 0x80000, 0x08000000
EX_STYLE = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

KEY_COLOR = "#010203"                 # colour key of the "transparent" background (never visible)
TOPMOST_MS = 2000                     # how often the strip is put back on top / re-placed
POLL_MS = 100                         # how often the Tk thread looks at the hotkey thread
PAD, GAP = 6, 18                      # inner margin and space between two metrics (pixels at 100 % scaling)

# hotkey choice -> (modifiers, virtual key, text shown to the user)
HOTKEYS = {
    "ctrl+shift+m": (MOD_CONTROL | MOD_SHIFT, 0x4D, "Ctrl+Shift+M"),
    "ctrl+alt+m": (MOD_CONTROL | MOD_ALT, 0x4D, "Ctrl+Alt+M"),
    "ctrl+shift+f10": (MOD_CONTROL | MOD_SHIFT, 0x79, "Ctrl+Shift+F10"),
}

# the rows picked by default (i18n keys of the automatic layout's row names), in this order
DEFAULT_PICKS = ("fps", "cpu_temp", "cpu_usage", "gpu_temp", "gpu_load", "gpu_power")
SHORT = {"fps": "FPS", "cpu_temp": "CPU", "cpu_usage": "CPU", "gpu_temp": "GPU", "gpu_load": "GPU",
         "gpu_power": "GPU"}          # short labels; the unit (°C, %, W) tells the rows of one chip apart


_NAMES = {}


def _name_keys(name):
    """i18n keys (of the default row names) that this row name equals in any language."""
    if not _NAMES:
        for k in DEFAULT_PICKS:
            for s in i18n.every(k):
                _NAMES.setdefault(s.lower(), []).append(k)
    return _NAMES.get((name or "").strip().lower(), [])


def metric_key(row):
    """What the config stores for a chosen row: its sensor / label pattern, not its name (the names of an automatic
    layout change with the language)."""
    return f"{row.sensor or ''}|{row.label}|{'' if row.rtype is None else row.rtype}"


def default_keys(rows):
    """Row keys of the default metrics that exist in `rows` (FPS, CPU temperature / usage, GPU temperature / usage /
    power), at most one row per kind."""
    found = {}
    for r in rows:
        for k in _name_keys(r.name):
            found.setdefault(k, metric_key(r))
    return [found[k] for k in DEFAULT_PICKS if k in found]


def short_label(row):
    for k in _name_keys(row.name):
        return SHORT[k]
    return row.name if len(row.name) <= 12 else row.name[:11] + "…"


# ---------------------------------------------------------------- Win32
class _Win:
    """user32 calls with explicit prototypes (a 64-bit HWND must not be squeezed into a C int)."""
    _u = None

    @classmethod
    def user32(cls):
        if cls._u is None:
            u = ctypes.WinDLL("user32")
            hw, ui, i = wintypes.HWND, wintypes.UINT, ctypes.c_int
            u.GetParent.argtypes, u.GetParent.restype = [hw], hw
            u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = [hw, i], ctypes.c_long
            u.SetWindowLongW.argtypes, u.SetWindowLongW.restype = [hw, i, ctypes.c_long], ctypes.c_long
            u.SetWindowPos.argtypes, u.SetWindowPos.restype = [hw, hw, i, i, i, i, ui], wintypes.BOOL
            u.RegisterHotKey.argtypes, u.RegisterHotKey.restype = [hw, i, ui, ui], wintypes.BOOL
            u.UnregisterHotKey.argtypes, u.UnregisterHotKey.restype = [hw, i], wintypes.BOOL
            u.GetMessageW.argtypes, u.GetMessageW.restype = [ctypes.POINTER(wintypes.MSG), hw, ui, ui], i
            u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), hw, ui, ui, ui]
            u.PeekMessageW.restype = wintypes.BOOL
            u.PostThreadMessageW.argtypes = [wintypes.DWORD, ui, wintypes.WPARAM, wintypes.LPARAM]
            u.PostThreadMessageW.restype = wintypes.BOOL
            cls._u = u
        return cls._u


def hwnd_of(win):
    """The real top-level window of a Tk toplevel: Tk's wrapper, the parent of winfo_id() (the id itself for
    overrideredirect windows is only a child of that wrapper)."""
    wid = win.winfo_id()
    return _Win.user32().GetParent(wid) or wid


def ex_style(hwnd):
    return _Win.user32().GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


def make_overlay(hwnd, frame=False):
    """Click-through, never active, no taskbar entry, topmost (without moving, resizing or activating it).
    Returns True when the extended style had to be corrected (Tk may rewrite it when window attributes change)."""
    u = _Win.user32()
    cur = ex_style(hwnd)
    want = cur | EX_STYLE
    if want != cur:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(want).value)
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | (SWP_FRAMECHANGED if (frame or want != cur) else 0)
    u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    return want != cur


class Hotkey:
    """A global hotkey. RegisterHotKey belongs to the thread that called it, so this thread registers, runs its own
    GetMessage loop and unregisters on the way out. It never touches Tk: it only queues one token per press, which
    the Tk thread collects with presses(). `state` is None until the registration was tried, then "ok" or "fail"."""
    ID = 0x6D71

    def __init__(self, combo):
        self.mods, self.vk, self.label = HOTKEYS[combo]
        self.state = None
        self._q = queue.SimpleQueue()
        self._ready = threading.Event()
        self._tid = 0
        self._th = threading.Thread(target=self._run, name="minimode-hotkey", daemon=True)
        self._th.start()

    def _run(self):
        u = _Win.user32()
        msg = wintypes.MSG()
        try:
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
            u.PeekMessageW(ctypes.byref(msg), None, 0x400, 0x400, 0)      # makes the thread's message queue exist
            ok = bool(u.RegisterHotKey(None, self.ID, self.mods | MOD_NOREPEAT, self.vk))
            self.state = "ok" if ok else "fail"
        except Exception:
            ok = False
            self.state = "fail"
        finally:
            self._ready.set()
        if not ok:
            self._tid = 0
            return
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:        # 0 = WM_QUIT, -1 = error
                if msg.message == WM_HOTKEY:
                    self._q.put(1)
        finally:
            u.UnregisterHotKey(None, self.ID)
            self._tid = 0

    def presses(self):
        """Number of key presses since the last call."""
        n = 0
        try:
            while True:
                self._q.get_nowait()
                n += 1
        except queue.Empty:
            return n

    def stop(self):
        self._ready.wait(1.0)
        tid = self._tid
        if tid:
            _Win.user32().PostThreadMessageW(tid, WM_QUIT, 0, 0)
        self._th.join(1.0)


# ---------------------------------------------------------------- the strip
class MiniMode:
    def __init__(self, api, notify):
        self.api, self.root, self.notify = api, api.root, notify
        self.win = None
        self.visible = False
        self.items = []               # one dict per metric: row, widgets and the last text / colour / unit shown
        self.hk = None
        self.hk_told = False          # the "hotkey is taken" notice was shown for the current hotkey
        self._sig = None              # ids of the layout rows the strip was built from: a change rebuilds it
        self._geo = None
        self._jobs = {"poll": None, "top": None}
        self._start_hotkey()
        self._jobs["poll"] = self.root.after(POLL_MS, self._poll)
        if self.conf["enabled"]:
            self.show()

    @property
    def conf(self):
        return self.api.cfg["minimode"]

    # ------------------------------------------------------------ hotkey
    def _start_hotkey(self):
        if self.hk is not None:
            self.hk.stop()
            self.hk = None
        self.hk_told = False
        combo = self.conf["hotkey"]
        if combo in HOTKEYS:
            self.hk = Hotkey(combo)

    def _poll(self):
        """Tk thread: act on hotkey presses and report a hotkey that could not be registered."""
        try:
            self._jobs["poll"] = self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            return
        hk = self.hk
        if hk is not None:
            if hk.state == "fail" and not self.hk_told:
                self.hk_told = True
                self.notify(t("mini_hk_taken", combo=hk.label))
            if hk.state == "ok" and hk.presses() % 2:          # (an even number of presses cancels out)
                self.toggle()

    # ------------------------------------------------------------ show / hide
    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        self.hide()
        self.visible = True
        self._create(self.api.mini_rows())
        self._schedule_top()

    def hide(self):
        self.visible = False
        if self._jobs["top"] is not None:
            try:
                self.root.after_cancel(self._jobs["top"])
            except tk.TclError:
                pass
            self._jobs["top"] = None
        win, self.win = self.win, None
        self.items = []
        self._sig = self._geo = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def reconfigure(self, old=None):
        """Settings (or theme / language) changed. `old`: the previous "minimode" dict, when the settings changed."""
        new = self.conf
        if old is None or old.get("hotkey") != new["hotkey"]:
            self._start_hotkey()
        if old is not None and old.get("enabled") != new["enabled"]:
            if new["enabled"]:                                      # the "show at start" switch was flipped: do it now
                self.show()
            else:
                self.hide()
        elif self.visible:
            self.show()

    def shutdown(self):
        for k, j in self._jobs.items():
            if j is not None:
                try:
                    self.root.after_cancel(j)
                except tk.TclError:
                    pass
                self._jobs[k] = None
        self.hide()
        if self.hk is not None:
            self.hk.stop()
            self.hk = None

    # ------------------------------------------------------------ window
    def _scale(self):
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            return 1.0

    def _resolve(self, rows):
        """[(row, short label)] of the chosen metrics that exist in the layout (explicit list, else the defaults)."""
        want = self.conf["metrics"]
        keys = default_keys(rows) if not isinstance(want, list) else want
        by_key = {metric_key(r): r for r in rows}
        return [(by_key[k], short_label(by_key[k])) for k in keys[:config.MINI_MAX_METRICS] if k in by_key]

    def _create(self, rows):
        """(Re)build the toplevel with the chosen metrics."""
        conf, C = self.conf, self.api.C
        F_TXT, _, F_NUM = self.api.fonts_now()
        sc = self._scale()
        pad, gap = int(round(PAD * sc)), int(round(GAP * sc))
        plate = conf["background"] == "plate"
        bg = C["card"] if plate else KEY_COLOR
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=bg)
        win.attributes("-alpha", min(0.996, conf["opacity"] / 100.0))    # 1.0 would drop the layered style
        if not plate:
            win.attributes("-transparentcolor", KEY_COLOR)
        win.attributes("-topmost", True)
        box = tk.Frame(win, bg=bg, highlightthickness=1 if plate else 0, highlightbackground=C["border"],
                       highlightcolor=C["border"])
        box.pack()
        pt = conf["font_size"]
        f_val = tkfont.Font(root=win, family=F_NUM, size=pt, weight="bold")
        f_lbl = tkfont.Font(root=win, family=F_TXT, size=max(7, round(pt * 0.75)))
        vertical = conf["orientation"] == "vertical"
        metrics = self._resolve(rows)
        self.items = []
        for i, (row, label) in enumerate(metrics):
            r, c0 = (i, 0) if vertical else (0, 3 * i)
            padx = (pad, 0) if vertical or i == 0 else (gap, 0)
            lbl = None
            if conf["labels"]:
                lbl = tk.Label(box, text=label, font=f_lbl, fg=C["dim"], bg=bg, bd=0, padx=0, pady=0, anchor="w")
                lbl.grid(row=r, column=c0, sticky="w", padx=padx, pady=(pad // 2, pad // 2) if vertical else pad)
            val = tk.Label(box, text="–", font=f_val, fg=C["dim"], bg=bg, bd=0, padx=0, pady=0, anchor="e", width=3)
            val.grid(row=r, column=c0 + 1, sticky="e", padx=(4, 0) if lbl else padx,
                     pady=(pad // 2, pad // 2) if vertical else pad)
            unit = tk.Label(box, text="", font=f_lbl, fg=C["dim"], bg=bg, bd=0, padx=0, pady=0, anchor="w")
            unit.grid(row=r, column=c0 + 2, sticky="w", padx=(2, pad if (vertical or i == len(metrics) - 1) else 0))
            self.items.append({"row": row, "lbl": lbl, "val": val, "unit": unit, "width": 3, "last": ("–", None, "")})
        if not metrics:
            tk.Label(box, text="…" if not rows else t("mini_empty"), font=f_lbl, fg=C["dim"], bg=bg, bd=0,
                     padx=pad, pady=pad).grid(row=0, column=0)
        self.win = win
        self._sig = tuple(map(id, rows))
        self._geo = None
        self._place()
        win.deiconify()
        win.update_idletasks()
        self._assert(frame=True)

    def _monitor(self):
        """The chosen monitor (the panel's helper): "primary", or an index."""
        mons = config.monitors()
        want = self.conf["monitor"]
        if isinstance(want, int) and 0 <= want < len(mons):
            return config.pick_monitor(want, mons)
        return next((m for m in mons if m["primary"]), mons[0] if mons else None)

    def _place(self):
        """Corner + offset on the chosen monitor, in the pixels Tk and the monitor list share (DPI-consistent)."""
        win, conf = self.win, self.conf
        if win is None:
            return
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        m = self._monitor()
        mx, my, mw, mh = (m["x"], m["y"], m["w"], m["h"]) if m else (0, 0, win.winfo_screenwidth(),
                                                                       win.winfo_screenheight())
        left, top = conf["corner"] in ("tl", "bl"), conf["corner"] in ("tl", "tr")
        x = mx + conf["x"] if left else mx + mw - w - conf["x"]
        y = my + conf["y"] if top else my + mh - h - conf["y"]
        geo = f"{w}x{h}+{x}+{y}"
        if geo != self._geo:
            self._geo = geo
            win.geometry(geo)

    def _assert(self, frame=False):
        """Put the strip back on top and repair its extended style (Tk can rewrite it)."""
        if self.win is None:
            return
        try:
            make_overlay(hwnd_of(self.win), frame)
        except (OSError, tk.TclError):
            pass

    def _schedule_top(self):
        if self._jobs["top"] is not None:
            return
        self._jobs["top"] = self.root.after(TOPMOST_MS, self._top_tick)

    def _top_tick(self):
        self._jobs["top"] = None
        if not self.visible or self.win is None:
            return
        try:
            self._place()               # a game can change the resolution / monitor layout
            self._assert()
        except tk.TclError:
            pass
        self._schedule_top()

    # ------------------------------------------------------------ values
    def update(self, rows, rd, live=True):
        """Show the current values of the chosen metrics (called by the panel on every tick).
        live=False (data paused): the last values stay."""
        if not self.visible or self.win is None:
            return
        try:
            if tuple(map(id, rows)) != self._sig:
                self.show()                                 # the layout changed (rows appeared / were edited)
            if not live:
                return
            grew = False
            for it in self.items:
                row = it["row"]
                r = row.match(rd)
                if r:
                    v = r["value"]
                    cur = (row.fmt_value(v), layout.value_color(row, v, self.api.C["fg"]), row.unit_eff)
                else:
                    cur = ("–", self.api.C["dim"], "")
                if cur == it["last"]:
                    continue
                text, col, unit = cur
                old_text, old_col, old_unit = it["last"]
                it["last"] = cur
                if len(text) > it["width"]:
                    it["width"] = len(text)
                    it["val"].config(width=it["width"])         # grows only: the strip does not jitter
                    grew = True
                if text != old_text or col != old_col:
                    it["val"].config(text=text, fg=col)
                if unit != old_unit:
                    it["unit"].config(text=unit)
                    grew = True
            if grew:
                self._place()
        except tk.TclError:
            pass
        except Exception:
            traceback.print_exc()                           # the strip must never stop the panel's tick
