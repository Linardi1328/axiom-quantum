# Version 2 Phase 5 Paper Recovery Runbook

Status: Operator runbook for non-submitting Phase 5 recovery scaffolding.

This runbook is conceptual and offline for the first Phase 5 PR. It does not require
credentials, does not call Alpaca, and does not authorize paper-order submission.

## Recognize Reserved Or Incomplete Attempts

A `reserved` attempt means local durable state consumed a `client_order_id` and execution
session, but no terminal broker receipt is recorded. Treat it as incomplete. Do not create a
replacement order.

Inspect local evidence:

- `client_order_id`;
- signal ID;
- approval ID;
- instruction fingerprint;
- attempt status;
- event history;
- timestamps;
- failure code, if present.

## Recognize Submission Unknown

`submission_unknown` means the order request may have reached the broker or local receipt
persistence may have failed after acceptance. It is not a rejection and it is not permission
to retry.

Common causes include timeout, cancellation during broker request, transport exception,
broker snapshot mismatch, accepted receipt persistence failure, and local state failure
after broker interaction.

## Why Automatic Resubmission Is Forbidden

Uncertainty never means retry the order. A second submission could duplicate exposure. The
only safe primary key for investigation is the deterministic `client_order_id`.

## Broker Lookup Procedure Concept

When a later approved owner recovery drill permits broker access, query the paper broker by
the exact `client_order_id`. Do not query by vague time windows or recreate a replacement
order. Do not submit while lookup is pending.

## Local Evidence To Inspect

Before any broker lookup is authorized, collect local evidence:

- the paper-execution attempt row;
- paper-execution event rows for the same `client_order_id`;
- current kill-switch state;
- sanitized command output;
- package/runtime version;
- Git commit;
- operator notes.

Do not copy credentials, account IDs, raw broker payloads, private paths, or runtime
SQLite databases into tracked files.

## If Broker Lookup Finds An Order

Compare the broker evidence against the local instruction:

- same `client_order_id`;
- same symbol `SPY`;
- same side;
- same whole-share quantity;
- market order;
- day time-in-force;
- extended hours disabled;
- paper environment only.

If all values match, future authorized reconciliation may record a recovered terminal state
such as `reconciled`. Do not resubmit.

## If Broker Lookup Finds No Order

No matching broker order is still an investigation result, not automatic permission to
resubmit. Preserve the lookup timestamp and evidence. Manual owner review decides whether a
new, separately approved future instruction is allowed.

## If Broker Lookup Fails

Lookup failure leaves the attempt unresolved. Keep the disposition as
`RECONCILIATION_REQUIRED`, preserve evidence, and stop. Do not infer that no order exists.

## If Local Persistence Is Unavailable

If the SQLite paper ledger cannot be opened, is locked, or appears corrupted, stop paper
operation. Preserve the database file, logs, package version, Git commit, and sanitized
operator notes for manual investigation.

## When To Engage The Kill Switch

Engage or leave engaged the durable kill switch whenever:

- an attempt is `reserved` or `submission_unknown`;
- broker lookup is unavailable or ambiguous;
- local state is unavailable;
- an order snapshot mismatches local instruction;
- credentials or account state are uncertain;
- any live endpoint is suspected.

## Preserve Evidence

Preserve sanitized evidence sufficient for owner review:

- `client_order_id`;
- local attempt status;
- local event sequence;
- gate states;
- reconciliation disposition;
- broker lookup classification if separately authorized;
- timestamps.

Do not commit secrets, account identifiers, raw broker payloads, real provider data, or
runtime SQLite files.

## Stop And Require Manual Investigation

Stop when state is unknown, mismatched, unavailable, or not covered by the recovery matrix.
Unknown/unrecognized local attempt states fail closed. Manual investigation is required
before any future paper activity.
