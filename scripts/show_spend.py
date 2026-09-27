"""Print the last N rows from LiteLLM's own spend log.

Usage: uv run python scripts/show_spend.py [--limit N]

Reads Postgres connection values from .env (POSTGRES_HOST/PORT/USER/PASSWORD,
LITELLM_DB_NAME) and SPEND_LOG_DEFAULT_LIMIT. Connects directly to Postgres
rather than LiteLLM's /spend/logs API: that endpoint requires a mandatory
date range and has no documented "most recent N" sort, while the table
itself trivially supports ORDER BY startTime DESC LIMIT N.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
COMPOSE_HINT = "is `docker compose up -d` running?"

QUERY = """
    SELECT
        sl.model,
        sl.model_group,
        sl.spend,
        sl.total_tokens,
        sl.prompt_tokens,
        sl.completion_tokens,
        COALESCE(t.team_alias, sl.team_id) AS team,
        sl.metadata,
        sl."startTime"
    FROM "LiteLLM_SpendLogs" sl
    LEFT JOIN "LiteLLM_TeamTable" t ON t.team_id = sl.team_id
    ORDER BY sl."startTime" DESC
    LIMIT %s
"""


class ConfigError(Exception):
    """A required .env variable is missing or invalid."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def run(limit: int) -> int:
    try:
        with psycopg.connect(
            host=require("POSTGRES_HOST"),
            port=int(require("POSTGRES_PORT")),
            user=require("POSTGRES_USER"),
            password=require("POSTGRES_PASSWORD"),
            dbname=require("LITELLM_DB_NAME"),
            connect_timeout=5,
        ) as conn:
            rows = conn.execute(QUERY, (limit,)).fetchall()
    except psycopg.Error as exc:
        reason = (
            str(exc).strip().splitlines()[-1]
            if str(exc).strip()
            else type(exc).__name__
        )
        return fail(f"could not read spend logs: {reason}", COMPOSE_HINT)

    if not rows:
        print("No spend log rows yet.")
        print("hint: run scripts/check_gateway.py to generate one, then retry.")
        return 0

    for (
        model,
        model_group,
        spend,
        total,
        prompt,
        completion,
        team,
        metadata,
        started,
    ) in rows:
        alias = f" ({model_group})" if model_group else ""
        print(f"time:    {started}")
        print(f"model:   {model}{alias}")
        print(f"spend:   ${spend:.6f}")
        print(f"tokens:  {total} total ({prompt} prompt + {completion} completion)")
        print(f"team:    {team or '(none)'}")
        meta_str = json.dumps(metadata or {}, separators=(",", ":"))
        if len(meta_str) > 200:
            meta_str = meta_str[:200] + "...(truncated)"
        print(f"metadata: {meta_str}")
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="override SPEND_LOG_DEFAULT_LIMIT")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    try:
        limit = args.limit or int(require("SPEND_LOG_DEFAULT_LIMIT"))
        return run(limit)
    except ConfigError as exc:
        return fail(f"config: {exc}")


if __name__ == "__main__":
    sys.exit(main())
