# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt        # install deps (use a venv)
uvicorn app.main:app --reload           # run dev server (port 8000)
python tests/test_local.py              # run the test suite (plain asserts, no pytest config)
docker compose up --build               # run app (host :9000) + mongo via compose
docker build -t croi-8 . && docker run -p 8000:8000 croi-8   # standalone container
```

`tests/test_local.py` has no fixtures/parametrization — it's a flat script of
`test_*` functions run both as `__main__` and importable by `pytest`. There is
no single-test filter beyond editing the file or `pytest tests/test_local.py::test_name`.

## Architecture

FastAPI app with two endpoints (`app/main.py`): `GET /health` and
`POST /optimize-energy`. The optimize endpoint is a real fixed six-stage
pipeline, one module per stage, wired together in `main.optimize_energy`:

```
ScenarioRequest -> llm.get_directives -> (on any exception) fallback.extract_directives
                -> guardrails.validate_and_normalize -> apply.build_params
                -> optimizer.solve -> replay.verify -> ScenarioResponse
```

`app/schemas.py` defines the request/response contract: `ScenarioRequest`
(`scenario_id`, 1-3 `operator_notes`, exactly 24 `hours`, `battery`) and
`ScenarioResponse` (`directive_interpretation`, `hourly_plan`,
`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, `plan_summary`).
`main.py` also enforces the one semantic check that can't be a single-field
constraint: `battery.minimum_energy_kwh <= battery.initial_energy_kwh <=
battery.capacity_kwh` (violations -> HTTP 422). Structural validation
failures (`RequestValidationError`) are mapped to HTTP 400.

- `llm.py`: single LLM call via the `google-genai` SDK (`gemini-3.5-flash-lite`),
  given `operator_notes` and `battery.capacity_kwh`. Returns one raw
  directive-interpretation dict per note, in order. Raises `RuntimeError` on
  a blank/empty note list, missing `GEMINI_API_KEY`, or a response whose
  length doesn't match `len(operator_notes)` — any of these are the
  intended trigger for falling through to `fallback.py`. The `google.genai`
  import is lazy, inside the function.
- `fallback.py`: regex/rule-based backup classifier (`classify_note`,
  `extract_directives`). Runs a strict decision order (no_discharge_window
  first, to dodge the "discharge" contains "charge" substring trap) and
  only commits to a label when it also finds the structure that label
  needs (a parseable hour window, a magnitude, etc); otherwise falls
  through toward `no_op`. `main.py` catches *any* exception from
  `llm.get_directives` and falls back here — this is the intended,
  expected control flow, not an error case.
- `guardrails.py`: two independent validation layers — classification
  invariants (`applies` is always derived from `directive_type`, never
  read off the raw entry; exactly one entry per note, missing/malformed
  entries coerce to `no_op`) and parameter-shape validation (a bad
  `structured_adjustment` does not demote a correct label to `no_op`; it
  just leaves nothing for `apply.py` to fold in).
- `apply.py`: `build_params(directive_interpretation, request) ->
  OptimizerParams` — folds each `applies=True` directive's
  `structured_adjustment` into per-hour LP parameters (effective solar,
  battery floor, no-charge/no-discharge hour sets, per-hour grid caps).
- `optimizer.py`: a real LP (`scipy.optimize.linprog`, `method="highs"`)
  over 96 variables (charge/discharge/grid/solar_used x 24 hours).
  Minimizes grid cost subject to per-hour energy balance, a closed
  battery cycle (`E[23] == initial_energy_kwh`), and per-hour
  capacity/floor bounds. Raises `RuntimeError` if infeasible (mapped to
  HTTP 500 by `main.py`). Aggregates (`total_grid_kwh`, `total_cost_bdt`,
  `peak_grid_kwh`) are summed from the rounded hourly rows, not from
  `result.fun`, so the response is internally self-consistent.
- `replay.py`: independently re-derives everything from `hourly_plan`
  alone (energy balance, floor/capacity bounds, window enforcement, grid
  caps, solar-used bounds, return-to-initial-state, and the three
  top-level aggregates) and compares against `OptimizerParams` and the
  response's own fields. Any mismatch becomes a warning; `main.py` turns
  non-empty warnings into HTTP 500 rather than returning a plan that
  fails its own internal check.

`tests/classification_pack.json` + `tests/classify_scorer.py` are a
self-authored regression pack for `fallback.classify_note` (the verbatim
hidden 18-note pack referenced in `plan.md` was never available in this
repo). `tests/test_local.py::test_classification_pack` runs it as part of
the normal suite.

`docker-compose.yml` maps the app to host port **9000** (container still
listens on 8000) and runs a `mongo:7` service with a `mongo_data` volume. The
app container is passed `MONGO_URL` and `GEMINI_API_KEY`, but no app code
talks to Mongo yet — there is no Mongo client/driver in `requirements.txt` or
`app/`.
