# RIC-154 / PR #98 — Remediation closure evidence

Baseline: `ccc14d6526852681501189f8cd29a50f7a89c466`, main
`793f005f79bbe10434ec0cc21a5b5b3f539296c6`. References: owner-supplied complete
independent technical review dated 12 November 2026 and authoritative RIC-154 issue.
Review text is evidence, not permission for provider access, merge or acquisition.

Status below means implementation and regression coverage added, **not merge
approval**. Exact-head validation numbers and independent-review disposition are
recorded in the PR delivery report after the final commit. A fresh independent
technical review remains mandatory. This cycle may use GPT review if CodeRabbit
is unavailable, as explicitly authorized by the owner.

## Finding closure matrix

| Finding | Correction | Principal regression evidence in `tests/unit/test_context_acquisition_security.py` |
| --- | --- | --- |
| H-01 | Context-only descriptor-relative storage, no-follow ancestry/finals, ignored directory checks, pre-staging and post-provider checks, ownership/mode constraints | `test_h01_symlink_ancestry_denies_provider`, `test_h01_dangling_final_artifacts_are_denied_before_fetch`, `test_h01_symlink_swap_after_preflight_is_not_followed`, `test_h01_ancestor_relocation_before_stage_does_not_write_payload`, `test_h01_specific_filename_ignore_is_not_an_approved_directory` |
| H-02 | Reconstruct request fields with strict defaults/extras/acknowledgment, injected-clock range/session checks, physical preflight before provider/client | `test_h02_revalidates_forged_requests_before_client`, `test_h02_incomplete_early_close_denies_client`, `test_h02_direct_context_transport_requires_acquisition_preflight` |
| H-03 | Strict allowlist, credential-value exclusion, fixed classified errors raised outside raw exception handlers | `test_h03_secret_payloads_are_never_serialized`, `test_h03_public_tracebacks_have_no_raw_secret_or_cause`, `test_h03_sdk_errors_keep_classification_not_sensitive_tracebacks`, `test_h03_client_factory_exception_is_sanitized`, `test_h03_retry_classification_and_bounds` |
| H-04 | Exclusive lock creation/global context flock, no-clobber link publication, manifest-last, inode-owned rollback, no automatic partial-set recovery | `test_h04_synchronized_process_writers_cannot_clobber`, `test_h04_each_write_failure_rolls_back_only_own_files`, `test_h04_rollback_cannot_remove_unowned_replacement`, `test_h04_interrupted_set_requires_manual_recovery`, `test_h04_stale_staging_is_not_automatically_deleted` |
| M-01 | v2 code/runtime lineage bound into identity/self-checksum; verifier requires matching recorded implementation/environment | `test_m01_lineage_is_present_and_identity_bound`, `test_m01_m05_malicious_recomputed_manifests`; existing manifest self-checksum regression |
| M-02 | Page/row/byte/elapsed/retry limits; full pagination-chain parity; remaining deadline caps each real SDK request | `test_m02_actual_adapter_multi_page_round_trip`, `test_m02_global_budgets`, `test_m02_m05_recomputed_raw_metadata_is_rejected`, `test_m02_context_budget_clamps_each_sdk_request_timeout`, `test_m02_forged_response_count_type_is_rejected` |
| M-03 | Exact bounded decimals; original OHLC consistency before serialization; integer volume limits | `test_m03_decimal_context_cannot_round_prices_or_hide_ohlc`, `test_m03_invalid_prices_are_bounded`, `test_m03_invalid_volumes` |
| M-04 | Separate request start and completion, aware UTC checks at every boundary, ordered raw retrieval stamp | `test_m04_distinct_start_and_completion_timestamps`, `test_m04_naive_clock_at_any_boundary_is_denied`, `test_m05_acquisition_rejects_source_identity_and_time_mismatch` |
| M-05 | Strict namespace/schema, exact metadata/pagination/request/paths, re-canonicalization, checksums and supported runtime | `test_m01_m05_malicious_recomputed_manifests`, `test_m02_m05_recomputed_raw_metadata_is_rejected`, `test_m05_recomputed_wire_and_summary_tampering` |
| L-01 | Updated source-policy taxonomy, v2 incompatibility and explicit real-data hold points | `docs/AQ_P3C_QQQ_IWM_SOURCE_POLICY.md`; frozen Phase 3C-A/B and four-source regression suite |
| L-02 | Actual hostile existing/recomputed artifacts and zero-client entry-boundary assertions rather than constructor-only tests | All security regressions, especially wrong-ID/swapped metadata, exact SPY params and fresh-process boundary |

