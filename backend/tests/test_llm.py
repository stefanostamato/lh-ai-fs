import asyncio
import json
from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict

import llm
from usage import UsageCollector


def _make_429(retry_after: str | None = None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(
        429,
        headers=headers,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    return openai.RateLimitError("rate limit", response=response, body=None)


class _Toy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int


def _msg(content: str, finish_reason: str = "stop", prompt_tokens: int = 10, completion_tokens: int = 5):
    """Build the minimal shape `call_llm_async` reads off the OpenAI response."""

    class _Message:
        def __init__(self, c: str) -> None:
            self.content = c

    class _Choice:
        def __init__(self, c: str, fr: str) -> None:
            self.message = _Message(c)
            self.finish_reason = fr

    class _Usage:
        def __init__(self, p: int, c: int) -> None:
            self.prompt_tokens = p
            self.completion_tokens = c

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice(content, finish_reason)]
            self.usage = _Usage(prompt_tokens, completion_tokens)

    return _Response()


def _patch_create(monkeypatch, responder):
    """Patch the AsyncOpenAI.chat.completions.create call site.

    Reaches the patch through `llm._async_client` so the test file doesn't
    need to import from `openai` directly - the production module owns the
    SDK seam, the test owns the response shape.

    `responder` is a callable that receives the kwargs production code passed
    through, plus the 0-based attempt index, and returns a fake response.
    Records every call.
    """

    calls: list[dict[str, Any]] = []

    async def _fake_create(**kwargs):
        calls.append(kwargs)
        return responder(kwargs, len(calls) - 1)

    monkeypatch.setattr(llm._async_client.chat.completions, "create", _fake_create)
    return calls


def test_call_llm_async_returns_parsed_pydantic_instance(monkeypatch):
    payload = {"name": "alice", "count": 3}

    def responder(kwargs, attempt):
        return _msg(json.dumps(payload))

    _patch_create(monkeypatch, responder)

    result = asyncio.run(llm.call_llm_async("hello", _Toy))

    assert isinstance(result, _Toy)
    assert result.name == "alice"
    assert result.count == 3


def test_call_llm_async_returns_string_when_no_response_model(monkeypatch):
    def responder(kwargs, attempt):
        return _msg("plain text reply")

    _patch_create(monkeypatch, responder)

    result = asyncio.run(llm.call_llm_async("hello", None))

    assert result == "plain text reply"


def test_truncation_triggers_2x_then_4x_token_retry_then_succeeds(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        # Truncated on attempts 0 and 1, full body on attempt 2.
        if attempt < 2:
            return _msg('{"name": "ok", "cou', finish_reason="length")
        return _msg(json.dumps(payload), finish_reason="stop")

    calls = _patch_create(monkeypatch, responder)

    result = asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert isinstance(result, _Toy)
    assert len(calls) == 3
    assert calls[0]["max_tokens"] == 100
    assert calls[1]["max_tokens"] == 200
    assert calls[2]["max_tokens"] == 400


def test_three_truncations_raises_llm_error(monkeypatch):
    def responder(kwargs, attempt):
        return _msg('{"name": "ok"', finish_reason="length")

    calls = _patch_create(monkeypatch, responder)

    with pytest.raises(llm.LlmError):
        asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=50))

    assert len(calls) == 3
    assert [c["max_tokens"] for c in calls] == [50, 100, 200]


