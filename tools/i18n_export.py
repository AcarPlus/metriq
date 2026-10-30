"""Translation helper (no GUI is started).

    python tools/i18n_export.py              # writes tools/i18n_en.json: {"strings": {key: English}, "help": {key: English}}
    python tools/i18n_export.py --check de   # compares src/lang/de.py with it (also: es ru pt)

"help" keys have no "hp_" prefix: they are the keys of HELP in src/lang/xx.py; "strings" are the keys of STRINGS.
--check prints the missing keys (they fall back to English, so that is only a progress report), extra keys and
placeholder mismatches ({name} sets must be identical, a mismatch makes metriQ show the English text). Exit code
1 when there are extra keys / placeholder problems.
"""
import importlib
import json
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "tools", "i18n_en.json")
sys.path.insert(0, SRC)


def load_english():
    """Import every src module that calls i18n.register() and return ({key: en}, {key without hp_: en})."""
    import i18n
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(SRC, fn), encoding="utf-8") as f:
            if re.search(r"\bi18n\.register\(", f.read()):
                importlib.import_module(fn[:-3])
    en = i18n._STR["en"]
    strings = {k: v for k, v in en.items() if not k.startswith("hp_")}
    help_ = {k[3:]: v for k, v in en.items() if k.startswith("hp_")}
    return dict(sorted(strings.items())), dict(sorted(help_.items()))


def placeholders(s):
    """Set of {names} of a format string, or None when it is not a valid format string."""
    try:
        return {f[1] if f[1] else "{}" for f in string.Formatter().parse(s) if f[1] is not None}
    except (ValueError, IndexError):
        return None


def export():
    strings, help_ = load_english()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"strings": strings, "help": help_}, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"{OUT}: {len(strings)} strings, {len(help_)} help texts")


def compare(label, en, tr, prefix=""):
    """Print the problems of one dict; returns the number of hard problems (extra keys, bad placeholders)."""
    bad = 0
    missing = sorted(set(en) - set(tr))
    extra = sorted(set(tr) - set(en))
    print(f"{label}: {len(tr) - len(extra)} / {len(en)} translated, {len(missing)} missing")
    if missing:
        print("  missing:", ", ".join(prefix + k for k in missing))
    for k in extra:
        bad += 1
        print(f"  EXTRA key (not in English, ignored): {prefix}{k}")
    for k in sorted(set(en) & set(tr)):
        if not isinstance(tr[k], str):
            bad += 1
            print(f"  NOT A STRING: {prefix}{k}")
            continue
        a, b = placeholders(en[k]), placeholders(tr[k])
        if b is None:
            bad += 1
            print(f"  INVALID braces in {prefix}{k}: {tr[k]!r}  (a literal brace must be written {{{{ or }}}})")
        elif a != b:
            bad += 1
            print(f"  PLACEHOLDERS {prefix}{k}: English {sorted(a)} vs translation {sorted(b)}")
    return bad


def check(code):
    strings, help_ = load_english()
    try:
        m = importlib.import_module("lang." + code)
    except ImportError as ex:
        print(f"src/lang/{code}.py cannot be imported: {ex}")
        return 2
    tr_s, tr_h = getattr(m, "STRINGS", {}), getattr(m, "HELP", {})
    print(f"language {code}: NAME = {getattr(m, 'NAME', None)!r}")
    bad = compare("STRINGS", strings, tr_s)
    bad += compare("HELP", help_, tr_h)
    print("OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Cyrillic etc. on a cp1252 console
    a = sys.argv[1:]
    if a and a[0] == "--check" and len(a) == 2:
        sys.exit(check(a[1].lower()))
    if a:
        print(__doc__)
        sys.exit(2)
    export()
