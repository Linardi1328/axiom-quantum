# Axiom Quantum Phase 7 — Supervised Paper-Trading Validation

Status: implementation in progress; operational paper trial NOT passed.

Phase 6 is merged at main commit \`81c331c29e15ff20e6764b1ba4e2b6e7ede8779e\`.
Linear delivery: RIC-53 through RIC-57. Each slice branches from the verified previous squash merge.

## Purpose and non-negotiable boundaries

Validate reliability of the existing explicitly human-invoked paper execution workflow, without promoting a strategy, granting execution authority, or claiming real broker evidence from CI tests. Phase 7 **software completion** and **owner-run paper pilot completion** are separate verdicts.

* Phase 5 P5-A infrastructure is allowed. Phase 5 P5-C model-connected paper operation remains blocked: no approved paper model. Phase 6 single-use human authorizations remain mandatory for any paper broker submission.
* This phase may not add a broker adapter, direct order submission, scheduler, background scanning, unsolicited notifications, automatic resubmission, live trading, shorts, leverage, fractional shares, API/dashboard execution, or credential storage.
* All simulated/synthetic exercises remain \`synthetic\`; paper broker sessions require independently verifiable Phase 6 outcome/report lineage plus explicit operator attestation.
* No manual record, caller metadata, or synthetic test may unlock approved-model gates, override a kill switch, or self-certify a profitable strategy.
* No prescribed number of trades: abstain whenever the system has no eligible setup. An observation session with zero submitted orders may be valid if honestly recorded, but does not count as successful order execution.
* Never persist credentials, account identifiers, raw broker payloads, or private personal information.

## Five delivery slices

1. **RIC-53 — Policy and human validation session:** immutable content-addressed session, exact stored Phase 5 assessment admission, synthetic versus paper-broker mode, fixed conservative acceptance policy; no order authority.
2. **RIC-54 — Adversarial safety evidence:** deterministic scoped results for named failure probes (duplicate, kill switch, unauthorized submission, timeout/unknown, lookup-only reconciliation, record tampering); tests use fakes, never broker credentials.
3. **RIC-55 — Controlled pilot evidence:** explicit operator-recorded paper-only observation linked to persisted Phase 6 audit outcome and checksum-verified report, with verifiable session date and segregated evidence sources. No new submission route.
4. **RIC-56 — Validation memory and metrics:** append-only canonical artifacts, exact source and parent reload, duplicate-date protection and deterministic operational aggregates.
5. **RIC-57 — Assessment and reporting:** deterministic fail-closed go/no-go, readable checksum-verified report, full simulation tests, and merged-main implementation verification.

## Proposed operational acceptance — not automatically earned

* At least 20 distinct completed paper-broker observation sessions (initial reliability smoke window, not a claim of statistical edge).
* Zero unauthorized submissions and duplicate submissions.
* No unresolved broker-submission uncertainty at release.
* All critical safety probes pass, with no fabricated broker observations.
* Full audit lineage for every counted observation.
* Model-connected trading, supervised automation, and live trading remain explicitly blocked pending separate research/model approval.
* A real owner-run pilot, broker audit, and risk/performance assessment are required before authorizing any change in operational mode.

All exact-head PR gates: focused tests, Ruff, Ruff format, MyPy src/tests, full pytest coverage, whitespace check, Betterleaks, genuine CodeRabbit review, exact-head manual approval, squash merge and verified main.
