# Phase 3C-A — Dedicated QQQ/IWM research eligibility (RIC-152)

Scope: the owner's RIC-152 request authorizes an offline, synthetic-only policy,
bundle, historical alignment implementation and tests. This is a narrow research
amendment; it does not change the accepted V2 Phase 3 result, Phase 3B evidence,
Version 1 runtime, or any operational admission gate. Stop before implementing an
experiment runner or acquiring data. All outputs retain `execution_authority=none`.

## Versioned contracts

| Contract | Identifier |
| --- | --- |
| Eligibility policy | `aq-p3ca-qqq-iwm-synthetic-eligibility-v1` |
| Two-source feature bundle | `aq-p3ca-qqq-iwm-feature-bundle-v1` |
| Historical feature alignment | `aq-p3ca-qqq-iwm-feature-history-v1` |
| Synthetic aligned close schema | `aq-p3ca-synthetic-aligned-close-v1` |

`research/qqq_iwm_policy.py` declares separate frozen contracts. It imports the
existing MI-2B mathematical definitions for QQQ/IWM, without constructing an
MI-2A/MI-2B four-source bundle. Existing MI-2A, MI-2B, binary preflight, binary
alignment and MI-2C contracts remain unchanged and still require their original
four-source evidence. No VIX or US10Y input is fabricated.

`research/qqq_iwm_alignment.py:align_qqq_iwm_history` consumes only in-memory
synthetic contracts and an already-constructed synthetic binary supervised fixture.
It creates neither labels nor models. There is no CLI, loader, downloader, runner,
scheduler, artifact writer, broker, API route or dashboard integration.

## Frozen feature layout

The SPY portion explicitly uses
`feature_columns_for_families((*BASELINE_FAMILY_ORDER, DRAWDOWN_POSITION_FAMILY))`:
16 baseline-plus-drawdown-position columns, in registry order. The two unrelated
research feature families are excluded. The candidate has exactly 24 columns,
with these eight appended in order:

1. `qqq_return_5`
2. `qqq_return_20`
3. `qqq_relative_strength_5`
4. `qqq_relative_strength_20`
5. `iwm_return_5`
6. `iwm_return_20`
7. `iwm_relative_strength_5`
8. `iwm_relative_strength_20`

For lookback n, return is close[t] / close[t-n] - 1. Relative strength subtracts
SPY's corresponding return. At least 21 aligned, contiguous XNYS closes are
required. Feature values are recomputed and checked against the supplied bundle;
SPY's 5/20-session returns must also match its supervised feature row.

## Evidence and chronology boundary

- Synthetic close snapshots bind series, schema, consistent synthetic adjustment
  policy, exact session sequence and close values into a deterministic SHA-256 and
  snapshot identity. Proofs must match those identities and schemas exactly,
  including the SPY target. All source values must be positive and finite.
- Each bundle requires exactly QQQ and IWM in canonical order, plus one proof for
  SPY and one for each context source. Missing, duplicate, unknown, reordered or
  mismatched identities fail closed.
- Historical availability, quality and permitted-use references must use the
  explicit checksum-bound **synthetic fixture evidence protocol**. These references
  have no meaning as real-data certification. Booleans, `VERIFIED`, `UNVERIFIED`
  and download timestamps cannot substitute for them.
- Every availability instant is timezone-aware, after the anchor's actual market
  close and no later than its cutoff. The cutoff must be strictly after actual
  XNYS close and strictly before the next session's open, including early closes,
  weekends and holidays. Input histories end exactly at the prediction session.
- Frame columns, canonical RangeIndexes, metadata, prediction sessions, context
  coverage and source calendars are checked again because DataFrames are mutable.
  Labels require actual t+1 entry and t+6 exit, finite returns and a binary target
  matching the sign of the net return. Source, prediction and exit dates must all
  precede the supplied synthetic protected boundary.
- Alignment is atomic: any invalid input yields no sessions and no feature rows.
  Fixed rejection text does not echo input values or provider payloads.

Success is explicitly `eligible_for_synthetic_development`, never real-data
eligibility or model admission. Inputs marked as real evidence are rejected. Like
any fixture API, this API cannot detect a caller falsely labelling real observations
as synthetic; such use is outside its contract and authorization. It provides no
independent source authentication or legal verification.

## Frozen population and remaining hold points

The future SPY development population remains source 2018-01-02–2024-10-15
(1,708 observations), prediction sessions 2018-03-29–2024-10-07 (1,642 rows).
Synthetic test calendars may use smaller independent periods; they do not redefine
that population. No real dataset, private artifact, credential or protected outcome
is needed or loaded by this implementation.

Before **any eventual separately authorized real runner** builds labels, it must
verify the accepted Phase 1 parent identity and derive/apply
`apply_phase2_final_test_session_isolation` from the existing Phase 2 exclusion
contract. It must then verify the frozen source/prediction population and retain
the exclusion boundary and lineage. A caller-supplied date in this synthetic
adapter is not a replacement for that procedure.

Real-data eligibility remains blocked pending a separately reviewed evidence
verification path, Alpaca licensing/permitted-use confirmation, historical
availability and as-of adjustment evidence, independently verified source bytes,
and exact source/target schema and snapshot lineage. A current adjusted download
or checksum alone proves neither historical availability nor rights. Acquisition
manifests marked `UNVERIFIED` cannot pass this contract as real evidence.

No real-data training, protected evaluation, automatic promotion, model-connected
shadow/paper operation or live trading is authorized. Phase 3B remains
`NO CANDIDATE PROMOTION`. PR #98 is outside this change.
