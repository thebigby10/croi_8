# Plan: /optimize-energy real implementation

## 0. Why this order

Section 11.2/11.3 make validity a gate, not a scored component: a schedule
that fails energy balance or doesn't return the battery to
`initial_energy_kwh` at hour 23 scores **zero on the case**, no matter how
good the directive interpretation is. `app/optimizer.py` currently returns
24 flat dummy rows (`grid_kw=1.0`) — that fails validity on every case,
unconditionally. So the optimizer is the bottleneck and goes first.
Section 02 makes the LLM path mandatory (not an enhancement over a regex
baseline), so `llm.py` outranks `fallback.py` — fallback is the degraded
path for when the LLM is unavailable, not the primary deliverable.

Order: `optimizer.py` → `apply.py` → `llm.py` → `guardrails.py` →
`fallback.py` (+ fixture + scorer) → `replay.py` → wire `main.py` last,
once every stage it calls is real.

## 1. `app/schemas.py` — done, reference only

Already committed: `Hour`, `Battery`, `ScenarioRequest` (with
`notes_non_empty` and `hours_cover_0_23` validators), `DirectiveType`
(seven-way `Literal`), `DirectiveInterpretation`, `HourlyPlanEntry`,
`ScenarioResponse`. Not yet wired into `main.py` — the endpoint still uses
the old placeholder `OptimizeRequest`/`OptimizeResponse`. No changes
needed here except adding a semantic check at the request boundary
(step 7): `minimum_energy_kwh <= initial_energy_kwh <= capacity_kwh`,
which can't be expressed as a single-field `Field(...)` constraint.

## 2. `app/optimizer.py` — the LP

Dependencies: add `scipy` and `numpy` back to `requirements.txt` (removed
in an earlier simplification pass).

**Variables** (4 × 24 = 96, flat vector `x`): for each hour `h` in 0..23,
`charge[h]`, `discharge[h]`, `grid[h]`, `solar_used[h]`, all ≥ 0. Index
helpers: `charge(h) = h`, `discharge(h) = 24+h`, `grid(h) = 48+h`,
`solar_used(h) = 72+h`.

**Objective**: minimize `sum(grid[h] * tariff_bdt_per_kwh[h])`. Charging,
discharging, and solar use are free — only grid import costs money.

**Equality constraints** (`A_eq`, `b_eq`):
- Per hour, energy balance:
  `grid[h] + discharge[h] + solar_used[h] - charge[h] == demand_kwh[h]`
  (24 rows).
- Closed cycle: `sum_h(charge[h] - discharge[h]) == 0`, i.e.
  `E[23] == initial_energy_kwh` (1 row). Without this the LP is free to
  end the day anywhere in bounds, which the objective has no reason to
  avoid — Section 11.3 requires the literal return-to-initial state, so
  this has to be a hard constraint, not an emergent property.

**Inequality constraints** (`A_ub`, `b_ub`), per hour `h`, two rows each
(48 total): let `net[i] = charge[i] - discharge[i]`.
- Upper: `sum_{i<=h} net[i] <= capacity_kwh - initial_energy_kwh`
  (`E[h] <= capacity_kwh`).
- Lower: `-sum_{i<=h} net[i] <= initial_energy_kwh - floor[h]`
  (`E[h] >= floor[h]`), where `floor[h] = max(battery.minimum_energy_kwh,
  any active minimum_battery_reserve for h)` — computed in `apply.py`, not
  here; `optimizer.py` just consumes the per-hour floor array.

**Bounds** per hour: `charge[h] in [0, 0 if no_charge else max_charge]`,
`discharge[h] in [0, 0 if no_discharge else max_discharge]`,
`grid[h] in [0, max_grid_kwh[h] or None]`,
`solar_used[h] in [0, effective_solar_kwh[h]]`. These come from
`apply.py`'s `OptimizerParams`, not computed here either — `optimizer.py`
only knows LP mechanics, not directive semantics. That separation is
what lets `apply.py` and `optimizer.py` be tested independently.

**Solve**: `scipy.optimize.linprog(c, A_ub, b_ub, A_eq, b_eq, bounds,
method="highs")`. `result.success is False` → raise `RuntimeError`
(caller maps this to HTTP 500 — should not happen on any public case per
the "reproduces all 10 reference optima" claim, but a real LP can go
infeasible on an adversarial hidden case, e.g. conflicting `max_grid_window`
+ `minimum_battery_reserve`).

