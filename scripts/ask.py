"""Send one question to the running copilot API and print the answer.

Usage:
    uv run python scripts/ask.py "What is your return policy for mixers?"
    uv run python scripts/ask.py --customer 32 "Can I return order SE-10613?"
    uv run python scripts/ask.py --conversation demo1 "And for phones?"

The API address comes from COPILOT_API_HOST and COPILOT_API_PORT in .env (start
the API with `uv run python -m copilot.api`). The connect timeout is
INFRA_CHECK_TIMEOUT_SECONDS; there is no read timeout, because one answer can
take several model calls (press Ctrl+C to give up).
"""

import argparse
import sys
import textwrap
import uuid
from pathlib import Path

import httpx

# Plain `python scripts/ask.py` only puts scripts/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.settings import SettingsError, load_settings

WIDTH = 88
# A wildcard listen address is not something a client can connect to.
LOCAL_HOSTS = {"0.0.0.0": "127.0.0.1"}


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def error_detail(response: httpx.Response) -> str:
    """The API's own error text, whichever shape it came back in."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300] or "(empty response)"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg')}"
            for err in detail
        )
    if isinstance(body, dict) and body.get("error"):
        return f"{body['error']}: {detail}"
    return str(detail or body)[:300]


def show(question: str, conversation: str, customer: int | None, data: dict) -> None:
    print(f"question:      {question}")
    print(
        f"conversation:  {conversation}"
        + (f"   customer: {customer}" if customer else "")
    )
    print("-" * WIDTH)
    print("reply:")
    for paragraph in data["reply"].splitlines() or [""]:
        print(
            textwrap.fill(paragraph, WIDTH, initial_indent="  ", subsequent_indent="  ")
        )
    print("\nsources:")
    for source in data["sources"] or ["(none cited)"]:
        print(f"  - {source}")
    print(f"\ntools called:  {', '.join(data['tools_called']) or '(none)'}")
    print(f"escalated:     {'yes' if data['escalated'] else 'no'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", help="the customer's message")
    parser.add_argument(
        "--conversation",
        help="conversation id (default: a new random one for each run)",
    )
    parser.add_argument("--customer", type=int, help="customer id, e.g. 32")
    args = parser.parse_args()
    question = args.question.strip()
    if not question:
        return fail("question is empty")
    conversation = args.conversation or f"ask-{uuid.uuid4().hex[:8]}"

    try:
        settings = load_settings()
    except SettingsError as exc:
        return fail(str(exc))
    host = LOCAL_HOSTS.get(settings.copilot_api_host, settings.copilot_api_host)
    url = f"http://{host}:{settings.copilot_api_port}/chat"

    body: dict[str, object] = {"conversation_id": conversation, "message": question}
    if args.customer is not None:
        body["customer_id"] = args.customer
    try:
        response = httpx.post(
            url,
            json=body,
            timeout=httpx.Timeout(None, connect=settings.infra_check_timeout_seconds),
        )
    except httpx.ConnectError:
        return fail(
            f"cannot connect to the copilot API at {url}",
            "start it with `uv run python -m copilot.api` "
            "(check COPILOT_API_PORT in .env)",
        )
    except httpx.TimeoutException:
        return fail(
            f"the copilot API at {url} accepted the connection but did not answer"
        )
    except httpx.HTTPError as exc:
        return fail(f"request to {url} failed: {exc}")

    if response.status_code != 200:
        return fail(
            f"copilot API returned HTTP {response.status_code}: {error_detail(response)}"
        )
    try:
        data = response.json()
        show(question, conversation, args.customer, data)
    except (ValueError, KeyError, TypeError):
        return fail("the copilot API sent a response in an unexpected format")
    return 0


if __name__ == "__main__":
    # Replies can contain characters (e.g. the rupee sign) a redirected Windows
    # console cannot encode; print a "?" instead of crashing.
    sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
