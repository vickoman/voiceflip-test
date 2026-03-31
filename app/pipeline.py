from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial

from app.handlers import primary_handler, optional_handler
from app.models import HandlerResult, HandlerStatus, RequestRecord, RequestStatus
from app.retry import RetryConfig, run_with_retry
from app.store import store

# Configuraciones por defecto — pueden ser sobreescritas
PRIMARY_CONFIG = RetryConfig(max_retries=3, base_delay=0.5, max_delay=10.0, jitter_max=0.5, timeout=5.0)
OPTIONAL_CONFIG = RetryConfig(max_retries=3, base_delay=0.5, max_delay=10.0, jitter_max=0.5, timeout=3.0)

# Mantener referencias a tareas en background para prevenir garbage collection
_background_tasks: set[asyncio.Task] = set()


async def process_request(record: RequestRecord) -> None:
    """Ejecuta ambos handlers en paralelo y agrega resultados."""
    record.status = RequestStatus.RUNNING
    record.started_at = datetime.now(timezone.utc)
    store.save(record)

    payload = record.payload
    primary_scenario = payload.scenario
    optional_scenario = payload.optional_scenario or payload.scenario

    primary_coro = run_with_retry(
        partial(primary_handler, primary_scenario, record.id),
        config=PRIMARY_CONFIG,
    )
    optional_coro = run_with_retry(
        partial(optional_handler, optional_scenario, record.id),
        config=OPTIONAL_CONFIG,
    )

    results = await asyncio.gather(primary_coro, optional_coro, return_exceptions=True)

    primary_result: HandlerResult = results[0] if isinstance(results[0], HandlerResult) else HandlerResult(
        status=HandlerStatus.FAILED, result=str(results[0])
    )
    optional_result: HandlerResult = results[1] if isinstance(results[1], HandlerResult) else HandlerResult(
        status=HandlerStatus.FAILED, result=str(results[1])
    )

    record.result = {
        "primary_handler": primary_result,
        "optional_handler": optional_result,
    }

    if primary_result.status != HandlerStatus.SUCCESS:
        record.status = RequestStatus.FAILED
    else:
        record.status = RequestStatus.COMPLETED
        if optional_result.status != HandlerStatus.SUCCESS:
            record.degraded = True
            last_error = optional_result.attempts[-1].error if optional_result.attempts else "desconocido"
            record.degradation_reason = f"optional_handler fallo: {last_error}"

    record.finished_at = datetime.now(timezone.utc)
    store.save(record)


def launch_pipeline(record: RequestRecord) -> None:
    """Lanza el pipeline como tarea en background."""
    task = asyncio.create_task(process_request(record))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
