from typing import Dict, List

from .schemas import HourlyRow

N_HOURS = 24


def solve(params: Dict) -> List[HourlyRow]:
    """Produce 24 hourly rows. Placeholder: flat grid draw, no real solve yet."""
    return [HourlyRow(hour=h, grid_kw=1.0) for h in range(N_HOURS)]
