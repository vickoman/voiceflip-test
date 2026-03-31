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
