"""check_refund_eligibility tool: can this order be returned or replaced, and who approves?

Every number comes from config/business_rules.yaml (return windows, defect
replacement days, refund_needs_human_above_inr, refund timelines), and "today"
is as_of_date from config/seed.yaml, never the system clock, so results are
reproducible. Days are counted the same way as scripts/show_edge_cases.py:
as_of_date minus the delivery date in seed.yaml's timezone. An order delivered
exactly `window` days ago is still eligible; `window + 1` is not.

Each line item is judged against its own category's window. Existing refunds
are reported but not used to decide, because business_rules.yaml has no rule
about them and the copilot must not invent policy.
"""

import datetime
from pathlib import Path
from typing import Literal, get_args
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from common.business_rules import load_business_rules
from copilot.tools.get_order import (
    OrderRefund,
    ToolError,
    get_order,
    tool_schema,
)

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "seed.yaml"

# orders.payment_mode -> refund_timeline_days key; other modes use their own name.
PAYMENT_MODE_TIMELINE_KEY = {"cod": "cod_bank_transfer"}

Reason = Literal["changed_mind", "defective"]
Rule = Literal[
    "order_cancelled",
    "order_already_returned",
    "order_not_delivered",
    "category_not_returnable",
    "within_return_window",
    "outside_return_window",
    "within_defect_window",
    "outside_defect_window",
]


class ItemVerdict(BaseModel):
    sku: str
    name: str
    category: str
    qty: int
    line_total_inr: int
    window_days: int
    eligible: bool


class RefundEligibility(BaseModel):
    ok: Literal[True] = True
    order_no: str
    order_status: str
    reason: Reason
    as_of_date: datetime.date
    eligible: bool
    rule: Rule
    outcome: Literal["refund", "replacement", "none"]
    days_since_delivery: int | None
    refundable_amount_inr: int
    needs_human_approval: bool
    refund_timeline_days: int | None
    explanation: str
    items: list[ItemVerdict]
    existing_refunds: list[OrderRefund]


class CheckRefundEligibilityArgs(BaseModel):
    order_no: str = Field(description="ShopEase order number, e.g. SE-10613")
    reason: Reason = Field(
        description="'changed_mind' if the customer just wants to return the item; "
        "'defective' if it is faulty or stopped working"
    )


CHECK_REFUND_ELIGIBILITY_TOOL = tool_schema(
    "check_refund_eligibility",
    "Check whether an order can be returned for a refund (changed_mind) or "
    "replaced (defective) under ShopEase policy, which rule decided it, and "
    "whether a human must approve the refund. Never promise a refund without it.",
    CheckRefundEligibilityArgs,
)


def load_as_of() -> tuple[datetime.date, ZoneInfo]:
    data = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))
    return datetime.date.fromisoformat(str(data["as_of_date"])), ZoneInfo(
        data["timezone"]
    )


def explain(
    rule: Rule,
    *,
    order_no: str,
    status: str,
    days: int | None,
    as_of: datetime.date,
    defect_days: int,
    eligible_count: int,
    item_count: int,
    amount: int,
    threshold: int,
    timeline: int | None,
) -> str:
    since = f"Delivered {days} day(s) before {as_of}"
    if rule == "order_cancelled":
        return f"Order {order_no} is cancelled, so there is nothing to return."
    if rule == "order_already_returned":
        return f"Order {order_no} is already marked returned."
    if rule == "order_not_delivered":
        return (
            f"Order {order_no} has status {status}, so it has not been delivered "
            "and a return or defect claim cannot start yet."
        )
    if rule == "category_not_returnable":
        return "None of the items have a return window, so none can be returned."
    if rule == "outside_return_window":
        return f"{since}: past the return window of every returnable item."
    if rule == "within_defect_window":
        return f"{since}: within the {defect_days}-day defect replacement window."
    if rule == "outside_defect_window":
        return f"{since}: past the {defect_days}-day defect replacement window."
    text = f"{since}: inside the return window for {eligible_count} of {item_count} item(s)."
    if amount > threshold:
        return f"{text} The INR {amount} refund is above INR {threshold}, so a human must approve it."
    return f"{text} The INR {amount} refund is paid within {timeline} days of approval."


def check_refund_eligibility(
    order_no: str, reason: Reason
) -> RefundEligibility | ToolError:
    if reason not in get_args(Reason):
        return ToolError(
            error="invalid_input",
            message=f"reason must be one of {list(get_args(Reason))}, got {reason!r}.",
        )
    order = get_order(order_no)
    if isinstance(order, ToolError):
        return order

    try:
        rules = load_business_rules()
        as_of, tz = load_as_of()
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        return ToolError(
            error="configuration_error",
            message=f"Cannot load business_rules.yaml or seed.yaml: {exc}",
        )

    days: int | None = None
    if order.status == "delivered":
        if order.delivered_at is None:
            return ToolError(
                error="invalid_data",
                message=f"Order {order.order_no} is delivered but has no delivery date.",
            )
        days = (as_of - order.delivered_at.astimezone(tz).date()).days

    verdicts = []
    for item in order.items:
        window = (
            rules.defect_replacement_days
            if reason == "defective"
            else rules.return_window_for(item.category)
        )
        verdicts.append(
            ItemVerdict(
                sku=item.sku,
                name=item.name,
                category=item.category,
                qty=item.qty,
                line_total_inr=item.qty * item.unit_price_inr,
                window_days=window,
                eligible=days is not None and window > 0 and days <= window,
            )
        )
    any_eligible = any(v.eligible for v in verdicts)

    rule: Rule
    if order.status == "cancelled":
        rule = "order_cancelled"
    elif order.status == "returned":
        rule = "order_already_returned"
    elif days is None:
        rule = "order_not_delivered"
    elif reason == "defective":
        rule = "within_defect_window" if any_eligible else "outside_defect_window"
    elif any_eligible:
        rule = "within_return_window"
    elif any(v.window_days > 0 for v in verdicts):
        rule = "outside_return_window"
    else:
        rule = "category_not_returnable"

    outcome: Literal["refund", "replacement", "none"] = "none"
    if any_eligible:
        outcome = "refund" if reason == "changed_mind" else "replacement"
    amount = (
        sum(v.line_total_inr for v in verdicts if v.eligible)
        if outcome == "refund"
        else 0
    )
    threshold = rules.refund_needs_human_above_inr

    timeline = None
    if outcome == "refund":
        key = PAYMENT_MODE_TIMELINE_KEY.get(order.payment_mode, order.payment_mode)
        timeline = rules.refund_timeline_days.model_dump().get(key)
        if timeline is None:
            return ToolError(
                error="invalid_data",
                message=f"Unknown payment mode {order.payment_mode!r} on {order.order_no}.",
            )

    return RefundEligibility(
        order_no=order.order_no,
        order_status=order.status,
        reason=reason,
        as_of_date=as_of,
        eligible=any_eligible,
        rule=rule,
        outcome=outcome,
        days_since_delivery=days,
        refundable_amount_inr=amount,
        needs_human_approval=amount > threshold,
        refund_timeline_days=timeline,
        explanation=explain(
            rule,
            order_no=order.order_no,
            status=order.status,
            days=days,
            as_of=as_of,
            defect_days=rules.defect_replacement_days,
            eligible_count=sum(v.eligible for v in verdicts),
            item_count=len(verdicts),
            amount=amount,
            threshold=threshold,
            timeline=timeline,
        ),
        items=verdicts,
        existing_refunds=order.refunds,
    )
