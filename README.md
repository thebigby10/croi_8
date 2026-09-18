# GridWise Energy Optimizer (`croi-8`)

Production-ready, LLM-assisted 24-hour smart campus energy optimizer for **BUP CSE Fest 2026 Hackathon (GridWise Preliminary)**.

The system translates natural-language campus operator notes into structured, machine-checkable operational directives, deterministically validates them, applies them to campus energy constraints, and uses a two-phase Linear Programming solver to compute a cost-optimal, peak-minimized 24-hour battery and grid dispatch schedule.

---

## 🌐 Public Production Endpoints

* **Base URL:** `https://161-248-188-105.nip.io`
* **Health Check:** `GET https://161-248-188-105.nip.io/health`
* **Optimization Endpoint:** `POST https://161-248-188-105.nip.io/optimize-energy`

---

## 📐 Architecture & Pipeline

The system strictly decouples natural language understanding from mathematical scheduling through a robust six-stage pipeline:

```
[Operator Notes + Scenario Data]
               │
               ▼
   [Stage 1: LLM Interpreter] (app/llm.py)
   ├── Primary: Google Gemini (gemini-3.5-flash-lite)
   └── On Failure/Missing Key ──► [Stage 2: Deterministic Fallback] (app/fallback.py)
               │
               ▼
   [Stage 3: Guardrail Validator] (app/guardrails.py)
   ├── Enforces unique ascending hours [0..23]
   ├── Bounds checking & directive syntax validation
   └── Guarantees no hallucinated constraints reach solver
               │
               ▼
   [Stage 4: Parameter Translator] (app/apply.py)
   ├── Modifies effective solar profile
   ├── Applies minimum reserve floors & grid import caps
   └── Configures charge/discharge lockout windows
               │
               ▼
   [Stage 5: Two-Phase LP Solver] (app/optimizer.py)
   ├── Phase 1: Minimize total grid electricity cost (SciPy HiGHS LP)
   └── Phase 2: Lexicographic peak-shaving without sacrificing optimal cost
               │
               ▼
   [Stage 6: Replay & Verification] (app/replay.py)
   ├── Validates hourly energy balance: grid + solar + discharge = demand + charge
   ├── Verifies battery capacity, rate limits, and end-of-day neutrality (SoC_23 == SoC_0)
   └── Validates recalculated cost, grid totals, and peak values
               │
               ▼
        [API Response JSON]
```

---

## ⚙️ Configuration & Environment Variables

| Variable | Required | Description |
| :--- | :---: | :--- |
| `GEMINI_API_KEY` | **Yes** (for LLM) | Google Gemini API key used by `app/llm.py` (`gemini-3.5-flash-lite`). If unset or if the API is unreachable, the system automatically falls back to `app/fallback.py` without failing requests. |
| `MONGO_URL` | No | Connection string for MongoDB (provisioned in `docker-compose.yml`, optional). |

> **Security Note:** Secrets and credentials must never be committed to source control. Configure them via `.env` or container environment flags.

---

## 🚀 Clean Local Quickstart

### Prerequisites
* Python 3.10+ (or Docker)
* Git

### Step-by-Step Setup
```bash
# 1. Clone the repository
git clone https://github.com/thebigby10/croi_8.git
cd croi_8

# 2. Create and activate a clean virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and supply your GEMINI_API_KEY
export GEMINI_API_KEY="your-gemini-api-key"

# 5. Start the development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Testing & Public Sample Validation

### 1. Automated Test Suite
Run the full test suite locally (covering health checks, negative validation cases, and the official 10-case public reference pack):
```bash
python tests/test_local.py
```

Run the standalone LLM classification accuracy benchmark (18 synthetic edge cases):
```bash
python tests/classify_scorer.py
```

### 2. Live Public Sample Request (`SAMPLE-01`)
Test the running service (local or remote) using `curl`:

```bash
curl -X POST https://161-248-188-105.nip.io/optimize-energy \
  -H "Content-Type: application/json" \
  -d @tests/manual/02_solar_reduction.json
```

#### Expected Response Format:
```json
{
  "scenario_id": "TEST-SOLAR",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [10, 11, 12, 13],
        "factor": 0.2
      },
      "explanation": "An 80% reduction means 20% remains, applied from 10am to 2pm (end-exclusive)."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 3.85,
      "solar_used_kwh": 0.0,
      "battery_action": "charge",
      "battery_kwh": 2.35,
      "battery_energy_after_kwh": 7.35
    },
    "... 23 more hourly entries ..."
  ],
  "total_grid_kwh": 34.74,
  "total_cost_bdt": 176.48,
  "peak_grid_kwh": 3.85,
  "plan_summary": "1 directive(s) applied (solar_reduction); 0 note(s) treated as no_op."
}
```

---

## 🐳 Docker Fallback Execution

The solution is packaged and published to Docker Hub as an official fallback execution container:

* **Image Reference:** `thebigby01/croi-8:latest`
* **Docker Hub URL:** `https://hub.docker.com/r/thebigby01/croi-8`

### Run from Docker Hub
```bash
# 1. Pull the public image
docker pull thebigby01/croi-8:latest

# 2. Run the container
docker run -d \
  --name croi-8 \
  -p 8000:8000 \
  -e GEMINI_API_KEY="your-gemini-api-key" \
  thebigby01/croi-8:latest

# 3. Verify health
curl http://localhost:8000/health
```

### Run with Docker Compose
```bash
# Start all services (mapped to port 9000 by default)
docker compose up -d --build

# Verify
curl http://localhost:9000/health
```

### Automated VPS Redeployment with Rollback
The repository includes an automated zero-downtime deployment script with error trapping and git/container rollback:
```bash
chmod +x redeploy.sh
./redeploy.sh
```

---

## 🛠️ Technology Stack & Credits

* **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) with [Uvicorn](https://www.uvicorn.org/) (High-performance ASGI server)
* **Data Validation:** [Pydantic v2](https://docs.pydantic.dev/) for strict schema contracts and HTTP 400/422 handling
* **LLM Provider:** [Google GenAI SDK](https://github.com/google/genai) running `gemini-3.5-flash-lite`
* **Mathematical Solver:** [SciPy](https://scipy.org/) (`scipy.optimize.linprog` with the HiGHS dual-simplex/interior-point solver) & [NumPy](https://numpy.org/)
* **Reverse Proxy:** [Caddy 2](https://caddyserver.com/) providing automated TLS on `https://161-248-188-105.nip.io`

---

## 📌 Implementation Notes & Known Limitations

* **Solver Guarantee:** The linear program guarantees mathematical optimality under the 24-hour horizon. The second phase guarantees minimal peak grid demand without degrading the total cost.
* **Fallback Guarantee:** If Gemini experiences network timeouts, quota limits, or malformed responses, the rule-based regex fallback (`app/fallback.py`) automatically steps in to prevent service disruption.
* **Database Independence:** A MongoDB service container is provided in `docker-compose.yml` for persistence scaffolding, but core optimization and API request handling remain completely stateless for maximum speed, concurrency, and reliability.
