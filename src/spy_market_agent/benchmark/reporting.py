from __future__ import annotations

from spy_market_agent.benchmark.locks import (
    BenchmarkLock,
    DatasetEligibilityReport,
    FinalTestReadiness,
    SplitManifest,
    ValidationResult,
)


def benchmark_report(
    *,
    lock: BenchmarkLock,
    eligibility: DatasetEligibilityReport,
    split: SplitManifest,
    validation: ValidationResult | None = None,
    readiness: FinalTestReadiness | None = None,
    final_results_available: bool = False,
) -> str:
    lines = [
        f"# Version 2 Phase 2 Benchmark Report: {lock.benchmark_id}",
        "",
        "This is historical research.",
        "Results do not guarantee profitability.",
        "Backtests are not live fills.",
        "Alpaca provider coverage may be limited.",
        "IEX is not consolidated SIP data.",
        "No investment recommendation is produced.",
        "No live-money readiness is established.",
        "",
        "## Dataset Eligibility",
        f"- Dataset ID: `{lock.dataset_id}`",
        f"- Provider/feed: `{lock.provider}` / `{lock.feed}`",
        f"- Adjustment mode: `{lock.adjustment_mode}`",
        f"- Benchmark role: `{lock.benchmark_role}`",
        f"- Eligibility passed: `{eligibility.passed}`",
        f"- Actual range: `{eligibility.actual_start}` to `{eligibility.actual_end}`",
        f"- Rows: `{eligibility.row_count}`",
        "",
        "## Split",
        f"- Train rows: `{split.train.included_row_count}`",
        f"- Validation rows: `{split.validation.included_row_count}`",
        f"- Final-test rows: `{split.final_test.included_row_count}`",
        f"- Boundary exclusion sessions: `{split.boundary_exclusion_sessions}`",
        "",
        "## Policies",
        f"- Selection rule: `{lock.benchmark_policy.selection_rule_id}`",
        f"- Signal policy: `{lock.benchmark_policy.signal_policy_id}`",
        f"- Risk policy: `{lock.benchmark_policy.risk_policy_id}`",
        f"- Primary cost scenario: `{lock.benchmark_policy.primary_cost_scenario}`",
        "- Volatility regime threshold: "
        f"`{lock.benchmark_policy.regime_policy.volatility_threshold}`",
        "",
        "## Validation Results",
    ]
    if validation is None:
        lines.append("- Validation has not run.")
    else:
        lines.extend(
            [
                f"- Selected model: `{validation.selected_model_name}`",
                f"- Selection reason: {validation.selection_reason}",
                f"- Model metric rows: `{len(validation.model_metrics)}`",
                f"- Classification baselines: `{len(validation.classification_baselines)}`",
                f"- Strategy comparator results: `{len(validation.strategy_results)}`",
            ]
        )
    lines.append("")
    lines.append("## Final-Test Results")
    if final_results_available:
        lines.append("- Final-test results are available in locked machine-readable artifacts.")
    else:
        lines.append(
            "- Final-test results are locked and unavailable before explicit Stage B access."
        )
    if readiness is not None:
        lines.append(f"- Final-test readiness: `{readiness.ready}`")
    lines.extend(
        [
            "",
            "## Limitations",
            "- Historical adjusted-price backtests approximate research returns and "
            "are not live fills.",
            "- Classification metrics are diagnostics and do not establish profitability.",
            "- Provider data rights and coverage limitations must be reviewed before publication.",
            "- Phase 2 does not add model research, threshold tuning, API write "
            "routes, dashboard controls, or live trading.",
        ]
    )
    return "\n".join(lines) + "\n"
