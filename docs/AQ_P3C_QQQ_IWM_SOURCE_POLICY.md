# Axiom Quantum — QQQ/IWM Historical Context Source Policy (Draft)

**Scope:** RIC-154. A development-only acquisition adapter for **one QQQ or IWM symbol at a time**. This document does **not** approve data-provider access, rights, point-in-time research eligibility, model fitting, the protected test, or any trading execution.

## 1. Why this is separate from SPY Phase 1

The original Phase 1 `AcquisitionRequest`, `CanonicalDailyBar`, `DatasetManifest`, source checksum and dataset identity are frozen to SPY. **Do not widen or reinterpret them.** The new `ContextAcquisitionRequest` uses the existing Alpaca paginated stock-bars transport and the existing fail-closed, atomic `DatasetStore`, but creates a **different** schema and `aq-ctx-v1-...` dataset IDs.

An explicit caller may invoke `acquire_historical_context_data` after separately obtaining market-data credentials and confirming rights. **There is no context CLI, scheduler, background downloader, broker adapter, or automatic invocation.** All automated tests use synthetic data and fake clients.

## 2. Frozen initial source choices

| Setting | Proposed fixed value | Why |
|---|---|---|
| Symbols | QQQ or IWM, separate requests | First controlled market-context hypothesis |
| Endpoint | Alpaca historical stock bars, one symbol per call | Reuse existing paginated provider rather than invent transport |
| Provider | Alpaca | Same provider family as frozen SPY dataset |
| Stock feed | `sip` only | Avoid incomparable IEX-only coverage as main evidence |
| Timeframe | `1Day` | Match daily research horizon |
| Adjustment | `all` | Aligned with original SPY adjustment convention, **but not proof of historical adjustment as-of** |
| Session calendar | XNYS | Require exact nonmissing dates and complete daily candles |
| Ticker `asof` | Explicit `-` to Alpaca | Disable default symbol remapping; never infer entity continuity from ticker alone |
| Persist | Only ignored `data/{raw,canonical,manifests}/alpaca/{QQQ,IWM}/1Day/sip/all/` | Reject arbitrary root directories to avoid accidentally committing licensed bars |
| Rights/PIT status | **UNVERIFIED** | Actual legal use and historical availability are separate gates |

The provider must use **market-data-only** API credentials, not trading/paper credentials. It must never retrieve data during CI tests. Every request requires explicit terms acknowledgment before client construction. The adapter is not called by the application runtime or trading agent.

Primary source: Alpaca [Historical Bars (multi-symbol)](https://docs.alpaca.markets/us/reference/stockbars). Pagination is sorted by symbol then timestamp, which is why this path requests **only one symbol at a time** and validates the full paginated response. Alpaca [About Market Data API](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api) describes historical SIP access limits and plan entitlements. The owner must verify **current** account access and commercial/research licensing directly; documentation alone is not an account authorization.

## 3. What the acquisition implementation verifies

- Request allows only QQQ/IWM, historical dates, `1Day`, `alpaca`, `sip`, `all`, explicit no-remapping token `asof=-`, the ignored `data/` root only, and terms acknowledgment.
- Reuses bounded Alpaca retries/pagination and timestamps the credential-free raw response. Rejects mismatched request/provenance, foreign symbols, malformed OHLCV, nonpositive/nonfinite prices, invalid volume, duplicates, reordered bars, missing XNYS sessions, and incomplete latest session.
- Rejects bars whose timestamps do not identify the New York daily-interval start (including intraday bars on otherwise valid XNYS sessions).
- Produces stable context CSV fields `symbol,session,open,high,low,close,volume`; computes canonical, raw, and manifest SHA-256; creates a deterministic dataset ID and metadata manifest. The existing storage layer performs atomic, write-once checksum checks and refuses conflicting artifacts. `verify_context_artifacts` separately re-reads the saved manifest, raw snapshot and CSV, validates their checksums, reconstructed canonical bars, and dataset identity without a provider call.
- Every manifest records `point_in_time_status=UNVERIFIED`, `usage_rights_status=UNVERIFIED`, `execution_authority=none`. **Do not convert these fields to verified by assumption, code defaults, downloaded timestamps, or a caller-provided boolean.**

## 4. Blocking conditions for actual historical-feature eligibility

1. **Provider/license:** confirm SIP historical access and the exact permitted storage and research use; do not assume redistribution, derivative analytics or long retention rights. Never commit raw/provider bars.
2. **Market identity:** confirm the requested QQQ/IWM price/bar semantics, same trading-date calendar, corporate-action convention and source resolution needed by MI-2B feature formulas; independently verify saved checksums.
3. **Revision history:** Alpaca `adjustment=all` can represent adjustments relative to the retrieval time. An adjusted historical price downloaded today may encode later corporate actions. It is **not** by itself proof of values knowable on historical prediction date `t`. The owner must approve a reproducible as-of adjustment policy with actual evidence, or the experiment remains blocked.
4. **Feature observability:** independently establish timestamp `available_as_of` for each historical input feature/snapshot at each decision cutoff, after SPY daily close and before `t+1` open. No forward fill/backcast of contemporary context snapshots to earlier dates.
5. **Completeness:** verify the relevant MI-2B lookback warmups, nonmissing XNYS alignment, no protected outcomes, historical data range and exact frozen SPY parent checksum. A smaller common subset must not silently alter the frozen Phase 3B fold population.
6. **Four-source gate:** the current `assess_binary_context_preflight` still demands **four** context source proofs: QQQ, IWM, VIX and US-10Y, despite selecting only QQQ/IWM feature values. **Do not fabricate VIX/rate evidence or bypass this guard.** A separate reviewed and tested scientific-policy amendment must approve a truly QQQ/IWM-only provenance policy **before** any such candidate experiment.
7. **Protected evaluation:** 2021–2024 explored development and the previously opened Phase 2 test cannot become new independent confirmation. Reserve a genuinely unexposed post-selection period and authorize one-time evaluation separately.

## 5. Explicit hold points and operator sequence

1. Review/merge implementation and fake-client tests (Ruff, formatting, MyPy, coverage, Betterleaks, CodeRabbit).
2. Decide and document required source history, exact start/end dates and lookback needs, costs, entitlements, allowed use, and whether a four-source preflight amendment is scientifically justified.
3. Obtain **separate explicit owner permission for one or two local data downloads**; only then call the acquisition function using dedicated market-data credentials. The function has no default scheduling.
4. After download, use the new read-only `verify_context_artifacts` function to check saved raw/canonical/manifest checksums and identity, then separately establish historical point-in-time availability and provider rights. Successful checksums **alone** never constitute source authentication or research admission. Until this happens, artifacts are quarantine evidence only.
5. Freeze numeric candidate selection thresholds and model/fold identity before **separate** real-data training/evaluation authorization.
6. No automatic model admission, model-connected shadow operation, paper order or live execution. All trading state remains `execution_authority=none` and Phase 3B `NO CANDIDATE PROMOTION`.

**Status on 12 November 2026:** Only original SPY canonical files were found under the owner's inspected local data directories. No QQQ/IWM source data has been downloaded or certified under this issue.
