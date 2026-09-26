"""Check that Postgres (with pgvector) and Redis from docker-compose.yml are reachable.

Reads connection values from .env, enables the pgvector extension if missing,
and prints OK or a clear FAIL for each service. Exit code 0 only if all pass.
"""

import os
import sys
from pathlib import Path

import psycopg
import redis
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
COMPOSE_HINT = "is `docker compose up -d` running and are the .env values correct?"


class ConfigError(Exception):
    """A required .env variable is missing."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def check_postgres(timeout: int) -> bool:
    try:
        host, port = require("POSTGRES_HOST"), require("POSTGRES_PORT")
        db = require("POSTGRES_DB")
        with psycopg.connect(
            host=host,
            port=int(port),
            user=require("POSTGRES_USER"),
            password=require("POSTGRES_PASSWORD"),
            dbname=db,
            connect_timeout=timeout,
            autocommit=True,
        ) as conn:
            print(f"OK    Postgres  {host}:{port}/{db}")
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            if row is None:
                print("FAIL  pgvector: extension not present after CREATE EXTENSION")
                return False
            print(f"OK    pgvector  {row[0]}")
            return True
    except ConfigError as exc:
        print(f"FAIL  Postgres: {exc}")
    except psycopg.Error as exc:
        reason = (
            str(exc).strip().splitlines()[-1]
            if str(exc).strip()
            else type(exc).__name__
        )
        print(f"FAIL  Postgres: {reason}\n      hint: {COMPOSE_HINT}")
    return False


def check_redis(timeout: int) -> bool:
    try:
        host, port = require("REDIS_HOST"), require("REDIS_PORT")
        client = redis.Redis(
            host=host,
            port=int(port),
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        client.ping()
        print(f"OK    Redis     {host}:{port}")
        return True
    except ConfigError as exc:
        print(f"FAIL  Redis: {exc}")
    except redis.RedisError as exc:
        print(f"FAIL  Redis: {exc}\n      hint: {COMPOSE_HINT}")
    return False


def main() -> int:
    load_dotenv(ENV_PATH)
    try:
        timeout = int(require("INFRA_CHECK_TIMEOUT_SECONDS"))
    except (ConfigError, ValueError) as exc:
        print(f"FAIL  config: {exc}")
        return 1
    results = [check_postgres(timeout), check_redis(timeout)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
