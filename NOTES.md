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
