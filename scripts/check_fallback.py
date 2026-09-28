"""Show which real model answered a request to a gateway alias, to check the fallback.

Usage:
    uv run python scripts/check_fallback.py [prompt] [--tools] [--alias NAME]

Sends one chat request through the LiteLLM gateway with COPILOT_GATEWAY_KEY and
prints the provider and model that actually served it, whether that was the
primary or the fallback, and the reply. --tools attaches a simple tool definition
so tool-calling fallback can be checked too. The alias defaults to the first one
with a fallback in gateway/config.yaml (router_settings.fallbacks). The SDK's own
retries are switched off so they cannot hide what the gateway did, and the
timeout is request_timeout_seconds from config/copilot.yaml.

To see the fallback, make the primary fail (for example add an unreachable
`api_base: http://127.0.0.1:9` under `strong` in gateway/config.yaml, then
`docker compose restart litellm`), run this again, and undo the edit afterwards.
"""

import argparse
import sys
import time
from pathlib import Path

import httpx
import openai
import yaml
from openai import OpenAI

# Plain `python scripts/check_fallback.py` only puts scripts/ on sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from common.settings import SettingsError, load_settings

GATEWAY_CONFIG_PATH = REPO_ROOT / "gateway" / "config.yaml"
COPILOT_CONFIG_PATH = REPO_ROOT / "config" / "copilot.yaml"
COMPOSE_HINT = "is `docker compose up -d litellm` running?"

DEFAULT_PROMPT = "Reply with one short sentence: what is ShopEase?"
DEFAULT_TOOLS_PROMPT = "Where is order SE-10765?"
STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Look up the status of a ShopEase order by its order number.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "e.g. SE-10765"}
            },
            "required": ["order_no"],
        },
    },
}


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def configured_fallbacks() -> dict[str, list[str]]:
    config = yaml.safe_load(GATEWAY_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    fallbacks: dict[str, list[str]] = {}
    for entry in (config.get("router_settings") or {}).get("fallbacks") or []:
        fallbacks.update(entry)
    return fallbacks


def deployment_models(base_url: str, api_key: str, timeout: float) -> dict[str, str]:
    """Map deployment id -> 'provider/model' from the gateway (empty if unavailable)."""
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/model/info",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
        return {
            item["model_info"]["id"]: item["litellm_params"]["model"]
            for item in response.json()["data"]
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt", nargs="?", help="what to send (a default is used)")
    parser.add_argument(
        "--tools", action="store_true", help="attach a simple tool definition"
    )
    parser.add_argument(
        "--alias", help="alias to call (default: the first with a fallback configured)"
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
        fallbacks = configured_fallbacks()
        timeout = float(
            yaml.safe_load(COPILOT_CONFIG_PATH.read_text(encoding="utf-8"))[
                "request_timeout_seconds"
            ]
        )
    except SettingsError as exc:
        return fail(str(exc))
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        return fail(f"cannot read the gateway or copilot config: {exc}")
    api_key = settings.copilot_gateway_key.get_secret_value()
    if not api_key:
        return fail(
            "COPILOT_GATEWAY_KEY is not set in .env",
            "see .env.example and docs/CONFIG.md (create_keys.py prints the key)",
        )
    alias = args.alias or next(iter(fallbacks), None)
    if alias is None:
        return fail(
            "no fallback is configured in gateway/config.yaml (router_settings.fallbacks)",
            "add one, or pass --alias to call an alias anyway",
        )
    prompt = args.prompt or (DEFAULT_TOOLS_PROMPT if args.tools else DEFAULT_PROMPT)

    request: dict[str, object] = {
        "model": alias,
        "messages": [{"role": "user", "content": prompt}],
    }
    if args.tools:
        request["tools"] = [STATUS_TOOL]

    client = OpenAI(
        base_url=settings.gateway_base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
    )
    started = time.perf_counter()
    try:
        raw = client.chat.completions.with_raw_response.create(**request)
    except openai.APITimeoutError:
        return fail(
            f"no answer from the gateway within {timeout:g}s",
            "the primary and the fallback together took too long; "
            "see request_timeout_seconds in config/copilot.yaml",
        )
    except openai.APIConnectionError:
        return fail(f"cannot connect to the gateway at {client.base_url}", COMPOSE_HINT)
    except openai.AuthenticationError as exc:
        return fail(
            f"authentication failed: {exc}", "check COPILOT_GATEWAY_KEY in .env"
        )
    except openai.PermissionDeniedError as exc:
        return fail(
            f"the copilot key may not use alias {alias!r}: {exc.message}",
            "the copilot team's allowed aliases are in gateway/teams.yaml",
        )
    except openai.NotFoundError:
        return fail(
            f"model alias {alias!r} not found on the gateway",
            "check the model_list in gateway/config.yaml",
        )
    except openai.APIStatusError as exc:
        return fail(
            f"gateway returned HTTP {exc.status_code}: {exc.message}",
            "if both the primary and its fallback failed, check each provider",
        )
    elapsed = time.perf_counter() - started
    message = raw.parse().choices[0].message

    model_id = raw.headers.get("x-litellm-model-id")
    api_base = raw.headers.get("x-litellm-model-api-base") or "unknown"
    try:
        fallbacks_used: int | None = int(raw.headers["x-litellm-attempted-fallbacks"])
    except (KeyError, ValueError):
        fallbacks_used = None
    full_name = deployment_models(settings.gateway_base_url, api_key, timeout).get(
        model_id or ""
    )
    provider, _, model = (full_name or "").partition("/")

    fallback_names = ", ".join(fallbacks.get(alias, [])) or "none configured"
    print(f"gateway:     {settings.gateway_base_url}")
    print(f"requested:   {alias}   (configured fallback: {fallback_names})")
    if full_name and model:
        print(f"served by:   {provider} / {model}")
    else:
        print(f"served by:   unknown model (deployment id {model_id or 'unknown'})")
    print(f"api base:    {api_base}")
    if fallbacks_used is None:
        print("result:      unknown (the gateway sent no fallback header)")
    elif fallbacks_used == 0:
        print(f"result:      PRIMARY answered ({alias} did not need its fallback)")
    else:
        print(
            f"result:      FALLBACK answered ({alias} failed; {fallbacks_used} fallback attempted)"
        )
    print(f"elapsed:     {elapsed:.1f}s")
    print(f"reply:       {(message.content or '').strip() or '(empty)'}")
    if args.tools:
        calls = [
            f"{c.function.name}({c.function.arguments})"
            for c in message.tool_calls or []
        ]
        print(f"tool calls:  {'; '.join(calls) or '(none)'}")
    return 0


if __name__ == "__main__":
    # Replies can contain characters a redirected Windows console cannot encode.
    sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
