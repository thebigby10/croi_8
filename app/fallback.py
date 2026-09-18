"""Regex/rule-based backup classifier for when the LLM call fails or is
unavailable. This is the intended degraded path, not an error case.

Classification and parameter extraction are NOT separate work items here:
each label only commits if it also finds the structure it needs (a
parseable hour window, a magnitude, etc). A keyword-only classifier would
be structurally weaker — it would commit to a label on a stray keyword
with nothing to anchor it.

Decision order (first match wins, mandatory):
  1. no_discharge_window  — tested first because "discharge" contains
     "charge" as a substring; this is the single most likely silent bug
     in this file.
  2. no_charge_window
  3. solar_reduction
  4. max_grid_window      — before reserve; see the discriminator below.
  5. minimum_battery_reserve
  6. no_op                — residual, nothing else matched.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

_NUMBER_RE = r"(\d+(?:\.\d+)?)"

_SOLAR_NOUNS = ["solar", "pv", "rooftop", "panel", "array", "inverter", "photovoltaic"]

_CHARGE_NOUN = ["charger", "charging", "charge"]
_DISCHARGE_NOUN = ["discharge", "discharging"]

_GRID_NOUNS = ["grid", "import", "intake", "feeder", "transformer", "substation"]
_GRID_CAP_VERBS = [
    "not exceed",
    "at or below",
    "limit",
    "cap",
    "no more than",
    "less than or equal",
    "maximum of",
]

_RESERVE_NOUNS = ["battery", "stored", "remain", "keep"]
_RESERVE_WORDS = ["at least", "keep", "remain", "stored", "reserve", "minimum"]

_ALL_DAY_RE = re.compile(r"\ball day\b|\ball 24 hours\b|\baround the clock\b|\b24/7\b")

_TIME_TOKEN = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"
_RANGE_RE = re.compile(
    r"(" + _TIME_TOKEN + r")\s*(?:to|until|through|-|\u2013|and)\s*(" + _TIME_TOKEN + r")",
    re.IGNORECASE,
)

_WORD_FRACTIONS = [
    ("three quarters", 0.75),
    ("a quarter", 0.25),
    ("quarter", 0.25),
    ("a third", 1.0 / 3.0),
    ("third", 1.0 / 3.0),
    ("half", 0.5),
]

_REDUCE_WORDS = [
    "reduce",
    "reduced",
    "reducing",
    "cut",
    "cutting",
    "drop",
    "dropping",
    "decrease",
    "decreasing",
    "lose",
    "losing",
    "loss of",
    "less",
]


def _time_to_hour(token: str) -> Optional[Tuple[int, bool]]:
    """Returns (hour_0_23, had_explicit_am_pm)."""
    token = token.strip().lower()
    if token == "noon":
        return 12, True
    if token == "midnight":
        return 0, True
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", token)
    if not m:
        return None
    hour = int(m.group(1))
    period = m.group(3)
    if period == "pm":
        return (hour % 12) + 12, True
    if period == "am":
        return (0 if hour == 12 else hour), True
    return hour % 24, False


def _borrow_period(raw_token: str, borrowed_pm: bool) -> Optional[int]:
    m = re.match(r"(\d{1,2})", raw_token.strip())
    if not m:
        return None
    hour = int(m.group(1))
    if borrowed_pm:
        return (hour % 12) + 12
    return 0 if hour == 12 else hour % 12


def _extract_hours(text: str) -> Optional[List[int]]:
    if _ALL_DAY_RE.search(text):
        return list(range(24))

    m = _RANGE_RE.search(text)
    if not m:
        return None

    start_raw, end_raw = m.group(1), m.group(2)
    start = _time_to_hour(start_raw)
    end = _time_to_hour(end_raw)
    if start is None or end is None:
        return None

    start_hour, start_explicit = start
    end_hour, end_explicit = end

    if not start_explicit and end_explicit:
        borrowed = _borrow_period(start_raw, end_raw.strip().lower().endswith("pm"))
        if borrowed is not None:
            start_hour = borrowed
    if not end_explicit and start_explicit:
        borrowed = _borrow_period(end_raw, start_raw.strip().lower().endswith("pm"))
        if borrowed is not None:
            end_hour = borrowed

    if end_hour <= start_hour:
        # Overnight/wraparound windows aren't handled by this parser;
        # fall through rather than guessing.
        return None

    hours = list(range(start_hour, end_hour))
    if not hours or any(h > 23 for h in hours):
        return None
    return hours


def _find_percent(text: str) -> Optional[float]:
    m = re.search(_NUMBER_RE + r"\s*(?:%|percent)", text)
    if m:
        return float(m.group(1)) / 100.0
    return None


def _find_kwh(text: str) -> Optional[float]:
    m = re.search(_NUMBER_RE + r"\s*k?wh\b", text)
    if m:
        return float(m.group(1))
    m = re.search(_NUMBER_RE + r"\s*kw\b", text)
    if m:
        return float(m.group(1))
    return None


def _solar_factor(text: str) -> Optional[float]:
    remain_patterns = [
        r"only\s+" + _NUMBER_RE + r"\s*%",
        r"to\s+" + _NUMBER_RE + r"\s*%",
        _NUMBER_RE + r"\s*%\s+of\s+(?:normal|usual|typical|rated)",
    ]
    for pat in remain_patterns:
        m = re.search(pat, text)
        if m:
            pct = float(m.group(1)) / 100.0
            return max(0.0, min(1.0, pct))

    pct = _find_percent(text)
    if pct is not None:
        if any(w in text for w in _REDUCE_WORDS) or "reduction" in text:
            return max(0.0, min(1.0, 1.0 - pct))
        return max(0.0, min(1.0, pct))

    for word, frac in _WORD_FRACTIONS:
        if word in text:
            if any(w in text for w in _REDUCE_WORDS):
                return max(0.0, min(1.0, 1.0 - frac))
            return max(0.0, min(1.0, frac))

    return None


def _reserve_value(text: str, capacity_kwh: float) -> Optional[float]:
    m = re.search(_NUMBER_RE + r"\s*%\s*of\s*capacity", text)
    if m:
        pct = float(m.group(1)) / 100.0
        return round(pct * capacity_kwh, 4)

    kwh = _find_kwh(text)
    if kwh is not None:
        return kwh

    pct = _find_percent(text)
    if pct is not None:
        return round(pct * capacity_kwh, 4)

    return None


def _no_op(explanation: str) -> Dict[str, Any]:
    return {"directive_type": "no_op", "structured_adjustment": None, "explanation": explanation}


def classify_note(note: str, capacity_kwh: float) -> Dict[str, Any]:
    text = note.lower()
    hours = _extract_hours(text)

    # 1. no_discharge_window — checked first: "discharge" contains "charge".
    if hours and any(w in text for w in _DISCHARGE_NOUN):
        return {
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": f"discharge-window note, hours={hours}",
        }

    # 2. no_charge_window
    if hours and any(w in text for w in _CHARGE_NOUN):
        return {
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": f"charge-window note, hours={hours}",
        }

    # 3. solar_reduction
    if hours and any(w in text for w in _SOLAR_NOUNS):
        factor = _solar_factor(text)
        if factor is not None:
            return {
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": hours, "factor": round(factor, 4)},
                "explanation": f"solar note, hours={hours}, factor={factor}",
            }

    # 4. max_grid_window — before reserve: both are "a kWh number with a
    #    time window"; require a grid noun AND a cap verb before committing.
    has_grid_noun = any(w in text for w in _GRID_NOUNS)
    has_cap_verb = any(w in text for w in _GRID_CAP_VERBS)
    if hours and has_grid_noun and has_cap_verb:
        value = _find_kwh(text)
        if value is not None:
            return {
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": hours, "max_grid_kwh": value},
                "explanation": f"grid-cap note, hours={hours}, cap={value}",
            }

    # 5. minimum_battery_reserve — require a domain noun AND a direction
    #    word before committing (the discriminator against max_grid_window).
    has_reserve_noun = any(w in text for w in _RESERVE_NOUNS)
    has_reserve_direction = any(w in text for w in _RESERVE_WORDS)
    if hours and has_reserve_noun and has_reserve_direction:
        value = _reserve_value(text, capacity_kwh)
        if value is not None:
            return {
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": hours, "minimum_energy_kwh": value},
                "explanation": f"reserve note, hours={hours}, floor={value}",
            }

    # 6. no_op — residual: either zero energy vocabulary (admin notices)
    #    or energy-related but out of scope (demand shift, diesel
    #    generator, export tariff, power-factor correction, EV charger).
    return _no_op("no recognizable directive vocabulary or missing required structure")


def extract_directives(operator_notes: List[str], capacity_kwh: float) -> List[Dict[str, Any]]:
    return [classify_note(note, capacity_kwh) for note in operator_notes]
