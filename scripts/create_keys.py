"""Create one LiteLLM team and virtual key per entry in gateway/teams.yaml.

Usage: uv run python scripts/create_keys.py

Reads GATEWAY_BASE_URL and LITELLM_MASTER_KEY from .env. Idempotent: re-running
reuses existing teams (matched by team_alias) and just prints a fresh key.
"""

import os
import sys
import uuid
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TEAMS_PATH = ROOT / "gateway" / "teams.yaml"
COMPOSE_HINT = "is `docker compose up -d litellm` running?"


class ConfigError(Exception):
    """A required .env value, or gateway/teams.yaml, is missing or invalid."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def load_teams() -> list[dict]:
    if not TEAMS_PATH.exists():
        raise ConfigError(f"{TEAMS_PATH} not found")
    data = yaml.safe_load(TEAMS_PATH.read_text(encoding="utf-8")) or {}
    teams = data.get("teams")
    if not teams:
        raise ConfigError(f"{TEAMS_PATH} has no 'teams' entries")
    return teams


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def run() -> int:
    # Admin routes (/team/new, /key/generate) sit at the proxy root, not
    # under /v1 like GATEWAY_BASE_URL's chat/embeddings routes.
    admin_base = require("GATEWAY_BASE_URL").removesuffix("/v1")
    master_key = require("LITELLM_MASTER_KEY")
    teams = load_teams()

    client = httpx.Client(
        base_url=admin_base,
        headers={"Authorization": f"Bearer {master_key}"},
        timeout=15,
    )

    existing = client.get("/team/list")
    existing.raise_for_status()
    existing_by_alias = {
        team["team_alias"]: team["team_id"] for team in existing.json()
    }

    for team in teams:
        alias = team["team_alias"]
        team_id = existing_by_alias.get(alias)
        if team_id:
            print(f"team:    {alias} (already exists)")
        else:
            created = client.post("/team/new", json=team)
            created.raise_for_status()
            team_id = created.json()["team_id"]
            print(f"team:    {alias} (created)")

        # key_alias must be unique across ALL keys on the proxy, so a fixed
        # alias would collide on every re-run; a short suffix keeps it
        # readable while letting each run mint a genuinely fresh key.
        key_alias = f"{alias}-key-{uuid.uuid4().hex[:8]}"
        key_response = client.post(
            "/key/generate", json={"team_id": team_id, "key_alias": key_alias}
        )
        key_response.raise_for_status()
        key = key_response.json()["key"]

        print(f"team_id: {team_id}")
        print(f"key:     {key}")
        print()

    return 0


def main() -> int:
    load_dotenv(ENV_PATH)
    try:
        return run()
    except ConfigError as exc:
        return fail(f"config: {exc}")
    except httpx.ConnectError:
        return fail("cannot connect to the gateway", COMPOSE_HINT)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            return fail(
                "gateway returned HTTP 401 (unauthorized)",
                "does .env's LITELLM_MASTER_KEY match what the running "
                "container was started with? `docker compose up -d litellm` "
                "again after changing it",
            )
        return fail(f"gateway returned HTTP {status}: {exc.response.text[:300]}")


if __name__ == "__main__":
    sys.exit(main())
