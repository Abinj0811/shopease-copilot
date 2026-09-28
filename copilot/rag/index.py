"""Index the policy docs for RAG: chunk by heading, embed via the gateway, store in pgvector.

Usage: uv run python -m copilot.rag.index

Reads every parameter from config/rag.yaml. Each run embeds all chunks first,
then replaces the whole policy_chunks table in one transaction, so re-running
is idempotent, a failed run leaves the previous index untouched, and a changed
embed model can never leave stale vectors behind.
"""

import datetime
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import openai
import psycopg
import yaml
from openai import OpenAI
from pydantic import ValidationError

from common.settings import Settings, SettingsError, load_settings
from copilot.rag.config import RagConfig, load_rag_config

# Rough English-text estimate; avoids a tokenizer dependency for a size ceiling.
TOKENS_PER_WORD = 1.3
HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
FRONT_MATTER_FIELDS = ("title", "category", "version", "last_updated")

INSERT_SQL = """
    INSERT INTO policy_chunks (file, doc_title, category, version, last_updated,
                               heading, heading_path, chunk_index, content, embedding)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
"""


class IndexingError(Exception):
    """A problem worth reporting to the user as `FAIL message` plus an optional hint."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Chunk:
    file: str
    doc_title: str
    category: str
    version: str
    last_updated: datetime.date
    heading: str
    heading_path: str
    chunk_index: int
    content: str

    def embed_text(self) -> str:
        # The title and heading path keep a bare "Conditions" section meaningful.
        return f"{self.doc_title} / {self.heading_path}\n\n{self.content}"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.split()) * TOKENS_PER_WORD)


def split_paragraphs(text: str, limit: int) -> list[str]:
    """Pack paragraphs into parts under `limit` tokens; a paragraph is never cut."""
    if estimate_tokens(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if current and estimate_tokens("\n\n".join([*current, paragraph])) > limit:
            parts.append("\n\n".join(current))
            current = []
        current.append(paragraph)
    parts.append("\n\n".join(current))
    return parts


def read_document(path: Path) -> tuple[dict[str, object], list[str]]:
    """Return (front matter, body lines); raise IndexingError if it is unusable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise IndexingError(f"{path.name}: cannot read file: {exc}") from None
    if not lines or lines[0].strip() != "---":
        raise IndexingError(f"{path.name}: front matter missing")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise IndexingError(f"{path.name}: front matter is never closed")
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        raise IndexingError(f"{path.name}: front matter is not valid YAML") from None
    if not isinstance(meta, dict):
        raise IndexingError(f"{path.name}: front matter must be `key: value` lines")
    missing = [f for f in FRONT_MATTER_FIELDS if meta.get(f) in (None, "")]
    if missing:
        raise IndexingError(f"{path.name}: front matter missing {', '.join(missing)}")
    if not isinstance(meta["last_updated"], datetime.date):
        raise IndexingError(f"{path.name}: last_updated must be a YYYY-MM-DD date")
    return meta, lines[end + 1 :]


def chunk_document(path: Path, cfg: RagConfig) -> list[Chunk]:
    """Split one policy doc at H2 (chunk_by: h2) or at H2 and H3 (chunk_by: h3)."""
    meta, body = read_document(path)
    split_level = int(cfg.chunk_by[1])
    title = str(meta["title"])

    sections: list[tuple[list[str], list[str]]] = []  # (heading path, body lines)
    headings: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body:
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match and 2 <= len(match.group(1)) <= split_level:
            sections.append((headings, current))
            current = []
            headings = [*headings[: len(match.group(1)) - 2], match.group(2)]
        else:
            current.append(line)
    sections.append((headings, current))

    chunks: list[Chunk] = []
    for path_parts, lines in sections:
        content = "\n".join(lines).strip()
        if not content:  # e.g. an H2 that goes straight into an H3
            continue
        for part in split_paragraphs(content, cfg.max_chunk_tokens):
            chunks.append(
                Chunk(
                    file=path.name,
                    doc_title=title,
                    category=str(meta["category"]),
                    version=str(meta["version"]),
                    last_updated=meta["last_updated"],  # type: ignore[arg-type]
                    heading=path_parts[-1] if path_parts else title,
                    heading_path=" > ".join(path_parts) or title,
                    chunk_index=len(chunks),
                    content=part,
                )
            )
    return chunks


