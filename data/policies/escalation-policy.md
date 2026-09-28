---
title: Escalation to a Human Agent
category: escalation
version: "1.0"
last_updated: 2026-08-01
---

## When the Copilot Must Escalate

The copilot must hand off to a human agent, rather than resolve the
conversation itself, when any of the following apply:

### Legal Threats or Abuse

Any message threatening legal action, or containing abusive language
toward ShopEase or its staff, is escalated immediately without further
troubleshooting.

### High-Value Refunds

Any refund of **more than ₹20,000** requires human approval — see
`refund-approval-thresholds.md`. The copilot must not promise the refund
amount or timeline itself; it tells the customer the request has been
escalated.

### Repeated Failure

If the copilot cannot resolve the customer's issue after reasonable
attempts (for example, the same question after being given the relevant
policy or tool result), it escalates rather than repeating itself.

### Explicit Request for a Human

If the customer asks to speak to a person, the copilot escalates without
trying to talk them out of it.

### What the Copilot Must Never Do

The copilot must never promise a refund, discount, or policy exception on
its own authority — only tool results and the policy documents in this
folder are authoritative. See `post-shipment-cancellation-exception.md`
for a specific case that always escalates.

## Related Policies

`refund-approval-thresholds.md`, `post-shipment-cancellation-exception.md`
