"""Tests for the four copilot tools and their CLI, using the seeded database.

No test calls an LLM. Tests that need the database skip (with the fix named)
when Postgres is unreachable or not seeded, so CI, which has no database, runs
only the schema and CLI-usage tests. Orders are discovered from the data with
the same queries as scripts/show_edge_cases.py, and every policy number is read
from config/business_rules.yaml, so reseeding or editing the config does not
break them.
"""

import json

import psycopg
import pytest

from common.business_rules import load_business_rules
from common.settings import SettingsError, load_settings
from copilot.tools.check_refund_eligibility import (
    CHECK_REFUND_ELIGIBILITY_TOOL,
    check_refund_eligibility,
)
from copilot.tools.cli import main as cli_main
from copilot.tools.create_ticket import CREATE_TICKET_TOOL, create_ticket
from copilot.tools.escalate import ESCALATE_TOOL, escalate
from copilot.tools.get_order import GET_ORDER_TOOL, ToolError, connect, get_order
from data.seed_db import load_seed_config
from scripts.show_edge_cases import QUERIES

MISSING_ORDER = "SE-99999999"

ZERO_WINDOW_ORDERS_SQL = """
    SELECT o.order_no FROM orders o
    WHERE o.status = 'delivered'
      AND EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id)
      AND NOT EXISTS (
          SELECT 1 FROM order_items i JOIN products p ON p.id = i.product_id
          WHERE i.order_id = o.id AND p.category <> ALL(%(zero_categories)s))
    ORDER BY o.order_no"""

ALL_TOOLS = [
    GET_ORDER_TOOL,
    CHECK_REFUND_ELIGIBILITY_TOOL,
    CREATE_TICKET_TOOL,
    ESCALATE_TOOL,
]


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def db():
    """A connection to the seeded app database, or skip the test."""
    try:
        settings = load_settings()
    except SettingsError:
        pytest.skip(
            "settings unavailable (no .env / POSTGRES_PASSWORD): needs local DB"
        )
    try:
        conn = connect(settings)
    except psycopg.Error:
        pytest.skip("Postgres not reachable: run `docker compose up -d`")
    conn.autocommit = True
    with conn:
        if conn.execute("SELECT to_regclass('public.orders')").fetchone()[0] is None:
            pytest.skip(
                "no orders table: run `uv run alembic -c db/alembic.ini upgrade head`"
            )
        if not conn.execute("SELECT count(*) FROM orders").fetchone()[0]:
            pytest.skip("no seed data: run `uv run python data/seed_db.py`")
        yield conn


@pytest.fixture(scope="module")
def rules():
    return load_business_rules()


@pytest.fixture(scope="module")
def orders(db, rules):
    """Order numbers per scenario, found from the data (sorted, so stable)."""
    cfg = load_seed_config()
    params = {
        "as_of": cfg.as_of_date,
        "tz": cfg.timezone,
        "cancel_category": cfg.cancel_request_ticket_category,
        "high_value": rules.high_value_order_inr,
    }
    found = {
        name: [r[0] for r in db.execute(sql + " ORDER BY o.order_no", params)]
        for name, sql in QUERIES.items()
    }
    found["cancelled"] = [
        r[0]
        for r in db.execute(
            "SELECT order_no FROM orders WHERE status = 'cancelled' ORDER BY order_no"
        )
    ]
    zero_categories = [
        category
        for category, days in rules.return_window_days.items()
        if days == 0 and category != "default"
    ]
    found["zero_window"] = (
        [
            r[0]
            for r in db.execute(
                ZERO_WINDOW_ORDERS_SQL, {"zero_categories": zero_categories}
            )
        ]
        if zero_categories
        else []
    )
    return found


def pick(orders, scenario: str) -> str:
    if not orders[scenario]:
        pytest.skip(f"the seeded data has no {scenario} order")
    return orders[scenario][0]


@pytest.fixture
def restore_tables(db):
    """Undo rows a test inserts into tickets/escalations, including the id counter."""
    before = {
        table: db.execute(f"SELECT coalesce(max(id), 0) FROM {table}").fetchone()[0]
        for table in ("tickets", "escalations")
    }
    yield
    for table, max_id in before.items():
        db.execute(f"DELETE FROM {table} WHERE id > %s", (max_id,))
        db.execute(
            f"SELECT setval('{table}_id_seq', %s, %s)", (max(max_id, 1), max_id > 0)
        )


# --- no database needed -----------------------------------------------------


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t["function"]["name"])
def test_tool_schema_is_well_formed(tool):
    function = tool["function"]
    parameters = function["parameters"]
    assert tool["type"] == "function"
    assert function["name"] and function["description"]
    assert parameters["type"] == "object"
    assert set(parameters["required"]) <= set(parameters["properties"])
    # customer_id comes from the chat session, so the model must never see it.
    assert "customer_id" not in parameters["properties"]


def test_enum_values_are_offered_to_the_model():
    refund = CHECK_REFUND_ELIGIBILITY_TOOL["function"]["parameters"]["properties"]
    assert refund["reason"]["enum"] == ["changed_mind", "defective"]
    ticket = CREATE_TICKET_TOOL["function"]["parameters"]["properties"]
    assert "general" in ticket["category"]["enum"]
    escalation = ESCALATE_TOOL["function"]["parameters"]["properties"]
    assert "legal_threat" in escalation["reason"]["enum"]


def test_invalid_reason_is_a_typed_error():
    result = check_refund_eligibility("SE-10001", "because")
    assert isinstance(result, ToolError)
    assert result.error == "invalid_input"


def test_invalid_category_is_a_typed_error():
    result = create_ticket("not_a_category", "summary", "SE-10001")
    assert isinstance(result, ToolError)
    assert result.error == "invalid_input"


