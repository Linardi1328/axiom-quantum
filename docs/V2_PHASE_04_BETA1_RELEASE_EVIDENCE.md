# Version 2 Phase 4 Beta 1 Release Evidence

Status: Accepted release evidence for `v2.0.0-beta.1`.

Public release identifier: `v2.0.0-beta.1` has been tagged and released.

Release commit: `1c8feae478c0f5536b2193eeb408e580f3f7e33c`.

Released package/runtime version: `2.0.0b1`.

Release-preparation branch:
`review/v2-phase-04-beta1-release-preparation`.

Base main: `6fb92c0a6f630d7faa3626cef515d0bb6c742951`.

## 1. Phase 4 Scope

Version 2 Phase 4 establishes the approved Real-Time Shadow Mode infrastructure in its
current observation-only form. In this release, "real-time" means daily SPY operation
aligned to the XNYS calendar after a daily session has completed and provider finalization
has been explicitly confirmed. It does not mean tick-level, intraday, unattended,
model-connected, broker-connected, paper-trading, or live-trading operation.

The release-preparation branch recorded sanitized acceptance evidence and prepared package
metadata for the public Beta 1 tag. It did not run a new real-data campaign and did not add
new operational functionality.

## 2. Governing Specification

The governing document is
[`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`](V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md).
Phase 4 Gate A infrastructure entry is authorized. Gate B model-connected shadow inference
remains locked because Phase 3 produced `NO CANDIDATE PROMOTION` and no approved shadow
model exists.

## 3. Repository Implementation Evidence

- PR #27 merged the approved Phase 4 shadow-mode specification and infrastructure-first
  scaffold.
- PR #28 merged Observation Pipeline V1, including manual observation-only operation,
  dedicated shadow SQLite audit persistence, health events, local alerts, and read-only
  inspection.
- PR #29 merged Scheduled Observation Operations V1, including deterministic latest
  completed XNYS session resolution, read-only `schedule-preview`, operator-triggered
  `run-due-observation`, already-processed no-ops, recovery-required handling, and missed
  observation detection without backfill.

## 4. Automated Evidence

The following release-preparation gate results were produced on this branch:

| Command | Result |
| --- | --- |
| `python -m pip install -e ".[dev]"` | Passed; installed `spy-market-agent 2.0.0b1`. |
| `pytest --cov-fail-under=85` | Passed; `1136 passed`; total coverage `85.19%`. |
| `pytest tests/unit -q` | Passed with exit code 0. Unit collection count for this branch: `1072` tests. |
| `pytest tests/integration -q` | Passed with exit code 0. Integration collection count for this branch: `64` tests. |
| `pytest -W error::FutureWarning` | Passed; `1136 passed`. |
| `ruff check .` | Passed; all checks passed. |
| `ruff format --check .` | Passed; `217 files already formatted`. |
| `mypy src tests` | Passed; no issues found in `178` source files. |

Focused Phase 4 verification also passed on this branch for:

```text
pytest tests/unit/test_v2_phase4_shadow.py \
  tests/unit/test_v2_phase4_observation_pipeline.py \
  tests/unit/test_v2_phase4_scheduled_observation.py \
  tests/integration/test_v2_phase4_observation_pipeline.py \
  tests/integration/test_v2_phase4_scheduled_observation.py -q
```

The focused Phase 4 command exited 0. Collection for the same focused file set contains
`85` tests covering Phase 4 shadow contracts, Observation Pipeline V1, Scheduled Observation
Operations V1, persistence, model admission, scheduling, and CLI behavior.

Static safety review of `src/spy_market_agent/shadow` found no prohibited runtime imports or
references for broker/execution modules, Alpaca trading clients, credential environment
names, scheduler libraries, or scheduler loop constructs. `ShadowProposal` remains a
non-executable contract type only, and the observation runner records
`proposal_generated=False`.

## 5. Owner Acceptance Evidence

The owner completed and verified the Phase 4 acceptance procedures with the accepted Phase 1
parent dataset:

- dataset ID: `spy-v2p1-825930b0a2bcab20c733b867`
- canonical dataset checksum:
  `d1c62194a3e13a164bbe09edad8cb6b4aa8bbd17a621d34da17e3e8edc96a259`

Sanitized owner smoke-test run:

- shadow run ID: `spy-v2p4-shadow-09c5104d1614fe7902a3fadf`
- mode: `observation_only_no_model`
- session: 2025-12-31
- run status: `completed`
- freshness status: `fresh`
- monitoring status: `healthy`
- model gate status: `blocked_no_approved_model`
- provider finalization policy ID: `owner-smoke-finalized-v1`
- health events: `fresh_data`, `model_not_approved`
- alerts: `none`

The owner also confirmed successful validation of complete quality gates, Phase 1 manifest
verification, `schedule-preview`, `run-due-observation`, `show-run`, persisted shadow SQLite
state, already-processed/idempotent rerun behavior, provider-not-finalized fail-closed
behavior, stale-data fail-closed behavior, Gate B remaining locked, and absence of broker or
order behavior.

## 6. Functional Acceptance

Phase 4 Beta 1 acceptance records that:

- local Phase 1 data is deeply verified before shadow use;
- target sessions derive from XNYS calendar semantics;
- freshness fails closed;
- provider finalization evidence is explicit;
- `schedule-preview` is read-only;
- eligible scheduled operation delegates to the existing observation runner;
- scheduled reruns become safe no-ops;
- shadow persistence remains isolated in the dedicated Phase 4 SQLite schema;
- monitoring and health evidence are durable.

## 7. Safety Acceptance

The accepted Beta 1 safety state is:

```text
Gate B = BLOCKED
approved shadow model = none
model inference = unavailable
ShadowProposal operational generation = unavailable
broker communication = unavailable
paper execution = unauthorized
unattended scheduling = unauthorized
live trading = prohibited
```

No predictive edge, profitability, production readiness, paper-trading readiness, or live
readiness is claimed.

## 8. Sanitization Boundary

This tracked evidence excludes credentials, secret values, local usernames, hostnames,
private absolute paths, Alpaca account identifiers, raw provider responses, real-data files,
SQLite database files, and personal machine metadata.
