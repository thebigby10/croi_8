from typing import List

from pydantic import BaseModel, Field


class OptimizeRequest(BaseModel):
    instructions: str = ""


class HourlyRow(BaseModel):
    hour: int
    grid_kw: float


class OptimizeResponse(BaseModel):
    rows: List[HourlyRow]
    directives: List[str]
    warnings: List[str] = Field(default_factory=list)