def test_ticket_without_order_or_customer_is_a_typed_error():
    result = create_ticket("general", "summary")
    assert isinstance(result, ToolError)
    assert result.error == "invalid_input"
    assert "order number" in result.message


def test_cli_help_lists_all_tools(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for tool in ALL_TOOLS:
        assert tool["function"]["name"] in out


def test_cli_prints_typed_error_as_json_and_exits_1(capsys):
    assert cli_main(["create_ticket", "not_a_category", "summary"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is False
    assert printed["error"] == "invalid_input"


def test_cli_usage_error_exits_2(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["no_such_tool"])
    assert exit_info.value.code == 2
    capsys.readouterr()


# --- against the seeded database ---------------------------------------------


def test_order_on_the_return_window_boundary_is_eligible(orders, rules):
    result = check_refund_eligibility(
        pick(orders, "boundary_return_date"), "changed_mind"
    )
    assert result.ok
    assert result.eligible
    assert result.rule == "within_return_window"
    assert result.outcome == "refund"
    on_boundary = [i for i in result.items if i.eligible]
    assert on_boundary
    assert any(
        i.window_days
        == rules.return_window_for(i.category)
        == result.days_since_delivery
        for i in on_boundary
    )


def test_order_one_day_past_the_window_is_not_eligible(orders, rules):
    order_no = pick(orders, "one_day_past_return_window")
    result = check_refund_eligibility(order_no, "changed_mind")
    assert result.ok
    assert not result.eligible
    assert result.rule == "outside_return_window"
    assert result.outcome == "none"
    assert result.refundable_amount_inr == 0
    assert any(
        result.days_since_delivery == rules.return_window_for(i.category) + 1
        for i in result.items
    )

    # The separate defect-replacement window (15 days) may still be open.
    defect = check_refund_eligibility(order_no, "defective")
    assert defect.eligible == (
        result.days_since_delivery <= rules.defect_replacement_days
    )


def test_category_with_zero_day_window_cannot_be_returned(orders, rules):
    order_no = pick(orders, "zero_window")
    result = check_refund_eligibility(order_no, "changed_mind")
    assert result.ok
    assert not result.eligible
    assert result.rule == "category_not_returnable"
    assert all(i.window_days == 0 for i in result.items)

    defect = check_refund_eligibility(order_no, "defective")
    within_defect_window = defect.days_since_delivery <= rules.defect_replacement_days
    assert defect.eligible == within_defect_window
    assert defect.rule == (
        "within_defect_window" if within_defect_window else "outside_defect_window"
    )


def test_cancelled_order_is_not_eligible(orders):
    result = check_refund_eligibility(pick(orders, "cancelled"), "changed_mind")
    assert result.ok
    assert not result.eligible
    assert result.rule == "order_cancelled"
    assert result.outcome == "none"


def test_nonexistent_order_is_a_typed_not_found_error(db):
    for result in (
        get_order(MISSING_ORDER),
        check_refund_eligibility(MISSING_ORDER, "changed_mind"),
    ):
        assert isinstance(result, ToolError)
        assert result.ok is False
        assert result.error == "order_not_found"
        assert MISSING_ORDER in result.message


def test_high_value_order_needs_human_approval(orders, rules):
    result = check_refund_eligibility(pick(orders, "high_value_order"), "changed_mind")
    assert result.ok
    assert result.eligible
    assert result.refundable_amount_inr > rules.refund_needs_human_above_inr
    assert result.needs_human_approval


def test_small_refund_does_not_need_human_approval(orders, rules):
    result = check_refund_eligibility(
        pick(orders, "boundary_return_date"), "changed_mind"
    )
    assert result.ok
    assert result.refundable_amount_inr <= rules.refund_needs_human_above_inr
    assert not result.needs_human_approval


def test_get_order_returns_items_and_no_personal_details(orders):
    result = get_order(pick(orders, "high_value_order"))
    assert result.ok
    assert result.items
    dumped = result.model_dump_json()
    for personal in ("email", "phone", "customer_name"):
        assert personal not in dumped


def test_create_ticket_writes_a_row(db, orders, restore_tables):
    order_no = pick(orders, "boundary_return_date")
    result = create_ticket("product_defect", "Item stopped working (pytest)", order_no)
    assert result.ok
    assert result.status == "open"

    row = db.execute(
        "SELECT t.customer_id, t.category, t.status, t.summary, o.order_no, o.customer_id "
        "FROM tickets t JOIN orders o ON o.id = t.order_id WHERE t.id = %s",
        (result.ticket_id,),
    ).fetchone()
    assert row is not None
    customer_id, category, status, summary, row_order_no, order_customer_id = row
    assert (category, status) == ("product_defect", "open")
    assert summary == "Item stopped working (pytest)"
    assert row_order_no == order_no
    assert customer_id == order_customer_id == result.customer_id


def test_escalate_writes_a_row(db, restore_tables):
    result = escalate("customer_requested_agent", "Wants a person (pytest)")
    assert result.ok
    row = db.execute(
        "SELECT reason, status, customer_id FROM escalations WHERE id = %s",
        (result.handoff_id,),
    ).fetchone()
    assert row == ("customer_requested_agent", "open", None)


def test_cli_runs_a_tool_and_prints_json(orders, capsys):
    order_no = pick(orders, "boundary_return_date")
    assert cli_main(["check_refund_eligibility", order_no, "changed_mind"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["order_no"] == order_no
    assert printed["eligible"] is True


def test_cli_prints_not_found_as_typed_error(db, capsys):
    assert cli_main(["get_order", MISSING_ORDER]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "ok": False,
        "error": "order_not_found",
        "message": f"No order with number {MISSING_ORDER} exists.",
    }
