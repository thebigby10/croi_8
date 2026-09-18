from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import apply as apply_mod
from . import fallback, guardrails, optimizer, replay
from .llm import get_directives
from .schemas import DirectiveInterpretation, ScenarioRequest, ScenarioResponse

app = FastAPI(title="croi-8 energy optimizer")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # exc.errors() isn't directly JSON-serializable in pydantic v2 (a "ctx"
    # key can carry a raw exception object).
    return JSONResponse(
        status_code=400,
        content={"detail": jsonable_encoder(exc.errors(), exclude={"ctx"})},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=ScenarioResponse)
def optimize_energy(request: ScenarioRequest):
    battery = request.battery
    if not (battery.minimum_energy_kwh <= battery.initial_energy_kwh <= battery.capacity_kwh):
        raise HTTPException(
            status_code=422,
            detail=(
                "battery.minimum_energy_kwh <= battery.initial_energy_kwh <= "
                "battery.capacity_kwh must hold"
            ),
        )

    try:
        raw_directives = get_directives(request.operator_notes, battery.capacity_kwh)
    except Exception:
        # Intended, expected control flow: the LLM path is mandatory when
        # available, but any failure (missing key, network, parse, shape
        # mismatch) falls through to the regex/rule-based backup.
        raw_directives = fallback.extract_directives(request.operator_notes, battery.capacity_kwh)

    normalized = guardrails.validate_and_normalize(raw_directives, request.operator_notes, battery)
    directive_interpretation = [DirectiveInterpretation(**entry) for entry in normalized]

    params = apply_mod.build_params(directive_interpretation, request)

    try:
        result = optimizer.solve(params)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response = ScenarioResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directive_interpretation,
        hourly_plan=result.hourly_plan,
        total_grid_kwh=result.total_grid_kwh,
        total_cost_bdt=result.total_cost_bdt,
        peak_grid_kwh=result.peak_grid_kwh,
        plan_summary=_build_plan_summary(directive_interpretation),
    )

    warnings = replay.verify(response, params)
    if warnings:
        raise HTTPException(status_code=500, detail={"warnings": warnings})

    return response


def _build_plan_summary(directive_interpretation) -> str:
    applied = [d for d in directive_interpretation if d.applies]
    ignored = len(directive_interpretation) - len(applied)
    if not applied:
        return f"No directives applied ({ignored} note(s) treated as no_op)."
    types = ", ".join(d.directive_type for d in applied)
    return f"{len(applied)} directive(s) applied ({types}); {ignored} note(s) treated as no_op."
