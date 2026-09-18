"""Scorer for the fallback regex classifier against tests/classification_pack.json.

NOTE: this pack is a self-authored reconstruction of the vocabulary/counts
described in plan.md (the verbatim 18-note public pack was not present in
this repo — see plan.md's "Open items"). It's useful as a regression check
for app/fallback.py's classifier, but it is not the hidden reference pack.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fallback import classify_note

PACK_PATH = Path(__file__).parent / "classification_pack.json"


def _failure_class(expected: str, got: str) -> str:
    pair = {expected, got}
    if pair == {"no_discharge_window", "no_charge_window"}:
        return "discharge<->charge"
    if pair == {"minimum_battery_reserve", "max_grid_window"}:
        return "reserve<->grid-cap"
    if "no_op" in pair:
        return "distractor<->real-label"
    return "other"


def run() -> bool:
    pack = json.loads(PACK_PATH.read_text())
    matrix = defaultdict(lambda: defaultdict(int))
    misses = []

    for case in pack:
        note = case["note"]
        expected = case["expected_directive_type"]
        capacity_kwh = case.get("capacity_kwh", 10.0)
        got = classify_note(note, capacity_kwh)["directive_type"]
        matrix[expected][got] += 1
        if got != expected:
            misses.append((note, expected, got, _failure_class(expected, got)))

    total = len(pack)
    correct = sum(matrix[k][k] for k in matrix)

    if misses:
        print("Confusion matrix (expected -> got: count):")
        for expected, row in matrix.items():
            for got, count in row.items():
                print(f"  {expected:28s} -> {got:28s}: {count}")
        print("\nMisses:")
        for note, expected, got, failure_class in misses:
            print(f"  [{failure_class}] expected={expected} got={got}: {note}")

    print(f"{correct}/{total} correct on classification pack")
    return correct == total


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
