"""List order numbers per edge-case category, recomputed from the seeded data.

Usage: uv run python scripts/show_edge_cases.py

Nothing in the database tags an order as an edge case; each category is found
with the same rule data/seed_db.py builds it by, using config/seed.yaml
(as_of_date, timezone, cancel_request_ticket_category) and
config/business_rules.yaml (return windows, high_value_order_inr). So this
also verifies the seeded data rather than just echoing it.
"""

import sys
from pathlib import Path

import psycopg
import yaml
from pydantic import ValidationError

# Plain `python scripts/show_edge_cases.py` only puts scripts/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.business_rules import load_business_rules
from common.settings import SettingsError, load_settings
from data.seed_db import EDGE_CASES, load_seed_config

# Days between as_of_date and the delivery date, both in the configured zone.
DAYS_SINCE = "(%(as_of)s::date - (o.delivered_at AT TIME ZONE %(tz)s)::date)"

QUERIES = {
    "boundary_return_date": f"""
        SELECT o.order_no FROM orders o
        WHERE o.status = 'delivered' AND EXISTS (
            SELECT 1 FROM order_items i JOIN products p ON p.id = i.product_id
            WHERE i.order_id = o.id AND p.returnable_days > 0
              AND {DAYS_SINCE} = p.returnable_days)""",
    "one_day_past_return_window": f"""
        SELECT o.order_no FROM orders o
        WHERE o.status = 'delivered' AND EXISTS (
            SELECT 1 FROM order_items i JOIN products p ON p.id = i.product_id
            WHERE i.order_id = o.id AND p.returnable_days > 0
              AND {DAYS_SINCE} = p.returnable_days + 1)""",
    "cancelled_after_shipped": """
        SELECT o.order_no FROM orders o
        WHERE o.status = 'shipped' AND EXISTS (
            SELECT 1 FROM tickets t
            WHERE t.order_id = o.id AND t.category = %(cancel_category)s)""",
    "cod_payment": """
        SELECT o.order_no FROM orders o
        WHERE o.payment_mode = 'cod'
          AND EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.id)""",
    "high_value_order": """
        SELECT o.order_no FROM orders o WHERE o.total_inr > %(high_value)s""",
    "repeated_refunds": """
        SELECT o.order_no FROM orders o
        WHERE (SELECT count(*) FROM refunds r WHERE r.order_id = o.id) >= 2""",
}


def fail(message: str, hint: str | None = None) -> int:
    print(f"FAIL  {message}")
    if hint:
        print(f"      hint: {hint}")
    return 1


def main() -> int:
    try:
        cfg = load_seed_config()
        rules = load_business_rules()
        settings = load_settings()
    except (ValidationError, SettingsError, yaml.YAMLError) as exc:
        return fail(f"invalid configuration:\n{exc}")

    params = {
        "as_of": cfg.as_of_date,
        "tz": cfg.timezone,
        "cancel_category": cfg.cancel_request_ticket_category,
        "high_value": rules.high_value_order_inr,
    }
    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            dbname=settings.postgres_db,
            connect_timeout=settings.infra_check_timeout_seconds,
        ) as conn:
            if (
                conn.execute("SELECT to_regclass('public.orders')").fetchone()[0]
                is None
            ):
                return fail(
                    "orders table missing",
                    "run `uv run alembic -c db/alembic.ini upgrade head` first",
                )
            if not conn.execute("SELECT count(*) FROM orders").fetchone()[0]:
                return fail(
                    "no orders yet", "run `uv run python data/seed_db.py` first"
                )
            results = {
                name: [
                    row[0]
                    for row in conn.execute(
                        QUERIES[name] + " ORDER BY o.order_no", params
                    ).fetchall()
                ]
                for name in EDGE_CASES
            }
    except psycopg.Error as exc:
        reason = (
            str(exc).strip().splitlines()[-1]
            if str(exc).strip()
            else type(exc).__name__
        )
        return fail(f"database error: {reason}", "is `docker compose up -d` running?")

    print(f"as_of_date {cfg.as_of_date} ({cfg.timezone})")
    width = max(len(name) for name in EDGE_CASES)
    for name in EDGE_CASES:
        numbers = results[name]
        listed = ", ".join(numbers) if numbers else "(none)"
        print(f"  {name:<{width}}  {len(numbers):>2}  {listed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
