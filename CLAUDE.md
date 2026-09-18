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
`POST /optimize-energy`. The optimize endpoint is a fixed five-stage pipeline,
one module per stage, wired together in `main.optimize_energy`:

```
instructions -> llm.get_directives -> (on any exception) fallback.extract_directives
             -> guardrails.validate_and_normalize -> apply.build_params
             -> optimizer.solve -> replay.verify -> OptimizeResponse
```

- `llm.py`: single LLM call via the `google-genai` SDK (`gemini-3.5-flash-lite`).
  Raises `RuntimeError` immediately (without calling the API) if `instructions`
  is blank or `GEMINI_API_KEY` is unset. The `google.genai` import is lazy,
  inside the function.
- `fallback.py`: regex/rule-based backup path. `main.py` catches *any*
  exception from `llm.get_directives` and falls back here — this is the
  intended, expected control flow, not an error case.
- `guardrails.py`: validates/normalizes whatever directive list came out of
  either path above before it's trusted downstream.
- `apply.py`: maps normalized directives to `optimizer` parameters.
- `optimizer.py`: currently a placeholder — returns 24 flat hourly rows
  (`grid_kw=1.0`), not a real solve. Real optimization logic (e.g. an LP
  solve) belongs here.
- `replay.py`: re-checks the plan `optimizer.py` produced before it's
  returned to the caller (currently just row-count).

Each stage is a separate module by design so the placeholder logic in
`optimizer.py`/`fallback.py`/`guardrails.py` can be swapped for real
implementations independently.

`docker-compose.yml` maps the app to host port **9000** (container still
listens on 8000) and runs a `mongo:7` service with a `mongo_data` volume. The
app container is passed `MONGO_URL` and `GEMINI_API_KEY`, but no app code
talks to Mongo yet — there is no Mongo client/driver in `requirements.txt` or
`app/`.