**Post-processing** (build `HourlyPlanEntry` rows from `x`, iterating
hour by hour, tracking a running `energy` accumulator):
- `net = charge[h] - discharge[h]`. `net > tol` → `battery_action="charge"`,
  `battery_kwh=net`; `net < -tol` → `"discharge"`, `battery_kwh=-net`;
  else `"idle"`, `battery_kwh=0`. (An LP has no cost incentive to run
  both charge and discharge simultaneously, but nothing forbids a
  degenerate solution with both slightly positive — net them rather than
  reporting raw `charge`/`discharge`, since the response schema wants one
  `battery_action` + magnitude, not both raw variables.)
- `energy += net` (cumulative, starts at `initial_energy_kwh`) →
  `battery_energy_after_kwh`, rounded to 2 dp.
- `grid_kwh`, `solar_used_kwh` rounded to 2 dp directly from `x`.
- **Compute `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` by summing
  over the rows just built, not from `result.fun`** — the LP objective
  value and a post-rounding sum of the same numbers can drift in the last
  decimal; the response must be internally consistent with itself, since
  `replay.py` (step 6) re-derives the same aggregates from `hourly_plan`
  and compares.

**Self-check**: run against SAMPLE-01 (already in
`tests/sample_cases.json` history / needs restoring) once `apply.py`
exists, confirm `total_cost_bdt` lands at 38365 (± rounding) and
`battery_energy_after_kwh` at hour 23 equals `initial_energy_kwh`.

## 3. `app/apply.py` — directives → LP parameters

`OptimizerParams` dataclass: `demand_kwh`, `tariff_bdt_per_kwh`,
`effective_solar_kwh`, `min_energy_kwh` (all `List[float]`, len 24),
`capacity_kwh`, `initial_energy_kwh`, `max_charge_kwh_per_hour`,
`max_discharge_kwh_per_hour` (floats), `no_charge_hours`,
`no_discharge_hours` (`Set[int]`), `max_grid_kwh` (`Dict[int, float]`).

