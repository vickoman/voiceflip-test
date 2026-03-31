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

            # Polling deterministico en lugar de sleep fijo
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

            # Polling deterministico en lugar de sleep fijo
            data = await poll_until_done(client, request_id)

        assert data["status"] == "completed"
        assert data["degraded"] is True
        assert data["degradation_reason"] is not None
