from collections.abc import Callable
from typing import Any

import pytest

from schemas import CaseLookupResult, ExtractedCitation


@pytest.fixture
def mock_lookup(monkeypatch):
    """Patch `CourtListenerLookup.fetch` for unit tests.

    Same factory shape as `mock_llm`. Call it with one of:

    - a `dict[str, CaseLookupResult]` mapping a substring of `citation.cite`
      to the result. The first key whose substring is found in the cite wins.
      KeyError if nothing matches.
    - a `Callable[[ExtractedCitation], CaseLookupResult]` for arbitrary logic.

    The factory returns the recorded call list. Each entry is
    `{"citation": ExtractedCitation, "result": CaseLookupResult}`.

    At teardown the fixture asserts the patched function was called at least
    once. A test that mocks the lookup and never invokes it has a wiring bug.
    """

    calls: list[dict[str, Any]] = []
    configured: dict[str, Any] = {"responder": None}

    def _resolve(citation: ExtractedCitation) -> CaseLookupResult:
        responder = configured["responder"]
        if responder is None:
            raise RuntimeError(
                "mock_lookup was patched but never configured. "
                "Call mock_lookup({...}) first."
            )
        if callable(responder):
            return responder(citation)
        for key, value in responder.items():
            if key in citation.cite:
                return value
        raise KeyError(
            f"mock_lookup: no configured response matches cite. "
            f"Keys: {list(responder.keys())}. Cite: {citation.cite!r}"
        )

    async def _fetch(self: Any, citation: ExtractedCitation) -> CaseLookupResult:
        result = _resolve(citation)
        calls.append({"citation": citation, "result": result})
        return result

    monkeypatch.setattr(
        "agents.tools.courtlistener_lookup.CourtListenerLookup.fetch",
        _fetch,
        raising=True,
    )

    def _configure(
        responder: dict[str, CaseLookupResult]
        | Callable[[ExtractedCitation], CaseLookupResult],
    ) -> list[dict[str, Any]]:
        configured["responder"] = responder
        return calls

    yield _configure

    assert calls, (
        "mock_lookup was set up but never called - check the test's lookup wiring"
    )


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch the llm.py boundary for unit tests.

    The fixture is a factory. Call it with one of:

    - a `dict[str, Any]` mapping a substring of the prompt to the response
      payload to return. The first key whose substring is found in the prompt
      wins. KeyError if nothing matches - tests should be explicit about which
      prompts they expect to fire.
    - a `Callable[[str], Any]` taking the full prompt and returning the
      response payload. Use this when matching by substring isn't enough.

    The factory returns the recorded call list. Each entry is
    `{"prompt": str, "response": Any}`. Tests can inspect this list to assert
    how many calls happened, in what order, and with what prompt content.

    Both `llm.call_llm` and `llm.call_llm_async` are patched.
    Async wraps sync - the response payload contract is identical.

    At teardown the fixture asserts the patched function was called at least
    once. A test that mocks the LLM and never invokes it has a wiring bug.
    """

    calls: list[dict[str, Any]] = []
    configured: dict[str, Any] = {"responder": None}

    def _resolve(prompt: str) -> Any:
        responder = configured["responder"]
        if responder is None:
            raise RuntimeError(
                "mock_llm was patched but never configured. Call mock_llm({...}) first."
            )
        if callable(responder):
            return responder(prompt)
        for key, value in responder.items():
            if key in prompt:
                return value
        raise KeyError(
            f"mock_llm: no configured response matches prompt. "
            f"Keys: {list(responder.keys())}. Prompt head: {prompt[:120]!r}"
        )

    def _sync(prompt: str, *args: Any, **kwargs: Any) -> Any:
        response = _resolve(prompt)
        calls.append({"prompt": prompt, "response": response})
        return response

    async def _async(prompt: str, *args: Any, **kwargs: Any) -> Any:
        response = _resolve(prompt)
        calls.append({"prompt": prompt, "response": response})
        return response

    monkeypatch.setattr("llm.call_llm", _sync, raising=False)
    monkeypatch.setattr("llm.call_llm_async", _async, raising=False)

    def _configure(responder: dict[str, Any] | Callable[[str], Any]) -> list[dict[str, Any]]:
        configured["responder"] = responder
        return calls

    yield _configure

    assert calls, "mock_llm was set up but never called - check the test's LLM wiring"
