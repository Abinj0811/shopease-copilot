"""Search the indexed policy docs: embed a query via the gateway, rank chunks by similarity.

Usage: uv run python -m copilot.rag.search "<query>" [--top-k N]

Defaults come from config/rag.yaml (top_k, min_score); --top-k overrides top_k.
Every top-k result is printed with its cosine similarity and the doc's status,
and results below min_score are flagged rather than hidden so the threshold can
be calibrated by eye. Later steps that feed a chat model will drop them.
"""

import argparse
import sys

import openai
import psycopg
import yaml
from openai import OpenAI
from pydantic import ValidationError

from common.settings import SettingsError, load_settings
from copilot.rag.config import load_rag_config
from copilot.rag.index import vector_literal

SEARCH_SQL = """
    SELECT file, heading_path, category, 1 - (embedding <=> %(q)s::vector) AS score
    FROM policy_chunks
    ORDER BY embedding <=> %(q)s::vector
    LIMIT %(k)s
"""


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="question to search the policy docs for")
    parser.add_argument(
        "--top-k",
        type=positive_int,
        help="results to show (default: top_k in config/rag.yaml)",
    )
    args = parser.parse_args()
    query = args.query.strip()
    if not query:
        return fail("query is empty")

    try:
        cfg = load_rag_config()
        settings = load_settings()
    except (ValidationError, SettingsError, yaml.YAMLError, OSError) as exc:
        return fail(f"invalid configuration:\n{exc}")
    api_key = settings.gateway_api_key.get_secret_value()
    if not api_key:
        return fail(
            "GATEWAY_API_KEY is not set in .env",
            "see .env.example and docs/CONFIG.md (create_keys.py prints the key)",
        )
    top_k = args.top_k if args.top_k is not None else cfg.top_k

    client = OpenAI(base_url=settings.gateway_base_url, api_key=api_key)
    try:
        response = client.embeddings.create(
            model=cfg.embed_alias, input=[query], encoding_format="float"
        )
        vector = response.data[0].embedding
        if len(vector) != cfg.embed_dim:
            return fail(
                f"embed_dim is {cfg.embed_dim} in config/rag.yaml but alias "
                f"{cfg.embed_alias!r} returned a vector of length {len(vector)}",
                "fix embed_dim, rebuild the table (alembic downgrade -1, upgrade "
                "head) and re-run `python -m copilot.rag.index`",
            )
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            dbname=settings.postgres_db,
            connect_timeout=settings.infra_check_timeout_seconds,
        ) as conn:
            if (
                conn.execute("SELECT to_regclass('public.policy_chunks')").fetchone()[0]
                is None
            ):
                return fail(
                    "policy_chunks table missing",
                    "run `uv run alembic -c db/alembic.ini upgrade head`, then "
                    "`uv run python -m copilot.rag.index`",
                )
            rows = conn.execute(
                SEARCH_SQL, {"q": vector_literal(vector), "k": top_k}
            ).fetchall()
    except openai.APIConnectionError:
        return fail(
            f"cannot connect to the gateway at {client.base_url}",
            "is `docker compose up -d litellm` running?",
        )
    except openai.AuthenticationError as exc:
        return fail(f"gateway authentication failed: {exc}", "check GATEWAY_API_KEY")
    except openai.NotFoundError:
        return fail(
            f"model alias {cfg.embed_alias!r} not found on the gateway",
            "check embed_alias in config/rag.yaml against gateway/config.yaml",
        )
    except openai.APIStatusError as exc:
        return fail(f"gateway returned HTTP {exc.status_code}: {exc.message}")
    except psycopg.Error as exc:
        reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else "unknown"
        return fail(f"database error: {reason}", "is `docker compose up -d` running?")

    if not rows:
        return fail(
            "policy_chunks is empty", "run `uv run python -m copilot.rag.index`"
        )

    print(f"query:  {query}")
    print(f"top_k:  {top_k}   min_score: {cfg.min_score}")
    table = []
    for rank, (file, heading_path, category, score) in enumerate(rows, start=1):
        status = "DEPRECATED" if category == cfg.deprecated_category else "current"
        note = "" if score >= cfg.min_score else "below min_score"
        table.append(
            (str(rank), f"{score:.3f}", status, f"{file}#{heading_path}", note)
        )
    for row in table:
        print("  ".join((row[0].rjust(2), row[1], row[2].ljust(10), *row[3:])).rstrip())
    if all(row[4] for row in table):
        print("no result reaches min_score: the copilot would say it is unsure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
