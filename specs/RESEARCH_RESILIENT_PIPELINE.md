# Investigacion: Pipeline de Procesamiento Resiliente

## 1. Objetivo del Proyecto

Construir un microservicio in-memory con FastAPI que procese solicitudes usando dos handlers en paralelo (`primary_handler` y `optional_handler`), con soporte para fallos parciales, reintentos con backoff exponencial, y degradacion elegante.

---

## 2. Stack Tecnologico

| Tecnologia | Version | Proposito |
|------------|---------|-----------|
| Python | 3.11+ | Lenguaje base |
| FastAPI | 0.115+ | Framework HTTP / API |
| Uvicorn | 0.34+ | Servidor ASGI |
| asyncio | stdlib | Concurrencia (gather, wait_for) |
| Pydantic | 2.x | Validacion de esquemas |
| pytest | 8.x | Pruebas |
| pytest-asyncio | 0.24+ | Pruebas asincronas |
| httpx | 0.28+ | Cliente de pruebas para FastAPI |

### Sin infraestructura externa

No se requiere: Redis, RabbitMQ, base de datos, Docker. Todo corre in-memory en un solo proceso.

---

## 3. Arquitectura General

```
POST /requests
     │
     ▼
┌─────────────────────────────┐
│  Endpoint FastAPI            │
│  - Crea RequestRecord       │
│  - estado: pending          │
│  - Lanza tarea en background│
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Orquestador del Pipeline    │
│  - estado: running          │
│  - asyncio.gather(          │
│      ejecutar_con_retry(primary),│
│      ejecutar_con_retry(optional)│
│    )                         │
└──────┬──────────┬───────────┘
       │          │
       ▼          ▼
   primary    optional
   handler    handler
       │          │
       ▼          ▼
┌─────────────────────────────┐
│  Agregacion de Resultados    │
│  - primary OK + optional OK │
│    → completed, degraded=F  │
│  - primary OK + optional FALLO│
│    → completed, degraded=T  │
│  - primary FALLO            │
│    → failed                 │
└─────────────────────────────┘
```

---

## 4. Modelo de Datos: RequestRecord

```python
{
    "id": "uuid4",
    "payload": dict,
    "status": "pending | running | completed | failed",
    "degraded": bool,
    "degradation_reason": str | None,
    "created_at": datetime,
    "started_at": datetime | None,
    "finished_at": datetime | None,
    "result": {
        "primary_handler": {
            "status": "success | failed | timeout",
            "result": any,
            "latency_ms": float,
            "attempts": [
                {
                    "attempt": int,
                    "delay_applied": float,
                    "error": str | None,
                    "timestamp": datetime
                }
            ]
        },
        "optional_handler": { ... mismo esquema ... }
    }
}
```

### Almacenamiento

- `Dict[str, RequestRecord]` en memoria (diccionario simple con UUID como clave)
- No se necesita persistencia — la prueba pide in-memory

---

## 5. Concurrencia con asyncio

### Ejecucion paralela

```python
tarea_primary = ejecutar_con_retry(primary_handler, payload, config)
tarea_optional = ejecutar_con_retry(optional_handler, payload, config)

resultados = await asyncio.gather(tarea_primary, tarea_optional, return_exceptions=True)
```

`return_exceptions=True` es clave: evita que el fallo de un handler cancele el otro. Ambos resultados se reciben como valores (o excepciones) en la lista.

### Timeouts por handler

```python
resultado = await asyncio.wait_for(handler(payload), timeout=config.timeout)
```

- Si se excede el timeout, lanza `asyncio.TimeoutError`
- Este error ES reintentable segun los requisitos

### Procesamiento en background

Se usa `asyncio.create_task()` para que el POST retorne inmediatamente con `status: pending` mientras el pipeline corre en segundo plano.

---

## 6. Reintentos con Backoff Exponencial y Jitter

### Formula

```
delay = min(base_delay * (2 ** intento), max_delay) + random(0, jitter_max)
```

### Parametros configurables

| Parametro | Default | Descripcion |
|-----------|---------|-------------|
| max_retries | 3 | Numero maximo de reintentos |
| base_delay | 0.5s | Delay base antes del primer reintento |
| max_delay | 10s | Tope maximo del delay |
| jitter_max | 0.5s | Aleatoriedad maxima agregada |

### Ejemplo de delays

```
Intento 1: min(0.5 * 2^0, 10) + rand(0, 0.5) = 0.5 + 0.23 = 0.73s
Intento 2: min(0.5 * 2^1, 10) + rand(0, 0.5) = 1.0 + 0.41 = 1.41s
Intento 3: min(0.5 * 2^2, 10) + rand(0, 0.5) = 2.0 + 0.12 = 2.12s
```

### Errores reintentables

```python
EXCEPCIONES_REINTENTABLES = (TimeoutError, asyncio.TimeoutError, ConnectionError, ConnectionRefusedError)
```

Nota: `ConnectionRefusedError` es subclase de `ConnectionError` en Python, pero se lista explicitamente por claridad.

### Errores NO reintentables

Cualquier otra excepcion (ValueError, RuntimeError, etc.) falla inmediatamente sin reintentos.

### Registro por intento

Cada intento se registra con:
- `attempt`: numero secuencial (1, 2, 3...)
- `delay_applied`: tiempo real esperado antes de este intento
- `error`: cadena del error o None si fue exitoso
- `timestamp`: cuando ocurrio

---

## 7. Handlers y Simulacion Determinista

### Escenarios via payload

El campo `payload.scenario` controla el comportamiento del handler:

| Escenario | Comportamiento |
|-----------|---------------|
| `ok` | Retorna exito inmediato |
| `timeout` | Ejecuta `await asyncio.sleep(999)` para forzar timeout |
| `transient_fail_then_ok` | Lanza `ConnectionError` las primeras N veces, luego retorna exito |
| `hard_fail` | Lanza `ValueError` (no reintentable) |

