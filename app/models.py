from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HandlerStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AttemptRecord(BaseModel):
    attempt: int
    delay_applied: float
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HandlerResult(BaseModel):
    status: HandlerStatus = HandlerStatus.FAILED
    result: Any = None
    latency_ms: float = 0.0
    attempts: list[AttemptRecord] = Field(default_factory=list)


class RequestPayload(BaseModel):
    scenario: str = "ok"
    optional_scenario: str | None = None


class RequestRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: RequestPayload
    status: RequestStatus = RequestStatus.PENDING
    degraded: bool = False
    degradation_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, HandlerResult] = Field(default_factory=dict)


class CreateRequestResponse(BaseModel):
    request_id: str
    status: RequestStatus


class HandlerMetrics(BaseModel):
    successes: int = 0
    failures: int = 0
    avg_latency_ms: float = 0.0


class HealthResponse(BaseModel):
    status: str = "healthy"
    total_requests: int = 0
    requests_by_status: dict[str, int] = Field(default_factory=dict)
    handlers: dict[str, HandlerMetrics] = Field(default_factory=dict)
