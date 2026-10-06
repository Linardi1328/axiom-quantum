# Axiom Quantum Phase 3 Slice 4 - Intelligence Memory

Status: Implementation in progress

Depends on: Phase 3 Slices 1-3

## Purpose

Slice 4 adds durable, append-only memory for the exact Phase 3 session, Market Intelligence
evidence, and decision-support assessment chain. It reuses `ResearchArtifactStore`, including its
safe paths, canonical JSON, atomic writes, checksum verification, and conflict refusal.

## Persistence Chain

The registry persists records beneath the originating experiment in dependency order:

1. the session, only when its exact Phase 2 validation decision already exists in Validation
   Memory;
2. the evidence, only when its exact parent session is already stored;
3. the assessment, only when its exact parent evidence is already stored.

Each load re-runs the public model-boundary validation from Slices 1-3, checks the requested
identity and parent experiment, and then re-loads and compares its upstream stored parent. A
missing parent, mutated record, identity mismatch, parent mismatch, or conflicting append fails
closed.

Writes are idempotent only for byte-identical canonical content. Existing content is never
replaced by the Phase 3 memory API.

## Safety Boundary

Intelligence Memory stores research and decision-support evidence only. It adds no execution,
paper/shadow/live, scheduling, broker, position sizing, promotion, or network authority.

## Acceptance Gates

Slice 4 is acceptable only when exact append/reload/list behavior, idempotency, required-parent
ordering, Phase 2 decision linkage, corruption rejection, and isolation are covered; focused and
repository-wide quality gates pass; Betterleaks passes; and CodeRabbit has no unresolved
actionable finding.

Passing Slice 4 does not approve Phase 3. Slice 5 and the final completion verification remain
required.
