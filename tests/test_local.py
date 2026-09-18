import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
        assert len(body["rows"]) == 24
        assert body["warnings"] == [], f"{case['name']}: warnings {body['warnings']}"


if __name__ == "__main__":
    test_health()
    test_optimize_cases()
    print("all tests passed")
