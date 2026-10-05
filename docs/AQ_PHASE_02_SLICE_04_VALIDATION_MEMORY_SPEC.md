# Axiom Quantum Phase 2 Slice 4 - Validation Memory and Strategy Graveyard

Status: Implementation candidate

Linear: `RIC-28`

## Purpose

Slice 4 makes Phase 2 decisions durable research evidence. It stores every canonical validation
verdict append-only and creates a Strategy Graveyard entry only when the verdict is `rejected`.
Neither validation memory nor the graveyard grants execution authority.

## Decision Memory

A validation decision receives the content identity:

`aq-decision-<24 lowercase hex characters>`

The identity includes the complete canonical `ValidationDecision`, including validation case ID,
experiment/result IDs, policy ID and policy digest, verdict, ordered gate outcomes, and the
`execution_authority = none` boundary.

Decision records are written append-only beneath the parent experiment's existing research artifact
directory. Repeated writes of identical content are idempotent. Same-name conflicting content is
rejected by the artifact store. Every successful write is immediately re-loaded and compared with
the canonical record.

## Strategy Graveyard

Only a decision whose canonical verdict is `rejected` may create a graveyard entry. A graveyard
entry receives the deterministic identity:

`aq-graveyard-<24 lowercase hex characters>`

Each entry retains:

- the validation decision identity;
- validation, experiment, and result identities;
- policy ID and exact policy digest;
- every failed validation stage;
- the sorted gate check identifiers and failure reasons for each failed stage;
- `execution_authority = none`.

Validated candidates and insufficient-evidence cases remain queryable in validation memory but are
not graveyarded. Slice 4 does not implement deletion, revival, retraining, or promotion automation.

## Integrity Rules

The registry fails closed when:

- a stored decision or graveyard payload fails canonical validation;
- a requested identity does not match the stored content identity;
- a record is stored beneath the wrong parent experiment;
- a graveyard entry does not link to an already-persisted rejected decision;
- linked validation/result/policy fields differ between graveyard and decision memory;
- a non-rejected decision is submitted to the Strategy Graveyard.

## Acceptance Gates

Slice 4 is complete only when:

- validation decision identities are deterministic;
- rejected verdicts create deterministic graveyard entries;
- validated and insufficient-evidence decisions are stored but never graveyarded;
- repeated identical writes are idempotent;
- corrupt/conflicting records fail closed;
- list/load APIs preserve internal decision/graveyard references;
- all records retain `execution_authority = none`;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit performs a genuine ready-for-review pass with no unresolved actionable findings.
