"""Typed application settings, loaded from environment variables and .env.

Every value here must also be listed in .env.example and docs/CONFIG.md.
Real environment variables override .env. Run `uv run python -m common.settings`
to print the loaded values (secrets masked).
"""

from pathlib import Path

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the repo root so it works from any working directory.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class SettingsError(RuntimeError):
    """Settings are missing or invalid; the message names each offending variable."""


class Settings(BaseSettings):
    # extra="ignore": .env may hold variables that later steps add or that
    # other tools (Compose, LiteLLM) read; only declared fields are validated.
    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")

    postgres_user: str = "shopease"
    # Required, no default: never bake a credential into code. Empty counts as missing.
    postgres_password: SecretStr = Field(min_length=1)
    postgres_db: str = "shopease"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)

    infra_check_timeout_seconds: int = Field(default=5, ge=1)


def load_settings() -> Settings:
    """Load settings, or raise SettingsError naming every missing/invalid variable."""
    try:
        return Settings()  # type: ignore[call-arg]  # required fields come from the environment
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            name = str(err["loc"][0]).upper()
            if err["type"] in ("missing", "string_too_short", "too_short"):
                problems.append(
                    f"{name}: missing or empty (set it in .env, see .env.example)"
                )
            else:
                problems.append(f"{name}: {err['msg']}")
        raise SettingsError(
            "Invalid configuration:\n  - " + "\n  - ".join(problems)
        ) from None


if __name__ == "__main__":
    try:
        loaded = load_settings()
    except SettingsError as error:
        raise SystemExit(str(error)) from None
    for field, value in loaded.model_dump().items():
        print(f"{field.upper()}={value}")