def load_chunks(cfg: RagConfig) -> tuple[dict[str, list[Chunk]], list[str]]:
    """Chunk every policy doc; return (chunks per file, skipped file names)."""
    if not cfg.policies_path.is_dir():
        raise IndexingError(
            f"policy folder not found: {cfg.policies_path}",
            "check policies_dir in config/rag.yaml",
        )
    paths = sorted(cfg.policies_path.glob("*.md"))
    skipped = [p.name for p in paths if p.name in cfg.exclude_files]
    paths = [p for p in paths if p.name not in cfg.exclude_files]
    if not paths:
        raise IndexingError(f"no policy .md files in {cfg.policies_path}")

    by_file: dict[str, list[Chunk]] = {}
    problems: list[str] = []
    for path in paths:
        try:
            by_file[path.name] = chunk_document(path, cfg)
        except IndexingError as exc:
            problems.append(str(exc))
    if problems:
        raise IndexingError(
            "policy docs are invalid:\n      - " + "\n      - ".join(problems),
            "run `uv run python scripts/check_policies.py` for the full report",
        )
    return by_file, skipped


def embed_texts(client: OpenAI, cfg: RagConfig, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), cfg.embed_batch_size):
        batch = texts[start : start + cfg.embed_batch_size]
        # The SDK defaults to base64, which Ollama's embeddings (via LiteLLM) reject.
        response = client.embeddings.create(
            model=cfg.embed_alias, input=batch, encoding_format="float"
        )
        items = sorted(response.data, key=lambda item: item.index)
        if len(items) != len(batch):
            raise IndexingError(
                f"gateway returned {len(items)} vectors for {len(batch)} chunks"
            )
        for item in items:
            if len(item.embedding) != cfg.embed_dim:
                got = len(item.embedding)
                raise IndexingError(
                    f"embed_dim is {cfg.embed_dim} in config/rag.yaml but alias "
                    f"{cfg.embed_alias!r} returned vectors of length {got}",
                    f"set embed_dim: {got} in config/rag.yaml, run `alembic downgrade "
                    "-1` then `alembic upgrade head`, and re-run the indexer "
                    "(or point embed_alias at a model with the configured length)",
                )
            vectors.append(item.embedding)
    return vectors


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vector) + "]"


def write_index(
    cfg: RagConfig, settings: Settings, chunks: list[Chunk], vectors: list[list[float]]
) -> None:
    rows = [
        (
            c.file,
            c.doc_title,
            c.category,
            c.version,
            c.last_updated,
            c.heading,
            c.heading_path,
            c.chunk_index,
            c.content,
            vector_literal(v),
        )
        for c, v in zip(chunks, vectors, strict=True)
    ]
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
            raise IndexingError(
                "policy_chunks table missing",
                "run `uv run alembic -c db/alembic.ini upgrade head` first",
            )
        # For pgvector columns, atttypmod is the declared vector length.
        width = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'public.policy_chunks'::regclass AND attname = 'embedding'"
        ).fetchone()[0]
        if width != cfg.embed_dim:
            raise IndexingError(
                f"policy_chunks.embedding is vector({width}) but embed_dim is "
                f"{cfg.embed_dim} in config/rag.yaml",
                "run `alembic downgrade -1` then `alembic upgrade head` to rebuild "
                "the table at the configured length",
            )
        with conn.transaction():
            conn.execute("TRUNCATE policy_chunks RESTART IDENTITY")
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, rows)


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def main() -> int:
    started = time.perf_counter()
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

    client = OpenAI(base_url=settings.gateway_base_url, api_key=api_key)
    try:
        by_file, skipped = load_chunks(cfg)
        chunks = [c for file_chunks in by_file.values() for c in file_chunks]
        vectors = embed_texts(client, cfg, [c.embed_text() for c in chunks])
        write_index(cfg, settings, chunks, vectors)
    except IndexingError as exc:
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

    width = max(len(name) for name in by_file)
    for name, file_chunks in by_file.items():
        print(f"  {name:<{width}}  {len(file_chunks):>2} chunks")
    print(
        f"docs:    {len(by_file)} indexed"
        + (f" (skipped: {', '.join(skipped)})" if skipped else "")
    )
    print(
        f"chunks:  {len(chunks)}  (chunk_by {cfg.chunk_by}, max {cfg.max_chunk_tokens} tokens)"
    )
    print(f"embed:   alias {cfg.embed_alias}, dim {cfg.embed_dim}")
    print(f"time:    {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
