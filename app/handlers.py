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
