"""get_order tool: look up one ShopEase order (status, items, payment, refunds).

A plain function plus an OpenAI-format tool schema. It never raises for
expected problems: it returns an OrderDetails, or a ToolError the copilot can
read and explain. Customer name, email and phone are deliberately not returned
(PII stays out of anything that may reach a model provider).
"""

import datetime
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from common.settings import Settings, SettingsError, load_settings


class ToolError(BaseModel):
    """A tool could not produce a result; `message` explains why in plain words."""

    ok: Literal[False] = False
    error: Literal[
        "order_not_found",
        "invalid_input",
        "invalid_data",
        "database_unavailable",
        "configuration_error",
    ]
    message: str


class OrderItem(BaseModel):
    sku: str
    name: str
    category: str
    qty: int
    unit_price_inr: int


class OrderRefund(BaseModel):
    status: str
    amount_inr: int
    reason: str
    created_at: datetime.datetime


class OrderDetails(BaseModel):
    ok: Literal[True] = True
    order_no: str
    status: str
    placed_at: datetime.datetime
    delivered_at: datetime.datetime | None
    payment_mode: str
    total_inr: int
    customer_city: str | None
    customer_tier: str | None
    items: list[OrderItem]
    refunds: list[OrderRefund]


class GetOrderArgs(BaseModel):
    order_no: str = Field(description="ShopEase order number, e.g. SE-10613")


def tool_schema(name: str, description: str, args: type[BaseModel]) -> dict[str, Any]:
    """Build an OpenAI-format tool definition from a Pydantic args model."""
    parameters = args.model_json_schema()
    parameters.pop("title", None)
    for prop in parameters["properties"].values():
        prop.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


GET_ORDER_TOOL = tool_schema(
    "get_order",
    "Look up a ShopEase order by order number. Returns its status, payment mode, "
    "total, items and any refunds. Use it to answer 'where is my order' questions.",
    GetOrderArgs,
)


def connect(settings: Settings) -> psycopg.Connection:
    """Open a connection to the app database (shared by all the tools)."""
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_db,
        connect_timeout=settings.infra_check_timeout_seconds,
    )


def database_error(exc: psycopg.Error) -> ToolError:
    reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else "unknown"
    return ToolError(error="database_unavailable", message=f"Database error: {reason}")


def get_order(order_no: str) -> OrderDetails | ToolError:
    order_no = order_no.strip().upper()
    if not order_no:
        return ToolError(error="invalid_input", message="order_no is empty")

    try:
        settings = load_settings()
    except SettingsError as exc:
        return ToolError(error="configuration_error", message=str(exc))

    try:
        with (
            connect(settings) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                "SELECT o.id, o.order_no, o.status, o.placed_at, o.delivered_at, "
                "o.payment_mode, o.total_inr, c.city AS customer_city, "
                "c.tier AS customer_tier "
                "FROM orders o JOIN customers c ON c.id = o.customer_id "
                "WHERE o.order_no = %s",
                (order_no,),
            )
            order = cur.fetchone()
            if order is None:
                return ToolError(
                    error="order_not_found",
                    message=f"No order with number {order_no} exists.",
                )
            order_id = order.pop("id")
            cur.execute(
                "SELECT p.sku, p.name, p.category, i.qty, i.unit_price_inr "
                "FROM order_items i JOIN products p ON p.id = i.product_id "
                "WHERE i.order_id = %s ORDER BY i.id",
                (order_id,),
            )
            items = cur.fetchall()
            cur.execute(
                "SELECT status, amount_inr, reason, created_at FROM refunds "
                "WHERE order_id = %s ORDER BY id",
                (order_id,),
            )
            refunds = cur.fetchall()
    except psycopg.Error as exc:
        return database_error(exc)

    return OrderDetails(**order, items=items, refunds=refunds)
