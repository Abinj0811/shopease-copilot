"""Search the indexed policy docs: embed a query via the gateway, rank chunks by similarity.

Usage: uv run python -m copilot.rag.search "<query>" [--top-k N]

Defaults come from config/rag.yaml (top_k, min_score); --top-k overrides top_k.
Every top-k result is printed with its cosine similarity and the doc's status,
and results below min_score are flagged rather than hidden so the threshold can
be calibrated by eye. `search_policies` is the reusable part (the chat API calls
it and drops results below min_score itself).
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Any

import openai
import psycopg
import yaml
from openai import OpenAI
from pydantic import ValidationError

from common.settings import Settings, SettingsError, load_settings
from copilot.rag.config import RagConfig, load_rag_config
from copilot.rag.index import vector_literal

SEARCH_SQL = """
    SELECT file, heading_path, category, content,
           1 - (embedding <=> %(q)s::vector) AS score
    FROM policy_chunks
    ORDER BY embedding <=> %(q)s::vector
    LIMIT %(k)s
"""


class SearchError(Exception):
    """The index cannot be searched; `hint` says how to fix it."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Hit:
    file: str
    heading_path: str
    category: str
    content: str
    score: float

    @property
    def source(self) -> str:
        return f"{self.file}#{self.heading_path}"


def search_policies(
    client: OpenAI,
    cfg: RagConfig,
    settings: Settings,
    query: str,
    top_k: int,
    extra_body: dict[str, Any] | None = None,
) -> list[Hit]:
    """Embed `query` through the gateway and return the `top_k` closest chunks.

    Raises SearchError for index/config problems; gateway (openai) and database
    (psycopg) errors propagate for the caller to report.
    """
    response = client.embeddings.create(
        model=cfg.embed_alias,
        input=[query],
        encoding_format="float",
        extra_body=extra_body,
    )
    vector = response.data[0].embedding
    if len(vector) != cfg.embed_dim:
        raise SearchError(
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
            raise SearchError(
                "policy_chunks table missing",
                "run `uv run alembic -c db/alembic.ini upgrade head`, then "
                "`uv run python -m copilot.rag.index`",
            )
        rows = conn.execute(
            SEARCH_SQL, {"q": vector_literal(vector), "k": top_k}
        ).fetchall()
    if not rows:
        raise SearchError(
            "policy_chunks is empty", "run `uv run python -m copilot.rag.index`"
        )
    return [Hit(*row) for row in rows]


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
        hits = search_policies(client, cfg, settings, query, top_k)
    except SearchError as exc:
        return fail(str(exc), exc.hint)
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

    print(f"query:  {query}")
    print(f"top_k:  {top_k}   min_score: {cfg.min_score}")
    table = []
    for rank, hit in enumerate(hits, start=1):
        status = "DEPRECATED" if hit.category == cfg.deprecated_category else "current"
        note = "" if hit.score >= cfg.min_score else "below min_score"
        table.append((str(rank), f"{hit.score:.3f}", status, hit.source, note))
    for row in table:
        print("  ".join((row[0].rjust(2), row[1], row[2].ljust(10), *row[3:])).rstrip())
    if all(row[4] for row in table):
        print("no result reaches min_score: the copilot would say it is unsure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
