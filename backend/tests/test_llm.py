"""LLM wrapper tests: provider compatibility, error diagnostics and JSON handling."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from app.llm import LLMClient, LLMError, _snippet


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.test/v1")


def chat_response(content: str, model: str = "test-model") -> httpx.Response:
    return httpx.Response(
        200,
        json={"model": model, "choices": [{"message": {"role": "assistant", "content": content}}]},
    )


async def complete_with(handler, **kwargs):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with LLMClient(client=client) as llm:
            return await llm.complete(system="s", user="u", **kwargs)


# ---------------------------------------------------------------------------
# endpoint construction
# ---------------------------------------------------------------------------


def test_endpoint_is_built_from_the_base_url() -> None:
    llm = LLMClient()

    assert llm._endpoint() == "https://provider.test/v1/chat/completions"


def test_an_explicit_chat_completions_url_is_not_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.test/v1/chat/completions")

    assert LLMClient()._endpoint() == "https://provider.test/v1/chat/completions"


def test_trailing_slashes_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.test/v1///")

    assert LLMClient()._endpoint() == "https://provider.test/v1/chat/completions"


# ---------------------------------------------------------------------------
# successful calls
# ---------------------------------------------------------------------------


async def test_a_plain_completion_is_returned() -> None:
    response = await complete_with(lambda request: chat_response("hello"))

    assert response.content == "hello"
    assert response.model == "test-model"


async def test_the_request_carries_the_model_and_messages() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        return chat_response("ok")

    await complete_with(handler)

    assert seen["model"] == "test-model"
    assert seen["messages"][0]["role"] == "system"
    assert seen["auth"] == "Bearer test-key"


# ---------------------------------------------------------------------------
# provider compatibility
# ---------------------------------------------------------------------------


async def test_json_mode_is_requested() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return chat_response('{"ok": true}')

    await complete_with(handler, json_mode=True)

    assert seen["response_format"] == {"type": "json_object"}


async def test_a_provider_without_json_mode_is_retried_without_it() -> None:
    """Not every OpenAI-compatible endpoint implements `response_format`."""
    calls: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format is not supported"})
        return chat_response('{"ok": true}')

    response = await complete_with(handler, json_mode=True)

    assert response.content == '{"ok": true}'
    assert calls == [True, False]


async def test_a_400_on_a_plain_request_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "model not found"})

    with pytest.raises(LLMError):
        await complete_with(handler)

    assert calls == 1


# ---------------------------------------------------------------------------
# error diagnostics
# ---------------------------------------------------------------------------


async def test_a_rejected_key_names_the_variable() -> None:
    with pytest.raises(LLMError) as excinfo:
        await complete_with(lambda request: httpx.Response(401, json={"error": "unauthorized"}))

    assert "LLM_API_KEY" in str(excinfo.value)


async def test_a_rate_limit_says_the_quota_resets() -> None:
    with pytest.raises(LLMError) as excinfo:
        await complete_with(lambda request: httpx.Response(429, json={"error": "quota"}))

    assert "rate-limited" in str(excinfo.value).lower()
    assert "reset" in str(excinfo.value).lower()


async def test_a_provider_error_body_is_included() -> None:
    """A bare status code is not actionable; the provider's message usually is."""
    body = {"error": {"message": "The model `nope` does not exist"}}

    with pytest.raises(LLMError) as excinfo:
        await complete_with(lambda request: httpx.Response(404, json=body))

    message = str(excinfo.value)
    assert "404" in message
    assert "does not exist" in message


async def test_an_unreachable_provider_is_reported_clearly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(LLMError) as excinfo:
        await complete_with(handler)

    assert "could not be reached" in str(excinfo.value)


async def test_an_unexpected_response_shape_is_reported() -> None:
    with pytest.raises(LLMError) as excinfo:
        await complete_with(lambda request: httpx.Response(200, json={"nope": True}))

    assert "unexpected response shape" in str(excinfo.value)


def test_snippet_collapses_whitespace() -> None:
    response = httpx.Response(400, text="line one\n\n line two\tend")

    assert _snippet(response) == "line one line two end"


def test_snippet_handles_an_empty_body() -> None:
    assert _snippet(httpx.Response(400, text="")) == "(empty body)"


# ---------------------------------------------------------------------------
# JSON mode parsing
# ---------------------------------------------------------------------------


async def test_complete_json_parses_an_object() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: chat_response('{"findings": []}'))
    ) as client:
        async with LLMClient(client=client) as llm:
            payload = await llm.complete_json(system="s", user="u")

    assert payload == {"findings": []}


async def test_complete_json_strips_a_markdown_fence() -> None:
    fenced = '```json\n{"ok": true}\n```'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: chat_response(fenced))
    ) as client:
        async with LLMClient(client=client) as llm:
            payload = await llm.complete_json(system="s", user="u")

    assert payload == {"ok": True}


async def test_complete_json_returns_none_for_prose() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: chat_response("I cannot help with that."))
    ) as client:
        async with LLMClient(client=client) as llm:
            assert await llm.complete_json(system="s", user="u") is None


async def test_complete_json_returns_none_for_a_non_object() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: chat_response("[1, 2, 3]"))
    ) as client:
        async with LLMClient(client=client) as llm:
            assert await llm.complete_json(system="s", user="u") is None


# ---------------------------------------------------------------------------
# unconfigured
# ---------------------------------------------------------------------------


async def test_an_unconfigured_client_refuses_to_call(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "")

    async with LLMClient() as llm:
        with pytest.raises(LLMError) as excinfo:
            await llm.complete(system="s", user="u")

    assert "No LLM is configured" in str(excinfo.value)
