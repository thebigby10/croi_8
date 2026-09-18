import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from tests.classify_scorer import run as run_classification_pack

client = TestClient(app)

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PACK_PATH = REPO_ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_optimize_cases():
    cases = json.loads((Path(__file__).parent / "sample_cases.json").read_text())
    for case in cases:
        resp = client.post("/optimize-energy", json=case["request"])
        assert resp.status_code == 200, f"{case['name']}: {resp.text}"
        body = resp.json()

        assert len(body["hourly_plan"]) == 24, f"{case['name']}: expected 24 hourly rows"
        assert [row["hour"] for row in body["hourly_plan"]] == list(range(24))

        battery = case["request"]["battery"]
        last_row = body["hourly_plan"][-1]
        assert abs(last_row["battery_energy_after_kwh"] - battery["initial_energy_kwh"]) <= 0.01, (
            f"{case['name']}: battery did not return to initial_energy_kwh"
        )

        assert len(body["directive_interpretation"]) == len(case["request"]["operator_notes"])

        recomputed_total_grid = round(sum(r["grid_kwh"] for r in body["hourly_plan"]), 2)
        assert abs(recomputed_total_grid - body["total_grid_kwh"]) <= 0.01

        recomputed_peak = round(max(r["grid_kwh"] for r in body["hourly_plan"]), 2)
        assert abs(recomputed_peak - body["peak_grid_kwh"]) <= 0.01


def test_semantic_validation_rejects_bad_battery_bounds():
    cases = json.loads((Path(__file__).parent / "sample_cases.json").read_text())
    request = json.loads(json.dumps(cases[0]["request"]))  # deep copy
    request["battery"]["initial_energy_kwh"] = request["battery"]["capacity_kwh"] + 1
    resp = client.post("/optimize-energy", json=request)
    assert resp.status_code == 422


def test_structural_validation_returns_400():
    resp = client.post("/optimize-energy", json={"scenario_id": "bad"})
    assert resp.status_code == 400


def test_classification_pack():
    assert run_classification_pack()


def test_public_reference_pack():
    """Validate against the official BUP CSE Fest public sample pack: 10
    fully worked cases with expected directive_interpretation and a valid
    optimal reference schedule for each.

    Per the pack's own `how_to_use` notes, equivalent optimal schedules are
    accepted (the hourly action sequence doesn't need to match byte-for-
    byte), so hourly_plan rows aren't compared directly -- only the
    directive classification and the three scored aggregates, which must
    match an optimal reference within rounding tolerance.
    """
    if not PUBLIC_PACK_PATH.exists():
        print(f"skipping test_public_reference_pack: {PUBLIC_PACK_PATH} not found")
        return

    pack = json.loads(PUBLIC_PACK_PATH.read_text())
    tol = 1.0

    for case in pack["cases"]:
        resp = client.post("/optimize-energy", json=case["input"])
        assert resp.status_code == 200, f"{case['id']}: {resp.text}"
        body = resp.json()
        expected = case["expected_output"]

        got_types = [d["directive_type"] for d in body["directive_interpretation"]]
        exp_types = [d["directive_type"] for d in expected["directive_interpretation"]]
        assert got_types == exp_types, (
            f"{case['id']}: directive_type mismatch, got {got_types} expected {exp_types}"
        )

        assert abs(body["total_cost_bdt"] - expected["total_cost_bdt"]) <= tol, (
            f"{case['id']}: total_cost_bdt {body['total_cost_bdt']} vs expected {expected['total_cost_bdt']}"
        )
        assert abs(body["total_grid_kwh"] - expected["total_grid_kwh"]) <= tol, (
            f"{case['id']}: total_grid_kwh {body['total_grid_kwh']} vs expected {expected['total_grid_kwh']}"
        )
        assert abs(body["peak_grid_kwh"] - expected["peak_grid_kwh"]) <= tol, (
            f"{case['id']}: peak_grid_kwh {body['peak_grid_kwh']} vs expected {expected['peak_grid_kwh']}"
        )


if __name__ == "__main__":
    test_health()
    test_optimize_cases()
    test_semantic_validation_rejects_bad_battery_bounds()
    test_structural_validation_returns_400()
    test_classification_pack()
    test_public_reference_pack()
    print("all tests passed")
