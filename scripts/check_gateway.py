"""Call the LiteLLM gateway through the OpenAI SDK and report which deployment served it.

Usage:
    uv run python scripts/check_gateway.py --alias <name> "<prompt>"

For --alias embed, prints the embedding vector length instead of a reply.
Reads GATEWAY_BASE_URL and GATEWAY_API_KEY from .env.
"""

import argparse
import os
import sys
from pathlib import Path

import openai
from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
COMPOSE_HINT = "is `docker compose up -d litellm` running?"


class ConfigError(Exception):
    """A required .env variable is missing."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def served_by(headers: "openai._legacy_response.LegacyAPIResponse | object") -> str:
    """Build a 'served by' string from LiteLLM's response headers, when present."""
    model_id = headers.get("x-litellm-model-id")
    api_base = headers.get("x-litellm-model-api-base")
    parts = [p for p in (model_id, api_base) if p]
    return " @ ".join(parts) if parts else "(unknown; headers not present)"


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def run(alias: str, prompt: str) -> int:
    client = OpenAI(
        base_url=require("GATEWAY_BASE_URL"), api_key=require("GATEWAY_API_KEY")
    )

    try:
        if alias == "embed":
            # The SDK defaults to encoding_format="base64"; Ollama's embedding
            # endpoint (via LiteLLM) rejects that param, so ask for floats.
            raw = client.embeddings.with_raw_response.create(
                model=alias, input=prompt, encoding_format="float"
            )
            response = raw.parse()
            print(f"alias:   {alias}")
            print(f"served:  {served_by(raw.headers)}")
            print(f"vector length: {len(response.data[0].embedding)}")
        else:
            raw = client.chat.completions.with_raw_response.create(
                model=alias, messages=[{"role": "user", "content": prompt}]
            )
            response = raw.parse()
            reply = (response.choices[0].message.content or "").strip()
            print(f"alias:   {alias}")
            print(f"served:  {served_by(raw.headers)}")
            print(f"reply:   {reply or '(empty)'}")
    except openai.APIConnectionError:
        return fail(f"cannot connect to the gateway at {client.base_url}", COMPOSE_HINT)
    except openai.AuthenticationError as exc:
        return fail(
            f"authentication failed: {exc}",
            "once LITELLM_MASTER_KEY is enabled, update GATEWAY_API_KEY in .env",
        )
    except openai.NotFoundError:
        return fail(
            f"model alias {alias!r} not found on the gateway",
            "check the model_list in gateway/config.yaml",
        )
    except openai.APIStatusError as exc:
        return fail(f"gateway returned HTTP {exc.status_code}: {exc.message}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--alias", required=True, help="gateway model alias, e.g. cheap, strong, embed"
    )
    parser.add_argument("prompt", help="text to send to the alias")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    try:
        return run(args.alias, args.prompt)
    except ConfigError as exc:
        return fail(f"config: {exc}")


if __name__ == "__main__":
    sys.exit(main())
