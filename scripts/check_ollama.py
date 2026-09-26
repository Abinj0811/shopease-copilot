"""Send one short prompt to Ollama's OpenAI-compatible endpoint and report speed.

Usage: uv run python scripts/check_ollama.py --model <ollama-tag> [--prompt "..."]

Reads OLLAMA_BASE_URL and the OLLAMA_CHECK_* values from .env. Talks to Ollama
directly on purpose: this is an infra check that runs before the gateway exists.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class ConfigError(Exception):
    """A required .env variable is missing or invalid."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def require_int(name: str) -> int:
    raw = require(name)
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def run(model: str, prompt: str | None) -> int:
    base_url = require("OLLAMA_BASE_URL").rstrip("/")
    timeout = require_int("OLLAMA_CHECK_TIMEOUT_SECONDS")
    max_tokens = require_int("OLLAMA_CHECK_MAX_TOKENS")
    prompt = prompt or require("OLLAMA_CHECK_PROMPT")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    start = time.perf_counter()
    try:
        response = httpx.post(
            f"{base_url}/chat/completions", json=payload, timeout=timeout
        )
    except httpx.ConnectError:
        return fail(
            f"cannot connect to Ollama at {base_url}",
            "is Ollama running? check OLLAMA_BASE_URL in .env",
        )
    except httpx.TimeoutException:
        return fail(
            f"no reply within {timeout}s",
            "a cold model load on CPU can be slow; raise OLLAMA_CHECK_TIMEOUT_SECONDS",
        )
    except httpx.HTTPError as exc:
        return fail(f"request failed: {exc}")
    elapsed = time.perf_counter() - start

    if response.status_code == 404:
        return fail(
            f"model {model!r} not found in Ollama",
            f"run `ollama list`, or `ollama pull {model}`",
        )
    if response.is_error:
        return fail(f"HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    message = data["choices"][0]["message"]
    reply = (message.get("content") or "").strip()
    completion_tokens = int(data.get("usage", {}).get("completion_tokens", 0))
    tokens_per_sec = completion_tokens / elapsed if elapsed > 0 else 0.0

    print(f"model:   {model}")
    if reply:
        print(f"reply:   {reply}")
    else:
        print("reply:   (empty)")
        reasoning = (message.get("reasoning") or "").strip()
        if reasoning:
            print(f"thinking: {reasoning[:300]}")
        print(
            "note:    the token budget was likely spent on reasoning; "
            "raise OLLAMA_CHECK_MAX_TOKENS"
        )
    print(f"elapsed: {elapsed:.2f}s")
    print(f"tokens:  {completion_tokens} generated")
    print(
        f"speed:   {tokens_per_sec:.1f} tokens/s end-to-end "
        "(includes model load and prompt processing; run twice for warm speed)"
    )
    print("next:    run `ollama ps` and read the PROCESSOR column (CPU vs GPU)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", required=True, help="Ollama model tag, e.g. qwen3:1.7b"
    )
    parser.add_argument("--prompt", help="override OLLAMA_CHECK_PROMPT")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    try:
        return run(args.model, args.prompt)
    except ConfigError as exc:
        return fail(f"config: {exc}")


if __name__ == "__main__":
    sys.exit(main())
