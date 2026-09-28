---
title: Cash on Delivery (COD) Refund Process
category: refunds
version: "1.0"
last_updated: 2026-08-01
---

## Why COD Refunds Work Differently

COD orders are paid for in cash at delivery, so ShopEase has no card or
UPI account to refund to. Instead, COD refunds are paid out by **bank
transfer**, which takes longer to arrange than a UPI or card reversal.

### Timeline

A COD refund is paid within **10 days** of approval — the
`cod_bank_transfer` timeline in `refund-payout-timelines.md`.

### What's Needed

- The customer's bank account number and IFSC code, collected after the
  return or replacement is approved.
- Refunds above ₹20,000 still require human approval first — see
  `refund-approval-thresholds.md` — the bank-transfer step happens only
  after that approval.

## Related Policies

`refund-payout-timelines.md`, `refund-approval-thresholds.md`
