# Plan Review Report

> RPI Review phase — Senior engineer audit
> Plan file: `specs/VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`
> Research file: `specs/RESEARCH_RESILIENT_PIPELINE.md`
> Mode: NORMAL
> Date: 2026-03-31

## Verdict: ⚠️ APPROVED WITH WARNINGS

The plan is architecturally sound and the code snippets are complete and correct. However, there are **3 blockers** that must be fixed before implementation: missing `__init__.py` ordering, missing `pytest-asyncio` configuration, and a slow timeout test scenario (~12s). All are quick fixes.

## Score Summary

| Pillar | Score | Blockers | Warnings |
|--------|-------|----------|----------|
| 1. Approach & Order | ⚠️ | 1 | 2 |
| 2. Code Snippets | ✅ | 0 | 4 |
| 3. Test Strategy | ❌ | 3 | 4 |
| 4. Intent Compression | ✅ | 0 | 0 |
| **Total** | | **4** | **10** |

## Research <> Plan Alignment
- [x] All research findings addressed: YES
- [x] No contradictions with research: YES
- [x] All foot guns accounted for: YES (background task GC, memory growth, race conditions)
- [x] File/line references match research: YES (greenfield, no existing code to mismatch)

All 12 research sections are covered by the plan's 13 steps. The dependency graph matches the architecture diagram from research. Retry formula, exception taxonomy, and handler scenarios all match the research specification.

---

## Pillar 1: Approach & Order

### [BLOCKER] `__init__.py` files created too late (Step 11)
**Step(s):** 11, affects 2-7
**Issue:** Step 11 creates `app/__init__.py` AFTER Steps 2-7 create modules inside `app/`. Any step that imports from `app` (e.g., `app/store.py` importing `app.models`) requires `app/__init__.py` to exist. The verification commands in Steps 2-7 will fail with `ModuleNotFoundError`.
**Recommendation:** Move `app/__init__.py` creation to Step 1 (alongside `requirements.txt`). Move `tests/__init__.py` to just before Step 8.

### [WARNING] No explicit directory creation step
**Step(s):** Before Step 2
**Issue:** No step creates `app/` and `tests/` directories. While file-writing tooling may create parent dirs, the plan should be explicit.
**Recommendation:** Add `mkdir -p app tests` to Step 1.

### [WARNING] Step 5 dependency incomplete
**Step(s):** 5
**Issue:** Step 5 lists only Step 2 as dependency, but the handlers raise exceptions that must match the retryable exception tuple defined in Step 3 (`retry.py`). In practice this works because the exceptions are Python builtins (`ConnectionError`, `ValueError`), but the dependency should be acknowledged.
**Recommendation:** Add Step 3 as a soft dependency for Step 5, or note in the step that exception types used are Python builtins, not imported from `retry.py`.

### [OK] Core architecture and approach
The bottom-up construction order (models → retry/store/handlers → pipeline → API → tests) is correct and idiomatic. The asyncio approach, `return_exceptions=True` pattern, and separation of concerns are all sound.

---

## Pillar 2: Code Snippet Audit

### [OK] All code snippets are syntactically and logically correct
After thorough line-by-line audit of all 13 steps, no syntax errors, logic errors, import mismatches, or type errors were found. The retry loop math is correct, the `asyncio.gather` usage is proper, and the Pydantic v2 models are valid.

### [WARNING] Redundant exceptions in `RETRYABLE_EXCEPTIONS`
**Step:** 3 — `app/retry.py`
**Problem:** `ConnectionRefusedError` is a subclass of `ConnectionError`, so listing both is redundant. Similarly, in Python 3.11+, `asyncio.TimeoutError` is an alias for `TimeoutError`.
**In snippet:** `RETRYABLE_EXCEPTIONS = (TimeoutError, asyncio.TimeoutError, ConnectionError, ConnectionRefusedError)`
**Should be:** Keep as-is — the redundancy is harmless and makes the exception list self-documenting per the test requirements.
**Impact:** None. The research explicitly lists all four exceptions, so keeping them matches the spec.

### [WARNING] `_transient_counters` memory leak
**Step:** 5 — `app/handlers.py`
**Problem:** Module-level dict grows unboundedly. Entries are never cleaned up.
**Impact:** Acceptable for a demo/test app. The research acknowledges this in "Risks and Mitigations."

### [WARNING] `asyncio.sleep(0.5)` race condition in tests
**Step:** 10 — `tests/test_api.py`
**Problem:** `test_get_request_after_processing` and `test_degraded_request_via_api` rely on `asyncio.sleep(0.5)` for background task completion. This is inherently flaky under CI load.
**Should be:** A polling loop with short timeout, e.g., poll `GET /requests/{id}` until status is not `pending`/`running`.
**Impact:** Potential flaky test failures in slow environments.

### [WARNING] Test coverage gap in `test_pipeline.py`
**Step:** 9 — `tests/test_pipeline.py`
**Problem:** `test_transient_failure_recovers` doesn't verify the optional handler's behavior (which also runs `transient_fail_then_ok` by default).
**Impact:** Minor — the primary handler path is the critical one being tested.

---

## Pillar 3: Test Strategy

