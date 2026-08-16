"""LLM orchestration: retries, fallbacks, concurrency, no 'busy' user copy."""

from __future__ import annotations

import threading
import time

import pytest
import requests

from app.ai import llm_generate
from app.ai.llm_generate import (
    LLMServiceUnavailableError,
    USER_SAFE_FAIL_MESSAGE,
    USER_SAFE_WAIT_MESSAGE,
    generate_llm_content,
    reset_pipeline_metrics,
    reset_role_semaphores,
)
from app.ai.llm_models import (
    enable_luna_simple_document_routing,
    pipeline_version,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _ok_payload(text: str = '{"ok": true}') -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        },
    }


@pytest.fixture(autouse=True)
def _llm_test_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_MAX_RETRIES", "3")
    monkeypatch.setenv("SOL_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("ENABLE_LUNA_SIMPLE_DOCUMENT_ROUTING", "false")
    monkeypatch.setenv("DOCUMENT_AI_PIPELINE_VERSION", "multi_model")
    monkeypatch.setattr(llm_generate, "_sleep_backoff", lambda *_args, **_kwargs: None)
    reset_pipeline_metrics()
    reset_role_semaphores()
    yield
    reset_role_semaphores()
    reset_pipeline_metrics()


def test_user_messages_never_say_busy():
    blob = f"{USER_SAFE_FAIL_MESSAGE} {USER_SAFE_WAIT_MESSAGE}".lower()
    for forbidden in ("busy", "429", "sol", "terra", "luna", "gpt-4o", "openai"):
        assert forbidden not in blob


def test_luna_simple_routing_disabled_by_default():
    assert enable_luna_simple_document_routing() is False
    assert pipeline_version() == "multi_model"


def test_sol_429_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(429)
        return _FakeResponse(200, _ok_payload('{"section":"section7_insurance_policies"}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(contents=["Policy Number HO-1"], role="sol")
    assert calls["n"] == 3
    assert "section7" in response.text
    assert llm_generate.PIPELINE_METRICS["retries"] >= 2
    assert llm_generate.PIPELINE_METRICS["sol_fallback_gpt4o"] == 0


def test_sol_5xx_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(503)
        return _FakeResponse(200, _ok_payload('{"ok":true}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(contents=["Named Assrd Jane"], role="sol")
    assert '"ok"' in response.text
    assert calls["n"] == 2


def test_slow_sol_success_does_not_fallback(monkeypatch):
    models: list[str] = []

    def fake_post(_url, **kwargs):
        body = kwargs.get("json") or {}
        models.append(str(body.get("model")))
        time.sleep(0.25)
        return _FakeResponse(200, _ok_payload('{"ok":true}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    generate_llm_content(contents=["Insurance Carrier State Farm"], role="sol")
    assert len(models) == 1
    assert "sol" in models[0]
    assert llm_generate.PIPELINE_METRICS["sol_fallback_gpt4o"] == 0


def test_sol_retries_exhausted_falls_back_to_gpt4o(monkeypatch):
    monkeypatch.setenv("AI_MAX_RETRIES", "2")
    models: list[str] = []

    def fake_post(_url, **kwargs):
        body = kwargs.get("json") or {}
        model = str(body.get("model"))
        models.append(model)
        if "sol" in model:
            return _FakeResponse(429)
        return _FakeResponse(200, _ok_payload('{"from":"gpt4o"}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(contents=["coverage ends 2027-01-01"], role="sol")
    assert "gpt4o" in response.text
    assert any("sol" in m for m in models)
    assert any("gpt-4o" in m for m in models)
    assert llm_generate.PIPELINE_METRICS["sol_fallback_gpt4o"] == 1


def test_gpt4o_failure_falls_back_to_luna(monkeypatch):
    monkeypatch.setenv("AI_MAX_RETRIES", "1")
    models: list[str] = []

    def fake_post(_url, **kwargs):
        body = kwargs.get("json") or {}
        model = str(body.get("model"))
        models.append(model)
        if "luna" in model:
            return _FakeResponse(200, _ok_payload('{"from":"luna"}'))
        return _FakeResponse(503)

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(contents=["policy number AB"], role="sol")
    assert "luna" in response.text
    assert llm_generate.PIPELINE_METRICS["sol_fallback_luna"] == 1


def test_all_models_fail_user_safe_message(monkeypatch):
    monkeypatch.setenv("AI_MAX_RETRIES", "1")

    def fake_post(*_args, **_kwargs):
        return _FakeResponse(429)

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    with pytest.raises(LLMServiceUnavailableError) as raised:
        generate_llm_content(contents=["unknown scan"], role="sol")
    message = str(raised.value).lower()
    assert "busy" not in message
    assert "429" not in message
    assert "sol" not in message
    assert "openai" not in message


def test_terra_failure_falls_back_to_gpt4o_vision(monkeypatch):
    monkeypatch.setenv("AI_MAX_RETRIES", "1")
    models: list[str] = []

    def fake_post(_url, **kwargs):
        body = kwargs.get("json") or {}
        model = str(body.get("model"))
        models.append(model)
        if "terra" in model:
            return _FakeResponse(503)
        return _FakeResponse(200, _ok_payload('{"text":"Named Insured Jane"}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(
        contents=[
            "read this page",
            {"type": "image", "mime_type": "image/png", "data_b64": "xx"},
        ],
        role="terra",
    )
    assert "Named Insured" in response.text
    assert any("terra" in m for m in models)
    assert any("gpt-4o" in m for m in models)
    assert llm_generate.PIPELINE_METRICS["terra_fallback_gpt4o"] == 1


def test_sol_concurrent_requests_share_semaphore(monkeypatch):
    monkeypatch.setenv("SOL_MAX_CONCURRENCY", "2")
    reset_role_semaphores()
    in_flight = {"n": 0, "max": 0}
    lock = threading.Lock()

    def fake_post(*_args, **_kwargs):
        with lock:
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
        time.sleep(0.2)
        with lock:
            in_flight["n"] -= 1
        return _FakeResponse(200, _ok_payload('{"ok":true}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)

    results: list[str] = []
    errors: list[BaseException] = []

    def worker():
        try:
            out = generate_llm_content(contents=["doc"], role="sol")
            results.append(out.text)
        except BaseException as error:  # noqa: BLE001 — collect for assert
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=8)

    assert not errors
    assert len(results) == 4
    assert in_flight["max"] <= 2
    assert in_flight["max"] >= 2


def test_connection_reset_is_retried(monkeypatch):
    calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("connection reset")
        return _FakeResponse(200, _ok_payload('{"ok":true}'))

    monkeypatch.setattr(llm_generate.requests, "post", fake_post)
    response = generate_llm_content(contents=["retry me"], role="sol")
    assert calls["n"] == 2
    assert "ok" in response.text
