# Axiom Quantum Phase 3 Slice 3 - Fail-Closed Decision Support

Status: Implementation in progress

Depends on: Phase 3 Slice 2 canonical Market Intelligence evidence

## Purpose

Slice 3 turns one exact `MarketIntelligenceEvidence` snapshot into a deterministic, auditable
decision-support assessment. It decides only whether the evidence may be presented to a human for
review or Axiom must abstain. It never selects a trade or grants execution authority.

## Policy and Gates

The v1 `axiom-intelligence-decision-support-v1` policy is intentionally frozen. Every gate is
mandatory; weakening one requires a separately reviewed policy version.

The engine requires:

1. an admitted Phase 2 `validated_research_candidate`;
2. verified and analysis-eligible Market Intelligence data;
3. every Market Intelligence state dimension to be available;
4. at least one scenario, with every scenario calibrated and `high_evidence` actionable;
5. at least one degradation assessment, with every assessment `stable`.

Missing, unavailable, uncalibrated, abstaining, insufficient, warning, or degraded evidence fails
closed. Gate reasons are retained in the canonical assessment.

## Terminal Contract

The only terminal verdicts are:

- `present_for_human_review`; and
- `abstain`.

The assessment has no trade direction, quantity, order, position-size, leverage, capital,
scheduling, or broker fields. `execution_authority` is always `none`.

The assessment embeds the exact evidence and policy, records the policy digest, records every gate
result, and has a content-addressed `aq-intel-assessment-<24 lowercase hex characters>` identity.
Direct model validation recomputes policy digest, gates, verdict, and identity so callers cannot
turn failed evidence into a human-review result by recomputing only an identifier.

## Acceptance Gates

Slice 3 is acceptable only when human-review and every fail-closed gate path are covered; direct
tampering is rejected; policy weakening is rejected; the no-execution field/import boundary is
tested; focused and repository-wide quality gates pass; Betterleaks passes; and CodeRabbit has no
unresolved actionable finding.

Passing Slice 3 does not approve Phase 3. Slices 4-5 and final completion verification remain
required.
