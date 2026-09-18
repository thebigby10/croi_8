"""Validation for raw directive lists, regardless of whether they came from
`llm.py` or `fallback.py`.

Two independent layers, deliberately kept separate:

1. Classification invariants — `applies` is always derived from
   `directive_type`, and there is always exactly one entry per note.
2. Parameter shape validation — a bad `structured_adjustment` does not
   flip a correct label to `no_op`; it just leaves the adjustment out
   (`apply.py` then has nothing to fold in for that entry), so a
   classification success is never hidden behind a parameter failure.
"""

from typing import Any, Dict, List, Optional

from .schemas import Battery, DirectiveType

VALID_TYPES = set(DirectiveType.__args__)

_ADJUSTMENT_KEYS = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "max_grid_window": {"hours", "max_grid_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
}



def _valid_hours(hours: Any) -> bool:
    if not isinstance(hours, list) or not hours:
        return False
    if not all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in hours):
        return False
    if len(set(hours)) != len(hours):
        return False
    return hours == sorted(hours)


def _validate_adjustment(
    directive_type: str, adjustment: Any, capacity_kwh: float
) -> Optional[Dict[str, Any]]:
    if directive_type == "no_op":
        return None
    if not isinstance(adjustment, dict):
        return None

    allowed_keys = _ADJUSTMENT_KEYS[directive_type]
    if set(adjustment.keys()) - allowed_keys:
        return None

    hours = adjustment.get("hours")
    if not _valid_hours(hours):
        return None

    if directive_type == "solar_reduction":
        factor = adjustment.get("factor")
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            return None
        if not (0 <= factor <= 1):
            return None
        return {"hours": hours, "factor": float(factor)}

    if directive_type == "minimum_battery_reserve":
        value = adjustment.get("minimum_energy_kwh")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if not (0 <= value <= capacity_kwh):
            return None
        return {"hours": hours, "minimum_energy_kwh": float(value)}

    if directive_type == "max_grid_window":
        value = adjustment.get("max_grid_kwh")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if value < 0:
            return None
        return {"hours": hours, "max_grid_kwh": float(value)}

    # no_charge_window / no_discharge_window: hours only
    return {"hours": hours}


def _coerced_no_op(note_index: int, explanation: str) -> Dict[str, Any]:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": explanation,
    }


def validate_and_normalize(
    raw: Any, notes: List[str], battery: Battery
) -> List[Dict[str, Any]]:
    """Return exactly len(notes) DirectiveInterpretation-shaped dicts."""
    raw_list = raw if isinstance(raw, list) else []
    results: List[Dict[str, Any]] = []

    for i in range(len(notes)):
        entry = raw_list[i] if i < len(raw_list) else None

        if not isinstance(entry, dict):
            results.append(
                _coerced_no_op(i, "coerced to no_op: missing or malformed entry")
            )
            continue

        directive_type = entry.get("directive_type")
        if directive_type not in VALID_TYPES:
            results.append(
                _coerced_no_op(
                    i, f"coerced to no_op: unrecognized directive_type {directive_type!r}"
                )
            )
            continue
        assert isinstance(directive_type, str)

        explanation = entry.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            explanation = f"directive_type={directive_type}"

        # applies is always derived, never trusted from the raw entry.
        applies = directive_type != "no_op"

        structured_adjustment = None
        if applies:
            structured_adjustment = _validate_adjustment(
                directive_type, entry.get("structured_adjustment"), battery.capacity_kwh
            )
            # A malformed adjustment does NOT demote directive_type to
            # no_op — it just leaves nothing for apply.py to act on.

        results.append(
            {
                "note_index": i,
                "applies": applies,
                "directive_type": directive_type,
                "structured_adjustment": structured_adjustment,
                "explanation": explanation,
            }
        )

    return results
