import asyncio

import pytest

from app.models import RequestPayload, RequestRecord, RequestStatus
from app.pipeline import process_request
from app.retry import RetryConfig
from app.store import store
from app import handlers
import app.pipeline as pipeline


@pytest.fixture(autouse=True)
def limpiar_estado():
    """Resetea el store y los contadores entre pruebas."""
    store._requests.clear()
    handlers._transient_counters.clear()
    yield
    store._requests.clear()
    handlers._transient_counters.clear()


class TestPipeline:
    async def test_camino_feliz_ambos_exitosos(self):
        record = RequestRecord(payload=RequestPayload(scenario="ok"))
        store.save(record)
        await process_request(record)

        assert record.status == RequestStatus.COMPLETED
        assert record.degraded is False
        assert record.degradation_reason is None
        assert record.started_at is not None
        assert record.finished_at is not None
        assert record.result["primary_handler"].status.value == "success"
        assert record.result["optional_handler"].status.value == "success"

    async def test_degradado_cuando_optional_falla(self):
        record = RequestRecord(
            payload=RequestPayload(scenario="ok", optional_scenario="hard_fail")
        )
        store.save(record)
        await process_request(record)

        assert record.status == RequestStatus.COMPLETED
        assert record.degraded is True
        assert record.degradation_reason is not None
        assert "optional_handler" in record.degradation_reason
        assert record.result["primary_handler"].status.value == "success"
        assert record.result["optional_handler"].status.value == "failed"

    async def test_fallido_cuando_primary_falla(self):
        record = RequestRecord(
            payload=RequestPayload(scenario="hard_fail")
        )
        store.save(record)
        await process_request(record)

        assert record.status == RequestStatus.FAILED
        assert record.result["primary_handler"].status.value == "failed"
        assert len(record.result["primary_handler"].attempts) == 1  # sin reintento

    async def test_fallo_transitorio_se_recupera(self):
        record = RequestRecord(
            payload=RequestPayload(scenario="transient_fail_then_ok")
        )
        store.save(record)
        await process_request(record)

        assert record.status == RequestStatus.COMPLETED
        primary = record.result["primary_handler"]
        assert primary.status.value == "success"
        assert len(primary.attempts) == 3  # 2 fallos + 1 exito

    async def test_timeout_optional_no_bloquea(self, monkeypatch):
        # Usar config rapida para evitar test lento (~12s con config por defecto)
        monkeypatch.setattr(
            pipeline, "OPTIONAL_CONFIG",
            RetryConfig(max_retries=1, timeout=0.1, base_delay=0.01, max_delay=0.1, jitter_max=0.0)
        )

        record = RequestRecord(
            payload=RequestPayload(scenario="ok", optional_scenario="timeout")
        )
        store.save(record)
        await process_request(record)

        assert record.status == RequestStatus.COMPLETED
        assert record.degraded is True
        assert record.result["primary_handler"].status.value == "success"
        assert record.result["optional_handler"].status.value == "timeout"
