import asyncio
import json
import os
from typing import Any

import openai
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, ValidationError

from usage import UsageCollector

load_dotenv()


DEFAULT_MODEL: str = "gpt-4o"
DEFAULT_TIMEOUT_S: float = 60.0


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class LlmError(Exception):
    """Raised when the LLM call cannot produce a usable response.

    Callers convert this to `could_not_verify` in the report - the pipeline
    treats LLM failure as a finding, not a crash.
    """


def _build_response_format(response_model: type[BaseModel]) -> dict[str, Any]:
    schema = response_model.model_json_schema()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_model.__name__,
            "schema": schema,
            "strict": False,
        },
    }


def _is_truncated(content: str, finish_reason: str | None) -> bool:
    return finish_reason == "length" or not (content and content.strip())


async def _one_call(
    *,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    response_model: type[BaseModel] | None,
) -> tuple[str, str | None, int, int]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": DEFAULT_TIMEOUT_S,
    }
    if response_model is not None:
        kwargs["response_format"] = _build_response_format(response_model)

    response = await _async_client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    content = choice.message.content or ""
    finish_reason = getattr(choice, "finish_reason", None)
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    return content, finish_reason, prompt_tokens, completion_tokens


_MAX_429_RETRIES: int = 3


def _retry_after_seconds(err: openai.RateLimitError) -> float | None:
    response = getattr(err, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def _call_with_429_retry(
    *,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    response_model: type[BaseModel] | None,
) -> tuple[str, str | None, int, int]:
    """Wrap `_one_call` with exponential-backoff retries on 429s.

    The 5-way agent fan-out in `/analyze` regularly trips the gpt-4o TPM
    ceiling. Catching `RateLimitError` here keeps transient throttling from
    bubbling out as `LlmError` and showing up in the report as
    `partial_failure`. After `_MAX_429_RETRIES` retries we still raise so a
    sustained outage doesn't get silently absorbed.
    """

    for attempt in range(_MAX_429_RETRIES + 1):
        try:
            return await _one_call(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_model=response_model,
            )
        except openai.RateLimitError as err:
            if attempt == _MAX_429_RETRIES:
                raise LlmError(
                    f"LLM call hit rate limit on all {_MAX_429_RETRIES + 1} attempts"
                ) from err
            hint = _retry_after_seconds(err)
            delay = hint if hint is not None else 2.0**attempt
            await asyncio.sleep(delay)
    # Unreachable: the loop either returns or raises.
    raise LlmError("rate-limit retry loop exited without a result")


def _try_parse(body: str, response_model: type[BaseModel] | None) -> tuple[bool, Any]:
    if response_model is None:
        return True, body
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, None
    try:
        return True, response_model.model_validate(data)
    except ValidationError:
        return False, None


async def call_llm_async(
    prompt: str,
    response_model: type[BaseModel] | None,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    usage: UsageCollector | None = None,
) -> Any:
    """Single LLM seam: structured-output call with token-escalating retry.

    Two failure classes, handled separately:
    - Truncation (`finish_reason == "length"` or empty body): re-run the same
      prompt with 2x then 4x `max_tokens`, up to 3 attempts total. The prompt
      was fine, the budget wasn't.
    - Malformed-but-complete JSON: re-prompt once asking the model to fix that
      specific body. Don't escalate tokens, don't re-run the original prompt.

    Raises `LlmError` if both retry budgets are exhausted. Callers convert
    that to `could_not_verify` in the report.
    """

    base_messages = [{"role": "user", "content": prompt}]

    # Phase 1: token-escalating retry on truncation. Up to 3 attempts.
    last_body: str | None = None
    for attempt in range(3):
        attempt_max_tokens = max_tokens * (2**attempt)
        content, finish_reason, prompt_tokens, completion_tokens = await _call_with_429_retry(
            messages=base_messages,
            model=DEFAULT_MODEL,
            max_tokens=attempt_max_tokens,
            temperature=temperature,
            response_model=response_model,
        )
        truncated = _is_truncated(content, finish_reason)
        if response_model is None:
            if truncated:
                last_body = content
                continue
            if usage is not None:
                usage.add(prompt_tokens, completion_tokens)
            return content

        ok, parsed = _try_parse(content, response_model)
        if ok:
            if usage is not None:
                usage.add(prompt_tokens, completion_tokens)
            return parsed
        if truncated:
            last_body = content
            continue
        # Parse failed but the body was complete - drop into the fix-up retry.
        last_body = content
        break
    else:
        raise LlmError(
            f"LLM call truncated on all 3 attempts (final max_tokens={max_tokens * 4})"
        )

    # Phase 2: one fix-up retry for malformed-but-complete JSON.
    fixup_messages = [
        {
            "role": "user",
            "content": (
                "fix this malformed JSON: "
                f"{last_body}"
            ),
        }
    ]
    content, finish_reason, prompt_tokens, completion_tokens = await _call_with_429_retry(
        messages=fixup_messages,
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        response_model=response_model,
    )
    ok, parsed = _try_parse(content, response_model)
    if not ok:
        raise LlmError("LLM returned malformed JSON; fix-up retry also failed")
    if usage is not None:
        usage.add(prompt_tokens, completion_tokens)
    return parsed


def call_llm(
    prompt: str,
    response_model: type[BaseModel] | None = None,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    usage: UsageCollector | None = None,
) -> Any:
    """Sync wrapper around `call_llm_async`. Same semantics, blocking."""

    return asyncio.run(
        call_llm_async(
            prompt,
            response_model,
            max_tokens=max_tokens,
            temperature=temperature,
            usage=usage,
        )
    )
