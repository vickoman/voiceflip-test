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
