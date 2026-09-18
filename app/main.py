from fastapi import FastAPI

from . import apply as apply_mod
from . import fallback, guardrails, optimizer, replay
from .llm import get_directives
from .schemas import OptimizeRequest, OptimizeResponse

app = FastAPI(title="croi-8 energy optimizer")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(request: OptimizeRequest):
    try:
        raw_directives = get_directives(request.instructions)
    except Exception:
        raw_directives = fallback.extract_directives(request.instructions)

    directives = guardrails.validate_and_normalize(raw_directives)
    params = apply_mod.build_params(directives)
    rows = optimizer.solve(params)
    warnings = replay.verify(rows)

    return OptimizeResponse(rows=rows, directives=directives, warnings=warnings)
