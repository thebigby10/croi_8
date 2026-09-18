from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EXAMPLE_SCENARIO_REQUEST = {
    "scenario_id": "example-scenario-1",
    "operator_notes": [
        "Reduce solar output by 75% between 12pm and 2pm for panel cleaning.",
        "Keep at least 20 kWh in reserve from 6pm to 9pm.",
        "Do not charge the battery from 2am to 4am for maintenance.",
    ],
    "hours": [
        {"hour": 0, "demand_kwh": 25, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 1, "demand_kwh": 22, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 2, "demand_kwh": 20, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 3, "demand_kwh": 19, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 4, "demand_kwh": 20, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 5, "demand_kwh": 22, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
        {"hour": 6, "demand_kwh": 28, "solar_kwh": 5, "tariff_bdt_per_kwh": 6.5},
        {"hour": 7, "demand_kwh": 35, "solar_kwh": 15, "tariff_bdt_per_kwh": 6.5},
        {"hour": 8, "demand_kwh": 40, "solar_kwh": 30, "tariff_bdt_per_kwh": 7.5},
        {"hour": 9, "demand_kwh": 42, "solar_kwh": 45, "tariff_bdt_per_kwh": 7.5},
        {"hour": 10, "demand_kwh": 45, "solar_kwh": 60, "tariff_bdt_per_kwh": 7.5},
        {"hour": 11, "demand_kwh": 48, "solar_kwh": 70, "tariff_bdt_per_kwh": 7.5},
        {"hour": 12, "demand_kwh": 50, "solar_kwh": 75, "tariff_bdt_per_kwh": 7.5},
        {"hour": 13, "demand_kwh": 48, "solar_kwh": 72, "tariff_bdt_per_kwh": 7.5},
        {"hour": 14, "demand_kwh": 46, "solar_kwh": 65, "tariff_bdt_per_kwh": 7.5},
        {"hour": 15, "demand_kwh": 44, "solar_kwh": 55, "tariff_bdt_per_kwh": 7.5},
        {"hour": 16, "demand_kwh": 42, "solar_kwh": 40, "tariff_bdt_per_kwh": 7.5},
        {"hour": 17, "demand_kwh": 40, "solar_kwh": 25, "tariff_bdt_per_kwh": 8.0},
        {"hour": 18, "demand_kwh": 46, "solar_kwh": 10, "tariff_bdt_per_kwh": 12.0},
        {"hour": 19, "demand_kwh": 50, "solar_kwh": 0, "tariff_bdt_per_kwh": 12.0},
        {"hour": 20, "demand_kwh": 48, "solar_kwh": 0, "tariff_bdt_per_kwh": 12.0},
        {"hour": 21, "demand_kwh": 42, "solar_kwh": 0, "tariff_bdt_per_kwh": 10.0},
        {"hour": 22, "demand_kwh": 35, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.5},
        {"hour": 23, "demand_kwh": 28, "solar_kwh": 0, "tariff_bdt_per_kwh": 5.0},
    ],
    "battery": {
        "capacity_kwh": 100,
        "initial_energy_kwh": 50,
        "minimum_energy_kwh": 10,
        "max_charge_kwh_per_hour": 20,
        "max_discharge_kwh_per_hour": 20,
    },
}


class Hour(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0)
    solar_kwh: float = Field(ge=0)
    tariff_bdt_per_kwh: float = Field(ge=0)


class Battery(BaseModel):
    capacity_kwh: float = Field(gt=0)
    initial_energy_kwh: float = Field(ge=0)
    minimum_energy_kwh: float = Field(ge=0)
    max_charge_kwh_per_hour: float = Field(gt=0)
    max_discharge_kwh_per_hour: float = Field(gt=0)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": _EXAMPLE_SCENARIO_REQUEST})

    scenario_id: str
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[Hour] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, notes: List[str]) -> List[str]:
        if any(not note.strip() for note in notes):
            raise ValueError("operator_notes entries must be non-empty")
        return notes

    @model_validator(mode="after")
    def hours_cover_0_23(self) -> "ScenarioRequest":
        hour_values = {h.hour for h in self.hours}
        if hour_values != set(range(24)):
            raise ValueError("hours must contain each hour 0-23 exactly once, no duplicates")
        return self


DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class StructuredAdjustment(BaseModel):
    """Union of every directive's adjustment fields; only the fields
    relevant to a given `directive_type` are populated, the rest stay
    `None`. `no_op` directives carry `structured_adjustment = null`
    (there is no `StructuredAdjustment` instance at all).
    """

    model_config = ConfigDict(extra="forbid")

    hours: List[int] = Field(
        min_length=1,
        description="Unique, ascending hour indices (0-23) this directive applies to.",
    )
    factor: Optional[float] = Field(
        default=None, ge=0, le=1, description="solar_reduction: fraction of solar output remaining."
    )
    minimum_energy_kwh: Optional[float] = Field(
        default=None, ge=0, description="minimum_battery_reserve: battery floor in kWh."
    )
    max_grid_kwh: Optional[float] = Field(
        default=None, ge=0, description="max_grid_window: grid import cap in kWh for the window."
    )

    @field_validator("hours")
    @classmethod
    def hours_valid(cls, hours: List[int]) -> List[int]:
        if any(not (0 <= h <= 23) for h in hours):
            raise ValueError("hours entries must be 0-23")
        if len(set(hours)) != len(hours):
            raise ValueError("hours entries must be unique")
        if hours != sorted(hours):
            raise ValueError("hours must be in ascending order")
        return hours


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str


BatteryAction = Literal["charge", "discharge", "idle"]


class HourlyPlanEntry(BaseModel):
    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0)
    solar_used_kwh: float = Field(ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(ge=0)
    battery_energy_after_kwh: float


class ScenarioResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry] = Field(min_length=24, max_length=24)
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

    @model_validator(mode="after")
    def hourly_plan_sequential_0_23(self) -> "ScenarioResponse":
        hour_values = [entry.hour for entry in self.hourly_plan]
        if hour_values != list(range(24)):
            raise ValueError(
                "hourly_plan must contain exactly 24 sequential hourly entries, hour 0 to 23 in order"
            )
        return self


