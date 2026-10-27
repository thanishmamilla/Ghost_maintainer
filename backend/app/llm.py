"""Provider-agnostic LLM wrapper.

The application never depends on this module for its core workflow. Where an LLM
is used, it is only ever asked to *propose* something that is then validated
against a strict schema, restricted to approved files, and passed through the
same test gates as a deterministic change. The model never executes commands and
never selects arbitrary paths.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger("ghost_maintainer.llm")


class LLMError(Exception):
    """The provider was configured but the call failed."""


def _snippet(response: httpx.Response, limit: int = 240) -> str:
    """A short, single-line excerpt of an error body, for diagnostics.

    Providers put the actionable detail here ("model not found", "quota
    exhausted"), so hiding it leaves the user with a bare status code.
    """
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return "(no body)"
    text = " ".join(text.split())
    return text[:limit] if text else "(empty body)"


@dataclass
class LLMResponse:
    content: str
    model: str


class LLMClient:
    """A minimal OpenAI-compatible chat client."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.settings = get_settings()
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return self.settings.llm_configured

    async def __aenter__(self) -> "LLMClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("LLMClient must be used as an async context manager.")
        return self._client

    def _endpoint(self) -> str:
        base = (self.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = False,
        max_tokens: int = 1500,
    ) -> LLMResponse:
        """Send a chat completion request.

        If a provider rejects the JSON response format specifically, the request
        is retried once without it. Not every OpenAI-compatible endpoint
        implements ``response_format``, and failing the whole review because of
        an optional formatting hint would be the wrong trade-off.
        """
        if not self.configured:
            raise LLMError("No LLM is configured.")

        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = await self._post(payload)

        if json_mode and response.status_code == 400:
            logger.info("Provider rejected response_format; retrying without JSON mode.")
            payload.pop("response_format", None)
            response = await self._post(payload)

        if response.status_code == 401:
            raise LLMError("The LLM provider rejected the API key (401). Check LLM_API_KEY.")
        if response.status_code == 403:
            raise LLMError(f"The LLM provider refused the request (403): {_snippet(response)}")
        if response.status_code == 429:
            raise LLMError("The LLM provider rate-limited the request (429). Free tiers reset daily.")
        if response.status_code >= 400:
            raise LLMError(
                f"The LLM provider returned {response.status_code}: {_snippet(response)}"
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("The LLM provider returned an unexpected response shape.") from exc

        if not isinstance(content, str):
            raise LLMError("The LLM provider returned no text content.")

        return LLMResponse(content=content, model=str(body.get("model") or self.settings.llm_model))

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self.client.post(
                self._endpoint(),
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"The LLM provider could not be reached: {exc.__class__.__name__}.") from exc

    async def complete_json(self, *, system: str, user: str, max_tokens: int = 1500) -> dict | None:
        """Ask for a JSON object and parse it defensively."""
        try:
            response = await self.complete(system=system, user=user, json_mode=True, max_tokens=max_tokens)
        except LLMError as exc:
            logger.warning("LLM call failed: %s", exc)
            return None

        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            parsed = json.loads(text)
        except ValueError:
            logger.warning("The LLM returned non-JSON output; ignoring it.")
            return None
        return parsed if isinstance(parsed, dict) else None