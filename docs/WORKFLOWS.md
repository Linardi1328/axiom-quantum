# Workflows

These workflows are safe local operations. They do not submit broker orders.

## Run Verification

```bash
python -m pip install -e ".[dev]"
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
git diff --check
git status --short
```

## Initialize SQLite Explicitly

```bash
python -c "from spy_market_agent.persistence import initialize_database; initialize_database('./spy_market_agent.sqlite3')"
test -f spy_market_agent.sqlite3 && echo "Database initialized"
```

This creates or migrates only the named local SQLite file. It does not download data, run a
backtest, create a broker client, or submit an order.

## Start the Read-Only FastAPI Application

```bash
SQLITE_DATABASE_PATH=./spy_market_agent.sqlite3 \
python -m uvicorn "spy_market_agent.api.main:create_app" \
  --factory \
  --host 127.0.0.1 \
  --port 8000
```

Keep this terminal open. The app reads from `./spy_market_agent.sqlite3`. Empty or
unavailable databases return read-safe empty responses or sanitized service errors.

Verify before starting Streamlit:

```bash
curl http://127.0.0.1:8000/health
```

## Start the Streamlit Dashboard

```bash
DASHBOARD_API_BASE_URL=http://127.0.0.1:8000 \
streamlit run src/spy_market_agent/dashboard/streamlit_app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
```

The dashboard reads from the FastAPI API configured by `DASHBOARD_API_BASE_URL`, which
points to `http://127.0.0.1:8000` in this workflow. Use a separate terminal from FastAPI.
Headless mode avoids Streamlit's first-run email prompt. Open
`http://127.0.0.1:8501` after the Streamlit endpoint responds.

## Load Deterministic Test or Demo Data

Version 1 has no committed real SPY dataset. Version 2 Phase 1 adds an accepted explicit
historical SPY acquisition path and stores downloaded provider data only in ignored local
directories. The existing supported deterministic data paths are automated tests that create
synthetic artifacts in temporary directories:

```bash
pytest tests/unit/test_v2_phase1_market_data.py -q
pytest tests/integration/test_v2_phase1_acquisition_flow.py -q
pytest tests/integration/test_phase7_persistence_api_dashboard_flow.py -q
pytest tests/integration/test_phase8_paper_execution_flow.py -q
```

For a manual demo database, use the public repository APIs from your own local script with
validated `MarketDataBatch`, `FinalTestEvaluation`, and `BacktestResult` objects. Do not commit
the generated SQLite file or any external market data.

## Acquire Historical SPY Daily Data Explicitly

This command contacts the Alpaca Market Data API only when invoked directly. It does not
initialize SQLite, train a model, run a backtest, contact the trading API, or submit an
order.

```bash
ALPACA_MARKET_DATA_API_KEY=... \
ALPACA_MARKET_DATA_SECRET_KEY=... \
python -m spy_market_agent.market_data.cli acquire \
  --provider alpaca \
  --symbol SPY \
  --start 2016-01-04 \
  --end 2016-01-08 \
  --timeframe 1Day \
  --feed sip \
  --adjustment all \
  --data-root ./data \
  --acknowledge-provider-terms
```

The dates above are a small example only. Alpaca account access, feed selection, subscription
level, and provider history determine what data is actually available. Do not commit
downloaded provider data. `MARKET_DATA_TIMEOUT_SECONDS` bounds each provider HTTP request;
`MARKET_DATA_MAX_RETRIES` controls retry count separately.

## Verify a Local Phase 1 Dataset

```bash
python -m spy_market_agent.market_data.cli verify \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data
```

This performs deep offline verification. It validates the manifest model and self-checksum,
verifies raw and canonical artifact hashes, parses the sanitized raw JSON and canonical CSV,
recomputes source and canonical content checksums, recomputes the dataset ID, checks generated
file paths and filenames, confirms row counts and session ranges, and reruns OHLCV and XNYS
session validation from recorded acquisition metadata. It performs no network request.

Acquisition writes raw, canonical, and manifest files as a multi-artifact operation. If a
later write fails, files newly created by that attempt are cleaned up best-effort; matching
files that existed before the attempt are preserved.

## Prepare a Phase 2 Benchmark Lock

Phase 2 benchmark commands are manually invoked, offline, and file-based. They do not contact
Alpaca, construct a broker client, initialize SQLite, submit orders, or run a real final test
without explicit final-test acknowledgement.

Record owner-provided feed evidence:

```bash
python -m spy_market_agent.benchmark.cli record-feed-decision \
  --provider alpaca \
  --feed sip \
  --symbol SPY \
  --timeframe 1Day \
  --adjustment all \
  --start 2016-01-04 \
  --end 2025-12-31 \
  --available \
  --owner-acknowledge \
  --evidence-source "owner offline probe summary" \
  --output artifacts/benchmarks/local-feed-decision.json
```

