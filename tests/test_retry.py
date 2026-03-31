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
