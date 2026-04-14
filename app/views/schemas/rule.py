from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RuleCreate(BaseModel):
    name:           str   = Field(..., min_length=1, max_length=100)
    path_pattern:   str   = Field(..., min_length=1, max_length=255,
                                  description="Glob pattern, e.g. /proxy/*/users*")
    limit:          int   = Field(..., ge=1, le=100_000)
    window_seconds: int   = Field(..., ge=1, le=86_400)
    key_type:       Literal["ip", "global"] = "ip"


class RuleResponse(BaseModel):
    id:             int
    name:           str
    path_pattern:   str
    limit:          int
    window_seconds: int
    key_type:       str
    active:         bool
    created_at:     datetime

    model_config = {"from_attributes": True}
