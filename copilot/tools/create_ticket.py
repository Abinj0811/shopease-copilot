"""create_ticket tool: open a support ticket, optionally about one order.

The tickets table requires a customer. The model can only supply an order
number, so the customer is taken from that order; when there is no order the
caller (the API, from the chat session) passes `customer_id`. That argument is
deliberately not in the tool schema, so the model can never invent one.
Allowed categories are the ticket categories the seeded data uses
(config/seed.yaml), so live and seeded tickets share one vocabulary.
"""

import datetime
from pathlib import Path
from typing import Literal

import psycopg
import yaml
from pydantic import BaseModel, Field

from common.settings import SettingsError, load_settings
from copilot.tools.get_order import ToolError, connect, database_error, tool_schema

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "seed.yaml"

TICKET_STATUS_OPEN = "open"


def load_ticket_categories() -> list[str]:
    data = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))
    return [*data["ticket_categories"], data["cancel_request_ticket_category"]]


TICKET_CATEGORIES = load_ticket_categories()


class TicketCreated(BaseModel):
    ok: Literal[True] = True
    ticket_id: int
    category: str
    status: str
    order_no: str | None
    customer_id: int
    created_at: datetime.datetime


class CreateTicketArgs(BaseModel):
    category: str = Field(description="Ticket category")
    summary: str = Field(
        description="One or two sentences describing the customer's issue"
    )
    order_no: str | None = Field(
        default=None,
        description="Order number the ticket is about, e.g. SE-10613, if any",
    )


CREATE_TICKET_TOOL = tool_schema(
    "create_ticket",
    "Open a support ticket for a follow-up the copilot cannot resolve itself "
    "(for example a delivery delay or a product defect).",
    CreateTicketArgs,
)
CREATE_TICKET_TOOL["function"]["parameters"]["properties"]["category"]["enum"] = (
    TICKET_CATEGORIES
)


def create_ticket(
    category: str,
    summary: str,
    order_no: str | None = None,
    *,
    customer_id: int | None = None,
) -> TicketCreated | ToolError:
    category = category.strip()
    summary = summary.strip()
    order_no = (order_no or "").strip().upper() or None
    if category not in TICKET_CATEGORIES:
        return ToolError(
            error="invalid_input",
            message=f"category must be one of {TICKET_CATEGORIES}, got {category!r}.",
        )
    if not summary:
        return ToolError(error="invalid_input", message="summary is empty")
    if order_no is None and customer_id is None:
        return ToolError(
            error="invalid_input",
            message="Cannot create a ticket without knowing the customer: "
            "ask the customer for their order number.",
        )

    try:
        settings = load_settings()
    except SettingsError as exc:
        return ToolError(error="configuration_error", message=str(exc))

    try:
        with connect(settings) as conn:
            order_id = None
            if order_no is not None:
                row = conn.execute(
                    "SELECT id, customer_id FROM orders WHERE order_no = %s",
                    (order_no,),
                ).fetchone()
                if row is None:
                    return ToolError(
                        error="order_not_found",
                        message=f"No order with number {order_no} exists.",
                    )
                order_id, customer_id = row  # the order's customer wins
            elif not conn.execute(
                "SELECT 1 FROM customers WHERE id = %s", (customer_id,)
            ).fetchone():
                return ToolError(
                    error="invalid_input",
                    message=f"No customer with id {customer_id} exists.",
                )
            ticket_id, created_at = conn.execute(
                "INSERT INTO tickets (customer_id, order_id, category, status, summary) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at",
                (customer_id, order_id, category, TICKET_STATUS_OPEN, summary),
            ).fetchone()
    except psycopg.Error as exc:
        return database_error(exc)

    return TicketCreated(
        ticket_id=ticket_id,
        category=category,
        status=TICKET_STATUS_OPEN,
        order_no=order_no,
        customer_id=customer_id,
        created_at=created_at,
    )