### [BLOCKER] Missing `pytest-asyncio` mode configuration
**Step(s):** 8, 9, 10
**Issue:** With `pytest-asyncio >= 0.21` (plan pins `0.25.3`), the default asyncio mode is `strict`. The plan has no `conftest.py`, `pytest.ini`, or `pyproject.toml` setting `asyncio_mode = "auto"`. All async tests use `@pytest.mark.asyncio` per-method which works in strict mode but requires proper configuration. Without explicit mode configuration, tests may produce warnings or behave unpredictably.
**Recommendation:** Add a new step (Step 7.5) creating either:
```python
# tests/conftest.py
import pytest

pytest_plugins = ['pytest_asyncio']
```
And a `pyproject.toml` or `pytest.ini`:
```ini
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### [BLOCKER] `_transient_counters` not reset between tests
**Step:** 9 — `tests/test_pipeline.py`
**Issue:** The `clean_store` fixture clears `store._requests` but never resets `app.handlers._transient_counters`. While each test uses a unique `request_id` (UUID), if tests are re-run or if future tests reuse IDs, counter state will leak. More critically, the `_transient_counters` dict grows across the test suite.
**Recommendation:** Add to `clean_store` fixture:
```python
from app import handlers
handlers._transient_counters.clear()
```

### [BLOCKER] Timeout test scenario takes ~12 seconds
**Step:** 9 — `tests/test_pipeline.py::test_optional_timeout_does_not_block`
**Issue:** `OPTIONAL_CONFIG` has `max_retries=3` and `timeout=3.0`. The `timeout` scenario will retry 3 times, each timing out after 3s, plus backoff delays between retries. Total: ~12+ seconds for one test.
**Recommendation:** Override the pipeline config in this test to use fast timeouts:
```python
import app.pipeline as pipeline
original = pipeline.OPTIONAL_CONFIG
pipeline.OPTIONAL_CONFIG = RetryConfig(max_retries=1, timeout=0.1, base_delay=0.01, jitter_max=0.0)
# ... run test ...
pipeline.OPTIONAL_CONFIG = original
```

### [WARNING] Step verification commands are happy-path only
**Steps:** 3, 5, 6, 7
**Issue:** Step-level verification commands only test the success path. The retry loop, failure scenarios, and degradation logic are verified only by the formal test suite in Steps 8-10.
**Recommendation:** Acceptable for a build plan — step verifications are smoke tests, full coverage is in the test suite.

### [WARNING] Step 7 verification is weak
**Step:** 7
**Issue:** Uses `TestClient` for `/health` only. The plan acknowledges `TestClient` doesn't support async background tasks. POST `/requests` is not verified at step level.
**Recommendation:** Add POST verification to the step command.

### [WARNING] Step 11 has no rollback block
**Step:** 11
**Issue:** Unlike every other step, no `Rollback:` section is defined.
**Recommendation:** Add `rm app/__init__.py tests/__init__.py`.

### [WARNING] Final validation underspecified
**Issue:** "No regressions: all 5 test scenarios pass" — but there are 18 total tests across 3 files. The "5" is ambiguous.
**Recommendation:** Change to: "All 18 tests pass across test_retry.py, test_pipeline.py, and test_api.py."

---

## Pillar 4: Intent Compression

### [OK] Plan includes complete code for every step
The plan provides full, copy-paste-ready code snippets for all 13 steps. Every model, function signature, import, and test assertion is explicitly defined. There are no ambiguous instructions — the implementer has the exact code to write. The WHY fields adequately explain the rationale for each step.

Note: While step descriptions are high-level, the complete code blocks serve as the authoritative specification, eliminating ambiguity.

---

## Source Code Validation

Greenfield project — no existing source files to cross-reference. All steps are CREATE operations. N/A.

---

## Action Items

### Blockers (must fix before implementation)
1. **[Step 11→1]** Move `app/__init__.py` creation before Step 2 — Pillar 1
2. **[New step]** Add `pytest-asyncio` configuration (`asyncio_mode = "auto"`) — Pillar 3
3. **[Step 9]** Reset `_transient_counters` in test fixture — Pillar 3
4. **[Step 9]** Override `OPTIONAL_CONFIG` timeout in `test_optional_timeout_does_not_block` to avoid ~12s test — Pillar 3

### Warnings (recommended fixes)
1. **[Step 1]** Add `mkdir -p app tests` to directory creation — Pillar 1
2. **[Step 10]** Replace `asyncio.sleep(0.5)` with polling loop — Pillar 2
3. **[Step 11]** Add rollback block — Pillar 3
4. **[Step 7]** Verify POST endpoint in step command — Pillar 3
5. **[Final]** Clarify "all 5 test scenarios" → "all 18 tests" — Pillar 3

### Suggested Plan Modifications

**Fix 1: Move __init__.py to Step 1**

Add to Step 1 After block:
```bash
mkdir -p app tests
touch app/__init__.py tests/__init__.py
```

**Fix 2: Add pytest-asyncio config (new Step 7.5)**

Create `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Fix 3: Reset transient counters in test fixtures**

In `tests/test_pipeline.py`, update `clean_store`:
```python
@pytest.fixture(autouse=True)
def clean_store():
    store._requests.clear()
    yield
    store._requests.clear()
    from app import handlers
    handlers._transient_counters.clear()
```

**Fix 4: Fast timeout in timeout test**

In `tests/test_pipeline.py::test_optional_timeout_does_not_block`:
```python
@pytest.mark.asyncio
async def test_optional_timeout_does_not_block(self, monkeypatch):
    from app.retry import RetryConfig
    import app.pipeline as pipeline
    monkeypatch.setattr(pipeline, "OPTIONAL_CONFIG",
        RetryConfig(max_retries=1, timeout=0.1, base_delay=0.01, max_delay=0.1, jitter_max=0.0))
    # ... rest of test unchanged ...
```

---

## Next Steps
- Fix the 4 blockers using: `/rpi-adjust specs/VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md --review specs/REVIEW_VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`
- After fixes: `/rpi-implement specs/VOICEFLIP_TEST_IMPLEMENTATION_PLAN.md`
