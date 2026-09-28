# Deliberate traps in this policy set

Two traps are built into `data/policies/` on purpose, to test retrieval
and the copilot's answers in later steps (RAG search, evals, judge
calibration). Do not "fix" them by making the documents agree.

## 1. Deprecated document

**`return-policy-legacy-2024.md`** — an old return policy kept for
historical reference, marked Deprecated in its title and body, with
return windows and a refund timeline that no longer match
`config/business_rules.yaml` (14/21/30/10 days and a flat 5-day refund,
vs. the current 7/10/15/0 days and per-payment-method refund timeline).

A correct answer must cite `returns-overview.md` (or the relevant
category doc) and never this file. If RAG search or an eval case
surfaces this document as a top result, or the copilot quotes its
numbers, that's a retrieval/prompt failure worth recording in
`docs/decisions.md`.

## 2. General-rule / exception pair

**`order-cancellation-policy.md`** (general rule) and
**`post-shipment-cancellation-exception.md`** (exception) together
describe `cancellation_allowed_until_status: shipped` from
`config/business_rules.yaml`. Read alone, the general rule's wording
("cancel while Placed or Confirmed... stops once Shipped") is fine, but a
shallow read of the raw config key name — "allowed **until** shipped" —
invites the wrong conclusion that cancellation is still allowed *at* the
Shipped status.

The exception document exists specifically to force the correct reading:
cancellation is not available once status is Shipped, matching the
seeded `cancelled_after_shipped` edge-case orders (see
`scripts/show_edge_cases.py`), where the copilot must refuse the
cancellation and escalate per `escalation-policy.md`. An eval case that
asks about cancelling a Shipped order should retrieve both documents (or
at least the exception) and refuse — retrieving only the general-rule
document and answering "yes" is the failure mode this pair is meant to
catch.
