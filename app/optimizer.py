"""The LP: turns `OptimizerParams` into a 24-hour dispatch plan.

Flat variable vector `x` of length 4 * N_HOURS = 96:
  charge[h]      = x[charge_idx(h)]
  discharge[h]   = x[discharge_idx(h)]
  grid[h]        = x[grid_idx(h)]
  solar_used[h]  = x[solar_idx(h)]

Objective: minimize grid cost. Charging, discharging, and solar use are
free — only grid import costs money.

Solved in two phases because the primary objective (minimize total cost)
alone is degenerate: many hourly grid distributions can share the same
minimal total cost, and `linprog` may return any one of them, including
ones with a needlessly high single-hour peak. Phase 2 re-solves for the
minimum possible peak grid draw, constrained to not give up any of the
phase-1 optimal cost — a proper lexicographic tie-break (minimize cost,
then minimize peak) rather than leaving the choice to solver luck.
"""

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from scipy.optimize import linprog

from .apply import OptimizerParams
from .schemas import HourlyPlanEntry

N_HOURS = 24
N_VARS = 4 * N_HOURS
TOL = 1e-6


def _charge_idx(h: int) -> int:
    return h


def _discharge_idx(h: int) -> int:
    return N_HOURS + h


def _grid_idx(h: int) -> int:
    return 2 * N_HOURS + h


def _solar_idx(h: int) -> int:
    return 3 * N_HOURS + h


@dataclass
class OptimizationResult:
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def _build_base_lp(params: OptimizerParams) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list]:
    """Build the cost vector and constraint/bound matrices shared by both
    solve phases (everything except the phase-specific objective)."""
    c = np.zeros(N_VARS)
    for h in range(N_HOURS):
        c[_grid_idx(h)] = params.tariff_bdt_per_kwh[h]

    # --- equality constraints: per-hour energy balance + closed cycle ---
    A_eq = np.zeros((N_HOURS + 1, N_VARS))
    b_eq = np.zeros(N_HOURS + 1)
    for h in range(N_HOURS):
        A_eq[h, _grid_idx(h)] = 1.0
        A_eq[h, _discharge_idx(h)] = 1.0
        A_eq[h, _solar_idx(h)] = 1.0
        A_eq[h, _charge_idx(h)] = -1.0
        b_eq[h] = params.demand_kwh[h]

    for h in range(N_HOURS):
        A_eq[N_HOURS, _charge_idx(h)] += 1.0
        A_eq[N_HOURS, _discharge_idx(h)] -= 1.0
    b_eq[N_HOURS] = 0.0  # sum(charge - discharge) == 0 -> E[23] == initial_energy_kwh

    # --- inequality constraints: per-hour capacity/floor bounds on E[h] ---
    A_ub = np.zeros((2 * N_HOURS, N_VARS))
    b_ub = np.zeros(2 * N_HOURS)
    for h in range(N_HOURS):
        upper_row = 2 * h
        lower_row = 2 * h + 1
        for i in range(h + 1):
            A_ub[upper_row, _charge_idx(i)] += 1.0
            A_ub[upper_row, _discharge_idx(i)] -= 1.0
            A_ub[lower_row, _charge_idx(i)] -= 1.0
            A_ub[lower_row, _discharge_idx(i)] += 1.0
        b_ub[upper_row] = params.capacity_kwh - params.initial_energy_kwh
        floor_h = params.min_energy_kwh[h]
        b_ub[lower_row] = params.initial_energy_kwh - floor_h

    bounds = []
    for h in range(N_HOURS):
        max_charge = 0.0 if h in params.no_charge_hours else params.max_charge_kwh_per_hour
        bounds.append((0.0, max_charge))
    for h in range(N_HOURS):
        max_discharge = 0.0 if h in params.no_discharge_hours else params.max_discharge_kwh_per_hour
        bounds.append((0.0, max_discharge))
    for h in range(N_HOURS):
        max_grid = params.max_grid_kwh.get(h)
        bounds.append((0.0, max_grid if max_grid is not None else None))
    for h in range(N_HOURS):
        bounds.append((0.0, params.effective_solar_kwh[h]))

    return c, A_eq, b_eq, A_ub, b_ub, bounds


