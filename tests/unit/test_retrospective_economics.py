"""Synthetic-only tests of the locked economic-validation evaluator (RIC-152 D11).

Every input below is an internally generated array; no market data, credentials, network,
model fitting or file outside this repository is used.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import itertools
import json
import math
import shutil
import statistics
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.research import retrospective_economics as eco
from spy_market_agent.research.retrospective_economics import (
    EconomicPolicy,
    EconomicPolicyError,
    EconomicStructuralError,
    FoldInput,
    evaluate_economics,
    load_economic_policy,
    p_a_trades,
    parse_economic_policy,
    simulate_trades,
)

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / eco.ECONOMIC_LOCK_RELATIVE
SPEC = ROOT / eco.ECONOMIC_SPEC_RELATIVE
POLICY = load_economic_policy(ROOT)
MODELS = (
    "training_prevalence",
    "logistic_regression_research_00",
    "logistic_regression",
    "gradient_boosting",
    "candidate",
)
COMPARATOR = "logistic_regression"
ROWS = 60  # assessed predictions per synthetic fold (window = ROWS + 5 sessions)
UNITS = POLICY.initial_index_units


def _simulate(
    trades: list[tuple[int, int]], opens: list[float], closes: list[float], multiplier: int = 1
) -> eco.FoldSimulation:
    return simulate_trades(
        trades, opens, closes, POLICY.side_cost(multiplier), UNITS, annualization_sessions=252
    )


def _flat(length: int, price: float = 100.0) -> tuple[list[float], list[float]]:
    return [price] * length, [price] * length


def _trend(rate: float, rows: int = ROWS, dips: bool = True) -> tuple[list[float], list[float]]:
    """Opens rise ``rate`` per session; held-session closes sit a hair above the open and the
    close of every sixth session (flat for P-A) dips so that buy-and-hold sees drawdowns."""
    opens = [100.0 * (1.0 + rate) ** k for k in range(rows + 5)]
    closes = [o * (0.97 if dips and k % 6 == 5 else 1.0 + rate / 2.0) for k, o in enumerate(opens)]
    return opens, closes


def _inputs(
    candidate_p: float = 0.9,
    other_p: float = 0.1,
    folds: int = 7,
    build: Any = None,
) -> list[FoldInput]:
    result = []
    for index in range(folds):
        opens, closes = build(index) if build is not None else _trend(0.002)
        probabilities = dict.fromkeys(MODELS, (other_p,) * ROWS)
        probabilities["candidate"] = (candidate_p,) * ROWS
        result.append(FoldInput(probabilities, tuple(opens), tuple(closes)))
    return result


def _evaluate(
    folds: list[FoldInput],
    *,
    policy: EconomicPolicy = POLICY,
    comparator: str | None = COMPARATOR,
    screen_met: bool = True,
) -> dict[str, Any]:
    return evaluate_economics(
        policy,
        folds,
        models=MODELS,
        candidate="candidate",
        prevalence_model="training_prevalence",
        designated_comparator=comparator,
        classification_screen_met=screen_met,
    )


def _check(result: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["id"] == check_id)


# ---------------------------------------------------------------------------
# Locked policy, digests and identity
# ---------------------------------------------------------------------------


def test_lock_transcribes_the_owner_approved_policy_exactly() -> None:
    lock = json.loads(LOCK.read_text())
    assert lock["policy"] == "P-A"
    assert lock["signal_threshold"] == 0.5
    assert lock["accounting"] == {
        "initial_index_units": 10000,
        "fractional_units": True,
        "idle_return": 0,
        "annualization_sessions": 252,
        "reference_return": 0,
    }
    assert lock["costs"]["multipliers"] == [1, 5, 20]
    assert POLICY.side_cost(1) == pytest.approx(0.375e-4)
    assert POLICY.side_cost(5) == pytest.approx(1.875e-4)
    assert POLICY.side_cost(20) == pytest.approx(7.5e-4)
    assert (POLICY.min_pooled_round_trips, POLICY.min_traded_folds) == (50, 5)
    assert POLICY.min_round_trips_per_traded_fold == 5
    assert POLICY.required_fold_outputs == 7
    assert POLICY.max_median_cost_burden == 0.005
    assert POLICY.max_fold_drawdown == 0.20
    assert (POLICY.worst_decile_fraction, POLICY.min_worst_decile_mean) == (0.10, -0.08)
    assert POLICY.min_tail_sample == 50
    assert POLICY.risk_multipliers == (1, 20)
    assert POLICY.min_positive_paired_folds == 5
    assert POLICY.paired_benchmarks == ("B0", "B2", "B3")
    assert lock["execution_authority"] == "none"
    assert lock["disposition"] == "NO CANDIDATE PROMOTION"
    assert lock["classification_screen_id"] == "aq-p3cr-criteria-v1"
    assert lock["decisional_only_if_classification_screen"] == "CRITERIA_MET"


def test_pinned_digests_match_the_files_and_are_bound_into_identity() -> None:
    assert hashlib.sha256(SPEC.read_bytes()).hexdigest() == eco.ECONOMIC_SPEC_SHA256
    assert hashlib.sha256(LOCK.read_bytes()).hexdigest() == eco.ECONOMIC_LOCK_SHA256
    assert json.loads(LOCK.read_text())["specification"]["sha256"] == eco.ECONOMIC_SPEC_SHA256
    identity = eco.policy_identity(POLICY)
    assert identity["specification_sha256"] == eco.ECONOMIC_SPEC_SHA256
    assert identity["lock_sha256"] == eco.ECONOMIC_LOCK_SHA256
    assert identity["evidence_label"] == "adjusted_price_index_hypothetical"


def _repository_copy(tmp_path: Path) -> Path:
    for relative in (eco.ECONOMIC_LOCK_RELATIVE, eco.ECONOMIC_SPEC_RELATIVE):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    return tmp_path


def test_tampered_lock_or_specification_is_refused(tmp_path: Path) -> None:
    repo = _repository_copy(tmp_path)
    assert load_economic_policy(repo).lock_sha256 == eco.ECONOMIC_LOCK_SHA256
    lock = repo / eco.ECONOMIC_LOCK_RELATIVE
    lock.write_text(lock.read_text().replace('"signal_threshold": 0.5', '"signal_threshold": 0.4'))
    with pytest.raises(EconomicPolicyError, match="economic_lock_digest_mismatch"):
        load_economic_policy(repo)
    repo = _repository_copy(tmp_path / "second")
    spec = repo / eco.ECONOMIC_SPEC_RELATIVE
    spec.write_text(spec.read_text() + "\nedit\n")
    with pytest.raises(EconomicPolicyError, match="economic_specification_digest_mismatch"):
        load_economic_policy(repo)


def test_missing_or_symlinked_policy_files_are_refused(tmp_path: Path) -> None:
    with pytest.raises(EconomicPolicyError, match="economic_specification_digest_mismatch"):
        load_economic_policy(tmp_path)
    repo = _repository_copy(tmp_path / "linked")
    lock = repo / eco.ECONOMIC_LOCK_RELATIVE
    real = repo / "real.json"
    lock.rename(real)
    lock.symlink_to(real)
    with pytest.raises(EconomicPolicyError, match="economic_lock_digest_mismatch"):
        load_economic_policy(repo)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda lock: lock.pop("policy"),
        lambda lock: lock.update(extra=1),
        lambda lock: lock.update(policy="P-B"),
        lambda lock: lock.update(execution_authority="paper"),
        lambda lock: lock.update(disposition="PROMOTE"),
        lambda lock: lock.update(evidence_label="dollar_pnl"),
        lambda lock: lock.update(outcome_order=list(reversed(eco.OUTCOME_ORDER))),
        lambda lock: lock.update(signal_threshold=1.5),
        lambda lock: lock.update(signal_threshold=True),
        lambda lock: lock["costs"].update(multipliers=[1, 5]),
        lambda lock: lock["costs"].update(severe_multiplier=1),
        lambda lock: lock["costs"].update(commission_bps_per_side=0.125),
        lambda lock: lock["fold_validity"].update(abstained_fold_is_valid=False),
        lambda lock: lock["trade_minimums"].update(pooled_round_trips=0),
        lambda lock: lock["risk_criteria"].update(applied_at_multipliers=[1, 7]),
        lambda lock: lock["accounting"].update(idle_return=0.01),
        lambda lock: lock["comparison_criteria"].update(paired_benchmarks=["B0", "B2"]),
        lambda lock: lock["specification"].update(sha256="0" * 64),
        lambda lock: lock["cost_criteria"].update(severe_median_all_fold_net_return="any"),
        lambda lock: lock["cost_criteria"].update(
            severe_median_traded_fold_net_return=lock["cost_criteria"].pop(
                "severe_median_all_fold_net_return"
            )
        ),
    ],
)
def test_lock_parser_rejects_any_unexpected_field_type_or_value(mutate: Any) -> None:
    lock = json.loads(LOCK.read_text())
    mutate(lock)
    with pytest.raises(EconomicPolicyError):
        parse_economic_policy(json.dumps(lock).encode())


def test_lock_parser_rejects_duplicate_keys_and_nonfinite_numbers() -> None:
    text = LOCK.read_text()
    with pytest.raises(EconomicPolicyError, match="lock_duplicate_key"):
        parse_economic_policy(
            text.replace('"policy": "P-A",', '"policy": "P-A", "policy": "P-A",').encode()
        )
    with pytest.raises(EconomicPolicyError, match="lock_nonfinite_number"):
        parse_economic_policy(
            text.replace('"signal_threshold": 0.5', '"signal_threshold": NaN').encode()
        )
    with pytest.raises(EconomicPolicyError, match="lock_not_valid_json"):
        parse_economic_policy(b"\xff\xfe")


# ---------------------------------------------------------------------------
# Position ordering and holding periods (policy P-A)
# ---------------------------------------------------------------------------


def test_in_position_signals_are_rejected_and_not_queued() -> None:
    signals = [True] * 14
    trades, ignored, longs = p_a_trades(signals)
    assert trades == [(0, 5), (6, 11), (12, 17)]  # next eligible signal is the exit session
    assert (ignored, longs) == (11, 14)
    assert all(b[0] > a[1] for a, b in itertools.pairwise(trades))


def test_signal_at_the_close_before_an_exit_open_is_rejected_but_the_exit_close_is_eligible() -> (
    None
):
    only = [True] + [False] * 4 + [True, True]  # row 5 is the close before the exit open
    trades, ignored, _ = p_a_trades(only)
    assert trades == [(0, 5), (6, 11)]
    assert ignored == 1
    no_queue = [True] + [False] * 4 + [True] + [False] * 3
    assert p_a_trades(no_queue)[0] == [(0, 5)]  # a rejected signal never becomes a later entry


def test_every_position_is_held_exactly_five_sessions_with_six_session_spacing() -> None:
    trades, _, _ = p_a_trades([True] * 126)
    assert len(trades) == 21 == math.ceil(126 / 6)
    assert {x - e for e, x in trades} == {5}
    assert {b[0] - a[0] for a, b in itertools.pairwise(trades)} == {6}


def test_cash_signals_open_no_position_and_threshold_is_strict() -> None:
    assert p_a_trades([False] * 10) == ([], 0, 0)
    folds = _inputs(candidate_p=0.5)  # probability equal to the threshold is not long
    result = _evaluate(folds)
    assert result["candidate_aggregate"]["1x"]["round_trips_pooled"] == 0


def test_exposure_equals_held_sessions_over_window_length() -> None:
    opens, closes = _flat(11)
    sim = _simulate([(0, 5)], opens, closes)
    assert sim.exposure_fraction == pytest.approx(5 / 11)
    sim = _simulate([(0, 5), (6, 10)], opens, closes)
    assert sim.exposure_fraction == pytest.approx(9 / 11)


@pytest.mark.parametrize(
    "trades", [[(0, 11)], [(3, 3)], [(4, 2)], [(0, 5), (5, 8)], [(-1, 4)], [(0, 5), (4, 9)]]
)
def test_invalid_trade_schedules_abort(trades: list[tuple[int, int]]) -> None:
    opens, closes = _flat(11)
    with pytest.raises(EconomicStructuralError):
        _simulate(trades, opens, closes)


# ---------------------------------------------------------------------------
# Accounting identities, costs, drawdown and risk-adjusted diagnostics
# ---------------------------------------------------------------------------


def test_single_trade_matches_the_frozen_label_cost_arithmetic() -> None:
    opens, closes = _flat(6)
    opens[5] = 110.0
    closes[5] = 110.0
    cost = POLICY.side_cost(1)
    sim = _simulate([(0, 5)], opens, closes)
    expected_multiple = (110.0 * (1.0 - cost)) / (100.0 * (1.0 + cost))
    assert sim.net_return == pytest.approx(expected_multiple - 1.0, abs=1e-12)
    assert sim.terminal_index_units == pytest.approx(UNITS * expected_multiple)
    assert sim.trade_net_returns[0] == pytest.approx(expected_multiple - 1.0, abs=1e-12)
    assert len(sim.trade_net_returns) == 1
    assert sim.gross_return == pytest.approx(0.10)
    # total cost = units * (open_entry + open_exit) * c with units = equity / (open*(1+c))
    units = UNITS / (100.0 * (1.0 + cost))
    assert sim.total_transaction_cost == pytest.approx(units * (100.0 + 110.0) * cost)
    assert sim.cost_burden_fraction == pytest.approx(sim.total_transaction_cost / UNITS)
    assert sim.turnover_ratio > 0.0


def test_terminal_equity_is_initial_times_product_of_trade_multiples() -> None:
    opens, closes = _trend(0.002, dips=False)
    trades, _ignored, _longs = p_a_trades([True] * ROWS)
    sim = _simulate(trades, opens, closes)
    product = math.prod(1.0 + r for r in sim.trade_net_returns)
    assert sim.terminal_index_units == pytest.approx(UNITS * product, rel=1e-12)
    assert sim.round_trips == len(trades) == 10


def test_flat_prices_lose_exactly_the_round_trip_costs() -> None:
    opens, closes = _flat(11)
    cost = POLICY.side_cost(1)
    sim = _simulate([(0, 5)], opens, closes)
    assert sim.net_return == pytest.approx((1.0 - cost) / (1.0 + cost) - 1.0, abs=1e-12)
    assert sim.break_even_per_side_bps is None  # gross return is not positive


def test_costs_increase_monotonically_with_the_locked_multipliers() -> None:
    opens, closes = _trend(0.002, dips=False)
    trades = p_a_trades([True] * ROWS)[0]
    sims = [_simulate(trades, opens, closes, m) for m in POLICY.multipliers]
    assert sims[0].net_return > sims[1].net_return > sims[2].net_return
    assert (
        sims[0].cost_burden_fraction < sims[1].cost_burden_fraction < sims[2].cost_burden_fraction
    )
    assert sims[2].total_transaction_cost == pytest.approx(
        20.0 * sims[0].total_transaction_cost, rel=0.05
    )


def test_closed_form_break_even_cost_zeroes_the_net_return() -> None:
    opens, closes = _trend(0.002, dips=False)
    trades = p_a_trades([True] * ROWS)[0]
    base = _simulate(trades, opens, closes)
    assert base.break_even_per_side_bps is not None
    at_break_even = simulate_trades(
        trades,
        opens,
        closes,
        base.break_even_per_side_bps / 10_000.0,
        UNITS,
        annualization_sessions=252,
    )
    assert at_break_even.net_return == pytest.approx(0.0, abs=1e-9)
    above = simulate_trades(
        trades,
        opens,
        closes,
        1.1 * base.break_even_per_side_bps / 10_000.0,
        UNITS,
        annualization_sessions=252,
    )
    assert above.net_return < 0.0


def test_maximum_drawdown_is_measured_on_the_session_close_marks() -> None:
    opens, closes = _flat(11)
    closes[2] = 80.0  # a 20% dip while held, recovered by the exit open
    sim = _simulate([(0, 5)], opens, closes)
    cost = POLICY.side_cost(1)
    assert sim.max_drawdown == pytest.approx(1.0 - (80.0 / (100.0 * (1.0 + cost))), abs=1e-9)
    assert _simulate([], opens, closes).max_drawdown == 0.0  # abstained: nothing to draw down


def test_sharpe_and_sortino_follow_the_locked_zero_reference_formulas() -> None:
    opens, closes = _trend(0.002, dips=False)
    for k in range(ROWS + 5):
        if k % 6 == 2:  # a mark-to-market loss while held creates negative daily returns
            closes[k] = opens[k] * 0.985
    trades = p_a_trades([True] * ROWS)[0]
    sim = _simulate(trades, opens, closes)
    # Rebuild the daily marks independently.
    cost = POLICY.side_cost(1)
    marks = [UNITS]
    equity, held = UNITS, None
    last = len(opens) - 1
    plan = dict(trades)
    for s in range(last):
        if held and held[1] == s:
            equity = held[2] * opens[s] * (1 - cost)
            held = None
        if held is None and s in plan:
            held = (s, plan[s], equity / (opens[s] * (1 + cost)))
        marks.append(held[2] * closes[s] if held else equity)
    if held:
        equity = held[2] * opens[held[1]] * (1 - cost)
    marks.append(equity)
    daily = [marks[k] / marks[k - 1] - 1.0 for k in range(1, len(marks))]
    mean, vol = statistics.fmean(daily), statistics.stdev(daily)
    down = math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in daily))
    assert sim.sharpe_like == pytest.approx(mean / vol * math.sqrt(252), rel=1e-9)
    assert sim.sortino_like == pytest.approx(mean / down * math.sqrt(252), rel=1e-9)
    assert sim.return_over_max_drawdown == pytest.approx(sim.net_return / sim.max_drawdown)


def test_undefined_ratios_are_reported_undefined_never_zero() -> None:
    opens, closes = _flat(11)
    sim = _simulate([], opens, closes)
    assert (sim.sharpe_like, sim.sortino_like, sim.return_over_max_drawdown) == (None, None, None)
    record = eco._fold_record(POLICY, sim, True)
    for name in (
        "sharpe_like",
        "sortino_like",
        "return_over_max_drawdown",
        "break_even_per_side_bps",
    ):
        assert record[name]["value"] is None
        assert record[name]["undefined_reason"]
    assert record["abstained"] is True
    assert record["net_return"] == 0.0


def test_buy_and_hold_benchmark_is_one_round_trip_over_the_whole_window() -> None:
    result = _evaluate(_inputs())
    hold = result["benchmarks"]["B1"]["1x"]["folds"][0]
    opens, _closes = _trend(0.002)
    cost = POLICY.side_cost(1)
    assert hold["round_trips"] == 1
    assert hold["net_return"] == pytest.approx(
        opens[-1] * (1 - cost) / (opens[0] * (1 + cost)) - 1.0, abs=1e-12
    )
    assert result["benchmarks"]["B0"]["1x"]["folds"][0]["net_return"] == 0.0


# ---------------------------------------------------------------------------
# Outcome rules
# ---------------------------------------------------------------------------


def test_plausible_descriptive_outcome_requires_every_locked_criterion() -> None:
    result = _evaluate(_inputs())
    assert result["rule_outcome"] == "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE"
    assert result["outcome"] == "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE"
    assert result["decisional"]
    assert all(check["passed"] for check in result["checks"])
    assert result["missing_decisive_evidence"] == []
    assert result["execution_authority"] == "none"
    assert result["disposition"] == "NO CANDIDATE PROMOTION"
    assert _check(result, "min_pooled_round_trips")["observed"] == 70


def test_failed_classification_screen_makes_every_economic_result_non_decisional() -> None:
    result = _evaluate(_inputs(), screen_met=False)
    assert result["rule_outcome"] == "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE"
    assert result["outcome"] == eco.NON_DECISIONAL
    assert result["decisional"] is False


def test_abstention_is_valid_and_gives_no_trades_not_not_evaluable() -> None:
    result = _evaluate(_inputs(candidate_p=0.1))
    assert result["rule_outcome"] == "NO_TRADES"
    assert result["candidate_aggregate"]["1x"]["abstained_folds"] == 7
    folds = result["models"]["candidate"]["1x"]["folds"]
    assert len(folds) == 7
    assert all(f["abstained"] and f["net_return"] == 0.0 for f in folds)


def test_missing_designated_comparator_can_never_be_favorable() -> None:
    result = _evaluate(_inputs(), comparator=None)
    assert result["rule_outcome"] == "NOT_EVALUABLE"
    assert result["outcome"] == "NOT_EVALUABLE"
    assert "designated_comparator_unavailable" in result["missing_decisive_evidence"]
    result = _evaluate(_inputs(), comparator="not_a_model")
    assert result["rule_outcome"] == "NOT_EVALUABLE"


def test_insufficient_pooled_trades() -> None:
    rows = 30
    folds = []
    for _ in range(7):
        opens, closes = _trend(0.002, rows=rows)
        folds.append(
            FoldInput(
                {m: ((0.9 if m == "candidate" else 0.1),) * rows for m in MODELS},
                tuple(opens),
                tuple(closes),
            )
        )
    result = _evaluate(folds)  # 5 trades per fold, 35 pooled
    assert result["rule_outcome"] == "INSUFFICIENT_TRADES"
    assert _check(result, "min_pooled_round_trips")["observed"] == 35
    assert result["missing_decisive_evidence"] == []  # insufficient volume, not missing evidence


def test_enough_pooled_trades_but_too_few_folds_meeting_the_per_fold_minimum() -> None:
    rows = 78  # 13 trades per trading fold
    folds = []
    for index in range(7):
        opens, closes = _trend(0.002, rows=rows)
        candidate = 0.9 if index < 4 else 0.1  # three folds abstain
        folds.append(
            FoldInput(
                {m: ((candidate if m == "candidate" else 0.1),) * rows for m in MODELS},
                tuple(opens),
                tuple(closes),
            )
        )
    result = _evaluate(folds)
    assert _check(result, "min_pooled_round_trips")["observed"] == 52  # >= 50 pooled
    assert _check(result, "min_traded_folds_with_trade_minimum")["observed"] == 4  # < 5 folds
    assert result["rule_outcome"] == "INSUFFICIENT_TRADES"


def test_excessive_median_cost_burden() -> None:
    tight = dataclasses.replace(POLICY, max_median_cost_burden=1e-6)
    result = _evaluate(_inputs(), policy=tight)
    assert result["rule_outcome"] == "EXCESSIVE_COST_BURDEN"
    assert not _check(result, "base_median_traded_fold_cost_burden")["passed"]


def test_severe_cost_scenario_must_leave_a_strictly_positive_median_net_return() -> None:
    thin = _inputs(build=lambda _i: _trend(0.0002, dips=False))  # ~0.1% per hold < 20x costs
    result = _evaluate(thin)
    assert result["rule_outcome"] == "EXCESSIVE_COST_BURDEN"
    severe = _check(result, "severe_median_all_fold_net_return_positive")
    assert severe["observed"] < 0.0
    assert not severe["passed"]
    assert result["candidate_aggregate"]["1x"]["median_net_return_traded_folds"] > 0.0


def test_fold_drawdown_limit_is_applied_at_every_locked_multiplier() -> None:
    def build(index: int) -> tuple[list[float], list[float]]:
        opens, closes = _trend(0.002)
        if index == 0:
            closes[2] = opens[2] * 0.70  # 30% mark-to-market drop while held
        return opens, closes

    result = _evaluate(_inputs(build=build))
    assert result["rule_outcome"] == "UNACCEPTABLE_RISK"
    for multiplier in (1, 20):
        assert not _check(result, f"fold_max_drawdown_at_{multiplier}x")["passed"]


def test_pooled_worst_decile_tail_limit() -> None:
    def build(_index: int) -> tuple[list[float], list[float]]:
        opens, closes = _trend(0.004)
        opens[5] = opens[0] * 0.90  # first trade of every fold exits 10% lower
        closes[5] = opens[5]
        return opens, closes

    result = _evaluate(_inputs(build=build))
    assert result["rule_outcome"] == "UNACCEPTABLE_RISK"
    tail = _check(result, "pooled_worst_decile_mean_at_1x")
    assert tail["observed"] < -0.08
    assert not tail["passed"]
    assert math.ceil(0.10 * 70) == 7  # worst ceil(0.10 * n) of 70 pooled trades


def test_tail_evidence_with_too_small_a_sample_is_missing_never_passed() -> None:
    result = _evaluate(_inputs())
    tail = result["candidate_aggregate"]["1x"]["pooled_worst_decile"]
    assert tail["sample"] == 70
    assert tail["worst_count"] == 7
    assert tail["sufficient_sample"]
    more_demanding = dataclasses.replace(POLICY, min_tail_sample=71)
    result = _evaluate(_inputs(), policy=more_demanding)
    assert result["rule_outcome"] == "NOT_EVALUABLE"
    assert "tail_evidence_unavailable_at_1x" in result["missing_decisive_evidence"]


def test_paired_comparisons_use_strictly_positive_median_and_five_of_seven_folds() -> None:
    equal = []
    for fold in _inputs():  # B2 now trades exactly like the candidate
        probabilities = dict(fold.probabilities)
        probabilities["training_prevalence"] = probabilities["candidate"]
        equal.append(FoldInput(probabilities, fold.opens, fold.closes))
    result = _evaluate(equal)
    assert result["rule_outcome"] == "ECONOMICALLY_NOT_SUPPORTED"
    assert not _check(result, "paired_B2")["passed"]  # a zero difference is not positive
    assert _check(result, "paired_B0")["passed"]
    assert _check(result, "paired_B3")["passed"]
    # Four positive folds of seven fails the count rule even with a positive median.
    mixed = []
    for index, fold in enumerate(_inputs()):
        probabilities = dict(fold.probabilities)
        if index >= 4:
            probabilities["candidate"] = (0.1,) * ROWS  # abstains: difference 0 vs B0/B2/B3
        mixed.append(FoldInput(probabilities, fold.opens, fold.closes))
    result = _evaluate(mixed)
    assert _check(result, "paired_B0")["observed"]["positive_folds"] == 4
    assert not _check(result, "paired_B0")["passed"]


def test_buy_and_hold_drawdown_guard_uses_the_median_over_folds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build(_index: int) -> tuple[list[float], list[float]]:
        opens, closes = _trend(0.002)
        closes[2] = opens[2] * 0.99  # a small mark-to-market dip while held
        return opens, closes

    real = eco.simulate_trades

    def patched(trades: Any, opens: Any, closes: Any, *args: Any, **kwargs: Any) -> Any:
        sim = real(trades, opens, closes, *args, **kwargs)
        if list(trades) == [(0, len(opens) - 1)]:  # isolate the guard: B1 shows no drawdown
            return dataclasses.replace(sim, max_drawdown=0.0)
        return sim

    monkeypatch.setattr(eco, "simulate_trades", patched)
    result = _evaluate(_inputs(build=build))
    guard = _check(result, "drawdown_guard_vs_B1")
    assert guard["observed"]["B1"] == 0.0
    assert guard["observed"]["candidate"] > 0.0
    assert not guard["passed"]
    assert result["rule_outcome"] == "ECONOMICALLY_NOT_SUPPORTED"


def test_outcome_order_is_the_locked_order() -> None:
    assert eco.OUTCOME_ORDER == (
        "NOT_EVALUABLE",
        "NO_TRADES",
        "INSUFFICIENT_TRADES",
        "EXCESSIVE_COST_BURDEN",
        "UNACCEPTABLE_RISK",
        "ECONOMICALLY_NOT_SUPPORTED",
        "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE",
    )
    # Several failures at once: the earliest outcome in the locked order is the one reported.
    tight = dataclasses.replace(POLICY, max_median_cost_burden=1e-6)

    def build(_index: int) -> tuple[list[float], list[float]]:
        opens, closes = _trend(0.002)
        closes[2] = opens[2] * 0.70
        return opens, closes

    assert _evaluate(_inputs(build=build), policy=tight)["rule_outcome"] == "EXCESSIVE_COST_BURDEN"
    assert (
        _evaluate(_inputs(build=build), policy=tight, comparator=None)["rule_outcome"]
        == "NOT_EVALUABLE"
    )


# ---------------------------------------------------------------------------
# Structural aborts and determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 6, 8])
def test_wrong_fold_count_aborts(count: int) -> None:
    folds = _inputs(folds=7)
    folds = (folds * 2)[:count]
    with pytest.raises(EconomicStructuralError, match="economic_fold_output_count_invalid"):
        _evaluate(folds)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_invalid_probabilities_abort(bad: Any) -> None:
    folds = _inputs()
    probabilities = dict(folds[3].probabilities)
    probabilities["candidate"] = (*probabilities["candidate"][:-1], bad)
    folds[3] = FoldInput(probabilities, folds[3].opens, folds[3].closes)
    with pytest.raises(EconomicStructuralError, match="economic_probability_invalid"):
        _evaluate(folds)


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_invalid_prices_abort(bad: float) -> None:
    folds = _inputs()
    opens = list(folds[0].opens)
    opens[7] = bad
    folds[0] = FoldInput(folds[0].probabilities, tuple(opens), folds[0].closes)
    with pytest.raises(EconomicStructuralError, match="economic_nonpositive_or_nonfinite_price"):
        _evaluate(folds)


def test_window_probability_and_model_set_mismatches_abort() -> None:
    folds = _inputs()
    with pytest.raises(EconomicStructuralError, match="economic_window_length_invalid"):
        _evaluate(
            [
                FoldInput(folds[0].probabilities, folds[0].opens[:-1], folds[0].closes[:-1]),
                *folds[1:],
            ]
        )
    short = dict(folds[0].probabilities)
    short["gradient_boosting"] = short["gradient_boosting"][:-1]
    with pytest.raises(EconomicStructuralError, match="economic_probability_length_invalid"):
        _evaluate([FoldInput(short, folds[0].opens, folds[0].closes), *folds[1:]])
    missing = {k: v for k, v in folds[0].probabilities.items() if k != "gradient_boosting"}
    with pytest.raises(EconomicStructuralError, match="economic_model_set_invalid"):
        _evaluate([FoldInput(missing, folds[0].opens, folds[0].closes), *folds[1:]])


def test_evaluation_is_deterministic_and_sanitized() -> None:
    first = json.dumps(_evaluate(_inputs()), sort_keys=True, allow_nan=False)
    second = json.dumps(_evaluate(_inputs()), sort_keys=True, allow_nan=False)
    assert first == second

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    forbidden = {
        "probabilities",
        "row_probabilities",
        "predicted_probabilities",
        "feature_values",
        "features",
        "prices",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    assert not keys(json.loads(first)) & forbidden


def test_module_cannot_fit_read_data_touch_the_network_or_grant_authority() -> None:
    path = ROOT / "src/spy_market_agent/research/retrospective_economics.py"
    tree = ast.parse(path.read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "sklearn",
        "spy_market_agent",
        "pandas",
        "numpy",
        "socket",
        "os",
        "subprocess",
        "requests",
        "alpaca",
        "httpx",
    )
    assert not [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
    text = path.read_text()
    for needle in (
        "environ",
        "getenv",
        "fit(",
        "predict_proba",
        "read_csv",
        "open(",
        "write_text",
        "write_bytes",
    ):
        assert needle not in text
    result = _evaluate(_inputs())
    assert result["execution_authority"] == "none"
    assert result["disposition"] == "NO CANDIDATE PROMOTION"


# ---------------------------------------------------------------------------
# R1 — decisive severe-cost median over all seven valid folds (owner approval fef354a2)
# ---------------------------------------------------------------------------


def _mixed_abstention_inputs(*, positive: int, negative: int) -> list[FoldInput]:
    """Seven folds: ``positive`` winners and ``negative`` losers trade; the rest abstain."""
    folds = []
    for index in range(7):
        if index < positive:
            opens, closes = _trend(0.002)
            candidate = 0.9
        elif index < positive + negative:
            opens, closes = _flat(ROWS + 5)
            candidate = 0.9
        else:
            opens, closes = _trend(0.002)
            candidate = 0.1  # abstains: valid fold, net return exactly zero
        probabilities = dict.fromkeys(MODELS, (0.1,) * ROWS)
        probabilities["candidate"] = (candidate,) * ROWS
        folds.append(FoldInput(probabilities, tuple(opens), tuple(closes)))
    return folds


def test_traded_median_passes_but_all_fold_median_fails_so_the_severe_rule_fails() -> None:
    # Five traded folds (3 positive, 2 negative) plus two abstained zeros: traded median > 0,
    # all-seven median == 0, which is not strictly positive.
    result = _evaluate(_mixed_abstention_inputs(positive=3, negative=2))
    severe = result["candidate_aggregate"]["20x"]
    assert severe["abstained_folds"] == 2
    assert severe["median_net_return_traded_folds"] > 0.0  # descriptive, retained
    assert severe["median_net_return_all_folds"] == 0.0
    check = _check(result, "severe_median_all_fold_net_return_positive")
    assert check["observed"] == 0.0
    assert check["required"] == "> 0"
    assert check["passed"] is False
    assert result["rule_outcome"] == "EXCESSIVE_COST_BURDEN"
    assert _check(result, "min_pooled_round_trips")["passed"]  # trade minimums are not the cause


def test_all_fold_median_passes_when_a_strict_majority_of_valid_folds_is_positive() -> None:
    result = _evaluate(_mixed_abstention_inputs(positive=4, negative=1))
    check = _check(result, "severe_median_all_fold_net_return_positive")
    assert check["observed"] > 0.0
    assert check["passed"] is True
    assert result["candidate_aggregate"]["20x"]["median_net_return_all_folds"] > 0.0


def test_base_cost_burden_remains_a_traded_fold_median() -> None:
    result = _evaluate(_mixed_abstention_inputs(positive=3, negative=2))
    aggregate = result["candidate_aggregate"]["1x"]
    burden = _check(result, "base_median_traded_fold_cost_burden")
    traded = [
        fold["cost_burden_fraction"]
        for fold in result["models"]["candidate"]["1x"]["folds"]
        if not fold["abstained"]
    ]
    assert len(traded) == 5
    assert burden["observed"] == pytest.approx(statistics.median(traded))
    assert aggregate["median_cost_burden_traded_folds"] == burden["observed"]


# ---------------------------------------------------------------------------
# R2 — complete preregistered diagnostics (aggregate only; no new thresholds)
# ---------------------------------------------------------------------------


def _hand_marks(cost: float) -> list[float]:
    """Marks of one P-A trade (0 -> 5) in a flat 11-session window with a 10% dip at session 2."""
    entry_units = UNITS / (100.0 * (1.0 + cost))
    held = [entry_units * 100.0 * (0.9 if k == 2 else 1.0) for k in range(5)]
    exit_equity = entry_units * 100.0 * (1.0 - cost)
    return [UNITS, *held, *([exit_equity] * 6), exit_equity][:12]


def test_volatility_downside_drawdown_duration_and_tail_diagnostics_match_hand_marks() -> None:
    opens, closes = _flat(11)
    closes[2] = 90.0
    cost = POLICY.side_cost(1)
    sim = _simulate([(0, 5)], opens, closes)
    marks = _hand_marks(cost)
    assert len(marks) == 12
    daily = [marks[k] / marks[k - 1] - 1.0 for k in range(1, len(marks))]
    assert sim.daily_volatility == pytest.approx(statistics.stdev(daily), rel=1e-9)
    assert sim.downside_deviation == pytest.approx(
        math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in daily)), rel=1e-9
    )
    # Every mark after the initial one sits below the initial peak (costs, then the dip).
    assert sim.sessions_under_water == 11
    assert sim.drawdown_duration_sessions == 11
    expected_five = min(marks[k + 5] / marks[k] - 1.0 for k in range(len(marks) - 5))
    assert sim.worst_five_session_return is not None
    assert sim.worst_five_session_return == pytest.approx(expected_five, rel=1e-9)
    assert sim.worst_five_session_return < 0.0


def test_time_under_water_resets_after_a_new_peak() -> None:
    opens, closes = _trend(0.01, rows=6, dips=False)
    sim = _simulate([(0, 5)], opens, closes)
    assert sim.sessions_under_water < len(opens)  # new peaks occur while the index rises
    assert sim.drawdown_duration_sessions <= sim.sessions_under_water
    flat_opens, flat_closes = _flat(11)
    abstained = _simulate([], flat_opens, flat_closes)
    assert (abstained.sessions_under_water, abstained.drawdown_duration_sessions) == (0, 0)


def test_commission_slippage_and_notional_cost_diagnostics_are_separate_and_consistent() -> None:
    opens, closes = _flat(11)
    for multiplier in (1, 5, 20):
        sim = _simulate([(0, 5)], opens, closes, multiplier)
        record = eco._fold_record(POLICY, sim, True)
        total = record["total_transaction_cost"]
        assert record["commission_cost"] == pytest.approx(total / 3.0)  # 0.125 of 0.375 bps
        assert record["slippage_cost"] == pytest.approx(total * 2.0 / 3.0)
        assert record["commission_cost"] + record["slippage_cost"] == pytest.approx(total)
        # Cost over execution notional equals the per-side rate in bps by construction.
        assert record["transaction_cost_bps_of_notional"]["value"] == pytest.approx(
            0.375 * multiplier, rel=1e-9
        )
    idle = eco._fold_record(POLICY, _simulate([], opens, closes), True)
    assert idle["transaction_cost_bps_of_notional"] == {
        "value": None,
        "undefined_reason": "no_execution_notional",
    }
    assert idle["commission_cost"] == idle["slippage_cost"] == 0.0


def test_cost_negation_is_flagged_per_fold_and_counted() -> None:
    thin = _inputs(build=lambda _i: _trend(0.00001, dips=False))  # ~0.5 bp gross < 0.75 bp costs
    aggregate = _evaluate(thin)["candidate_aggregate"]["1x"]
    result = _evaluate(thin)
    folds = result["models"]["candidate"]["1x"]["folds"]
    assert all(f["cost_negated"] for f in folds)
    assert all(f["gross_return"] > 0.0 >= f["net_return"] for f in folds)
    assert aggregate["cost_negated_folds"] == 7
    assert aggregate["cost_negated_fold_indices"] == list(range(7))
    assert all(f["cost_drag"] == pytest.approx(f["net_return"] - f["gross_return"]) for f in folds)
    healthy = _evaluate(_inputs())["candidate_aggregate"]["1x"]
    assert (healthy["cost_negated_folds"], healthy["cost_negated_fold_indices"]) == (0, [])
    abstained = _evaluate(_inputs(candidate_p=0.1))["models"]["candidate"]["1x"]["folds"]
    assert not any(f["cost_negated"] for f in abstained)  # zero gross is not "negated"


def test_paired_benchmark_differences_are_reported_per_fold_at_every_multiplier() -> None:
    result = _evaluate(_mixed_abstention_inputs(positive=3, negative=2))
    paired = result["paired_benchmark_differences"]
    assert set(paired) == {"1x", "5x", "20x"}
    candidate = result["models"]["candidate"]["1x"]["folds"]
    for label, benchmark in (
        ("B0", result["benchmarks"]["B0"]["1x"]["folds"]),
        ("B1", result["benchmarks"]["B1"]["1x"]["folds"]),
        ("B2", result["models"]["training_prevalence"]["1x"]["folds"]),
        ("B3", result["models"][COMPARATOR]["1x"]["folds"]),
    ):
        entry = paired["1x"][label]
        expected = [
            c["net_return"] - b["net_return"] for c, b in zip(candidate, benchmark, strict=True)
        ]
        assert entry["differences"] == pytest.approx(expected)
        assert entry["folds_compared"] == 7
        assert entry["positive_folds"] == sum(1 for d in expected if d > 0.0)
        assert entry["median_difference"] == pytest.approx(statistics.median(expected))
        assert (entry["min_difference"], entry["max_difference"]) == (min(expected), max(expected))
    assert paired["1x"]["B0"]["differences"] == pytest.approx(
        [f["net_return"] for f in candidate]
    )  # B-0 earns exactly zero
    comparison = _check(result, "paired_B0")["observed"]
    assert comparison["positive_folds"] == paired["1x"]["B0"]["positive_folds"]


def test_missing_designated_comparator_makes_the_b3_pairing_undefined() -> None:
    result = _evaluate(_inputs(), comparator=None)
    assert result["paired_benchmark_differences"]["1x"]["B3"] == {
        "undefined_reason": "designated_comparator_unavailable"
    }
    assert result["rule_outcome"] == "NOT_EVALUABLE"


def test_trade_return_statistics_hit_rate_and_losing_streak() -> None:
    returns = [0.01, -0.02, -0.01, 0.03, -0.005, -0.004, -0.003, 0.02]
    stats = eco._trade_statistics(returns, True)
    assert stats["count"] == 8
    assert stats["mean"]["value"] == pytest.approx(statistics.fmean(returns))
    assert stats["median"]["value"] == pytest.approx(statistics.median(returns))
    assert (stats["best"]["value"], stats["worst"]["value"]) == (0.03, -0.02)
    assert stats["hit_rate"]["value"] == pytest.approx(3 / 8)
    assert stats["longest_losing_streak"]["value"] == 3


def test_trade_statistics_are_undefined_with_a_reason_never_zero() -> None:
    empty = eco._trade_statistics([], True)
    below = eco._trade_statistics([0.01, -0.01], False)
    for stats, reason in (
        (empty, "no_completed_round_trips"),
        (below, "pooled_round_trips_below_minimum"),
    ):
        for name in ("mean", "median", "best", "worst", "hit_rate", "longest_losing_streak"):
            assert stats[name] == {"value": None, "undefined_reason": reason}
    assert below["count"] == 2  # the count itself is a fact, not a statistic


def test_per_trade_statistics_are_undefined_below_the_pooled_trade_minimum() -> None:
    few = _evaluate(_inputs(build=None, candidate_p=0.1))  # candidate never trades
    pooled = few["candidate_aggregate"]["1x"]["pooled_trade_statistics"]
    assert pooled["count"] == 0
    assert pooled["worst"]["undefined_reason"] == "no_completed_round_trips"
    enough = _evaluate(_inputs())["candidate_aggregate"]["1x"]["pooled_trade_statistics"]
    assert enough["count"] == 70
    assert enough["hit_rate"]["value"] is not None
    assert enough["worst"]["value"] is not None
    insufficient = _evaluate(_mixed_abstention_inputs(positive=2, negative=2))
    aggregate = insufficient["candidate_aggregate"]["1x"]
    assert aggregate["round_trips_pooled"] < POLICY.min_pooled_round_trips
    assert aggregate["pooled_trade_statistics"]["mean"]["undefined_reason"] == (
        "pooled_round_trips_below_minimum"
    )
    for fold in insufficient["models"]["candidate"]["1x"]["folds"]:
        if fold["round_trips"]:
            assert fold["trade_statistics"]["worst"]["undefined_reason"] == (
                "pooled_round_trips_below_minimum"
            )


def test_expected_shortfall_is_undefined_below_the_approved_minimum_sample() -> None:
    returns = [-0.01 * (k + 1) for k in range(49)]
    tail = eco._worst_decile(POLICY, returns)
    assert tail["sample"] == 49
    assert tail["minimum_sample"] == 50
    assert tail["sufficient_sample"] is False
    assert tail["mean"] is None
    assert tail["undefined_reason"] == "tail_sample_below_minimum"
    assert tail["measure"] == "expected_shortfall_of_pooled_trade_returns"
    ok = eco._worst_decile(POLICY, [*returns, -0.5])
    assert ok["sufficient_sample"] is True
    assert ok["worst_count"] == 5
    assert ok["mean"] == pytest.approx(statistics.fmean(sorted([*returns, -0.5])[:5]))
    assert ok["undefined_reason"] is None


def test_per_fold_dispersion_reports_min_median_max_of_every_metric() -> None:
    result = _evaluate(_mixed_abstention_inputs(positive=3, negative=2))
    aggregate = result["candidate_aggregate"]["1x"]
    folds = result["models"]["candidate"]["1x"]["folds"]
    dispersion = aggregate["dispersion"]
    for name in ("net_return", "max_drawdown", "exposure_fraction", "turnover_ratio"):
        values = [f[name] for f in folds]
        assert dispersion[name]["min"] == min(values)
        assert dispersion[name]["max"] == max(values)
        assert dispersion[name]["median"] == pytest.approx(statistics.median(values))
        assert dispersion[name]["defined_folds"] == 7
    for name in (
        "gross_return",
        "cost_drag",
        "drawdown_duration_sessions",
        "sessions_under_water",
        "round_trips",
        "total_transaction_cost",
        "commission_cost",
        "slippage_cost",
        "cost_burden_fraction",
        "transaction_cost_bps_of_notional",
        "daily_volatility",
        "downside_deviation",
        "worst_five_session_return",
        "sharpe_like",
        "sortino_like",
        "return_over_max_drawdown",
        "break_even_per_side_bps",
        "trade_mean_net_return",
        "trade_hit_rate",
        "worst_trade_net_return",
    ):
        assert set(dispersion[name]) == {
            "defined_folds",
            "min",
            "median",
            "max",
            "undefined_reason",
        }
    # Abstained folds have no execution notional, so that metric is defined for traded folds only.
    assert dispersion["transaction_cost_bps_of_notional"]["defined_folds"] == 5
    assert dispersion["round_trips"]["min"] == 0


def test_dispersion_of_a_metric_undefined_in_every_fold_is_explicit() -> None:
    result = _evaluate(_inputs(candidate_p=0.1))
    dispersion = result["candidate_aggregate"]["1x"]["dispersion"][
        "transaction_cost_bps_of_notional"
    ]
    assert dispersion == {
        "defined_folds": 0,
        "min": None,
        "median": None,
        "max": None,
        "undefined_reason": "undefined_in_every_fold",
    }


def test_new_diagnostics_are_aggregate_only_and_add_no_decision_inputs() -> None:
    first = _evaluate(_inputs())
    second = _evaluate(_inputs())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    # Diagnostics never change which checks decide the outcome.
    ids = [c["id"] for c in first["checks"]]
    assert ids == [
        "min_pooled_round_trips",
        "min_traded_folds_with_trade_minimum",
        "base_median_traded_fold_cost_burden",
        "severe_median_all_fold_net_return_positive",
        "fold_max_drawdown_at_1x",
        "pooled_worst_decile_mean_at_1x",
        "fold_max_drawdown_at_20x",
        "pooled_worst_decile_mean_at_20x",
        "paired_B0",
        "paired_B2",
        "paired_B3",
        "drawdown_guard_vs_B1",
    ]