`build_params(directive_interpretation, request) -> OptimizerParams`:
start from `request.hours`/`request.battery` as the base (effective_solar
= raw `solar_kwh`, floor = `battery.minimum_energy_kwh` for all 24 hours,
no caps/windows). Then for each entry where `applies` is true, fold in
its `structured_adjustment`:
- `solar_reduction`: `effective_solar[h] *= factor` for `h in hours`
  (multiply the *base* value — if two directives somehow overlap the same
  hour, multiplying sequentially is the reasonable generalization of the
  spec's single-directive formula).
- `minimum_battery_reserve`: `floor[h] = max(floor[h],
  minimum_energy_kwh)` for `h in hours`.
- `no_charge_window` / `no_discharge_window`: add `hours` to the
  respective set.
- `max_grid_window`: `max_grid[h] = min(existing, max_grid_kwh)` for
  `h in hours` (tightest cap wins if two windows overlap).

~15 lines of logic once the dataclass shape is fixed; the only real
design decision already made above is "multiply from base, don't stack
sequentially-narrowing" for `solar_reduction`, and "min/tightest wins" for
overlapping caps.

## 4. `app/llm.py` — the mandatory interpretation path

Single call (`google-genai`, `gemini-3.5-flash-lite`, gated on
`GEMINI_API_KEY` same as today), given `operator_notes` and
`battery.capacity_kwh` (needed to resolve "50% of capacity" to an
absolute kWh figure). Returns a raw list, one entry per note, in order:
`{"directive_type": ..., "structured_adjustment": {...} | null,
"explanation": ...}`. Raises on missing key, network/parse failure, or a
response whose length doesn't match `len(notes)` — any of these fall
through to `fallback.py` in `main.py`.

**Prompt must state, explicitly and by name** (these are the specific
failure modes call out in the classification requirements, not generic
"be careful" boilerplate):
- The seven-label closed set — nothing else is a valid `directive_type`.
- Discharge vs. charge confusion: `"discharge"` contains `"charge"` as a
  substring, and this trips LLM classifiers too even though the
  mechanical ordering trick from the regex path doesn't apply to a model
  — call it out with a worked contrast pair.
- Hours are start-inclusive, end-exclusive ("noon until 2 PM" → `[12,
  13]"), with the three canonical phrasing examples from the spec.
- `factor` is the fraction that *remains* usable, not the reduction
  amount ("an 80% reduction" → `factor=0.2`).
- Percentage-of-capacity reserve values must be resolved to an absolute
  kWh number using the battery capacity given in the input.
- Energy-related-but-out-of-scope notes (demand shift, diesel generator,
  export tariff, power-factor correction, EV-charger install) are
  `no_op`, not "closest fit" — this is the case models reach for a
  near-match instead of admitting no match.
- When genuinely ambiguous, prefer `no_op` over inventing a directive:
  asymmetric cost — a missed directive only costs that note's
  interpretation points, a fabricated one also corrupts the optimizer's
  constraints and can invalidate the whole schedule.

## 5. `app/guardrails.py` — validation, two independent layers

**Classification invariants** (apply regardless of source):
- `applies` is always derived — `directive_type != "no_op"` — never read
  from the model's raw output even if present; this eliminates an entire
  guardrail-failure class at zero cost (nothing downstream can produce an
  inconsistent `applies`/`directive_type` pair).
- Exactly one entry per note, always. If the raw list is shorter than
  `operator_notes`, or an entry's `directive_type` isn't one of the seven
  labels, or its `structured_adjustment` doesn't parse — coerce that
  entry to `no_op`, never drop it. A missing entry breaks the
  one-entry-per-note cardinality check (an automatic, harder failure);
  a wrong label is merely a wrong label.

**Parameter shape validation** (independent of the label decision — a bad
`structured_adjustment` must not silently flip a correct label to
`no_op`, since that would hide a classification success behind a
parameter failure in the scorer):
- `hours`: non-empty list of unique ints, ascending, each in `[0, 23]`.
- `solar_reduction.factor`: number in `[0, 1]`.
- `minimum_battery_reserve.minimum_energy_kwh`: number in
  `[0, capacity_kwh]`.
- `max_grid_window.max_grid_kwh`: number ≥ 0.
- `no_charge_window` / `no_discharge_window`: `structured_adjustment` has
  no keys beyond `hours`.

`validate_and_normalize(raw, notes, battery) -> List[dict]` returns
exactly `len(notes)` entries shaped like `DirectiveInterpretation`
(`note_index`, `applies`, `directive_type`, `structured_adjustment`,
`explanation`), ready to construct the pydantic models directly.

## 6. `app/fallback.py` — regex classifier + fixture + scorer

One correction worth keeping explicit: classification and parameter
extraction are separate *functions*, but in the regex path they can't be
separate *work items* done in either order. A keyword-only classifier
(matching `"discharge"` + a testing-context word, say) passes 18/18 on
the public pack but is structurally weaker than one that also requires a
parseable time window — it'll commit to a label on a stray keyword
mention with nothing to anchor it. Build the window/magnitude-parse guard
into the label decision itself from the start, not bolted on after
`classify_note` "works."

**Decision order** (first match wins, mandatory — not stylistic):
1. `no_discharge_window` — `"discharge"` contains `"charge"`; testing
   this first is the single most likely silent bug in the whole layer.
2. `no_charge_window`
3. `solar_reduction`
4. `max_grid_window` — before reserve, because both carry a bare kWh +
   time window; see the discriminator table below.
5. `minimum_battery_reserve`
6. `no_op` — residual, nothing else matched.

Each of 1–5 only commits if it also finds the structure it needs (a
parseable time window for the window types; a percentage/fraction for
`solar_reduction`; a kWh or percent-of-capacity figure for the reserve/
grid-cap types) — otherwise it falls through toward `no_op` rather than
guessing.

**Reserve vs. grid-cap discriminator** — both are "a kWh number with a
time window," the most confusable pair (public SAMPLE-07/SAMPLE-10 each
pair one of each in overlapping hours specifically to punish a classifier
keying on the number alone):

| | bounds | direction | noun signal |
|---|---|---|---|
| `minimum_battery_reserve` | battery stored energy | floor ("at least") | *battery*, *stored*, *remain*, *keep* |
| `max_grid_window` | hourly grid import | ceiling ("at most") | *grid*, *import*, *intake*, *feeder*, *transformer*, *substation* |

Require **both** a domain noun and a direction word before committing to
either label.

**Vocabulary centroids** (not an allowlist — hidden notes paraphrase):
- `solar_reduction`: solar noun (`solar`, `PV`, `rooftop`, `panel`,
  `array`, `inverter`, `photovoltaic`) + percentage/fraction magnitude.
- `no_charge_window` / `no_discharge_window`: subject varies (*charger*,
  *circuit*, *charging*/*discharging* the action itself); discharge cases
  tend to pair with a testing context (protection/relay testing).
- `minimum_battery_reserve`: *at least*, *keep*, *remain in the battery*,
  *stored*, *reserve*, *minimum*.
- `max_grid_window`: grid noun + cap verb (*not exceed*, *at or below*,
  *limit*).
- `no_op` has two members, both real: (a) zero energy vocabulary
  (admin notices), (b) energy-related but outside the six types — this
  case must not be routed to the nearest real label.

No keying on note position (distractors happen to appear last in all
four public multi-note cases — sampling artifact, not a rule) or note
count.

**Fixture**: `tests/classification_pack.json` — the 18 public notes +
expected labels, need to source the verbatim text (only vocabulary/counts
are in the spec, not the full pack). `tests/classify_scorer.py`: build
`matrix[expected][got] += 1` for every note, assert 18/18 on the
diagonal, and on any miss print the matrix and flag whether it's one of
the three named failure classes (discharge→charge, reserve↔grid-cap,
distractor→anything) or something new the vocabulary list is missing.

## 7. `app/main.py` — wire it together, last

Replace `OptimizeRequest`/`OptimizeResponse` with `ScenarioRequest`/
`ScenarioResponse` only once steps 2–6 are real (no point wiring a real
endpoint around stub internals).

- `RequestValidationError` (structural: missing field, wrong type, hours
  not exactly 24, duplicate/out-of-range hour, empty/too-many
  `operator_notes`) → custom exception handler → HTTP 400. Note:
  `exc.errors()` isn't directly JSON-serializable in pydantic v2 (a `ctx`
  key can carry a raw exception object) — pass through
  `fastapi.encoders.jsonable_encoder(..., exclude={"ctx"})`.
- Semantic check not expressible as a single-field constraint —
  `minimum_energy_kwh <= initial_energy_kwh <= capacity_kwh` — checked
  explicitly in the endpoint, raises `HTTPException(422)`.
- `llm.get_directives` wrapped in `try/except Exception` → falls back to
  `fallback.extract_directives` (this is the intended, expected control
  flow, not an error path).
- `guardrails.validate_and_normalize` → `apply.build_params` →
  `optimizer.solve` → build `ScenarioResponse` → `replay.verify` before
  returning; any `replay` warnings → `HTTPException(500)` rather than
  silently returning a plan that fails its own internal check.
- `plan_summary`: templated from `directive_interpretation` (which
  directives applied, how many notes were ignored), not LLM-generated —
  the LLM requirement is about note *interpretation*, a summary string
  built from already-structured data doesn't need a second model call.

## 8. `app/replay.py` — the judge's own re-verification

Re-derive, independently from `hourly_plan` alone (never trusting
`optimizer.py`'s own totals):
- Per-hour energy balance and cumulative `battery_energy_after_kwh`,
  compared against a running accumulator started at
  `initial_energy_kwh`.
- Floor/capacity bounds per hour.
- `no_charge_window`/`no_discharge_window` enforcement checked against
  `battery_action` (only meaningful post-netting check available from the
  response shape — the raw-variable bound is enforced in `optimizer.py`
  itself, at LP-construction time, which is the version that actually
  matters: a simultaneous charge+discharge that nets to `"idle"` must
  never happen because the *bound* was zero, not because replay caught it
  after the fact).
- `max_grid_window` and `solar_used_kwh <= effective_solar_kwh` bounds.
- `E[23] == initial_energy_kwh` within 0.01.
- `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` recomputed from
  `hourly_plan` and compared to the response's own top-level fields
  within 0.01 — this is the check that catches drift between
  `optimizer.py`'s rounding and what's about to be serialized.

## Open items

- Need the verbatim 18-note public pack (text + expected labels) for
  step 6's scorer — not present in this repo yet.
- `requirements.txt` needs `scipy`/`numpy` reinstated for step 2.
- SAMPLE-01's full 24-hour request body (needed as the optimizer/apply
  self-check in step 2) previously existed in this session's test
  fixtures and was reverted; needs restoring alongside step 2's work.
