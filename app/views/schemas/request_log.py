from datetime import datetime

from pydantic import BaseModel


class LogResponse(BaseModel):
    id:          int
    client_ip:   str
    method:      str
    path:        str
    upstream:    str
    status_code: int
    latency_ms:  float
    blocked:     bool
    blocked_by:  str | None
    created_at:  datetime

    model_config = {"from_attributes": True}
