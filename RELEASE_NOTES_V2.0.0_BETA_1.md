# Version 2.0.0 Beta 1 Release Notes

Release identity:

- Package version: `2.0.0b1`
- Git release identifier: `v2.0.0-beta.1`
- Release commit: `1c8feae478c0f5536b2193eeb408e580f3f7e33c`

Release policy satisfied: the `v2.0.0-beta.1` tag points to the verified `main` release
commit after review approval, merge, post-merge verification, and separate owner approval.

## Purpose

Version 2 Phase 4 introduces Real-Time Shadow Mode infrastructure in its currently approved
form: SPY-only, daily-bars-only, XNYS-calendar-aligned, observation-only,
operator-triggered operation over verified local Phase 1 data with durable shadow audit
state and deterministic schedule eligibility.

This release is not tick-level, intraday, unattended, model-connected, broker-connected,
paper trading, or live trading. It does not claim profitability, predictive edge,
production readiness, paper-trading readiness, live readiness, or investment suitability.

## Major Capabilities

- Verified local Phase 1 manifest consumption.
- Checksum and lineage verification before loading canonical bars.
- SPY, `1Day`, XNYS, and adjustment-policy validation.
- Freshness and completeness gates that fail closed.
- Explicit provider-finalization evidence.
- Deterministic shadow run identity.
- Dedicated Phase 4 shadow SQLite persistence.
- Durable health events and local alerts.
- Manual `run-observation`.
- Read-only `show-run`.
- Read-only `schedule-preview`.
- Operator-triggered `run-due-observation`.
- Automatic latest-completed XNYS target resolution from explicit UTC `as_of`.
- Already-processed no-op behavior.
- Recovery-required handling for incomplete prior reservations.
- Missed-observation detection without backfill.
- Mechanically locked Gate B model-admission behavior.

## Phase 3 Handoff

Phase 3 produced:

```text
NO CANDIDATE PROMOTION
```

Therefore:

```text
Beta 1 contains no approved model-connected shadow inference.
```

No Phase 3 model is authorized for protected evaluation, shadow operation, production paper
operation, or live trading.

## Owner Acceptance Summary

The owner accepted Phase 4 using sanitized real SPY observation evidence from accepted Phase
1 dataset `spy-v2p1-825930b0a2bcab20c733b867`.

The sanitized smoke test completed for session 2025-12-31 with:

- run status: `completed`
- mode: `observation_only_no_model`
- freshness: `fresh`
- monitoring: `healthy`
- model gate: `blocked_no_approved_model`
- alerts: `none`

The owner also confirmed manifest verification, schedule preview, due observation,
read-only inspection, persisted shadow SQLite state, idempotent already-processed rerun,
provider-not-finalized fail-closed behavior, stale-data fail-closed behavior, Gate B locked,
and no broker/order behavior.

## Explicitly Not Included

- No model loading.
- No model inference.
- No predicted probabilities or scores.
- No model-generated `LONG` or `CASH` signals.
- No operational `ShadowProposal` generation.
- No broker communication.
- No paper execution.
- No live trading.
- No unattended scheduler, daemon, worker, cron job, polling loop, or background retry loop.
- No market-data acquisition or provider polling.
- No protected evaluation.
- No Phase 5 production paper operation.

## Verification

Normal automated verification remains offline, synthetic, credential-free, broker-free, and
network independent. Final Beta 1 release-preparation gate results are recorded in the pull
request and Codex completion report for this branch.

## Data and Security Boundaries

Generated provider data, raw provider responses, canonical real SPY files, manifests from
owner local data, SQLite shadow databases, credentials, account identifiers, private paths,
screenshots, and machine-specific metadata are not committed.