Prepare the immutable benchmark lock from a verified Phase 1 manifest:

```bash
python -m spy_market_agent.benchmark.cli prepare \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --feed-record artifacts/benchmarks/local-feed-decision.json \
  --benchmark-role primary \
  --latest-complete-research-year 2025 \
  --artifact-root ./artifacts/benchmarks \
  --owner-approve-assumptions
```

Generated benchmark outputs remain ignored under `artifacts/benchmarks/<benchmark_id>/`.
Codex verification uses synthetic manifests only. The owner-run primary real SIP benchmark
for `v2.0.0-alpha.2` has completed; do not rerun or reopen that final test during Phase 3
research. Record only sanitized summary evidence in Git.

Deep-verify a benchmark directory offline:

```bash
python -m spy_market_agent.benchmark.cli verify \
  --benchmark-root artifacts/benchmarks/BENCHMARK_ID
```

Require current runtime lineage to match the frozen lock during verification:

```bash
python -m spy_market_agent.benchmark.cli verify \
  --benchmark-root artifacts/benchmarks/BENCHMARK_ID \
  --require-runtime-lineage
```

Run Stage A validation only after reviewing the lock and assumptions:

```bash
python -m spy_market_agent.benchmark.cli run-validation \
  --benchmark-lock artifacts/benchmarks/BENCHMARK_ID/benchmark_lock.json
```

Finalize the final-test lock only after validation review:

```bash
python -m spy_market_agent.benchmark.cli finalize-lock \
  --benchmark-lock artifacts/benchmarks/BENCHMARK_ID/benchmark_lock.json \
  --acknowledge-final-test-policy
```

Stage B final-test access is an owner-run controlled action and must not be run by Codex
during Phase 3 research. For the accepted Phase 2 benchmark, this command has already been
run once by the owner. A new non-audit run would require a new approved benchmark identity.
The command writes immutable `final_test_access.json` before loading final-test labels and
writes `final_test_completion.json` only after all final artifacts are completed:

```bash
python -m spy_market_agent.benchmark.cli run-final-test \
  --final-test-lock artifacts/benchmarks/BENCHMARK_ID/final_test_lock.json \
  --acknowledge-final-test-access
```

Audit replay verifies an existing completed final-test bundle without creating a new access
record or overwriting accepted artifacts:

```bash
python -m spy_market_agent.benchmark.cli run-final-test \
  --final-test-lock artifacts/benchmarks/BENCHMARK_ID/final_test_lock.json \
  --audit-replay
```

## Run Phase 3 Development Walk-Forward Research

Phase 3 PR #24 merged the approved framework and initial research scaffolding. PR #25
merged the manual, offline, development-only classification experimentation runner under
`docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md`, owner development testing completed
locally, and Alpha 3 was released as `v2.0.0-alpha.3`. It starts from the completed
`v2.0.0-alpha.2` benchmark evidence, but it must not tune against the already-opened Phase 2
final test. Protected evaluation, strategy optimization, production paper operation, live
trading, and paper/live/broker behavior remain unauthorized. Phase 3 produced
`NO CANDIDATE PROMOTION`, so no model is approved for shadow operation.

For any future authorized local development run, the required workflow is:

1. Confirm the working tree is a reviewed Phase 3 development or release-preparation branch,
   not `main` with unreviewed edits.
2. Read `AGENTS.md`, `PROJECT_SPEC.md`, `FUTURE_ROADMAP.md`, and the Phase 3 specification.
3. Treat Phase 2 final-test row-level labels, predictions, strategy rows, fills, and
   generated benchmark JSON as unavailable for research.
4. Reconstruct the frozen Phase 2 final-test prediction-session boundary from the accepted
   Phase 2 split policy and truncate the eligible development source slice before labels are
   built.
5. Define walk-forward folds chronologically with the six-row boundary exclusion before any
   feature, model, or calibration research.
6. Record experiment lineage and predeclared candidate-selection configuration before
   substantive real-data research.
7. Keep generated research artifacts ignored under `artifacts/research/<experiment_id>/`.
8. Report classification metrics without using strategy return as selection evidence.

The recommended default Phase 3 protocol is expanding-window walk-forward validation:

```text
minimum initial training rows: 756
boundary exclusion after training: 6 supervised rows
assessment window: 126 supervised rows
step size: 63 supervised rows
```

The implemented classification-first development campaign stages are:

1. verify a local Phase 1 manifest deeply and load canonical SPY daily data only after
   verification succeeds;
2. derive the Phase 2 final-test exclusion boundary, write it into campaign lineage, and
   truncate the research slice before label construction;
