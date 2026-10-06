# Axiom Quantum Phase 3 Slice 2 - Canonical Market Intelligence Bridge

Status: Implementation in progress

Depends on: `RIC-30` / Phase 3 Slice 1

## Purpose

Slice 2 binds one admitted `IntelligenceSession` to one exact existing
`SPYMarketIntelligenceBrief`. The result is a deterministic, content-addressed evidence snapshot
for later fail-closed decision support. The bridge reuses the Market Intelligence stack; it does
not duplicate forecasting, state derivation, calibration, analogue, relationship, or degradation
logic.

## Contract

`MarketIntelligenceEvidence` embeds the exact Phase 3 session and the exact deterministic Market
Intelligence brief. It records a SHA-256 digest of the complete brief and a content-addressed
`aq-intel-evidence-<24 lowercase hex characters>` identity.

At the public model boundary the bridge requires:

- the embedded session to satisfy all Slice 1 admission and lineage checks;
- the brief run identity to equal the session Market Intelligence run identity exactly;
- every cross-asset relationship to use the session `as_of` cutoff;
- every relationship snapshot reference to belong to the session snapshot set;
- the brief digest to match the embedded brief content;
- the evidence identity to match its canonical session/brief content;
- `execution_authority` to remain `none`.

The complete embedded brief retains data quality, market state, scenarios and actionability,
historical analogues, cross-asset relationships, degradation evidence, limitations, and their
existing lineage without translating them into order or risk semantics.

## Acceptance Gates

Slice 2 is acceptable only when:

- equivalent session/brief input produces identical evidence and identity;
- any changed brief content changes the evidence identity;
- mismatched brief/session run lineage fails closed;
- relationship time or snapshot lineage outside the session fails closed;
- tampered brief checksums and evidence identities fail closed at direct `model_validate`;
- the public intelligence package exports the bridge contract;
- no execution, paper-operation, broker, scheduling, or network dependency is introduced;
- focused tests, Ruff, Mypy, full pytest coverage, whitespace checks, Betterleaks, and an
  independent CodeRabbit review all pass.

Passing Slice 2 does not approve Phase 3. Slices 3-5 and the final Phase 3 completion gates remain
required.
