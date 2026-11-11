"""Regression check: in Basic view a stale path stored on a hidden page must not block saving.

    python tools/check_settings_basic.py

Builds the settings window (hidden) against a stub panel API with a config whose presentmon_path / hwinfo_path do not
exist, then runs validate() in Basic (must pass) and in Advanced (must flag the HWiNFO page). Uses a temp data folder.
"""
import os
import sys
import tempfile
import types

os.environ["METRIQ_DIR"] = tempfile.mkdtemp(prefix="metriq_chk_")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import tkinter as tk  # noqa: E402

import config  # noqa: E402
import settings_ui  # noqa: E402
import themes  # noqa: E402


def make(mode):
    cfg, _ = config.load_config()
    cfg.update({"ui_mode": mode, "presentmon_path": r"C:\gone\PresentMon.exe", "hwinfo_path": r"C:\gone\HWiNFO64.EXE"})
    root = tk.Tk()
    root.withdraw()
    fonts = ("Segoe UI", "Segoe UI", "Consolas")
    api = types.SimpleNamespace(root=root, cfg=cfg, C=themes.palette(), fonts=fonts, fonts_now=lambda: fonts,
                                apply=lambda *a, **k: True, layout=lambda: [], readings=lambda: [],
                                monitor=lambda: 0, refresh_tasks=lambda *a: None, tasks=lambda: False,
                                detect=lambda s="auto": s, monitors=config.monitors, source=types.SimpleNamespace(via=lambda: "", notes=lambda: [], active="builtin"),
                                mini_rows=lambda: [])
    return root, settings_ui.SettingsWindow(api)


def one(mode, want):
    root, w = make(mode)
    root.update()
    got = w.validate()
    print(f"{mode}: validate() -> {got!r} (expected {want!r}) {'OK' if got == want else 'FAIL'}")
    w._closing = True
    root.destroy()
    return got == want


def main():
    if len(sys.argv) > 1:                         # child: one mode per process (uikit caches Tk fonts)
        return 0 if one(sys.argv[1], None if sys.argv[1] == "basic" else 2) else 1
    import subprocess
    return max(subprocess.call([sys.executable, __file__, m]) for m in ("basic", "advanced"))


if __name__ == "__main__":
    sys.exit(main())