3. construct unchanged Version 1 labels and research-only OHLCV features with a global
   60-session feature warm-up;
4. create deterministic folds shared by all feature-set and model candidates;
5. run feature ablations with fixed Phase 2 logistic regression as comparator;
6. select a development feature set using median ROC AUC, log loss, Brier score,
   worst-quartile ROC AUC, and simplicity;
7. rerun fixed Phase 2 model baselines and finite predeclared scikit-learn model grids;
8. run the predeclared no-calibration, sigmoid, and isotonic calibration sub-study on the
   highest-ranked rankable uncalibrated candidate;
9. write classification, regime, drift, calibration, registry, fold, manifest, and
   selection-report artifacts;
10. emit `NO CANDIDATE PROMOTION` unless all predeclared promotion gates pass.

Run the manual development command from the repository root:

```bash
python -m spy_market_agent.research.cli run-development \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data \
  --campaign-config configs/research/phase3_development_campaign.json
```

The command never acquires data, accesses the network, reads Alpaca keys, constructs broker
clients, submits orders, loads Phase 2 final-test artifacts, reconstructs Phase 2 final-test
row-level labels for development research, or opens Phase 3 protected evaluation. Generated
research outputs remain ignored under `artifacts/research/<experiment_id>/`. The Alpha 3
release record contains only sanitized aggregate acceptance evidence and does not authorize
rerunning or retuning the real owner campaign.

## Run Phase 4 Observation-Only Shadow Readiness

Phase 4 implementation is owner accepted and released as `v2.0.0-beta.1` under
`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`. The package/runtime version is
`2.0.0b1`, and the public beta tag points to
`1c8feae478c0f5536b2193eeb408e580f3f7e33c`.

This substage has manual observation-only CLIs, no model inference, no unattended scheduler,
no daemon, no API write route, no dashboard execution control, and no broker path. It
consumes only verified local Phase 1 manifests and never performs acquisition.

First verify the local Phase 1 manifest through the existing offline verifier:

```bash
python -m spy_market_agent.market_data.cli verify \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data
```

Then run one explicit observation-only readiness check:

```bash
python -m spy_market_agent.shadow.cli run-observation \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --session YYYY-MM-DD \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id operator-confirmed-daily-final-v1
```

Inspect the persisted run without mutating state:

```bash
python -m spy_market_agent.shadow.cli show-run \
  --shadow-db ./shadow.sqlite3 \
  --run-id SHADOW_RUN_ID
```

Rerunning the same logical command must fail closed as `duplicate_run`. A prior incomplete
reservation must surface `recovery_required` and must not be automatically overwritten.
Observation-only mode records `blocked_no_approved_model` as the model-gate state without
failing an otherwise healthy data-readiness run. It cannot generate predictions, operational
`ShadowProposal` records, model-based `LONG` or `CASH` proposals from real market data,
broker orders, paper orders, approvals, or execution requests. Model-connected shadow
inference remains locked until a future separately approved model-admission record exists.

## Run Phase 4 Scheduled Observation Operations

Scheduled Observation Operations V1 means deterministic, schedule-aware,
operator-triggered orchestration. It is not cron, a daemon, a background worker, a retry
loop, or automatic market-data acquisition. The schedule layer answers which XNYS session is
due, whether local verified data is fresh enough, whether that logical observation has
already been processed, and whether prior shadow-operation history has gaps. Eligible work
delegates to the approved `run_observation(...)` path at most once. Owner acceptance is
complete, and Beta 1 is released.

First update or verify the local Phase 1 dataset through the separately approved Phase 1
workflow. The schedule commands consume that local manifest only:

```bash
python -m spy_market_agent.market_data.cli verify \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data
```

Preview the latest due observation without mutating shadow state:

```bash
python -m spy_market_agent.shadow.cli schedule-preview \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id operator-confirmed-daily-final-v1
```

`schedule-preview` derives the latest completed XNYS target from explicit UTC `--as-of`.
It treats a missing shadow database as `no_prior_history`, validates any existing shadow
database before reading history, and reports due, blocked, degraded, already-processed, or
recovery-required state. It does not create a database solely for preview and does not
append duplicate audit events.

Run the currently due observation only when the preview is eligible:

```bash
python -m spy_market_agent.shadow.cli run-due-observation \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id operator-confirmed-daily-final-v1
```

`run-due-observation` accepts no `--session`; the target is calendar-derived. If the exact
deterministic run already ended as `completed`, `blocked`, or `failed`, the command returns
an `already_processed` no-op without creating a second run, input snapshot, duplicate health
event, or duplicate alert. If the existing run is `reserved`, it returns
`recovery_required` and does not resume, delete, overwrite, or backfill.

Inspect the persisted run:

```bash
python -m spy_market_agent.shadow.cli show-run \
  --shadow-db ./shadow.sqlite3 \
  --run-id SHADOW_RUN_ID
```

Missed shadow observation sessions are operational-history gaps between the latest prior
terminal observation and the current due XNYS session. They are not missing market-data
sessions, proof of model failure, or authorization for replay. The scheduled layer reports
them as warnings and never loops through historical sessions to backfill them. If current
data is verified, fresh, finalized, not already processed, and not in recovery, missed
history alone may make the schedule state `degraded` while still allowing the current due
observation to proceed.

## Review Phase 5 Paper-Operation Recovery State

Phase 5 is active for specification plus non-submitting safety/recovery scaffold under
`docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md`. Target future release is
`v2.0.0-beta.2`; package/runtime remains `2.0.0b1`, and no beta.2 tag exists.

Current gates:

- P5-A infrastructure entry: `AUTHORIZED`.
- P5-B Phase 5 broker submission: `BLOCKED PENDING SEPARATE OWNER AUTHORIZATION`.
- P5-C model-connected paper operation: `BLOCKED_NO_APPROVED_PAPER_MODEL`.

The new `spy_market_agent.paper_ops` package is an offline policy surface. It classifies
existing paper-attempt states and reports gate status; it does not read credentials, contact
Alpaca, submit orders, reconcile real broker state, start a scheduler, or load models.

For local investigation of a persisted paper attempt, classify the known status in Python:

```bash
python - <<'PY'
from spy_market_agent.paper_ops import classify_paper_attempt_recovery

decision = classify_paper_attempt_recovery("submission_unknown")
print(decision.disposition.value)
print(decision.requires_client_order_id_lookup)
PY
```

`reserved` and `submission_unknown` require reconciliation by `client_order_id`.
`accepted`, `broker_existing_order_found`, and `reconciled` are terminal. `blocked` and
`rejected` do not permit automatic resubmission. Unknown states fail closed. Use
`docs/V2_PHASE_05_PAPER_RECOVERY_RUNBOOK.md` for operator recovery guidance.

## Inspect Model Evaluations

With the read API running:

```bash
curl http://127.0.0.1:8000/api/v1/model-runs
curl http://127.0.0.1:8000/api/v1/model-runs/RUN_ID
curl "http://127.0.0.1:8000/api/v1/model-runs/RUN_ID/predictions?limit=100&offset=0"
```

Replace `RUN_ID` with a persisted run ID. Classification metrics are diagnostics only and do
not establish profitability.

## Inspect Backtest Results

```bash
curl http://127.0.0.1:8000/api/v1/backtests
curl http://127.0.0.1:8000/api/v1/backtests/RUN_ID
curl "http://127.0.0.1:8000/api/v1/backtests/RUN_ID/equity?limit=100&offset=0"
curl "http://127.0.0.1:8000/api/v1/backtests/RUN_ID/orders?limit=100&offset=0"
curl "http://127.0.0.1:8000/api/v1/backtests/RUN_ID/risk-decisions?limit=100&offset=0"
curl "http://127.0.0.1:8000/api/v1/backtests/RUN_ID/fills?limit=100&offset=0"
```

Historical backtests are approximations and do not guarantee future or executable results.

## Inspect Local Paper-Execution Status

```bash
curl http://127.0.0.1:8000/api/v1/paper-trading/status
curl "http://127.0.0.1:8000/api/v1/paper-orders?limit=100&offset=0"
curl http://127.0.0.1:8000/api/v1/paper-orders/CLIENT_ORDER_ID
```

These routes inspect local SQLite audit state only. They do not construct an Alpaca client,
change kill switches, approve orders, reconcile orders, or submit orders.

## Paper-Order Instruction and Approval Concept

The executable submission path is intentionally omitted from copyable shell commands. A
paper submission requires this conceptual sequence:

```text
PSEUDOCODE ONLY - do not paste

1. Build a ProposedOrder from an approved strategy and backtest/risk workflow.
2. Run independent risk evaluation and require an approved RiskDecision.
3. Build a PaperOrderInstruction with unique signal_id and client_order_id.
4. Review the deterministic instruction_fingerprint.
5. Record human approval with a unique approval_id bound to that exact fingerprint.
6. Confirm paper mode, paper execution enabled, dry-run disabled, configuration kill switch off.
7. Confirm the durable SQLite kill switch has been explicitly disengaged.
8. Confirm broker preflights, market hours, SPY asset, positions, open orders, and risk pass.
9. Reserve IDs and the SPY execution session before any broker submission.
10. Submit only through the explicit service call if every gate passes.
```

Any timeout or uncertain post-submit result must be treated as `submission_unknown`. Do not
resubmit automatically; use lookup-only reconciliation by `client_order_id`.