def _clean(val: float) -> float:
    """Snaps small solver tolerances (< 1e-4) to 0.0, else 2 decimal places."""
    if abs(val) < 1e-4:
        return 0.0
    return round(val, 2)


def solve(params: OptimizerParams) -> OptimizationResult:
    c, A_eq, b_eq, A_ub, b_ub, bounds = _build_base_lp(params)

    # --- phase 1: minimize total grid cost ---
    result1 = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not result1.success:
        raise RuntimeError(f"optimizer infeasible: {result1.message}")

    cost_star = float(np.dot(c, result1.x))

    # --- phase 2: among schedules that keep cost == cost_star, minimize
    # the single largest hourly grid draw.
    n_vars2 = N_VARS + 1
    peak_idx = N_VARS

    c2 = np.zeros(n_vars2)
    c2[peak_idx] = 1.0

    A_eq2 = np.hstack([A_eq, np.zeros((A_eq.shape[0], 1))])
    b_eq2 = b_eq

    cost_tol = 1e-8 * (1.0 + abs(cost_star))
    cost_row = np.zeros((1, n_vars2))
    cost_row[0, :N_VARS] = c
    peak_rows = np.zeros((N_HOURS, n_vars2))
    for h in range(N_HOURS):
        peak_rows[h, _grid_idx(h)] = 1.0
        peak_rows[h, peak_idx] = -1.0

    A_ub2 = np.vstack([np.hstack([A_ub, np.zeros((A_ub.shape[0], 1))]), cost_row, peak_rows])
    b_ub2 = np.concatenate([b_ub, [cost_star + cost_tol], np.zeros(N_HOURS)])

    bounds2 = list(bounds) + [(0.0, None)]

    result2 = linprog(c2, A_ub=A_ub2, b_ub=b_ub2, A_eq=A_eq2, b_eq=b_eq2, bounds=bounds2, method="highs")

    x = result2.x if result2.success else result1.x

    raw_energy = params.initial_energy_kwh
    prev_energy = round(params.initial_energy_kwh, 2)
    hourly_plan: List[HourlyPlanEntry] = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for h in range(N_HOURS):
        chg = _clean(x[_charge_idx(h)])
        dis = _clean(x[_discharge_idx(h)])
        su = _clean(x[_solar_idx(h)])
        su = round(min(params.effective_solar_kwh[h], su), 2)

        net = round(chg - dis, 2)
        raw_energy += net
        if h == N_HOURS - 1:
            energy_after = round(params.initial_energy_kwh, 2)
            net = round(energy_after - prev_energy, 2)
        else:
            energy_after = round(raw_energy, 2)
            energy_after = max(params.min_energy_kwh[h], min(params.capacity_kwh, energy_after))
            net = round(energy_after - prev_energy, 2)

        if net > 1e-4:
            action = "charge"
            bkwh = net
            chg_kwh = net
            dis_kwh = 0.0
        elif net < -1e-4:
            action = "discharge"
            bkwh = -net
            chg_kwh = 0.0
            dis_kwh = -net
        else:
            action = "idle"
            bkwh = 0.0
            chg_kwh = 0.0
            dis_kwh = 0.0

        # PHYSICAL ENERGY BALANCE: grid + solar_used + discharge = demand + charge
        grid_kwh = max(0.0, round(params.demand_kwh[h] + chg_kwh - su - dis_kwh, 2))

        row = HourlyPlanEntry(
            hour=h,
            grid_kwh=grid_kwh,
            solar_used_kwh=su,
            battery_action=action,
            battery_kwh=bkwh,
            battery_energy_after_kwh=energy_after,
        )
        hourly_plan.append(row)
        prev_energy = energy_after

        total_grid += row.grid_kwh
        total_cost += row.grid_kwh * params.tariff_bdt_per_kwh[h]
        peak_grid = max(peak_grid, row.grid_kwh)

    return OptimizationResult(
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid, 2),
        total_cost_bdt=round(total_cost, 2),
        peak_grid_kwh=round(peak_grid, 2),
    )
