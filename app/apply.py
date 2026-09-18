"""Turn directive interpretations + the raw request into LP parameters.

`optimizer.py` only knows LP mechanics (variables, bounds, constraints); it
has no idea what a "solar_reduction" or "no_charge_window" directive means.
This module is the only place that translates directive semantics into
numbers the optimizer consumes, so the two can be tested independently.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set

from .schemas import Battery, DirectiveInterpretation, ScenarioRequest

N_HOURS = 24


@dataclass
class OptimizerParams:
    demand_kwh: List[float]
    tariff_bdt_per_kwh: List[float]
    effective_solar_kwh: List[float]
    min_energy_kwh: List[float]
    capacity_kwh: float
    initial_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float
    no_charge_hours: Set[int] = field(default_factory=set)
    no_discharge_hours: Set[int] = field(default_factory=set)
    max_grid_kwh: Dict[int, float] = field(default_factory=dict)


def build_params(
    directive_interpretation: List[DirectiveInterpretation],
    request: ScenarioRequest,
) -> OptimizerParams:
    hours_sorted = sorted(request.hours, key=lambda h: h.hour)
    battery: Battery = request.battery

    demand_kwh = [h.demand_kwh for h in hours_sorted]
    tariff_bdt_per_kwh = [h.tariff_bdt_per_kwh for h in hours_sorted]
    effective_solar_kwh = [h.solar_kwh for h in hours_sorted]
    min_energy_kwh = [battery.minimum_energy_kwh for _ in range(N_HOURS)]
    no_charge_hours: Set[int] = set()
    no_discharge_hours: Set[int] = set()
    max_grid_kwh: Dict[int, float] = {}

    for entry in directive_interpretation:
        # A directive that classified correctly but carried a malformed
        # structured_adjustment must not be silently applied — guardrails
        # already scrubbed it to None in that case, so there's nothing
        # trustworthy to fold in here.
        if not entry.applies or not entry.structured_adjustment:
            continue

        adjustment = entry.structured_adjustment
        hours = adjustment.hours

        if entry.directive_type == "solar_reduction":
            factor = adjustment.factor
            for h in hours:
                effective_solar_kwh[h] *= factor

        elif entry.directive_type == "minimum_battery_reserve":
            value = adjustment.minimum_energy_kwh
            for h in hours:
                min_energy_kwh[h] = max(min_energy_kwh[h], value)

        elif entry.directive_type == "no_charge_window":
            no_charge_hours.update(hours)

        elif entry.directive_type == "no_discharge_window":
            no_discharge_hours.update(hours)

        elif entry.directive_type == "max_grid_window":
            value = adjustment.max_grid_kwh
            for h in hours:
                if h in max_grid_kwh:
                    max_grid_kwh[h] = min(max_grid_kwh[h], value)
                else:
                    max_grid_kwh[h] = value

    return OptimizerParams(
        demand_kwh=demand_kwh,
        tariff_bdt_per_kwh=tariff_bdt_per_kwh,
        effective_solar_kwh=effective_solar_kwh,
        min_energy_kwh=min_energy_kwh,
        capacity_kwh=battery.capacity_kwh,
        initial_energy_kwh=battery.initial_energy_kwh,
        max_charge_kwh_per_hour=battery.max_charge_kwh_per_hour,
        max_discharge_kwh_per_hour=battery.max_discharge_kwh_per_hour,
        no_charge_hours=no_charge_hours,
        no_discharge_hours=no_discharge_hours,
        max_grid_kwh=max_grid_kwh,
    )
