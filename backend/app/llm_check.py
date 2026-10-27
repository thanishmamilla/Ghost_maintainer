"""Verify the optional LLM configuration.

The application is fully functional without an LLM. This command exists so that
whoever configures one can confirm the endpoint, model and key work together
before relying on it during a demo.

Usage:
    python -m app.llm_check
    python -m app.llm_check --config-only
    python -m app.llm_check --prompt "Say hello in five words."

Exit codes: 0 when the provider answered (or nothing is configured), 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import get_settings
from .llm import LLMClient, LLMError

# Verified free-tier providers. All speak the OpenAI chat-completions API, so any
# of them works by setting the three environment variables.
KNOWN_PROVIDERS: list[tuple[str, str, str]] = [
    ("Groq (free, no card)", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    ("Cerebras (free, no card)", "https://api.cerebras.ai/v1", "llama3.1-70b"),
    ("Mistral (free, no card)", "https://api.mistral.ai/v1", "open-mistral-7b"),
    (
        "Google Gemini (free tier)",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-3.6-flash",
    ),
    (
        "OpenRouter (free :free models)",
        "https://openrouter.ai/api/v1",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
    ),
    ("LLM7.io (free, no card)", "https://api.llm7.io/v1", "gpt-oss-20b"),
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("Ollama (local, no key)", "http://localhost:11434/v1", "qwen2.5-coder:7b"),
]


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _print_configuration() -> bool:
    settings = get_settings()

    print("LLM configuration")
    print("-----------------")
    print(f"  LLM_BASE_URL : {settings.llm_base_url or '(default: https://api.openai.com/v1)'}")
    print(f"  LLM_MODEL    : {settings.llm_model or '(empty)'}")
    print(f"  LLM_API_KEY  : {_mask(settings.llm_api_key)}")
    print()

    if settings.llm_configured:
        print("Status: configured. The LLM is optional and only adds capabilities.")
        return True

    print("Status: not configured.")
    print()
    print("This is not a problem. Everything works without a key:")
    print("  * scanning, Rot Scores and reachability")
    print("  * safe version bumps and the moment->dayjs / request->axios codemods")
    print("  * the sandbox test run and the pull-request preview")
    print("  * the four pull-request review agents (deterministic rules)")
    print()
    print("What a key adds: model-assisted review findings and LLM-proposed migrations.")
    print()
    print("To enable one, set these in backend/.env and restart the backend:")
    print()
    print("  LLM_API_KEY=<your key>")
    print("  LLM_MODEL=<model id>")
    print("  LLM_BASE_URL=<base url>")
    print()
    print("Free tiers that need no credit card:")
    print()
    for name, base_url, model in KNOWN_PROVIDERS:
        print(f"  {name}")
        print(f"      LLM_BASE_URL={base_url}")
        print(f"      LLM_MODEL={model}")
    print()
    print("Get a key: console.groq.com/keys | cloud.cerebras.ai | console.mistral.ai")
    print("           aistudio.google.com/app/apikey | openrouter.ai/keys | token.llm7.io")
    return False


async def _probe(prompt: str) -> int:
    settings = get_settings()

    async with LLMClient() as client:
        try:
            response = await client.complete(
                system="You are a connectivity probe. Answer in one short sentence.",
                user=prompt,
                max_tokens=60,
            )
        except LLMError as exc:
            print(f"FAILED: {exc}")
            print()
            message = str(exc)
            if "401" in message:
                print("The endpoint is reachable but rejected the key. Check LLM_API_KEY.")
            elif "could not be reached" in message:
                print("The endpoint could not be reached. Check LLM_BASE_URL and the network.")
            elif "429" in message:
                print("Rate limited. Free tiers usually reset daily, or try another model.")
            elif "400" in message:
                print("The request was rejected. A wrong LLM_MODEL is the usual cause.")
            return 1

    print("OK: the provider answered.")
    print(f"  model    : {response.model}")
    print(f"  response : {response.content.strip()[:200]}")
    print(f"  endpoint : {settings.llm_base_url or 'https://api.openai.com/v1'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the optional LLM configuration.")
    parser.add_argument(
        "--prompt",
        default='Reply with exactly: {"ok": true}',
        help="Prompt sent to the provider.",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Print the configuration and known presets without calling the provider.",
    )
    args = parser.parse_args(argv)

    configured = _print_configuration()

    if args.config_only:
        return 0 if configured else 1
    if not configured:
        return 0

    print("Sending a probe request...")
    print()
    return asyncio.run(_probe(args.prompt))


if __name__ == "__main__":
    sys.exit(main())
