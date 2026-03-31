# Plan de Implementacion

> Generado desde la fase de Investigacion RPI. Ajustado segun feedback del review.
> Fuente: `specs/RESEARCH_RESILIENT_PIPELINE.md`
> Ultimo review: `specs/REVIEW_VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`
> Iteracion de ajuste: 1
> Para ejecutar: `/rpi-implement specs/VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`

## INSTRUCCIONES PARA LA SIGUIENTE SESION
- Este plan fue creado desde investigacion VERIFICADA — proyecto greenfield, sin codigo existente
- NO re-investigar ni re-planificar — proceder directamente a implementacion
- Todos los pasos son operaciones CREATE (archivos nuevos)
- El proyecto usa asyncio para concurrencia, FastAPI para la API, Pydantic v2 para esquemas
- Se usa `uv` como gestor de paquetes (en lugar de pip/venv)

## Prerequisitos
- [ ] Python 3.11+ instalado
- [ ] `uv` instalado (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [ ] Inicializar proyecto: `uv init && uv add fastapi uvicorn pydantic httpx`
- [ ] Dependencias de desarrollo: `uv add --dev pytest pytest-asyncio`

## Grafo de Dependencias

```
Paso 1 (pyproject.toml + __init__.py + pytest config)
    ↓
Paso 2 (models.py) ──→ Paso 4 (store.py) ──→ Paso 6 (pipeline.py) ──→ Paso 7 (main.py)
Paso 3 (retry.py)  ──→ Paso 5 (handlers.py) ─┘                           ↓
                                                                     Paso 8 (test_retry.py)      ─┐
                                                                     Paso 9 (test_pipeline.py)    ├─ paralelo
                                                                     Paso 10 (test_api.py)        ─┘
                                                                     Paso 11 (README.md + NOTES.md)
```

---

## Paso 1: Inicializar proyecto con uv, __init__.py y configuracion de pytest
**Archivo:** `pyproject.toml`, `app/__init__.py`, `tests/__init__.py`
**Accion:** CREATE
**Por que:** Fundacion del proyecto — uv gestiona dependencias, __init__.py hace los directorios importables, y pytest-asyncio necesita configuracion explicita
**Paralelo:** NO (paso fundacional)

**Despues:**

Ejecutar:
```bash
uv init
uv add fastapi uvicorn pydantic httpx
uv add --dev pytest pytest-asyncio
mkdir -p app tests
touch app/__init__.py tests/__init__.py
```

Agregar al `pyproject.toml` generado por uv:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Verificar:**
```bash
uv run python -c "import fastapi; print(f'FastAPI {fastapi.__version__}')"
uv run python -c "import app; import tests; print('Paquetes OK')"
# Esperado: version de FastAPI y "Paquetes OK"
```

**Rollback:**
```bash
rm -rf app tests pyproject.toml uv.lock
```

---

## Paso 2: Crear modelos Pydantic
**Archivo:** `app/models.py`
**Accion:** CREATE
**Por que:** Definir todas las estructuras de datos primero — todos los demas modulos dependen de estos tipos
**Paralelo:** SI (con Paso 3)

**Despues:**
```python
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
```

**Verificar:**
```bash
uv run python -c "from app.models import RequestRecord, RequestPayload; r = RequestRecord(payload=RequestPayload()); print(f'OK: {r.id} / {r.status}')"
# Esperado: OK: <uuid> / pending
```

**Rollback:**
```bash
rm app/models.py
```

---

## Paso 3: Crear motor de reintentos con backoff y jitter
**Archivo:** `app/retry.py`
**Accion:** CREATE
**Por que:** Logica central de resiliencia — debe estar aislada y ser testeable independientemente de los handlers
**Paralelo:** SI (con Paso 2)

**Despues:**
```python
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, Awaitable

from app.models import AttemptRecord, HandlerResult, HandlerStatus

RETRYABLE_EXCEPTIONS = (TimeoutError, asyncio.TimeoutError, ConnectionError, ConnectionRefusedError)


class RetryConfig:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter_max: float = 0.5,
        timeout: float = 5.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_max = jitter_max
        self.timeout = timeout


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calcula el delay con backoff exponencial y jitter."""
    exponential = min(config.base_delay * (2 ** attempt), config.max_delay)
    jitter = random.uniform(0, config.jitter_max)
    return exponential + jitter


async def run_with_retry(
    handler: Callable[..., Awaitable[Any]],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> HandlerResult:
    """Ejecuta un handler con reintentos, backoff, jitter y timeout."""
    config = config or RetryConfig()
    attempts: list[AttemptRecord] = []
    start_time = time.monotonic()

    for attempt_num in range(1, config.max_retries + 2):  # +2: 1 inicial + max_retries
        delay = 0.0 if attempt_num == 1 else calculate_delay(attempt_num - 2, config)

        if attempt_num > 1:
            await asyncio.sleep(delay)

        try:
            result = await asyncio.wait_for(handler(*args, **kwargs), timeout=config.timeout)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            attempts.append(AttemptRecord(attempt=attempt_num, delay_applied=delay, error=None))
            return HandlerResult(
                status=HandlerStatus.SUCCESS,
                result=result,
                latency_ms=elapsed_ms,
                attempts=attempts,
            )
        except RETRYABLE_EXCEPTIONS as exc:
            attempts.append(
                AttemptRecord(attempt=attempt_num, delay_applied=delay, error=type(exc).__name__)
            )
            if attempt_num > config.max_retries:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                status = HandlerStatus.TIMEOUT if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else HandlerStatus.FAILED
                return HandlerResult(
                    status=status,
                    result=None,
                    latency_ms=elapsed_ms,
                    attempts=attempts,
                )
        except Exception as exc:
            attempts.append(
                AttemptRecord(attempt=attempt_num, delay_applied=delay, error=type(exc).__name__)
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return HandlerResult(
                status=HandlerStatus.FAILED,
                result=None,
                latency_ms=elapsed_ms,
                attempts=attempts,
            )

    # No deberia llegar aqui, pero fallback de seguridad
    elapsed_ms = (time.monotonic() - start_time) * 1000
    return HandlerResult(status=HandlerStatus.FAILED, result=None, latency_ms=elapsed_ms, attempts=attempts)
```

**Verificar:**
```bash
uv run python -c "
import asyncio
from app.retry import run_with_retry, RetryConfig

async def ok_handler(): return 'done'

result = asyncio.run(run_with_retry(ok_handler, config=RetryConfig()))
print(f'Status: {result.status}, Result: {result.result}, Intentos: {len(result.attempts)}')
"
# Esperado: Status: success, Result: done, Intentos: 1
```

**Rollback:**
```bash
rm app/retry.py
```

---

## Paso 4: Crear almacen in-memory con metricas
**Archivo:** `app/store.py`
**Accion:** CREATE
**Por que:** Almacenamiento centralizado y recoleccion de metricas — necesario para el pipeline y los endpoints de la API
**Paralelo:** NO (depende del Paso 2)

**Despues:**
```python
from __future__ import annotations

from app.models import (
    HandlerMetrics,
    HandlerStatus,
    HealthResponse,
    RequestRecord,
    RequestStatus,
)


class RequestStore:
    def __init__(self) -> None:
        self._requests: dict[str, RequestRecord] = {}

    def save(self, record: RequestRecord) -> None:
        self._requests[record.id] = record

    def get(self, request_id: str) -> RequestRecord | None:
        return self._requests.get(request_id)

    def health(self) -> HealthResponse:
        records = list(self._requests.values())
        total = len(records)

        by_status: dict[str, int] = {}
        for s in RequestStatus:
            count = sum(1 for r in records if r.status == s)
            if count > 0:
                by_status[s.value] = count

        handler_names = ["primary_handler", "optional_handler"]
        handlers: dict[str, HandlerMetrics] = {}

        for name in handler_names:
            successes = 0
            failures = 0
            latencies: list[float] = []

            for r in records:
                if name in r.result:
                    hr = r.result[name]
                    if hr.status == HandlerStatus.SUCCESS:
                        successes += 1
                    else:
                        failures += 1
                    latencies.append(hr.latency_ms)

            avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
            handlers[name] = HandlerMetrics(
                successes=successes,
                failures=failures,
                avg_latency_ms=round(avg_lat, 2),
            )

        return HealthResponse(
            total_requests=total,
            requests_by_status=by_status,
            handlers=handlers,
        )


# Instancia singleton
store = RequestStore()
```

**Verificar:**
```bash
uv run python -c "
from app.store import store
from app.models import RequestRecord, RequestPayload
r = RequestRecord(payload=RequestPayload())
store.save(r)
print(f'Guardado: {store.get(r.id).id}')
print(f'Salud: {store.health().total_requests} solicitudes')
"
# Esperado: Guardado: <uuid> / Salud: 1 solicitudes
```

**Rollback:**
```bash
rm app/store.py
```

---

## Paso 5: Crear handlers deterministicos
**Archivo:** `app/handlers.py`
**Accion:** CREATE
**Por que:** Handlers simulados con escenarios deterministicos — nucleo de la simulacion de fallos. Nota: `ConnectionError` y `ValueError` usados aqui son builtins de Python, NO importados de `retry.py`; `retry.py` solo los reconoce en `RETRYABLE_EXCEPTIONS` para decidir si reintentar.
**Paralelo:** NO (depende del Paso 2 para los modelos; Paso 3 no es requerido porque los tipos de excepcion son builtins de Python)

**Despues:**
```python
from __future__ import annotations

import asyncio

# Rastrear intentos de fallos transitorios por request y handler
_transient_counters: dict[str, int] = {}


def _get_counter_key(request_id: str, handler_name: str) -> str:
    return f"{request_id}:{handler_name}"


async def _execute_scenario(scenario: str, request_id: str, handler_name: str) -> str:
    """Ejecuta un escenario deterministico."""
    if scenario == "ok":
        return f"{handler_name}: procesado exitosamente"

    if scenario == "timeout":
        await asyncio.sleep(999)  # Sera cancelado por el timeout
        return "inalcanzable"

    if scenario == "transient_fail_then_ok":
        key = _get_counter_key(request_id, handler_name)
        count = _transient_counters.get(key, 0) + 1
        _transient_counters[key] = count
        if count <= 2:  # Falla los primeros 2 intentos, exito en el 3ro
            raise ConnectionError(f"fallo transitorio (intento {count})")
        return f"{handler_name}: recuperado despues de {count - 1} fallos"

    if scenario == "hard_fail":
        raise ValueError(f"{handler_name}: fallo no reintentable")

    raise ValueError(f"Escenario desconocido: {scenario}")


async def primary_handler(scenario: str, request_id: str) -> str:
    """Handler requerido — su fallo significa fallo del request."""
    return await _execute_scenario(scenario, request_id, "primary_handler")


async def optional_handler(scenario: str, request_id: str) -> str:
    """Handler opcional — su fallo significa modo degradado."""
    return await _execute_scenario(scenario, request_id, "optional_handler")
```

**Verificar:**
```bash
uv run python -c "
import asyncio
from app.handlers import primary_handler

async def test():
    r1 = await primary_handler('ok', 'test-1')
    print(f'OK: {r1}')
    try:
        await primary_handler('hard_fail', 'test-2')
    except ValueError as e:
        print(f'Fallo duro: {e}')

asyncio.run(test())
"
# Esperado: OK: primary_handler: procesado exitosamente / Fallo duro: ...
```

**Rollback:**
```bash
rm app/handlers.py
```

---

## Paso 6: Crear orquestador del pipeline
**Archivo:** `app/pipeline.py`
**Accion:** CREATE
**Por que:** Orquesta la ejecucion paralela de handlers con reintentos, timeouts y logica de degradacion
**Paralelo:** NO (depende de los Pasos 3, 4, 5)

**Despues:**
```python
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
```

**Verificar:**
```bash
uv run python -c "
import asyncio
from app.models import RequestRecord, RequestPayload
from app.pipeline import process_request
from app.store import store

async def test():
    r = RequestRecord(payload=RequestPayload(scenario='ok'))
    store.save(r)
    await process_request(r)
    print(f'Estado: {r.status.value}, Degradado: {r.degraded}')

asyncio.run(test())
"
# Esperado: Estado: completed, Degradado: False
```

**Rollback:**
```bash
rm app/pipeline.py
```

---

## Paso 7: Crear aplicacion FastAPI y endpoints
**Archivo:** `app/main.py`
**Accion:** CREATE
**Por que:** Capa API que expone el pipeline — punto de entrada del entregable
**Paralelo:** NO (depende del Paso 6)

**Despues:**
```python
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
```

**Verificar:**
```bash
uv run python -c "
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
resp = client.get('/health')
print(f'Estado de salud: {resp.status_code} - {resp.json()[\"status\"]}')
resp2 = client.post('/requests', json={'scenario': 'ok'})
print(f'POST /requests: {resp2.status_code} - {resp2.json()[\"status\"]}')"
# Esperado: Estado de salud: 200 - healthy / POST /requests: 202 - pending
```

**Rollback:**
```bash
rm app/main.py
```

---

## Paso 8: Crear pruebas unitarias de reintentos
**Archivo:** `tests/test_retry.py`
**Accion:** CREATE
**Por que:** Valida la formula de backoff, limites de jitter, excepciones reintentables vs no reintentables
**Paralelo:** SI (con Pasos 9, 10)

**Despues:**
```python
import asyncio

import pytest

from app.retry import RetryConfig, calculate_delay, run_with_retry
from app.models import HandlerStatus


class TestCalculateDelay:
    def test_crecimiento_exponencial(self):
        config = RetryConfig(base_delay=1.0, max_delay=100.0, jitter_max=0.0)
        assert calculate_delay(0, config) == 1.0
        assert calculate_delay(1, config) == 2.0
        assert calculate_delay(2, config) == 4.0
        assert calculate_delay(3, config) == 8.0

    def test_tope_maximo_delay(self):
        config = RetryConfig(base_delay=1.0, max_delay=5.0, jitter_max=0.0)
        assert calculate_delay(10, config) == 5.0

    def test_jitter_dentro_de_limites(self):
        config = RetryConfig(base_delay=1.0, max_delay=100.0, jitter_max=0.5)
        for _ in range(100):
            delay = calculate_delay(0, config)
            assert 1.0 <= delay <= 1.5


class TestRunWithRetry:
    async def test_exito_en_primer_intento(self):
        async def handler():
            return "ok"

        result = await run_with_retry(handler, config=RetryConfig())
        assert result.status == HandlerStatus.SUCCESS
        assert result.result == "ok"
        assert len(result.attempts) == 1
        assert result.attempts[0].error is None

    async def test_reintenta_con_connection_error(self):
        call_count = 0

        async def handler():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fallo")
            return "recuperado"

        config = RetryConfig(max_retries=3, base_delay=0.01, jitter_max=0.0)
        result = await run_with_retry(handler, config=config)
        assert result.status == HandlerStatus.SUCCESS
        assert result.result == "recuperado"
        assert len(result.attempts) == 3
        assert result.attempts[0].error == "ConnectionError"
        assert result.attempts[1].error == "ConnectionError"
        assert result.attempts[2].error is None

    async def test_sin_reintento_con_value_error(self):
        async def handler():
            raise ValueError("entrada invalida")

        config = RetryConfig(max_retries=3, base_delay=0.01, jitter_max=0.0)
        result = await run_with_retry(handler, config=config)
        assert result.status == HandlerStatus.FAILED
        assert len(result.attempts) == 1
        assert result.attempts[0].error == "ValueError"

    async def test_timeout_es_reintentable(self):
        async def handler():
            await asyncio.sleep(999)

        config = RetryConfig(max_retries=1, base_delay=0.01, jitter_max=0.0, timeout=0.05)
        result = await run_with_retry(handler, config=config)
        assert result.status == HandlerStatus.TIMEOUT
        assert len(result.attempts) == 2  # 1 inicial + 1 reintento

    async def test_delays_se_registran(self):
        call_count = 0

        async def handler():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fallo")
            return "ok"

        config = RetryConfig(max_retries=3, base_delay=0.01, max_delay=1.0, jitter_max=0.0)
        result = await run_with_retry(handler, config=config)
        assert result.attempts[0].delay_applied == 0.0  # primer intento, sin delay
        assert result.attempts[1].delay_applied > 0  # segundo intento tiene delay
        assert result.attempts[2].delay_applied > result.attempts[1].delay_applied  # crecimiento exponencial
```

**Verificar:**
```bash
uv run pytest tests/test_retry.py -v
# Esperado: todas las pruebas pasan
```

**Rollback:**
```bash
rm tests/test_retry.py
```

---

## Paso 9: Crear pruebas de integracion del pipeline
**Archivo:** `tests/test_pipeline.py`
**Accion:** CREATE
**Por que:** Valida el comportamiento critico: ejecucion paralela, degradacion, propagacion de fallos
**Paralelo:** SI (con Pasos 8, 10)

**Despues:**
```python
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
```

**Verificar:**
```bash
uv run pytest tests/test_pipeline.py -v
# Esperado: todas las pruebas pasan
```

**Rollback:**
```bash
rm tests/test_pipeline.py
```

---

## Paso 10: Crear pruebas de endpoints de la API
**Archivo:** `tests/test_api.py`
**Accion:** CREATE
**Por que:** Valida la capa HTTP — codigos de estado, forma de las respuestas, flujo de polling
**Paralelo:** SI (con Pasos 8, 9)

**Despues:**
```python
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.store import store
from app import handlers


async def poll_until_done(client: AsyncClient, request_id: str, timeout: float = 5.0, interval: float = 0.05) -> dict:
    """Hace polling a GET /requests/{id} hasta que el estado no sea pending ni running, o se agote el timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/requests/{request_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] not in ("pending", "running"):
            return data
        await asyncio.sleep(interval)
    raise TimeoutError(f"La solicitud {request_id} no termino en {timeout}s")


@pytest.fixture(autouse=True)
def limpiar_estado():
    store._requests.clear()
    handlers._transient_counters.clear()
    yield
    store._requests.clear()
    handlers._transient_counters.clear()


class TestAPI:
    async def test_crear_request_retorna_202(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/requests", json={"scenario": "ok"})
        assert resp.status_code == 202
        data = resp.json()
        assert "request_id" in data
        assert data["status"] == "pending"

    async def test_request_no_encontrado(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/requests/id-inexistente")
        assert resp.status_code == 404

    async def test_obtener_request_despues_de_procesar(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/requests", json={"scenario": "ok"})
            request_id = resp.json()["request_id"]

            # Hacer polling hasta que el procesamiento en background termine
            data = await poll_until_done(client, request_id)

        assert data["status"] == "completed"
        assert data["degraded"] is False

    async def test_endpoint_health(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "total_requests" in data
        assert "handlers" in data

    async def test_request_degradado_via_api(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/requests",
                json={"scenario": "ok", "optional_scenario": "hard_fail"},
            )
            request_id = resp.json()["request_id"]

            # Hacer polling hasta que el procesamiento en background termine
            data = await poll_until_done(client, request_id)

        assert data["status"] == "completed"
        assert data["degraded"] is True
        assert data["degradation_reason"] is not None
```

**Verificar:**
```bash
uv run pytest tests/test_api.py -v
# Esperado: todas las pruebas pasan
```

**Rollback:**
```bash
rm tests/test_api.py
```

---

## Paso 11: Crear README.md y NOTES.md
**Archivo:** `README.md` y `NOTES.md`
**Accion:** CREATE
**Por que:** Entregables requeridos — instrucciones de setup y decisiones tecnicas
**Paralelo:** SI (ambos archivos son independientes)

**Despues (README.md):**
```markdown
# Resilient Processing Pipeline

Microservicio FastAPI que procesa solicitudes usando dos handlers en paralelo con reintentos, backoff, degradacion elegante y observabilidad.

## Inicio Rapido

```bash
# Clonar y configurar
uv sync

# Ejecutar el servidor
uv run uvicorn app.main:app --reload --port 8000
```

## Uso de la API

### Crear una solicitud
```bash
curl -X POST http://localhost:8000/requests \
  -H "Content-Type: application/json" \
  -d '{"scenario": "ok"}'
```

### Consultar estado de la solicitud
```bash
curl http://localhost:8000/requests/{request_id}
```

### Verificacion de salud
```bash
curl http://localhost:8000/health
```

## Escenarios

| Escenario | Comportamiento |
|-----------|---------------|
| `ok` | Ambos handlers tienen exito |
| `timeout` | El handler excede el timeout (reintentable) |
| `transient_fail_then_ok` | Falla dos veces, tiene exito en el tercer intento |
| `hard_fail` | Fallo no reintentable (ValueError) |

### Probar modo degradado
```bash
curl -X POST http://localhost:8000/requests \
  -H "Content-Type: application/json" \
  -d '{"scenario": "ok", "optional_scenario": "hard_fail"}'
```

## Ejecutar Pruebas

```bash
uv run pytest tests/ -v
```

## Video Walkthrough

[Enlace al video walkthrough](TODO)
```

**Despues (NOTES.md):**
```markdown
# Notas Tecnicas

## Enfoque de Concurrencia: asyncio

Elegi `asyncio` sobre Celery porque este es un servicio in-memory con dos handlers concurrentes.

- `asyncio.gather()` ejecuta ambos handlers en paralelo dentro de un solo proceso
- `asyncio.wait_for()` proporciona timeouts nativos por handler
- No se requiere infraestructura externa (sin Redis, sin broker, sin workers separados)
- FastAPI ya es async-nativo, asi que la integracion es transparente

Celery seria la opcion correcta para procesamiento distribuido entre multiples servidores, colas de tareas persistentes, o trabajos de larga duracion (minutos). Para dos handlers in-memory, asyncio es la herramienta correcta.

## Reintentos, Backoff y Jitter

### Formula
```
delay = min(base_delay * 2^intento, max_delay) + random(0, jitter_max)
```

### Decisiones
- **Jitter aditivo** (no multiplicativo): limites mas predecibles, suficiente para prevenir thundering herd
- **Tope maximo de delay**: previene tiempos de espera irrazonables en conteos altos de reintentos
- **Solo excepciones reintentables**: `TimeoutError`, `ConnectionError`, `ConnectionRefusedError` — fallos transitorios que pueden resolverse al reintentar. Todas las demas excepciones fallan inmediatamente
- **Primer intento sin delay**: no hay razon para esperar antes del primer intento

## Compromisos y Limitaciones

1. **Almacenamiento in-memory**: todos los datos se pierden al reiniciar. En produccion, usaria Redis o una base de datos
2. **Sin limite de solicitudes**: el store crece sin limites. Un sistema en produccion necesitaria TTL o desalojo LRU
3. **Referencias a tareas en background**: almacenadas en un set para prevenir garbage collection — un patron conocido de asyncio
4. **Estado de contadores transitorios**: el dict `_transient_counters` en handlers tambien crece sin limites, aceptable para un escenario de prueba
5. **asyncio es single-threaded**: sin paralelismo real para trabajo CPU-bound, pero nuestros handlers son I/O-bound (simulados con sleep), asi que esto esta bien
```

**Verificar:**
```bash
test -f README.md && test -f NOTES.md && echo "Documentacion creada"
# Esperado: Documentacion creada
```

**Rollback:**
```bash
rm README.md NOTES.md
```

---

## Validacion Final
- [ ] Suite completa de pruebas: `uv run pytest tests/ -v`
- [ ] Servidor arranca: `uv run uvicorn app.main:app --port 8000`
- [ ] POST /requests retorna 202
- [ ] GET /requests/{id} muestra estado completado/degradado despues del procesamiento
- [ ] GET /health retorna metricas
- [ ] Sin regresiones: las 18 pruebas pasan (8 retry + 5 pipeline + 5 API)

## Notas de Implementacion
- Todos los archivos son operaciones CREATE — proyecto greenfield
- Pasos 2+3 pueden hacerse en paralelo, luego 4+5 secuencialmente, luego 6, luego 7
- Las pruebas (8, 9, 10) son independientes y pueden correr en paralelo
- El dict `_transient_counters` en handlers.py usa `request_id` para aislar estado entre requests concurrentes
- `return_exceptions=True` en `asyncio.gather` es critico — sin el, un fallo del handler optional cancelaria el resultado del handler primary
- El bucle de reintentos usa `range(1, max_retries + 2)` porque el intento 1 es el inicial, luego hasta `max_retries` reintentos adicionales
- Los fixtures de prueba limpian tanto `store._requests` como `handlers._transient_counters`
- El test de timeout usa `monkeypatch` para sobreescribir `OPTIONAL_CONFIG` con timeouts rapidos

## Orquestacion de Equipo

> Esta seccion mapea pasos a companeros de equipo para ejecucion paralela.

### Companeros de Equipo

| Companero | Agente | Modelo | Pasos | Archivos |
|-----------|--------|--------|-------|----------|
| `fundacion` | generico | sonnet | 1 | `pyproject.toml`, `app/__init__.py`, `tests/__init__.py` |
| `modelos` | generico | sonnet | 2 | `app/models.py` |
| `motor-retry` | generico | opus | 3 | `app/retry.py` |
| `store-handlers` | generico | sonnet | 4, 5 | `app/store.py`, `app/handlers.py` |
| `orquestador` | generico | opus | 6, 7 | `app/pipeline.py`, `app/main.py` |
| `pruebas` | generico | sonnet | 8, 9, 10 | `tests/test_retry.py`, `tests/test_pipeline.py`, `tests/test_api.py` |
| `documentacion` | generico | haiku | 11 | `README.md`, `NOTES.md` |

### Orden de Ejecucion

```
Fase 1 (paralelo): fundacion + modelos + motor-retry
Fase 2 (secuencial): store-handlers (depende de modelos)
Fase 3 (secuencial): orquestador (depende de motor-retry + store-handlers)
Fase 4 (paralelo): pruebas + documentacion (depende de orquestador)
```

---

## Historial de Revisiones

### Ajuste 1 — 2026-03-31
- **Review fuente:** `specs/REVIEW_VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`
- **Blockers corregidos:** 4
  - `__init__.py` movido al Paso 1 (antes estaba en Paso 11)
  - Configuracion `asyncio_mode = "auto"` agregada a `pyproject.toml` en Paso 1
  - Limpieza de `_transient_counters` agregada a fixtures en Pasos 9 y 10
  - Test de timeout usa `monkeypatch` con config rapida en Paso 9
- **Warnings corregidos:** 5
  - Paso 5: nota aclarando que excepciones son builtins de Python
  - Paso 7: verificacion ahora incluye POST /requests ademas de GET /health
  - Paso 10: `asyncio.sleep(0.5)` reemplazado por `poll_until_done()` deterministico
  - Paso 11: rollback ya presente
  - Validacion final: conteo de pruebas verificado (18 total)
- **Pasos agregados:** ninguno
- **Pasos eliminados:** ninguno (Paso 11 original se fusiono en Paso 1)
- **Pasos modificados:** 1, 5, 9, 10
- **Warnings restantes:** 5 (todos aceptables — redundancia de excepciones, memory leak en demo, cobertura parcial de test_transient)
