"""Pydantic schema and loader for config/business_rules.yaml.

The copilot must never invent or override a policy number itself — every
number it uses has to come from here. Read by scripts/check_rules.py now,
and later by the refund-eligibility tool and data/seed_db.py.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "business_rules.yaml"

# A minimal order-status vocabulary for this one field. The authoritative
# set of statuses is defined later by the orders table (docs/STEP_PLAN.md
# A18); keep this list in step with it once that exists.
OrderStatus = Literal[
    "placed", "confirmed", "shipped", "delivered", "cancelled", "returned"
]


class RefundTimelineDays(BaseModel):
    """Days to pay out an already-approved refund, by payment method."""

    model_config = ConfigDict(extra="forbid")

    upi: int = Field(gt=0)
    card: int = Field(gt=0)
    cod_bank_transfer: int = Field(gt=0)


class ShippingSlaDays(BaseModel):
    """Expected delivery time in days, by shipping zone."""

    model_config = ConfigDict(extra="forbid")

    metro: int = Field(gt=0)
    other: int = Field(gt=0)


class BusinessRules(BaseModel):
    """ShopEase's policy numbers, validated. See config/business_rules.yaml."""

    model_config = ConfigDict(extra="forbid")

    return_window_days: dict[str, int]
    defect_replacement_days: int = Field(gt=0)
    refund_timeline_days: RefundTimelineDays
    refund_needs_human_above_inr: int = Field(gt=0)
    high_value_order_inr: int = Field(gt=0)
    warranty_months: dict[str, int]
    shipping_sla_days: ShippingSlaDays
    cancellation_allowed_until_status: OrderStatus

    @field_validator("return_window_days", "warranty_months")
    @classmethod
    def _must_have_default(cls, value: dict[str, int]) -> dict[str, int]:
        if "default" not in value:
            raise ValueError("must include a 'default' key as the fallback")
        if any(v < 0 for v in value.values()):
            raise ValueError("values must be zero or greater")
        return value

    def return_window_for(self, category: str) -> int:
        """Return window in days for a product category, falling back to default."""
        return self.return_window_days.get(category, self.return_window_days["default"])

    def warranty_months_for(self, category: str) -> int:
        """Warranty in months for a product category, falling back to default."""
        return self.warranty_months.get(category, self.warranty_months["default"])


def load_business_rules() -> BusinessRules:
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    return BusinessRules.model_validate(data)
