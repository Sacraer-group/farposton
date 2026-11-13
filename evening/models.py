from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Feature = Literal["dog", "laptop", "children", "terrace", "reservations"]
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)


class PlanCreateRequest(StrictModel):
    event_id: str = Field(..., min_length=1, max_length=64)
    date: str
    meal_start: str
    meal_end: str
    required_features: list[Feature] = Field(default_factory=list)
    preferred_features: list[Feature] = Field(default_factory=list)
    max_distance_m: int

    @field_validator("event_id")
    @classmethod
    def validate_event_id_uuid(cls, v: str) -> str:
        # accept UUID format; spec says "валидный UUID"
        if not UUID_RE.match(v):
            raise ValueError("event_id must be a valid UUID")
        # normalize
        return str(UUID(v))

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except Exception as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc
        if len(v) != 10:
            raise ValueError("date must be YYYY-MM-DD")
        return v

    @field_validator("meal_start", "meal_end")
    @classmethod
    def validate_hhmm(cls, v: str) -> str:
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("time must be HH:MM")
        return v

    @field_validator("max_distance_m")
    @classmethod
    def validate_distance(cls, v: int) -> int:
        if isinstance(v, bool):
            raise ValueError("max_distance_m must be integer")
        if v < 100 or v > 5000:
            raise ValueError("max_distance_m must be between 100 and 5000")
        return v

    @field_validator("required_features", "preferred_features")
    @classmethod
    def validate_feature_lists(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            raise ValueError("feature list must have at most 5 items")
        if len(set(v)) != len(v):
            raise ValueError("feature list must contain unique values")
        return v

    @model_validator(mode="after")
    def validate_no_intersection(self) -> "PlanCreateRequest":
        inter = set(self.required_features) & set(self.preferred_features)
        if inter:
            raise ValueError("required_features and preferred_features must not intersect")
        return self