def test_empty_response_treated_as_truncation(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        if attempt == 0:
            return _msg("", finish_reason="stop")
        return _msg(json.dumps(payload), finish_reason="stop")

    calls = _patch_create(monkeypatch, responder)

    result = asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert isinstance(result, _Toy)
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 200


def test_malformed_non_truncated_json_triggers_one_fixup_retry(monkeypatch):
    payload = {"name": "ok", "count": 7}

    def responder(kwargs, attempt):
        if attempt == 0:
            return _msg("{not valid json", finish_reason="stop")
        return _msg(json.dumps(payload), finish_reason="stop")

    calls = _patch_create(monkeypatch, responder)

    result = asyncio.run(llm.call_llm_async("the original prompt", _Toy, max_tokens=100))

    assert isinstance(result, _Toy)
    assert len(calls) == 2
    # Token budget did not escalate; this is a parse-fix retry, not a truncation retry.
    assert calls[1]["max_tokens"] == 100
    # The fix-up prompt mentions the malformed body.
    second_messages = calls[1]["messages"]
    fixup_text = " ".join(m["content"] for m in second_messages)
    assert "fix" in fixup_text.lower()
    assert "{not valid json" in fixup_text


def test_malformed_non_truncated_json_exhausts_after_one_retry(monkeypatch):
    def responder(kwargs, attempt):
        return _msg("{still bad", finish_reason="stop")

    calls = _patch_create(monkeypatch, responder)

    with pytest.raises(llm.LlmError):
        asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert len(calls) == 2


def test_successful_call_updates_usage_collector(monkeypatch):
    payload = {"name": "ok", "count": 2}

    def responder(kwargs, attempt):
        return _msg(json.dumps(payload), prompt_tokens=42, completion_tokens=11)

    _patch_create(monkeypatch, responder)
    usage = UsageCollector()

    asyncio.run(llm.call_llm_async("p", _Toy, usage=usage))

    assert usage.prompt == 42
    assert usage.completion == 11


def test_failed_call_does_not_update_usage(monkeypatch):
    def responder(kwargs, attempt):
        return _msg('{"truncated', finish_reason="length", prompt_tokens=10, completion_tokens=5)

    _patch_create(monkeypatch, responder)
    usage = UsageCollector()

    with pytest.raises(llm.LlmError):
        asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=10, usage=usage))

    # No successful attempt -> nothing recorded.
    assert usage.prompt == 0
    assert usage.completion == 0


def test_response_format_passes_json_schema_when_model_given(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        return _msg(json.dumps(payload))

    calls = _patch_create(monkeypatch, responder)

    asyncio.run(llm.call_llm_async("p", _Toy))

    rf = calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert "json_schema" in rf
    schema_block = rf["json_schema"]
    assert "schema" in schema_block


def _patch_sleep(monkeypatch):
    """Capture asyncio.sleep durations without actually sleeping."""

    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", _fake_sleep)
    return sleeps


def test_429_then_success_retries_with_exponential_backoff(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        if attempt == 0:
            raise _make_429()
        return _msg(json.dumps(payload))

    calls = _patch_create(monkeypatch, responder)
    sleeps = _patch_sleep(monkeypatch)

    result = asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert isinstance(result, _Toy)
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_three_429s_in_a_row_raises_llm_error(monkeypatch):
    def responder(kwargs, attempt):
        raise _make_429()

    calls = _patch_create(monkeypatch, responder)
    sleeps = _patch_sleep(monkeypatch)

    with pytest.raises(llm.LlmError):
        asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    # 3 retries after the initial attempt -> 4 SDK calls total, 3 sleeps.
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_429_with_retry_after_header_uses_that_value(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        if attempt == 0:
            raise _make_429(retry_after="5")
        return _msg(json.dumps(payload))

    _patch_create(monkeypatch, responder)
    sleeps = _patch_sleep(monkeypatch)

    asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert sleeps == [5.0]


def test_429_during_fixup_retry_is_also_caught(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        # First call: malformed-but-complete JSON -> drops into fix-up phase.
        if attempt == 0:
            return _msg("{not valid json", finish_reason="stop")
        # Second call (the fix-up): hits 429.
        if attempt == 1:
            raise _make_429()
        # Third call (the 429 retry): returns valid JSON.
        return _msg(json.dumps(payload))

    calls = _patch_create(monkeypatch, responder)
    sleeps = _patch_sleep(monkeypatch)

    result = asyncio.run(llm.call_llm_async("p", _Toy, max_tokens=100))

    assert isinstance(result, _Toy)
    assert len(calls) == 3
    assert sleeps == [1.0]


def test_successful_call_does_not_sleep(monkeypatch):
    payload = {"name": "ok", "count": 1}

    def responder(kwargs, attempt):
        return _msg(json.dumps(payload))

    _patch_create(monkeypatch, responder)
    sleeps = _patch_sleep(monkeypatch)

    asyncio.run(llm.call_llm_async("p", _Toy))

    assert sleeps == []
