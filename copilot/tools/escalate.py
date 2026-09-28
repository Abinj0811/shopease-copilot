"""escalate tool: record a handoff request from the copilot to a human agent.

Escalation must never fail just because the customer is unknown, so the
escalations table allows a NULL customer. As in create_ticket, `customer_id`
is passed by the caller from the chat session and is not in the tool schema.
The confirmation promises no response time or outcome: business_rules.yaml
defines none, and the copilot must not invent policy.
"""

import datetime
from typing import Literal, get_args

import psycopg
from pydantic import BaseModel, Field

from common.settings import SettingsError, load_settings
from copilot.tools.get_order import ToolError, connect, database_error, tool_schema

ESCALATION_STATUS_OPEN = "open"

# Mirrors the triggers in data/policies/escalation-policy.md.
Reason = Literal[
    "legal_threat",
    "abuse",
    "high_value_refund",
    "repeated_failure",
    "customer_requested_agent",
    "other",
]


class EscalationRecorded(BaseModel):
    ok: Literal[True] = True
    handoff_id: int
    reason: Reason
    status: str
    customer_id: int | None
    created_at: datetime.datetime
    confirmation: str


class EscalateArgs(BaseModel):
    reason: Reason = Field(
        description="Why a human is needed: legal_threat, abuse, high_value_refund, "
        "repeated_failure, customer_requested_agent, or other"
    )
    summary: str = Field(
        description="One or two sentences for the human agent: what the customer wants"
    )


ESCALATE_TOOL = tool_schema(
    "escalate",
    "Hand the conversation to a human agent. Use it for legal threats, abuse, "
    "refunds needing human approval, repeated failure, or when the customer asks "
    "for a person. Never promise the customer an outcome.",
    EscalateArgs,
)


def escalate(
    reason: Reason, summary: str, *, customer_id: int | None = None
) -> EscalationRecorded | ToolError:
    summary = summary.strip()
    if reason not in get_args(Reason):
        return ToolError(
            error="invalid_input",
            message=f"reason must be one of {list(get_args(Reason))}, got {reason!r}.",
        )
    if not summary:
        return ToolError(error="invalid_input", message="summary is empty")

    try:
        settings = load_settings()
    except SettingsError as exc:
        return ToolError(error="configuration_error", message=str(exc))

    try:
        with connect(settings) as conn:
            if (
                customer_id is not None
                and not conn.execute(
                    "SELECT 1 FROM customers WHERE id = %s", (customer_id,)
                ).fetchone()
            ):
                return ToolError(
                    error="invalid_input",
                    message=f"No customer with id {customer_id} exists.",
                )
            handoff_id, created_at = conn.execute(
                "INSERT INTO escalations (customer_id, reason, summary, status) "
                "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
                (customer_id, reason, summary, ESCALATION_STATUS_OPEN),
            ).fetchone()
    except psycopg.Error as exc:
        return database_error(exc)

    return EscalationRecorded(
        handoff_id=handoff_id,
        reason=reason,
        status=ESCALATION_STATUS_OPEN,
        customer_id=customer_id,
        created_at=created_at,
        confirmation="Handoff recorded: a human agent will review this conversation. "
        "Tell the customer their request has been passed to a human, and do not "
        "promise a response time or an outcome.",
    )
