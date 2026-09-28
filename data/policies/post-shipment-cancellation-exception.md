---
title: Cancellation Requests After Shipping
category: cancellation
version: "1.0"
last_updated: 2026-08-01
---

## No Self-Service Cancellation Once Shipped

Once an order's status is **Shipped**, it can no longer be cancelled
through self-service. This is the cutoff referenced in
`order-cancellation-policy.md`: cancellation is only available in the
Placed and Confirmed statuses, and stops being available the moment the
order moves to Shipped.

### Why

The order is already in transit and ShopEase cannot recall it from the
courier network.

### How This Must Be Handled

- The copilot must **not** cancel an order that is already Shipped, and
  must **not** tell the customer it has been cancelled.
- The request must be escalated to a human agent — see
  `escalation-policy.md` — who can arrange a refused-delivery return once
  the order arrives, if the customer still wants to return it.
- If the order has already reached **Delivered**, the same rule applies:
  cancellation is not possible; direct the customer to
  `returns-overview.md` instead.

## Related Policies

`order-cancellation-policy.md`, `escalation-policy.md`, `returns-overview.md`
