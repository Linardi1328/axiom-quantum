# Axiom Quantum Phase 7 — Supervised Paper-Trading Validation Completion Contract

## Separate verdicts

**Software delivery verdict:** Phase 7 code may be approved after RIC-53 through RIC-57 are individually squash-merged, the final main tree and commit lineage are verified, and exact-head Ruff, format, MyPy, full pytest (coverage >=85%), whitespace, Betterleaks, and the documented code review gate all pass.

**Operational pilot verdict:** `NO_GO` until independently verified real paper-broker evidence and separate explicit owner approval are obtained. A complete software merge **does not** indicate that any 20-session owner-run Alpaca paper pilot took place, that profitability was proven, or that a paper model was approved.

## Immutable evidence chain

`stored Phase 5 readiness assessment -> human-invoked Phase 7 validation session -> (synthetic safety record or operator-attested paper pilot observation with exact Phase 6 outcome + report) -> append-only Phase 7 memory -> complete-source operational assessment -> immutable checksum-verified report`.

* RIC-53: deterministic human-requested validation session, synthetic/paper evidence isolation.
* RIC-54: six explicit adversarial safety fixtures with fail-closed outcomes, always synthetic.
* RIC-55: operator-attested paper observations and honest zero-trade abstentions; no new broker calls.
* RIC-56: exact Phase 5/6/7 reloads, exclusive paper-date reservation, symlink/directory rejection, conflict and tamper defenses.
* RIC-57: assessment over all registered records, XNYS trading-session validation, immutable audit report and explicit invocation workflow.

## Readiness-to-audit gate (not a trading permit)

At least 20 distinct XNYS operator-attested sessions, at least one accepted or reconciled recorded paper outcome, no incidents, no unresolved submission-unknown state, and every registered critical synthetic fixture passing are required before the system may label evidence `ready_for_owner_audit`. An observation where no strategy qualifies can be recorded as an abstention; never force a trade to meet sample thresholds.

All operator-attested records declare `not_independently_verified`; a manually supplied hash is not an Alpaca-origin signed receipt. Fake brokers in the test suite can exercise the same code paths but do **not** establish real paper pilot evidence. A separate broker-account audit, exact order/position reconciliation, sample-period review, risk evaluation, model-admission decision and explicit owner go/no-go are still necessary. Even when software labels a snapshot ready for owner audit, the machine `operational_verdict` remains permanently `no_go` and `trading_authority` remains `none`.

## Continuing restrictions

No live trading, unattended trading, schedulers/daemons, model-connected execution without approved paper model, shorts, leverage, fractional shares, API/dashboard write routes, unsanctioned notifications, retries following uncertainty, credential persistence or automatic approval. P5-C remains `blocked_no_approved_paper_model`.

## Code review and merge

When CodeRabbit is available, obtain its genuine exact-head review; when rate-limited, the owner-approved structured AI fallback may be documented explicitly alongside CI and manual/security inspection, without claiming it is an independent human or CodeRabbit review. GitHub prohibits approving one's own pull request via the formal APPROVE API; record author approval as a PR discussion comment, use an exact head lease on squash merge, and verify main afterward. Never bypass failing CI or unresolved material security findings.

## Completion evidence

The final completion report must identify the actual merged PRs, immutable main commit, Linear status for RIC-53 through RIC-57, and both verdicts. Do not record invented owner-run pilot attempts, synthetic real-broker sessions, model profitability, or any live-trading authority.
