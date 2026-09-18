"""The mandatory LLM interpretation path.

One call per request: given the operator notes and the battery capacity
(needed to resolve "50% of capacity" to an absolute kWh figure), ask the
model to classify each note into the closed directive-type set. Any
failure here (missing key, network/parse error, wrong-length response)
is expected to raise so `main.py` can fall through to `fallback.py`.
"""

import json
import os
from typing import Any, Dict, List

from .schemas import DirectiveType

DIRECTIVE_TYPES: List[str] = list(DirectiveType.__args__)

MODEL_NAME = "gemini-3.5-flash-lite"


def _build_prompt(operator_notes: List[str], capacity_kwh: float) -> str:
    labels = ", ".join(f'"{t}"' for t in DIRECTIVE_TYPES)
    notes_block = "\n".join(f"{i}. {note}" for i, note in enumerate(operator_notes))

    return f"""You are classifying operator notes for a battery/solar/grid energy
optimizer. For EACH note, decide which single directive_type it expresses.

The directive_type MUST be exactly one of this closed set — nothing else
is valid, ever: {labels}.

Battery capacity for this scenario: {capacity_kwh} kWh. Use it to convert
any "X% of capacity" phrasing into an absolute kWh number.

Read carefully, these are the specific mistakes classifiers make on this task:

- "discharge" contains the substring "charge". A note about *stopping
  discharge* (e.g. "do not discharge the battery from 2pm to 4pm for relay
  testing") is directive_type "no_discharge_window", NOT
  "no_charge_window", even though the word "charge" appears inside it.
  Contrast: "disable the charger from 2pm to 4pm" is "no_charge_window"
  (nothing about discharging). Read the whole word, not the substring.
- Hour windows are start-inclusive, end-exclusive. "noon until 2pm" means
  hours [12, 13] (not 14). "from 5pm to 8pm" means hours [17, 18, 19].
  "between 9am and 10am" means hour [9] only.
- For solar_reduction, "factor" is the fraction of solar output that
  REMAINS usable, not the size of the reduction. "an 80% reduction in
  solar output" means factor=0.2 (20% remains). "solar will only deliver
  30% of normal output" means factor=0.3 directly.
- Percentage-of-capacity reserve values (e.g. "keep the battery at or
  above 20% of capacity") must be converted to an absolute kWh number
  using the battery capacity given above, not left as a percentage.
- Notes that are energy-related but describe something this optimizer
  does not model — shifting demand to another time, running a diesel
  generator, an export tariff, power-factor correction, installing an EV
  charger — are "no_op". Do not pick the closest real label for these;
  they are genuinely out of scope.
- When a note is genuinely ambiguous, prefer "no_op" over inventing a
  directive. A missed directive only costs that note's interpretation
  score; a fabricated one can also corrupt the optimizer's constraints
  and invalidate the whole schedule. The asymmetry favors caution.

Operator notes (0-indexed, in order):
{notes_block}

Respond with ONLY a JSON array of exactly {len(operator_notes)} objects,
one per note, in the same order, each shaped exactly like:
{{"directive_type": <one of the labels above>, "structured_adjustment":
<object or null>, "explanation": <short string>}}

structured_adjustment shapes by directive_type:
- solar_reduction: {{"hours": [int, ...], "factor": number in [0, 1]}}
- minimum_battery_reserve: {{"hours": [int, ...], "minimum_energy_kwh": number}}
- no_charge_window: {{"hours": [int, ...]}}
- no_discharge_window: {{"hours": [int, ...]}}
- max_grid_window: {{"hours": [int, ...], "max_grid_kwh": number}}
- no_op: null

Return raw JSON only, no markdown fences, no commentary.
"""


def get_directives(operator_notes: List[str], capacity_kwh: float) -> List[Dict[str, Any]]:
    """Single LLM call producing a raw (unvalidated) directive list."""
    if not operator_notes or not any(n.strip() for n in operator_notes):
        raise RuntimeError("LLM unavailable: no operator notes")
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("LLM unavailable: GEMINI_API_KEY not set")

    from google import genai  # lazy import: optional dependency at call time

    client = genai.Client()
    prompt = _build_prompt(operator_notes, capacity_kwh)
    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )

    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]

    raw = json.loads(text)
    if not isinstance(raw, list) or len(raw) != len(operator_notes):
        raise RuntimeError(
            f"LLM response shape mismatch: expected {len(operator_notes)} entries, "
            f"got {len(raw) if isinstance(raw, list) else type(raw).__name__}"
        )
    return raw
