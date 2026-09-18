from typing import List

from .optimizer import N_HOURS
from .schemas import HourlyRow


def verify(rows: List[HourlyRow]) -> List[str]:
    """Re-verify the plan before responding."""
    warnings: List[str] = []
    if len(rows) != N_HOURS:
        warnings.append(f"expected {N_HOURS} hourly rows, got {len(rows)}")
    return warnings