### Control independiente por handler

```json
{
    "scenario": "ok",
    "optional_scenario": "hard_fail"
}
```

- `scenario` controla el `primary_handler`
- `optional_scenario` controla el `optional_handler` (si no se especifica, usa el valor de `scenario`)

Esto permite probar combinaciones como:
- primary OK + optional falla → degradado
- primary falla + optional OK → fallido
- ambos OK → completado
- ambos fallan → fallido

---

## 8. Endpoints de la API

### POST /requests

**Solicitud:**
```json
{
    "scenario": "ok",
    "optional_scenario": "timeout"
}
```

**Respuesta (202 Accepted):**
```json
{
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending"
}
```

Codigo 202 porque el procesamiento es asincrono.

### GET /requests/{request_id}

**Respuesta (200):**
```json
{
    "id": "550e8400-...",
    "status": "completed",
    "degraded": true,
    "degradation_reason": "optional_handler fallo: TimeoutError",
    "created_at": "2026-03-31T10:00:00Z",
    "started_at": "2026-03-31T10:00:00.001Z",
    "finished_at": "2026-03-31T10:00:02.500Z",
    "result": {
        "primary_handler": {
            "status": "success",
            "result": "processed",
            "latency_ms": 150.5,
            "attempts": [
                {"attempt": 1, "delay_applied": 0, "error": null, "timestamp": "..."}
            ]
        },
        "optional_handler": {
            "status": "timeout",
            "result": null,
            "latency_ms": 3000.0,
            "attempts": [
                {"attempt": 1, "delay_applied": 0, "error": "TimeoutError", "timestamp": "..."},
                {"attempt": 2, "delay_applied": 0.73, "error": "TimeoutError", "timestamp": "..."},
                {"attempt": 3, "delay_applied": 1.41, "error": "TimeoutError", "timestamp": "..."}
            ]
        }
    }
}
```

**404** si el request_id no existe.

### GET /health

**Respuesta (200):**
```json
{
    "status": "healthy",
    "total_requests": 42,
    "solicitudes_por_estado": {
        "pending": 1,
        "running": 2,
        "completed": 35,
        "failed": 4
    },
    "handlers": {
        "primary_handler": {
            "exitos": 38,
            "fallos": 4,
            "latencia_promedio_ms": 230.5
        },
        "optional_handler": {
            "exitos": 30,
            "fallos": 12,
            "latencia_promedio_ms": 450.2
        }
    }
}
```

---

## 9. Estructura de Archivos

```
voiceflip-test/
├── app/
│   ├── __init__.py
│   ├── main.py            # App FastAPI, endpoints, lifespan
│   ├── models.py           # Modelos Pydantic y RequestRecord
│   ├── handlers.py         # primary_handler, optional_handler, escenarios
│   ├── retry.py            # retry_with_backoff() con jitter
│   ├── pipeline.py         # orquestacion: gather, timeouts, degradacion
│   └── store.py            # RequestStore (dict in-memory + metricas)
├── tests/
│   ├── __init__.py
│   ├── test_pipeline.py    # pruebas del pipeline completo
│   ├── test_retry.py       # pruebas de retry/backoff/jitter
│   └── test_api.py         # pruebas de endpoints
├── specs/
│   └── RESEARCH_RESILIENT_PIPELINE.md
├── requirements.txt
├── README.md
└── NOTES.md
```

---

## 10. Casos de Prueba Criticos (minimo 3)

### Prueba 1: Camino feliz — ambos handlers exitosos
- Escenario: `{"scenario": "ok"}`
- Esperado: `status=completed`, `degraded=false`, ambos handlers con 1 intento

### Prueba 2: Degradacion elegante — optional falla, primary OK
- Escenario: `{"scenario": "ok", "optional_scenario": "hard_fail"}`
- Esperado: `status=completed`, `degraded=true`, `degradation_reason` presente

### Prueba 3: Fallo total — primary falla
- Escenario: `{"scenario": "hard_fail"}`
- Esperado: `status=failed`, primary con 1 intento (sin retry por ser ValueError)

### Prueba 4: Reintento con fallo transitorio
- Escenario: `{"scenario": "transient_fail_then_ok"}`
- Esperado: `status=completed`, primary con multiples intentos, delays crecientes

### Prueba 5: Timeout del optional no bloquea resultado
- Escenario: `{"scenario": "ok", "optional_scenario": "timeout"}`
- Esperado: `status=completed`, `degraded=true`, optional muestra TimeoutError en intentos

---

## 11. Decisiones Clave

| Decision | Justificacion |
|----------|--------------|
| asyncio sobre Celery | In-memory, sin infraestructura externa, gather() para 2 tareas |
| return_exceptions=True | Permite capturar fallo del optional sin cancelar primary |
| 202 en POST | Procesamiento asincrono, el cliente debe hacer polling |
| Dict in-memory como almacen | Requisito de la prueba, sin persistencia |
| Timeouts configurables por handler | Permite diferentes SLAs para primary vs optional |
| Jitter aditivo (no multiplicativo) | Mas predecible, suficiente para evitar thundering herd |

---

## 12. Riesgos y Mitigaciones

| Riesgo | Mitigacion |
|--------|-----------|
| Fuga de memoria si se acumulan requests | Para esta prueba es aceptable; en produccion se agregaria TTL o LRU |
| Condiciones de carrera en el almacen | asyncio es single-threaded, no hay condiciones de carrera reales |
| Timeouts muy bajos en pruebas causan inestabilidad | Usar valores controlados y mock de sleep donde sea necesario |
| La tarea en background puede perder la referencia | Guardar referencia en set para evitar garbage collection |
