from __future__ import annotations

from fastapi import FastAPI, HTTPException

from app.models import (
    CreateRequestResponse,
    HealthResponse,
    RequestPayload,
    RequestRecord,
    RequestStatus,
)
from app.pipeline import launch_pipeline
from app.store import store

app = FastAPI(title="Resilient Processing Pipeline", version="1.0.0")


@app.post("/requests", response_model=CreateRequestResponse, status_code=202)
async def create_request(payload: RequestPayload) -> CreateRequestResponse:
    record = RequestRecord(payload=payload)
    store.save(record)
    launch_pipeline(record)
    return CreateRequestResponse(request_id=record.id, status=RequestStatus.PENDING)


@app.get("/requests/{request_id}")
async def get_request(request_id: str) -> RequestRecord:
    record = store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return record


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return store.health()
