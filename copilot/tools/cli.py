"""Run any of the four copilot tools from the command line.

Usage: uv run python -m copilot.tools.cli <tool> <args>

    get_order ORDER_NO
    check_refund_eligibility ORDER_NO REASON
    create_ticket CATEGORY SUMMARY [--order-no ORDER_NO] [--customer-id N]
    escalate REASON SUMMARY [--customer-id N]

Prints the result as JSON, typed errors included. Exit code: 0 on success, 1 when
the tool returned a typed error, 2 on a usage error. create_ticket and escalate
really insert rows. Bad categories and reasons are not rejected here on purpose,
so you can see the tool's own typed error for them.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import get_args

from copilot.tools.check_refund_eligibility import (
    Reason as RefundReason,
)
from copilot.tools.check_refund_eligibility import (
    check_refund_eligibility,
)
from copilot.tools.create_ticket import TICKET_CATEGORIES, create_ticket
from copilot.tools.escalate import Reason as EscalationReason
from copilot.tools.escalate import escalate
from copilot.tools.get_order import ToolError, get_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m copilot.tools.cli",
        description="Run a copilot tool and print its result as JSON.",
        epilog="create_ticket and escalate insert real rows into the database.",
    )
    tools = parser.add_subparsers(dest="tool", required=True, metavar="tool")

    sub = tools.add_parser("get_order", help="look up an order")
    sub.add_argument("order_no", help="e.g. SE-10613")
    sub.set_defaults(run=lambda a: get_order(a.order_no))

    sub = tools.add_parser(
        "check_refund_eligibility", help="check return or replacement eligibility"
    )
    sub.add_argument("order_no", help="e.g. SE-10613")
    sub.add_argument("reason", help=f"one of: {', '.join(get_args(RefundReason))}")
    sub.set_defaults(run=lambda a: check_refund_eligibility(a.order_no, a.reason))

    sub = tools.add_parser("create_ticket", help="open a support ticket (writes a row)")
    sub.add_argument("category", help=f"one of: {', '.join(TICKET_CATEGORIES)}")
    sub.add_argument("summary")
    sub.add_argument("--order-no", help="order the ticket is about")
    sub.add_argument("--customer-id", type=int, help="needed when there is no order")
    sub.set_defaults(
        run=lambda a: create_ticket(
            a.category, a.summary, a.order_no, customer_id=a.customer_id
        )
    )

    sub = tools.add_parser("escalate", help="hand off to a human (writes a row)")
    sub.add_argument("reason", help=f"one of: {', '.join(get_args(EscalationReason))}")
    sub.add_argument("summary")
    sub.add_argument("--customer-id", type=int)
    sub.set_defaults(
        run=lambda a: escalate(a.reason, a.summary, customer_id=a.customer_id)
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.run(args)
    print(result.model_dump_json(indent=2))
    return 1 if isinstance(result, ToolError) else 0


if __name__ == "__main__":
    sys.exit(main())
