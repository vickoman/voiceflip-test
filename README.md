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