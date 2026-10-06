# Axiom Quantum Phase 4 — Supervised Operations Specification

Status: Slice 1 governing contract. Phase 4 is not complete until the Phase 4 completion gates pass on merged `main`.

## Purpose

Phase 4 adds human-invoked supervision records on top of the completed Phase 3 Intelligence OS. It does not add trading, broker, order, position, sizing, or execution authority. Every Phase 4 public contract must retain `execution_authority = "none"`.

## Governing lineage

One supervised session binds exactly one stored Phase 3 Intelligence OS report and its exact assessment, evidence, Intelligence Session, and Phase 2 validation lineage. Admission must verify the stored Phase 3 report through the canonical Phase 3 loader before a Phase 4 session can exist. Missing, substituted, corrupted, or mismatched lineage fails closed.

The Phase 3 terminal verdict is immutable in Phase 4:

- `present_for_human_review` remains eligible only for human review;
- `abstain` remains an abstention and cannot be upgraded, cleared, or reinterpreted.

## Slice 1 — Supervised Session Contract (RIC-36)

A supervised session:

- is created only by an explicit `human_requested` invocation;
- has a deterministic content-addressed `aq-supervision-session-*` identity;
- records the exact Phase 3 report, assessment, evidence, Intelligence Session, experiment, and terminal verdict lineage;
- is immutable after construction;
- grants no downstream authority.

## Planned dependent slices

- RIC-37: derive an immutable supervised review item while preserving the Phase 3 verdict.
- RIC-38: record an immutable human-only observational disposition.
- RIC-39: add append-only supervision memory and exact parent-chain verification.
- RIC-40: add deterministic supervision reporting, explicit human-invoked orchestration, end-to-end coverage, and the Phase 4 completion contract.

Each slice depends on the preceding approved slice being squash-merged to `main`.

## Non-negotiable authority boundary

Phase 4 must not create or imply:

- trade direction, quantity, sizing, leverage, or position targets;
- paper or live order submission;
- broker communication;
- execution or trade approval semantics;
- schedulers, daemons, recurrence, autonomous loops, background session creation, or unsolicited alerts/signals;
- model promotion or protected-evaluation reopening.

Human review and human disposition are observational decision-support actions only. They do not authorize execution.

## Slice acceptance discipline

Before any Phase 4 PR is squash-merged, every pushed commit and the final PR head must be explicitly reviewed and approved after the required gates pass. Required gates are focused tests, Ruff check, Ruff format check, Mypy over `src` and `tests`, the full pytest coverage gate, whitespace checks, Betterleaks, and a genuine CodeRabbit ready-for-review pass with no unresolved actionable finding.

Phase 4 may be confirmed complete only after RIC-36 through RIC-40 are integrated into `main` and the final merged-main verification in `AQ_PHASE_04_COMPLETION_SPEC.md` passes.
