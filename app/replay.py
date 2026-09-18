"""The judge's own re-verification: re-derive everything from
`hourly_plan` alone, never trusting `optimizer.py`'s own totals.
"""

from typing import List

from .apply import OptimizerParams
from .schemas import ScenarioResponse

TOL = 0.01


def verify(response: ScenarioResponse, params: OptimizerParams) -> List[str]:
    warnings: List[str] = []
    rows = sorted(response.hourly_plan, key=lambda r: r.hour)

    if [r.hour for r in rows] != list(range(24)):
        return ["hourly_plan must contain exactly hours 0-23, each once, in order"]

    energy = params.initial_energy_kwh
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for row in rows:
        h = row.hour

        if row.battery_action == "charge":
            net = row.battery_kwh
            if h in params.no_charge_hours and row.battery_kwh > TOL:
                warnings.append(f"hour {h}: charging occurred during a no_charge_window hour")
            if row.battery_kwh > params.max_charge_kwh_per_hour + TOL:
                warnings.append(
                    f"hour {h}: battery_kwh {row.battery_kwh} exceeds "
                    f"max_charge_kwh_per_hour {params.max_charge_kwh_per_hour}"
                )
        elif row.battery_action == "discharge":
            net = -row.battery_kwh
            if h in params.no_discharge_hours and row.battery_kwh > TOL:
                warnings.append(f"hour {h}: discharging occurred during a no_discharge_window hour")
            if row.battery_kwh > params.max_discharge_kwh_per_hour + TOL:
                warnings.append(
                    f"hour {h}: battery_kwh {row.battery_kwh} exceeds "
                    f"max_discharge_kwh_per_hour {params.max_discharge_kwh_per_hour}"
                )
        else:
            net = 0.0

        energy += net

        floor = params.min_energy_kwh[h]
        if energy > params.capacity_kwh + TOL:
            warnings.append(
                f"hour {h}: battery_energy_after_kwh {energy:.2f} exceeds capacity_kwh {params.capacity_kwh}"
            )
        if energy < floor - TOL:
            warnings.append(f"hour {h}: battery_energy_after_kwh {energy:.2f} below floor {floor}")

        if abs(energy - row.battery_energy_after_kwh) > TOL:
            warnings.append(
                f"hour {h}: reported battery_energy_after_kwh {row.battery_energy_after_kwh} "
                f"does not match recomputed {energy:.2f}"
            )

        max_grid = params.max_grid_kwh.get(h)
        if max_grid is not None and row.grid_kwh > max_grid + TOL:
            warnings.append(f"hour {h}: grid_kwh {row.grid_kwh} exceeds max_grid_window cap {max_grid}")

        if row.solar_used_kwh > params.effective_solar_kwh[h] + TOL:
            warnings.append(
                f"hour {h}: solar_used_kwh {row.solar_used_kwh} exceeds "
                f"effective_solar_kwh {params.effective_solar_kwh[h]}"
            )

        total_grid += row.grid_kwh
        total_cost += row.grid_kwh * params.tariff_bdt_per_kwh[h]
        peak_grid = max(peak_grid, row.grid_kwh)

    if abs(energy - params.initial_energy_kwh) > TOL:
        warnings.append(
            f"battery did not return to initial_energy_kwh: ended at {energy:.2f}, "
            f"expected {params.initial_energy_kwh}"
        )

    if abs(total_grid - response.total_grid_kwh) > TOL:
        warnings.append(
            f"total_grid_kwh mismatch: recomputed {total_grid:.2f} vs reported {response.total_grid_kwh}"
        )
    if abs(total_cost - response.total_cost_bdt) > TOL:
        warnings.append(
            f"total_cost_bdt mismatch: recomputed {total_cost:.2f} vs reported {response.total_cost_bdt}"
        )
    if abs(peak_grid - response.peak_grid_kwh) > TOL:
        warnings.append(
            f"peak_grid_kwh mismatch: recomputed {peak_grid:.2f} vs reported {response.peak_grid_kwh}"
        )

    return warnings
