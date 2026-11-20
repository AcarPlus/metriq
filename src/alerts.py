"""Alerts: "tell me when a value stays above / below a limit" (Settings > Alerts, README: Alerts).

Config ("alerts" in config.json, normalised by config.norm_alerts):
    {"enabled": bool, "notify": bool (Windows notification), "sound": bool,
     "rules": "auto" | [{"metric": minimode.metric_key(row), "name": row name, "op": ">" | "<", "value": float,
                         "secs": seconds the condition must hold}]}
"auto" = the suggested rules (CPU temperature > 90 °C, GPU temperature > 85 °C, 10 s) for the rows that exist.

Engine.update(values, now) is pure logic (no Tk, no Win32) so tools/check_alerts.py can test it:
    values = {metric key: (value, row)}   ->   list of rules that fire now
A rule fires once when its condition has held for `secs` seconds. It fires again only after the value went back past
the limit by a small margin (hysteresis) AND at least REARM_S seconds passed, so a value hovering around the limit
does not produce a stream of notifications.
"""
import time

OPS = (">", "<")
SECS = (0, 5, 10, 30, 60)             # choices of the "for at least" drop-down (seconds)
REARM_S = 300                         # the same rule notifies at most every 5 minutes
MAX_RULES = 8
SUGGESTED = (("cpu_temp", ">", 90.0, 10), ("gpu_temp", ">", 85.0, 10))


def fmt_limit(v):
    """90.0 -> "90", 1.25 -> "1.25" (the limit as the user typed it)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def hysteresis(value):
    """How far back past the limit the value must go before the rule can fire again."""
    return max(1.0, abs(float(value)) * 0.03)


def _row_kind(row):
    """'cpu_temp' / 'gpu_temp' / ... for a row of the automatic layout (any language), else None."""
    rid = getattr(row, "rid", None)
    if rid:
        return rid
    import minimode                       # (lazy: keeps this module free of Tk for the check tool)
    keys = minimode._name_keys(getattr(row, "name", ""))
    return keys[0] if keys else None


def usable(row):
    """Rows a rule can watch: plain numbers (not a state / reason code or a clock)."""
    return not (getattr(row, "special", False) or getattr(row, "textual", False))


def suggested_rules(rows):
    """The "auto" rule list for these layout rows (only the kinds that exist)."""
    import minimode
    found = {}
    for r in rows:
        if usable(r):
            k = _row_kind(r)
            if k and k not in found:
                found[k] = r
    out = []
    for kind, op, val, secs in SUGGESTED:
        r = found.get(kind)
        if r is not None:
            out.append({"metric": minimode.metric_key(r), "name": r.name, "op": op, "value": val, "secs": secs})
    return out


def resolve_rules(conf, rows):
    """The concrete rule list of an "alerts" config ("auto" -> suggested_rules)."""
    rules = conf.get("rules")
    return suggested_rules(rows) if rules == "auto" or rules is None else list(rules)


def breached(op, v, limit):
    return v > limit if op == ">" else v < limit


def recovered(op, v, limit):
    h = hysteresis(limit)
    return v <= limit - h if op == ">" else v >= limit + h


def _rid(rule):
    return (rule.get("metric"), rule.get("op"), float(rule.get("value", 0)), int(rule.get("secs", 0)))


class Engine:
    def __init__(self):
        self.rules = []
        self.state = {}               # rule id -> {"since": t | None, "fired": bool, "last": t, "v": value}

    def set_rules(self, rules):
        """New rule list; rules that did not change keep their state (a rule that already fired stays quiet)."""
        self.rules = [dict(r) for r in rules]
        ids = {_rid(r) for r in self.rules}
        self.state = {k: s for k, s in self.state.items() if k in ids}

    def update(self, values, now=None):
        """values: {metric key: (value, row)} of this tick (missing key = no reading). Returns [(rule, value, row)]
        that fire now."""
        now = time.time() if now is None else now
        fired = []
        for rule in self.rules:
            k = _rid(rule)
            s = self.state.setdefault(k, {"since": None, "fired": False, "last": -1e18, "v": None})
            got = values.get(rule.get("metric"))
            if got is None:
                s["since"], s["v"] = None, None            # no reading: nothing to judge (a fired rule stays fired)
                continue
            v, row = got
            s["v"] = v
            op, limit = rule.get("op", ">"), float(rule.get("value", 0))
            if breached(op, v, limit):
                if s["since"] is None:
                    s["since"] = now
                if (not s["fired"] and now - s["since"] >= int(rule.get("secs", 0)) - 1e-6
                        and now - s["last"] >= REARM_S):
                    s["fired"], s["last"] = True, now
                    fired.append((rule, v, row))
            else:
                s["since"] = None
                if s["fired"] and recovered(op, v, limit):
                    s["fired"] = False
        return fired

    def active(self):
        """[(rule, value)] of the rules that fired and whose value is still past the limit (for the panel notice)."""
        out = []
        for rule in self.rules:
            s = self.state.get(_rid(rule))
            if s and s["fired"] and s["v"] is not None and breached(rule.get("op", ">"), s["v"], float(rule["value"])):
                out.append((rule, s["v"]))
        return out

    def reset(self):
        self.state.clear()
