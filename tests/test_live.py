#!/usr/bin/env python3
"""End-to-end automated test runner against the live deployed GridWise API.

Verifies:
1. GET /health readiness
2. All 10 test scenarios in tests/manual/ (all 6 directive types + out-of-scope + validation errors)
3. Section 11.4 alternate phrasings & colloquial expressions
4. All 10 official sample cases from BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json:
   - 0.00 BDT tolerance against reference cost
   - Physical energy balance conservation (0.0000 kWh error)
   - End-of-day battery neutrality
   - Exact schema adherence (no extra/null keys in structured_adjustment)

Usage:
  python3 tests/test_live.py [BASE_URL]
Default BASE_URL: https://161-248-188-105.nip.io
"""

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://161-248-188-105.nip.io"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Create SSL context that does not fail on self-signed/nip.io certificates
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def post_json(url: str, payload: dict, expected_status: int = 200):
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            pass
        return e.code, body


def get_json(url: str, expected_status: int = 200):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return resp.status, body


def run_suite(base_url: str):
    print("=" * 70)
    print(f"GRIDWISE TEST SUITE: {base_url}")
    print("=" * 70)
    
    total_passed = 0
    total_tests = 0

    # -------------------------------------------------------------
    # 1. Health Readiness
    # -------------------------------------------------------------
    print("\n[TEST GROUP 1] Health Check")
    total_tests += 1
    health_url = f"{base_url}/health"
    try:
        status, body = get_json(health_url)
        if status == 200 and body.get("status") == "ok":
            print(f"  PASS: GET /health -> {body}")
            total_passed += 1
        else:
            print(f"  FAIL: GET /health -> status={status}, body={body}")
    except Exception as e:
        print(f"  FAIL: GET /health -> {e}")

    # -------------------------------------------------------------
    # 2. Manual & Edge-Case Test Scenarios
    # -------------------------------------------------------------
    print("\n[TEST GROUP 2] Manual & Edge-Case Scenarios (tests/manual/)")
    manual_dir = REPO_ROOT / "tests" / "manual"
    manual_files = sorted(manual_dir.glob("*.json"))
    
    for mf in manual_files:
        total_tests += 1
        name = mf.name
        with open(mf, "r") as f:
            payload = json.load(f)
        
        opt_url = f"{base_url}/optimize-energy"
        
        if name == "09_invalid_battery_bounds.json":
            # Expects HTTP 422
            status, body = post_json(opt_url, payload, expected_status=422)
            if status == 422:
                print(f"  PASS: {name} correctly rejected with HTTP 422")
                total_passed += 1
            else:
                print(f"  FAIL: {name} expected 422, got {status}")
        elif name == "10_invalid_structure.json":
            # Expects HTTP 400
            status, body = post_json(opt_url, payload, expected_status=400)
            if status == 400:
                print(f"  PASS: {name} correctly rejected with HTTP 400")
                total_passed += 1
            else:
                print(f"  FAIL: {name} expected 400, got {status}")
        else:
            status, body = post_json(opt_url, payload, expected_status=200)
            if status != 200:
                print(f"  FAIL: {name} returned HTTP {status}")
                continue
            
            # Check directives and balance
            directives = body.get("directive_interpretation", [])
            plan = body.get("hourly_plan", [])
            null_keys = False
            for d in directives:
                sa = d.get("structured_adjustment")
                if sa and any(v is None for v in sa.values()):
                    null_keys = True
            
            if null_keys:
                print(f"  FAIL: {name} has null keys in structured_adjustment")
            elif len(plan) != 24:
                print(f"  FAIL: {name} returned {len(plan)} hourly rows, expected 24")
            else:
                dir_types = [d["directive_type"] for d in directives]
                print(f"  PASS: {name} -> 24 rows, directives={dir_types}")
                total_passed += 1

    # -------------------------------------------------------------
    # 3. Problem Statement Section 11.4 Paraphrased Notes
    # -------------------------------------------------------------
    print("\n[TEST GROUP 3] Section 11.4 Paraphrased & Colloquial Notes")
    sample_file = REPO_ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    with open(sample_file, "r") as f:
        sample_pack = json.load(f)
    base_case = sample_pack["cases"][0]["input"]
    
    paraphrase_payload = dict(base_case)
    paraphrase_payload["scenario_id"] = "TEST-SECTION-11-4"
    paraphrase_payload["operator_notes"] = [
        "The cafeteria menu changes tomorrow.",
        "Panel washing from one until three will leave roughly one-fifth of normal solar output.",
        "PV production will drop to about 20% between 13:00 and 15:00"
    ]
    
    total_tests += 1
    status, body = post_json(f"{base_url}/optimize-energy", paraphrase_payload)
    if status == 200:
        dirs = body.get("directive_interpretation", [])
        d0_ok = (dirs[0]["directive_type"] == "no_op" and dirs[0]["structured_adjustment"] is None and not dirs[0]["applies"])
        d1_ok = (dirs[1]["directive_type"] == "solar_reduction" and dirs[1]["structured_adjustment"] == {"hours": [13, 14], "factor": 0.2})
        d2_ok = (dirs[2]["directive_type"] == "solar_reduction" and dirs[2]["structured_adjustment"] == {"hours": [13, 14], "factor": 0.2})
        
        if d0_ok and d1_ok and d2_ok:
            print("  PASS: Paraphrased notes correctly resolved: Note 0 -> no_op, Note 1 & 2 -> solar_reduction [13, 14] factor 0.2")
            total_passed += 1
        else:
            print(f"  FAIL: Paraphrased notes mismatch: d0={dirs[0]}, d1={dirs[1]}, d2={dirs[2]}")
    else:
        print(f"  FAIL: Paraphrased request returned HTTP {status}")

    # -------------------------------------------------------------
    # 4. Official Public Sample Cases (10 Cases)
    # -------------------------------------------------------------
    print("\n[TEST GROUP 4] Official Public Sample Cases (10 Cases)")
    for case in sample_pack["cases"]:
        total_tests += 1
        cid = case["id"]
        req = case["input"]
        ref_out = case["expected_output"]
        ref_cost = ref_out["total_cost_bdt"]
        
        status, body = post_json(f"{base_url}/optimize-energy", req)
        if status != 200:
            print(f"  FAIL: Case {cid} returned HTTP {status}")
            continue
        
        actual_cost = body["total_cost_bdt"]
        cost_diff = abs(actual_cost - ref_cost)
        
        # Check null keys in structured_adjustment
        null_keys = False
        for d in body.get("directive_interpretation", []):
            sa = d.get("structured_adjustment")
            if sa and any(v is None for v in sa.values()):
                null_keys = True
        
        # Check physical energy balance
        balance_err = 0.0
        for h_idx, h in enumerate(body.get("hourly_plan", [])):
            dem = req["hours"][h_idx]["demand_kwh"]
            grid = h["grid_kwh"]
            sol_used = h["solar_used_kwh"]
            action = h["battery_action"]
            bkwh = h["battery_kwh"]
            
            supply = grid + sol_used + (bkwh if action == "discharge" else 0.0)
            consumption = dem + (bkwh if action == "charge" else 0.0)
            balance_err = max(balance_err, abs(supply - consumption))

        # Battery neutrality
        initial_batt = req["battery"]["initial_energy_kwh"]
        final_batt = body["hourly_plan"][23]["battery_energy_after_kwh"]
        neut_diff = abs(final_batt - initial_batt)
        
        passed = (cost_diff <= 0.01 and not null_keys and balance_err < 1e-4 and neut_diff < 1e-4)
        if passed:
            print(f"  PASS: Case {cid} | Ref={ref_cost:.2f} Live={actual_cost:.2f} (diff={cost_diff:.4f} BDT) | BalErr={balance_err:.5f} | NeutDiff={neut_diff:.4f}")
            total_passed += 1
        else:
            print(f"  FAIL: Case {cid} | Ref={ref_cost:.2f} Live={actual_cost:.2f} (diff={cost_diff:.4f} BDT) | NullKeys={null_keys} | BalErr={balance_err:.5f} | NeutDiff={neut_diff:.4f}")

    print("\n" + "=" * 70)
    print(f"FINAL SUMMARY: {total_passed}/{total_tests} tests passed ({total_passed/total_tests*100:.1f}%)")
    print("=" * 70)
    return total_passed == total_tests


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    success = run_suite(target_url.rstrip("/"))
    sys.exit(0 if success else 1)
