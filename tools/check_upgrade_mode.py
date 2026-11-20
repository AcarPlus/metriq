"""Check of the view-mode upgrade rule (temp data folders only, never your real config):

    no config.json                    -> created, ui_mode "basic"   (fresh install)
    config.json without ui_mode       -> "advanced", written back   (upgrade keeps the familiar view)
    config.json with ui_mode          -> kept as is
    unreadable config.json            -> "advanced" (it is an existing install), file backed up
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config  # noqa: E402

fails = 0


def case(name, content, want, want_written=None):
    global fails
    d = tempfile.mkdtemp(prefix="metriq_up_")
    os.environ["METRIQ_DIR"] = d
    p = os.path.join(d, "config.json")
    if content is not None:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    cfg, _ = config.load_config()
    got = cfg["ui_mode"]
    written = None
    try:
        with open(p, encoding="utf-8") as f:
            written = json.load(f).get("ui_mode")
    except (OSError, ValueError):
        pass
    ok = got == want and (want_written is None or written == want_written)
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name}: loaded {got!r}, file has {written!r}")


case("fresh install (no file)", None, "basic", "basic")
case("old config without ui_mode", json.dumps({"interval": 2}), "advanced", "advanced")
case("config with ui_mode basic", json.dumps({"ui_mode": "basic"}), "basic", "basic")
case("config with ui_mode advanced", json.dumps({"ui_mode": "advanced"}), "advanced", "advanced")
case("unreadable config", "{not json", "advanced")
sys.exit(1 if fails else 0)
