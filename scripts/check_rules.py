"""Validate config/business_rules.yaml and print every rule as a table.

Usage: uv run python scripts/check_rules.py
"""

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

# Plain `python scripts/check_rules.py` only puts scripts/ on sys.path, not
# the repo root, so common/ wouldn't otherwise be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.business_rules import RULES_PATH, load_business_rules


def flatten(data: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Turn a nested dict into (dotted.path, value) rows, in insertion order."""
    rows: list[tuple[str, object]] = []
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(flatten(value, full_key))
        else:
            rows.append((full_key, value))
    return rows


def fail(message: str) -> int:
    print(f"FAIL  {message}")
    return 1


def main() -> int:
    if not RULES_PATH.exists():
        return fail(f"{RULES_PATH} not found")

    try:
        rules = load_business_rules()
    except yaml.YAMLError as exc:
        return fail(f"{RULES_PATH} is not valid YAML: {exc}")
    except ValidationError as exc:
        print(f"FAIL  {RULES_PATH} failed validation:")
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            print(f"      {loc}: {error['msg']}")
        return 1

    rows = flatten(rules.model_dump())
    name_width = max(len(name) for name, _ in rows)
    print(f"{'rule':<{name_width}}  value")
    print(f"{'-' * name_width}  {'-' * 20}")
    for name, value in rows:
        print(f"{name:<{name_width}}  {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