## Independent review's complete 20-item checklist

| # | Required case | Regression mapping |
| --- | --- | --- |
| 1 | copy/construct request bypass, zero constructions | H-02 forged-request parametrization |
| 2 | unsafe/symlinked root, zero provider calls | H-01 ancestry and ignored-layout tests |
| 3 | parent and dangling final symlinks | H-01 ancestry, final-artifact and relocation tests |
| 4 | nested metadata and credential-value leakage | H-03 strict payload cases |
| 5 | SDK secrets and rendered exception chains | H-03 SDK, provider and factory traceback tests |
| 6 | synchronized writers and owned rollback | H-04 process and foreign-replacement cases |
| 7 | multipage QQQ and IWM, actual adapter/fake client | M-02 multipage round trip for both symbols |
| 8 | repeated and unlimited distinct tokens | M-02 global budgets |
| 9 | pagination metadata mismatch/terminal token | M-02/M-05 recomputed metadata cases |
| 10 | DST, holiday, early close, incomplete session | calendar parametrization and H-02 early-close denial |
| 11 | calendar out of range and empty interval before access | H-02 forged-request parametrization |
| 12 | nonfinite/extreme prices, fractional/boolean volume | M-03 price and volume parametrizations |
| 13 | request/provider/retrieval mismatch | M-05 acquisition mismatch and verifier metadata cases |
| 14 | lineage present, checksum/identity bound | M-01 lineage identity test and existing self-checksum test |
| 15 | existing wrong ID with recomputed checksums | M-01/M-05 malicious manifest wrong-ID case |
| 16 | swapped paths and altered source metadata | M-01/M-05 swapped case; M-02/M-05 metadata parametrization |
| 17 | dataset-ID traversal before construction | M-01/M-05 traversal case; strict filename validation |
| 18 | failures at each write stage and process death | H-04 each-stage rollback and interrupted-set cases |
| 19 | exact SPY params with absent/explicit asof | `test_l02_exact_spy_outbound_parameters` for SIP/all and IEX/raw |
| 20 | fresh-process import/research cannot acquire | `test_l02_fresh_process_cannot_acquire_on_import_or_synthetic_research` |

## Scope and compatibility

CodeRabbit's follow-up review is also addressed: preflight rejects a tracked
coordination lock before client construction
(`test_h02_tracked_coordination_lock_denies_client`); owned rollback syncs each
parent after unlink (`test_h04_rollback_syncs_each_removed_publication`); and
context clients disable alpaca-py 0.43.5's internal retry count/delay so only the
outer context budget applies (`test_m02_real_sdk_has_only_context_retry_budget`,
using the real SDK with intercepted synthetic HTTP 429/504 responses, zero
network calls, and zero/three outer retries). Other SDK instances retain their
default retry policy.

The extra context modules split contracts/numerical/wire policy, POSIX storage and
bounded transport so SPY storage, schemas and dataset identities stay frozen.
Only `AlpacaMarketDataProvider`'s context dispatch/configuration is extended; its
SPY transport, parameter construction, retries and exceptions retain existing
behavior and are regression-tested. New fake fixtures supply accurate supported
SDK metadata and real ignored temporary Git repositories; existing negative
assertions are retained. No test is removed or weakened to obtain a passing build.

Raw/manifest v2 and `aq-ctx-v2-` IDs are incompatible with v1. No existing pilot,
private artifact, market-data credential or protected outcome is read, migrated
or certified. Verification establishes internal integrity, not source authenticity.
See source policy for the trusted local POSIX filesystem boundary and socket-timeout
limitations; this is not a same-UID hostile-process sandbox or a hard process killer.

All acquisition/verification tests use synthetic fixtures and injected fake clients.
The required fresh-process research-boundary regression and normal offline suite
exercise only the existing internally generated synthetic research machinery.
The accepted Phase 3C-A/B synthetic contracts, Phase 3B evidence, Phase 2 boundaries,
SPY schemas and paper/live controls are unchanged. No actual provider call, real
acquisition, real-data model fitting, protected evaluation, model promotion or trade is
performed. PIT/rights remain `UNVERIFIED`, execution authority `none`, disposition
`NO CANDIDATE PROMOTION`. Do not merge without fresh independent review and owner action.
