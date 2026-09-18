# croi-8 energy optimizer

FastAPI scaffold for an LLM-assisted energy optimizer.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Set `ANTHROPIC_API_KEY` to enable LLM directive extraction; without it (or on
any LLM failure) requests fall back to `app/fallback.py` automatically.

## Endpoints

- `GET /health` -> `{"status": "ok"}`
- `POST /optimize-energy` -> 24 hourly rows + directives + warnings

## Pipeline

`instructions` -> `llm.py` (single LLM call) or `fallback.py` (regex) ->
`guardrails.py` (validate/normalize) -> `apply.py` (-> optimizer params) ->
`optimizer.py` (-> 24 hourly rows, currently a placeholder) -> `replay.py`
(re-verify the plan before responding).

## Test

```bash
python tests/test_local.py
```

## Docker

```bash
docker build -t croi-8 .
docker run -p 8000:8000 croi-8
```
