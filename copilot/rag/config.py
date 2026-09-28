"""Typed loader for config/rag.yaml, shared by the RAG indexer and search."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
RAG_CONFIG_PATH = REPO_ROOT / "config" / "rag.yaml"


class RagConfig(BaseModel):
    # forbid: a typo'd key should fail loudly, not silently fall back.
    model_config = ConfigDict(extra="forbid")

    embed_alias: str = Field(min_length=1)
    embed_dim: int = Field(gt=0)
    chunk_by: Literal["h2", "h3"]
    max_chunk_tokens: int = Field(gt=0)
    top_k: int = Field(ge=1)
    min_score: float = Field(ge=0, le=1)
    policies_dir: Path
    exclude_files: list[str]
    embed_batch_size: int = Field(ge=1)

    @property
    def policies_path(self) -> Path:
        return REPO_ROOT / self.policies_dir


def load_rag_config(path: Path = RAG_CONFIG_PATH) -> RagConfig:
    """Load and validate config/rag.yaml (raises ValidationError, OSError, YAMLError)."""
    with path.open(encoding="utf-8") as f:
        return RagConfig.model_validate(yaml.safe_load(f))
