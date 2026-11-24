"""AQ-P3C-R economic-validation evaluator (RIC-152 D11): pure, offline, in-memory.

Implements ONLY the owner-approved, locked economic protocol (policy ``P-A``,
adjusted-price-index accounting) over the out-of-fold probabilities of the existing
five-model comparison. It never fits or scores a model, never reads market data, and
returns sanitized aggregate metrics. Every result is an *adjusted-price-index hypothetical*:
it supports no claim about dollar profit, share affordability, dividends or broker
execution quality, and it grants no authority::

    execution_authority = none
    disposition = NO CANDIDATE PROMOTION

The numerical policy lives in the versioned lock file
``configs/research/aq_p3cr_economic_policy_lock_v1.json``; its SHA-256 and the SHA-256 of the
merged specification are pinned below and are checked (and bound into the experiment
identity) before anything is computed. A missing decisive metric can never produce a
favorable outcome.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Final

ECONOMIC_POLICY_ID: Final = "aq-p3cr-economic-policy-v1"
ECONOMIC_LOCK_SCHEMA: Final = "aq-p3cr-economic-policy-lock-v1"
ECONOMIC_LOCK_RELATIVE: Final = "configs/research/aq_p3cr_economic_policy_lock_v1.json"
ECONOMIC_SPEC_RELATIVE: Final = "docs/AQ_P3C_R_ECONOMIC_VALIDATION_PREREGISTRATION.md"
# SHA-256 of the specification as squash-merged by PR #103 (a0b59bf) and of the lock file.
ECONOMIC_SPEC_SHA256: Final = "8c22e9fbd507a6728a172d9209802a70ef2c77253b870fa6433d4d15a459424f"
ECONOMIC_LOCK_SHA256: Final = "5c4e67c6e076f080b3040c2ef40d4ab9e04098c085d9fbdc654ec97fa0bc94aa"
EVIDENCE_LABEL: Final = "adjusted_price_index_hypothetical"
HOLDING_SESSIONS: Final = 5  # t+1 open to t+6 open, the frozen label horizon
OUTCOME_ORDER: Final = (
    "NOT_EVALUABLE",
    "NO_TRADES",
    "INSUFFICIENT_TRADES",
    "EXCESSIVE_COST_BURDEN",
    "UNACCEPTABLE_RISK",
    "ECONOMICALLY_NOT_SUPPORTED",
    "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE",
)
NON_DECISIONAL: Final = "NON_DECISIONAL_DIAGNOSTIC_ONLY"

ECONOMIC_LIMITATIONS: Final = (
    "Economic results are an adjusted-price-index hypothetical on retrospectively adjusted "
    "daily prices: they are not dollar profit and loss, not whole-share affordability, not "
    "dividend income and not broker execution quality, and the frozen per-side cost is an "
    "assumption rather than an estimate of true cost. Fold results are chronologically "
    "dependent, trade counts are small, and several models and benchmarks are compared on the "
    "same data. Classification improvement does not establish economic value, and a favorable "
    "descriptive outcome cannot substitute for fresh, independently protected validation."
)


class EconomicPolicyError(ValueError):
    """The locked policy or its bound specification failed verification."""


class EconomicStructuralError(ValueError):
    """A structurally invalid fold output: the whole evaluation aborts."""


def _policy_fail(code: str) -> EconomicPolicyError:
    return EconomicPolicyError(code)


def _structural(code: str) -> EconomicStructuralError:
    return EconomicStructuralError(code)


# ---------------------------------------------------------------------------
# Locked policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EconomicPolicy:
    policy_id: str
    signal_threshold: float
    initial_index_units: float
    annualization_sessions: int
    commission_bps_per_side: Decimal
    slippage_bps_per_side: Decimal
    multipliers: tuple[int, ...]
    base_multiplier: int
    severe_multiplier: int
    required_fold_outputs: int
    min_pooled_round_trips: int
    min_traded_folds: int
    min_round_trips_per_traded_fold: int
    max_median_cost_burden: float
    max_fold_drawdown: float
    worst_decile_fraction: float
    min_worst_decile_mean: float
    min_tail_sample: int
    risk_multipliers: tuple[int, ...]
    paired_benchmarks: tuple[str, ...]
    min_positive_paired_folds: int
    specification_sha256: str
    lock_sha256: str

    def side_cost(self, multiplier: int) -> float:
        with localcontext() as context:  # independent of the caller's precision and rounding
            context.prec = 60
            base = (self.commission_bps_per_side + self.slippage_bps_per_side) / Decimal(10000)
            return float(base * multiplier)

    def commission_share(self) -> float:
        """Fraction of every transaction cost that is commission (scales with the multiplier)."""
        with localcontext() as context:
            context.prec = 60
            total = self.commission_bps_per_side + self.slippage_bps_per_side
            return float(self.commission_bps_per_side / total) if total > 0 else 0.0


_TOP_KEYS: Final = frozenset(
    {
        "schema",
        "policy_id",
        "status",
        "execution_authority",
        "disposition",
        "owner_approval",
        "specification",
        "classification_screen_id",
        "evidence_label",
        "policy",
        "signal_threshold",
        "accounting",
        "costs",
        "fold_validity",
        "trade_minimums",
        "cost_criteria",
        "risk_criteria",
        "comparison_criteria",
        "outcome_order",
        "decisional_only_if_classification_screen",
        "implementation_interpretations",
    }
)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _policy_fail("lock_duplicate_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise _policy_fail("lock_nonfinite_number")


def _section(lock: Mapping[str, Any], name: str, keys: frozenset[str]) -> Mapping[str, Any]:
    value = lock[name]
    if type(value) is not dict or set(value) != keys:
        raise _policy_fail(f"lock_section_invalid:{name}")
    return value


def _number(value: Any, code: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise _policy_fail(code)
    if minimum is not None and value < minimum:
        raise _policy_fail(code)
    return float(value)


def _integer(value: Any, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _policy_fail(code)
    return value


def _integer_tuple(value: Any, code: str) -> tuple[int, ...]:
    if type(value) is not list or not value:
        raise _policy_fail(code)
    return tuple(_integer(item, code, minimum=1) for item in value)


def parse_economic_policy(lock_bytes: bytes) -> EconomicPolicy:
    """Strictly parse the lock; any unexpected field, type or value is refused."""
    try:
        lock = json.loads(
            lock_bytes.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
        )
    except EconomicPolicyError:
        raise
    except (ValueError, UnicodeDecodeError):
        raise _policy_fail("lock_not_valid_json") from None
    if type(lock) is not dict or set(lock) != _TOP_KEYS:
        raise _policy_fail("lock_fields_invalid")
    if (
        lock["schema"] != ECONOMIC_LOCK_SCHEMA
        or lock["policy_id"] != ECONOMIC_POLICY_ID
        or lock["execution_authority"] != "none"
        or lock["disposition"] != "NO CANDIDATE PROMOTION"
        or lock["classification_screen_id"] != "aq-p3cr-criteria-v1"
        or lock["evidence_label"] != EVIDENCE_LABEL
        or lock["policy"] != "P-A"
        or lock["decisional_only_if_classification_screen"] != "CRITERIA_MET"
        or type(lock["outcome_order"]) is not list
        or tuple(lock["outcome_order"]) != OUTCOME_ORDER
    ):
        raise _policy_fail("lock_identity_invalid")
    specification = _section(lock, "specification", frozenset({"path", "sha256"}))
    if (
        specification["path"] != ECONOMIC_SPEC_RELATIVE
        or specification["sha256"] != ECONOMIC_SPEC_SHA256
    ):
        raise _policy_fail("lock_specification_binding_invalid")
    accounting = _section(
        lock,
        "accounting",
        frozenset(
            {
                "initial_index_units",
                "fractional_units",
                "idle_return",
                "annualization_sessions",
                "reference_return",
            }
        ),
    )
    if (
        accounting["fractional_units"] is not True
        or accounting["idle_return"] != 0
        or accounting["reference_return"] != 0
    ):
        raise _policy_fail("lock_accounting_invalid")
    costs = _section(
        lock,
        "costs",
        frozenset(
            {
                "commission_bps_per_side",
                "slippage_bps_per_side",
                "multipliers",
                "base_multiplier",
                "severe_multiplier",
            }
        ),
    )
    multipliers = _integer_tuple(costs["multipliers"], "lock_cost_multipliers_invalid")
    base = _integer(costs["base_multiplier"], "lock_cost_multipliers_invalid", minimum=1)
    severe = _integer(costs["severe_multiplier"], "lock_cost_multipliers_invalid", minimum=1)
    if base != 1 or base not in multipliers or severe not in multipliers or severe <= base:
        raise _policy_fail("lock_cost_multipliers_invalid")
    folds = _section(
        lock, "fold_validity", frozenset({"required_fold_outputs", "abstained_fold_is_valid"})
    )
    if folds["abstained_fold_is_valid"] is not True:
        raise _policy_fail("lock_fold_validity_invalid")
    minimums = _section(
        lock,
        "trade_minimums",
        frozenset({"pooled_round_trips", "traded_folds", "round_trips_per_traded_fold"}),
    )
    cost_criteria = _section(
        lock,
        "cost_criteria",
        frozenset({"max_median_traded_fold_cost_burden", "severe_median_all_fold_net_return"}),
    )
    if cost_criteria["severe_median_all_fold_net_return"] != "strictly_positive":
        raise _policy_fail("lock_cost_criteria_invalid")
    risk = _section(
        lock,
        "risk_criteria",
        frozenset(
            {
                "max_fold_drawdown",
                "worst_decile_fraction",
                "min_pooled_worst_decile_mean",
                "minimum_tail_sample",
                "applied_at_multipliers",
            }
        ),
    )
    risk_multipliers = _integer_tuple(risk["applied_at_multipliers"], "lock_risk_invalid")
    if not set(risk_multipliers) <= set(multipliers):
        raise _policy_fail("lock_risk_invalid")
    comparison = _section(
        lock,
        "comparison_criteria",
        frozenset(
            {
                "paired_benchmarks",
                "median_paired_net_return_difference",
                "min_positive_paired_folds",
                "drawdown_guard",
            }
        ),
    )
    if (
        type(comparison["paired_benchmarks"]) is not list
        or tuple(comparison["paired_benchmarks"]) != ("B0", "B2", "B3")
        or comparison["median_paired_net_return_difference"] != "strictly_positive"
        or comparison["drawdown_guard"] != "candidate_median_fold_max_drawdown_not_above_B1_median"
    ):
        raise _policy_fail("lock_comparison_invalid")
    threshold = _number(lock["signal_threshold"], "lock_threshold_invalid", minimum=0.0)
    if not 0.0 < threshold < 1.0:
        raise _policy_fail("lock_threshold_invalid")
    decile = _number(risk["worst_decile_fraction"], "lock_risk_invalid", minimum=0.0)
    if not 0.0 < decile <= 1.0:
        raise _policy_fail("lock_risk_invalid")
    for text in ("commission_bps_per_side", "slippage_bps_per_side"):
        if type(costs[text]) is not str:
            raise _policy_fail("lock_costs_invalid")
    try:
        commission = Decimal(costs["commission_bps_per_side"])
        slippage = Decimal(costs["slippage_bps_per_side"])
    except ArithmeticError:
        raise _policy_fail("lock_costs_invalid") from None
    if not (commission.is_finite() and slippage.is_finite()) or commission < 0 or slippage < 0:
        raise _policy_fail("lock_costs_invalid")
    return EconomicPolicy(
        policy_id=ECONOMIC_POLICY_ID,
        signal_threshold=threshold,
        initial_index_units=_number(
            accounting["initial_index_units"], "lock_accounting_invalid", minimum=1.0
        ),
        annualization_sessions=_integer(
            accounting["annualization_sessions"], "lock_accounting_invalid", minimum=1
        ),
        commission_bps_per_side=commission,
        slippage_bps_per_side=slippage,
        multipliers=multipliers,
        base_multiplier=base,
        severe_multiplier=severe,
        required_fold_outputs=_integer(
            folds["required_fold_outputs"], "lock_fold_validity_invalid", minimum=1
        ),
        min_pooled_round_trips=_integer(
            minimums["pooled_round_trips"], "lock_trade_minimums_invalid", minimum=1
        ),
        min_traded_folds=_integer(
            minimums["traded_folds"], "lock_trade_minimums_invalid", minimum=1
        ),
        min_round_trips_per_traded_fold=_integer(
            minimums["round_trips_per_traded_fold"], "lock_trade_minimums_invalid", minimum=1
        ),
        max_median_cost_burden=_number(
            cost_criteria["max_median_traded_fold_cost_burden"],
            "lock_cost_criteria_invalid",
            minimum=0.0,
        ),
        max_fold_drawdown=_number(risk["max_fold_drawdown"], "lock_risk_invalid", minimum=0.0),
        worst_decile_fraction=decile,
        min_worst_decile_mean=_number(risk["min_pooled_worst_decile_mean"], "lock_risk_invalid"),
        min_tail_sample=_integer(risk["minimum_tail_sample"], "lock_risk_invalid", minimum=1),
        risk_multipliers=risk_multipliers,
        paired_benchmarks=("B0", "B2", "B3"),
        min_positive_paired_folds=_integer(
            comparison["min_positive_paired_folds"], "lock_comparison_invalid", minimum=1
        ),
        specification_sha256=ECONOMIC_SPEC_SHA256,
        lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
    )


def _read_pinned(path: Path, expected_sha256: str, code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise _policy_fail(code)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise _policy_fail(code)
    return data


def load_economic_policy(repository_root: Path) -> EconomicPolicy:
    """Verify the pinned specification and lock bytes, then parse the lock strictly."""
    _read_pinned(
        repository_root / ECONOMIC_SPEC_RELATIVE,
        ECONOMIC_SPEC_SHA256,
        "economic_specification_digest_mismatch",
    )
    lock_bytes = _read_pinned(
        repository_root / ECONOMIC_LOCK_RELATIVE,
        ECONOMIC_LOCK_SHA256,
        "economic_lock_digest_mismatch",
    )
    return parse_economic_policy(lock_bytes)


def policy_identity(policy: EconomicPolicy) -> dict[str, Any]:
    """Binds the approved specification and policy lock into the experiment identity."""
    return {
        "policy_id": policy.policy_id,
        "evidence_label": EVIDENCE_LABEL,
        "specification_path": ECONOMIC_SPEC_RELATIVE,
        "specification_sha256": policy.specification_sha256,
        "lock_path": ECONOMIC_LOCK_RELATIVE,
        "lock_sha256": policy.lock_sha256,
        "signal_threshold": policy.signal_threshold,
        "cost_multipliers": list(policy.multipliers),
    }


# ---------------------------------------------------------------------------
# Position policy P-A and index-unit accounting
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoldSimulation:
    """One fold, one policy, one cost level. In-memory only; never serialized as-is."""

    round_trips: int
    long_signals: int
    ignored_in_position: int
    net_return: float
    gross_return: float
    max_drawdown: float
    exposure_fraction: float
    turnover_ratio: float
    total_transaction_cost: float
    cost_burden_fraction: float
    terminal_index_units: float
    trade_net_returns: tuple[float, ...]
    sharpe_like: float | None
    sortino_like: float | None
    return_over_max_drawdown: float | None
    break_even_per_side_bps: float | None
    reference_notional: float
    daily_volatility: float
    downside_deviation: float
    drawdown_duration_sessions: int
    sessions_under_water: int
    worst_five_session_return: float | None


def p_a_trades(signals: Sequence[bool]) -> tuple[list[tuple[int, int]], int, int]:
    """Policy P-A: one position at a time; in-position signals are rejected.

    Window index ``j`` is the entry session of prediction row ``j`` (``t+1``); its exit is at
    window index ``j+5`` (``t+6``). A signal made at the close of row ``j`` is eligible only
    if the account is flat with no pending entry at that close, so after an entry at ``j``
    the next eligible signal is row ``j+6`` (the exit session's own close).
    """
    trades: list[tuple[int, int]] = []
    free = 0
    ignored = 0
    long_signals = 0
    for row, long_signal in enumerate(signals):
        if not long_signal:
            continue
        long_signals += 1
        if row < free:
            ignored += 1
            continue
        trades.append((row, row + HOLDING_SESSIONS))
        free = row + HOLDING_SESSIONS + 1
    return trades, ignored, long_signals


def _finite_positive(values: Sequence[float], code: str) -> list[float]:
    result = [float(v) for v in values]
    if any((not math.isfinite(v)) or v <= 0.0 for v in result):
        raise _structural(code)
    return result


def simulate_trades(
    trades: Sequence[tuple[int, int]],
    opens: Sequence[float],
    closes: Sequence[float],
    side_cost: float,
    initial_units: float,
    *,
    annualization_sessions: int,
    long_signals: int = 0,
    ignored_in_position: int = 0,
) -> FoldSimulation:
    """Fully invested index-unit accounting over one fold window (sessions ``0..L-1``).

    Marks: the initial units (before any fill at the first open), a close mark for each
    session ``0..L-2``, and a terminal mark at the open of session ``L-1`` after any exit
    fill. A trade enters at ``open[e] * (1 + c)`` and exits at ``open[x] * (1 - c)``.
    """
    open_path = _finite_positive(opens, "economic_nonpositive_or_nonfinite_price")
    close_path = _finite_positive(closes, "economic_nonpositive_or_nonfinite_price")
    length = len(open_path)
    if length != len(close_path) or length < 2:
        raise _structural("economic_window_length_invalid")
    if not (math.isfinite(side_cost) and 0.0 <= side_cost < 1.0):
        raise _structural("economic_cost_invalid")
    previous_exit = -1
    for entry, exit_ in trades:
        if not (previous_exit < entry < exit_ <= length - 1):
            raise _structural("economic_trade_schedule_invalid")
        previous_exit = exit_

    equity = initial_units
    cash_marks: list[float] = []
    held_sessions = 0
    reference_notional = 0.0
    transaction_cost = 0.0
    gross_multiple = 1.0
    net_multiples: list[float] = []
    cursor = 0
    plan = list(trades)
    current: tuple[int, int, float, float] | None = None  # entry, exit, units, equity before
    for session in range(length - 1):  # close marks for sessions 0..L-2
        # Open of this session: process an exit scheduled here, then an entry scheduled here.
        if current is not None and current[1] == session:
            _, exit_, units, before = current
            equity = units * open_path[exit_] * (1.0 - side_cost)
            reference_notional += units * open_path[exit_]
            transaction_cost += units * open_path[exit_] * side_cost
            net_multiples.append(equity / before)
            current = None
        if current is None and cursor < len(plan) and plan[cursor][0] == session:
            entry, exit_ = plan[cursor]
            cursor += 1
            units = equity / (open_path[entry] * (1.0 + side_cost))
            reference_notional += units * open_path[entry]
            transaction_cost += units * open_path[entry] * side_cost
            gross_multiple *= open_path[exit_] / open_path[entry]
            current = (entry, exit_, units, equity)
        if current is not None:
            cash_marks.append(current[2] * close_path[session])
            held_sessions += 1
        else:
            cash_marks.append(equity)
    # Terminal mark: the open of the last session, after any exit fill there.
    if current is not None:
        if current[1] != length - 1:
            raise _structural("economic_trade_schedule_invalid")
        _, exit_, units, before = current
        equity = units * open_path[exit_] * (1.0 - side_cost)
        reference_notional += units * open_path[exit_]
        transaction_cost += units * open_path[exit_] * side_cost
        net_multiples.append(equity / before)
        current = None
    if cursor != len(plan):
        raise _structural("economic_trade_schedule_invalid")

    marks = [initial_units, *cash_marks, equity]
    if any((not math.isfinite(m)) or m <= 0.0 for m in marks):
        raise _structural("economic_nonpositive_or_nonfinite_equity")
    expected = initial_units * math.prod(net_multiples)
    if not math.isclose(equity, expected, rel_tol=1e-9, abs_tol=1e-9):
        raise _structural("economic_accounting_identity_violated")
    peak = marks[0]
    drawdown = 0.0
    under_water = 0
    run = 0
    longest_run = 0
    for index, mark in enumerate(marks):
        peak = max(peak, mark)
        drawdown = max(drawdown, 1.0 - mark / peak)
        if index > 0 and mark < peak:
            under_water += 1
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 0
    five_session = [marks[k + 5] / marks[k] - 1.0 for k in range(len(marks) - 5)]
    daily = [marks[k] / marks[k - 1] - 1.0 for k in range(1, len(marks))]
    mean_daily = statistics.fmean(daily)
    volatility = statistics.stdev(daily) if len(daily) > 1 else 0.0
    downside = math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in daily))
    scale = math.sqrt(annualization_sessions)
    net_return = equity / initial_units - 1.0
    mean_equity = statistics.fmean(marks[1:])
    # Descriptive closed-form break-even per-side cost for a full-equity-per-trade policy:
    # net multiple = G * ((1 - c)/(1 + c))**n is strictly decreasing in c, so c* is unique.
    break_even: float | None = None
    if net_multiples and gross_multiple > 1.0:
        ratio = gross_multiple ** (-1.0 / len(net_multiples))
        break_even = (1.0 - ratio) / (1.0 + ratio) * 10_000.0
    return FoldSimulation(
        round_trips=len(net_multiples),
        long_signals=long_signals,
        ignored_in_position=ignored_in_position,
        net_return=net_return,
        gross_return=gross_multiple - 1.0,
        max_drawdown=drawdown,
        exposure_fraction=held_sessions / length,
        turnover_ratio=reference_notional / mean_equity,
        total_transaction_cost=transaction_cost,
        cost_burden_fraction=transaction_cost / initial_units,
        terminal_index_units=equity,
        trade_net_returns=tuple(m - 1.0 for m in net_multiples),
        sharpe_like=mean_daily / volatility * scale if volatility > 0.0 else None,
        sortino_like=mean_daily / downside * scale if downside > 0.0 else None,
        return_over_max_drawdown=net_return / drawdown if drawdown > 0.0 else None,
        break_even_per_side_bps=break_even,
        reference_notional=reference_notional,
        daily_volatility=volatility,
        downside_deviation=downside,
        drawdown_duration_sessions=longest_run,
        sessions_under_water=under_water,
        worst_five_session_return=min(five_session) if five_session else None,
    )


# ---------------------------------------------------------------------------
# Evaluation of the locked protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoldInput:
    """Out-of-fold probabilities (from the existing single fit) and the fold's price window.

    ``opens``/``closes`` cover window sessions ``0..n+4`` for ``n`` assessed predictions:
    session ``j`` is the entry session of prediction ``j`` and ``j+5`` its exit session.
    """

    probabilities: Mapping[str, Sequence[float]]
    opens: Sequence[float]
    closes: Sequence[float]


def _metric(value: float | None, reason: str) -> dict[str, Any]:
    return {"value": value, "undefined_reason": None if value is not None else reason}


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _longest_losing_streak(returns: Sequence[float]) -> int:
    longest = run = 0
    for value in returns:
        run = run + 1 if value < 0.0 else 0
        longest = max(longest, run)
    return longest


def _trade_statistics(returns: Sequence[float], defined: bool) -> dict[str, Any]:
    """Per-trade distribution and tail diagnostics; undefined below the pooled trade minimum."""
    reason = "no_completed_round_trips" if not returns else "pooled_round_trips_below_minimum"
    show = defined and bool(returns)

    def pick(value: float | int | None) -> dict[str, Any]:
        return _metric(float(value) if show and value is not None else None, reason)

    return {
        "count": len(returns),
        "mean": pick(statistics.fmean(returns) if returns else None),
        "median": pick(statistics.median(returns) if returns else None),
        "best": pick(max(returns) if returns else None),
        "worst": pick(min(returns) if returns else None),
        "hit_rate": pick(sum(1 for r in returns if r > 0.0) / len(returns) if returns else None),
        "longest_losing_streak": pick(_longest_losing_streak(returns) if returns else None),
    }


def _trade_statistics_defined(policy: EconomicPolicy, sims: Sequence[FoldSimulation]) -> bool:
    return sum(s.round_trips for s in sims) >= policy.min_pooled_round_trips


def _fold_record(
    policy: EconomicPolicy, sim: FoldSimulation, trade_defined: bool
) -> dict[str, Any]:
    share = policy.commission_share()
    notional_bps = (
        sim.total_transaction_cost / sim.reference_notional * 10_000.0
        if sim.reference_notional > 0.0
        else None
    )
    return {
        "round_trips": sim.round_trips,
        "long_signals": sim.long_signals,
        "ignored_in_position": sim.ignored_in_position,
        "abstained": sim.round_trips == 0,
        "net_return": sim.net_return,
        "gross_return": sim.gross_return,
        "cost_drag": sim.net_return - sim.gross_return,
        "cost_negated": sim.gross_return > 0.0 and sim.net_return <= 0.0,
        "max_drawdown": sim.max_drawdown,
        "drawdown_duration_sessions": sim.drawdown_duration_sessions,
        "sessions_under_water": sim.sessions_under_water,
        "exposure_fraction": sim.exposure_fraction,
        "turnover_ratio": sim.turnover_ratio,
        "total_transaction_cost": sim.total_transaction_cost,
        "commission_cost": sim.total_transaction_cost * share,
        "slippage_cost": sim.total_transaction_cost * (1.0 - share),
        "cost_burden_fraction": sim.cost_burden_fraction,
        "transaction_cost_bps_of_notional": _metric(notional_bps, "no_execution_notional"),
        "daily_volatility": sim.daily_volatility,
        "downside_deviation": sim.downside_deviation,
        "worst_five_session_return": _metric(sim.worst_five_session_return, "fewer_than_six_marks"),
        "sharpe_like": _metric(sim.sharpe_like, "zero_or_undefined_volatility"),
        "sortino_like": _metric(sim.sortino_like, "zero_downside_deviation"),
        "return_over_max_drawdown": _metric(sim.return_over_max_drawdown, "zero_drawdown"),
        "break_even_per_side_bps": _metric(
            sim.break_even_per_side_bps, "no_trades_or_gross_return_not_positive"
        ),
        "trade_statistics": _trade_statistics(sim.trade_net_returns, trade_defined),
    }


def _fold_records(policy: EconomicPolicy, sims: Sequence[FoldSimulation]) -> list[dict[str, Any]]:
    defined = _trade_statistics_defined(policy, sims)
    return [{"fold": index, **_fold_record(policy, s, defined)} for index, s in enumerate(sims)]


_DISPERSION_FIELDS: Final = (
    "net_return",
    "gross_return",
    "cost_drag",
    "max_drawdown",
    "drawdown_duration_sessions",
    "sessions_under_water",
    "exposure_fraction",
    "turnover_ratio",
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
)
_TRADE_DISPERSION_FIELDS: Final = (
    ("trade_mean_net_return", "mean"),
    ("trade_hit_rate", "hit_rate"),
    ("worst_trade_net_return", "worst"),
)


def _numeric(item: Any) -> float | None:
    value = item["value"] if isinstance(item, dict) else item
    return None if value is None else float(value)


def _dispersion(values: Sequence[float | None]) -> dict[str, Any]:
    defined = [v for v in values if v is not None]
    return {
        "defined_folds": len(defined),
        "min": min(defined) if defined else None,
        "median": statistics.median(defined) if defined else None,
        "max": max(defined) if defined else None,
        "undefined_reason": None if defined else "undefined_in_every_fold",
    }


def _worst_decile(policy: EconomicPolicy, returns: Sequence[float]) -> dict[str, Any]:
    """Expected shortfall of pooled trade returns at the locked tail fraction (D9)."""
    sample = len(returns)
    sufficient = sample >= policy.min_tail_sample
    count = math.ceil(policy.worst_decile_fraction * sample) if sample else 0
    mean = statistics.fmean(sorted(returns)[:count]) if count and sufficient else None
    return {
        "measure": "expected_shortfall_of_pooled_trade_returns",
        "tail_fraction": policy.worst_decile_fraction,
        "sample": sample,
        "minimum_sample": policy.min_tail_sample,
        "worst_count": count,
        "mean": mean,
        "sufficient_sample": sufficient,
        "undefined_reason": None if mean is not None else "tail_sample_below_minimum",
    }


def _aggregate(policy: EconomicPolicy, sims: Sequence[FoldSimulation]) -> dict[str, Any]:
    traded = [s for s in sims if s.round_trips > 0]
    pooled = [r for s in sims for r in s.trade_net_returns]
    defined = _trade_statistics_defined(policy, sims)
    records = [_fold_record(policy, s, defined) for s in sims]
    dispersion = {
        name: _dispersion([_numeric(record[name]) for record in records])
        for name in _DISPERSION_FIELDS
    }
    for name, field in _TRADE_DISPERSION_FIELDS:
        dispersion[name] = _dispersion(
            [_numeric(record["trade_statistics"][field]) for record in records]
        )
    negated = [index for index, record in enumerate(records) if record["cost_negated"]]
    return {
        "round_trips_pooled": sum(s.round_trips for s in sims),
        "ignored_in_position_pooled": sum(s.ignored_in_position for s in sims),
        "traded_folds": len(traded),
        "folds_meeting_trade_minimum": sum(
            1 for s in sims if s.round_trips >= policy.min_round_trips_per_traded_fold
        ),
        "abstained_folds": len(sims) - len(traded),
        "positive_net_return_folds": sum(1 for s in sims if s.net_return > 0.0),
        "cost_negated_folds": len(negated),
        "cost_negated_fold_indices": negated,
        "median_net_return_all_folds": _median([s.net_return for s in sims]),
        "median_net_return_traded_folds": _median([s.net_return for s in traded]),
        "median_fold_max_drawdown": _median([s.max_drawdown for s in sims]),
        "max_fold_max_drawdown": max(s.max_drawdown for s in sims),
        "median_cost_burden_traded_folds": _median([s.cost_burden_fraction for s in traded]),
        "max_cost_burden_traded_folds": max((s.cost_burden_fraction for s in traded), default=None),
        "pooled_trade_statistics": _trade_statistics(pooled, defined),
        "pooled_worst_decile": _worst_decile(policy, pooled),
        "dispersion": dispersion,
    }


def _paired(candidate: Sequence[FoldSimulation], other: Sequence[FoldSimulation]) -> dict[str, Any]:
    """Per-fold candidate-minus-benchmark net-return differences (descriptive, no p-value)."""
    differences = [c.net_return - o.net_return for c, o in zip(candidate, other, strict=True)]
    return {
        "differences": differences,
        "folds_compared": len(differences),
        "median_difference": statistics.median(differences),
        "min_difference": min(differences),
        "max_difference": max(differences),
        "positive_folds": sum(1 for d in differences if d > 0.0),
    }


def _check(check_id: str, observed: Any, required: Any, passed: bool) -> dict[str, Any]:
    return {"id": check_id, "observed": observed, "required": required, "passed": bool(passed)}


def evaluate_economics(
    policy: EconomicPolicy,
    folds: Sequence[FoldInput],
    *,
    models: Sequence[str],
    candidate: str,
    prevalence_model: str,
    designated_comparator: str | None,
    classification_screen_met: bool,
) -> dict[str, Any]:
    """Apply the locked protocol to existing out-of-fold probabilities. Pure; no fitting."""
    if len(folds) != policy.required_fold_outputs:
        raise _structural("economic_fold_output_count_invalid")
    if (
        candidate not in models
        or prevalence_model not in models
        or len(set(models)) != len(tuple(models))
    ):
        raise _structural("economic_model_set_invalid")
    sims: dict[str, dict[int, list[FoldSimulation]]] = {}
    benchmark_sims: dict[str, dict[int, list[FoldSimulation]]] = {"B0": {}, "B1": {}}
    for multiplier in policy.multipliers:
        cost = policy.side_cost(multiplier)
        per_model: dict[str, list[FoldSimulation]] = {name: [] for name in models}
        zero: list[FoldSimulation] = []
        hold: list[FoldSimulation] = []
        for fold in folds:
            if set(fold.probabilities) != set(models):
                raise _structural("economic_model_set_invalid")
            rows = len(fold.probabilities[candidate])
            if (
                rows < 1
                or len(fold.opens) != rows + HOLDING_SESSIONS
                or len(fold.closes) != len(fold.opens)
            ):
                raise _structural("economic_window_length_invalid")
            common = {"annualization_sessions": policy.annualization_sessions}
            for name in models:
                probabilities = fold.probabilities[name]
                if len(probabilities) != rows:
                    raise _structural("economic_probability_length_invalid")
                if any(
                    isinstance(p, bool)
                    or not isinstance(p, int | float)
                    or not math.isfinite(p)
                    or not 0.0 <= p <= 1.0
                    for p in probabilities
                ):
                    raise _structural("economic_probability_invalid")
                signals = [p > policy.signal_threshold for p in probabilities]
                trades, ignored, longs = p_a_trades(signals)
                per_model[name].append(
                    simulate_trades(
                        trades,
                        fold.opens,
                        fold.closes,
                        cost,
                        policy.initial_index_units,
                        long_signals=longs,
                        ignored_in_position=ignored,
                        **common,
                    )
                )
            zero.append(
                simulate_trades(
                    [], fold.opens, fold.closes, cost, policy.initial_index_units, **common
                )
            )
            hold.append(
                simulate_trades(
                    [(0, len(fold.opens) - 1)],
                    fold.opens,
                    fold.closes,
                    cost,
                    policy.initial_index_units,
                    **common,
                )
            )
        for name in models:
            sims.setdefault(name, {})[multiplier] = per_model[name]
        benchmark_sims["B0"][multiplier] = zero
        benchmark_sims["B1"][multiplier] = hold

    base, severe = policy.base_multiplier, policy.severe_multiplier
    cand_base = sims[candidate][base]
    cand_severe = sims[candidate][severe]
    aggregate_base = _aggregate(policy, cand_base)
    aggregate_severe = _aggregate(policy, cand_severe)

    missing: list[str] = []
    comparator_ok = designated_comparator is not None and designated_comparator in sims
    if not comparator_ok:
        missing.append("designated_comparator_unavailable")

    pooled = aggregate_base["round_trips_pooled"]
    meeting = aggregate_base["folds_meeting_trade_minimum"]
    trade_checks = [
        _check(
            "min_pooled_round_trips",
            pooled,
            policy.min_pooled_round_trips,
            pooled >= policy.min_pooled_round_trips,
        ),
        _check(
            "min_traded_folds_with_trade_minimum",
            meeting,
            policy.min_traded_folds,
            meeting >= policy.min_traded_folds,
        ),
    ]
    burden = aggregate_base["median_cost_burden_traded_folds"]
    # Decisive severe-cost median: all seven structurally valid folds, abstained folds at zero.
    # The traded-fold median stays in the aggregate as a descriptive metric only.
    severe_median = aggregate_severe["median_net_return_all_folds"]
    cost_checks = [
        _check(
            "base_median_traded_fold_cost_burden",
            burden,
            policy.max_median_cost_burden,
            burden is not None and burden <= policy.max_median_cost_burden,
        ),
        _check(
            "severe_median_all_fold_net_return_positive",
            severe_median,
            "> 0",
            severe_median is not None and severe_median > 0.0,
        ),
    ]
    risk_checks: list[dict[str, Any]] = []
    for multiplier in policy.risk_multipliers:
        aggregate = _aggregate(policy, sims[candidate][multiplier])
        tail = aggregate["pooled_worst_decile"]
        risk_checks.append(
            _check(
                f"fold_max_drawdown_at_{multiplier}x",
                aggregate["max_fold_max_drawdown"],
                policy.max_fold_drawdown,
                aggregate["max_fold_max_drawdown"] <= policy.max_fold_drawdown,
            )
        )
        tail_ok = tail["sufficient_sample"] and tail["mean"] is not None
        risk_checks.append(
            _check(
                f"pooled_worst_decile_mean_at_{multiplier}x",
                tail["mean"],
                policy.min_worst_decile_mean,
                tail_ok and tail["mean"] >= policy.min_worst_decile_mean,
            )
        )
        if not tail_ok and pooled >= policy.min_pooled_round_trips:
            missing.append(f"tail_evidence_unavailable_at_{multiplier}x")

    paired: dict[str, dict[str, Any]] = {}
    for multiplier in policy.multipliers:
        candidate_sims = sims[candidate][multiplier]
        by_label: dict[str, Any] = {
            "B0": _paired(candidate_sims, benchmark_sims["B0"][multiplier]),
            "B1": _paired(candidate_sims, benchmark_sims["B1"][multiplier]),
            "B2": _paired(candidate_sims, sims[prevalence_model][multiplier]),
        }
        by_label["B3"] = (
            _paired(candidate_sims, sims[designated_comparator][multiplier])
            if comparator_ok and designated_comparator is not None
            else {"undefined_reason": "designated_comparator_unavailable"}
        )
        paired[f"{multiplier}x"] = by_label
    comparison_checks: list[dict[str, Any]] = []
    for label in policy.paired_benchmarks:
        difference = paired[f"{base}x"][label]
        if "undefined_reason" in difference:
            comparison_checks.append(
                _check(f"paired_{label}", None, "median > 0 and positive folds", False)
            )
            continue
        median_difference = difference["median_difference"]
        positives = difference["positive_folds"]
        comparison_checks.append(
            _check(
                f"paired_{label}",
                {"median_difference": median_difference, "positive_folds": positives},
                {"median": "> 0", "positive_folds": policy.min_positive_paired_folds},
                median_difference > 0.0 and positives >= policy.min_positive_paired_folds,
            )
        )
    hold_median_drawdown = statistics.median(s.max_drawdown for s in benchmark_sims["B1"][base])
    guard_observed = aggregate_base["median_fold_max_drawdown"]
    comparison_checks.append(
        _check(
            "drawdown_guard_vs_B1",
            {"candidate": guard_observed, "B1": hold_median_drawdown},
            "candidate <= B1",
            guard_observed <= hold_median_drawdown,
        )
    )
    checks = [*trade_checks, *cost_checks, *risk_checks, *comparison_checks]

    rule_outcome: str
    if missing:
        rule_outcome = "NOT_EVALUABLE"
    elif pooled == 0:
        rule_outcome = "NO_TRADES"
    elif not all(c["passed"] for c in trade_checks):
        rule_outcome = "INSUFFICIENT_TRADES"
    elif not all(c["passed"] for c in cost_checks):
        rule_outcome = "EXCESSIVE_COST_BURDEN"
    elif not all(c["passed"] for c in risk_checks):
        rule_outcome = "UNACCEPTABLE_RISK"
    elif not all(c["passed"] for c in comparison_checks):
        rule_outcome = "ECONOMICALLY_NOT_SUPPORTED"
    else:
        rule_outcome = "ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE"
    decisional = bool(classification_screen_met)
    outcome = rule_outcome if decisional else NON_DECISIONAL

    return {
        "policy": policy_identity(policy),
        "evidence_label": EVIDENCE_LABEL,
        "decisional": decisional,
        "outcome": outcome,
        "rule_outcome": rule_outcome,
        "missing_decisive_evidence": sorted(set(missing)),
        "checks": checks,
        "designated_comparator": designated_comparator,
        "candidate": candidate,
        "candidate_aggregate": {
            f"{multiplier}x": _aggregate(policy, sims[candidate][multiplier])
            for multiplier in policy.multipliers
        },
        "paired_benchmark_differences": paired,
        "models": {
            name: {
                f"{multiplier}x": {
                    "aggregate": _aggregate(policy, sims[name][multiplier]),
                    "folds": _fold_records(policy, sims[name][multiplier]),
                }
                for multiplier in policy.multipliers
            }
            for name in models
        },
        "benchmarks": {
            label: {
                f"{multiplier}x": {
                    "aggregate": _aggregate(policy, benchmark_sims[label][multiplier]),
                    "folds": _fold_records(policy, benchmark_sims[label][multiplier]),
                }
                for multiplier in policy.multipliers
            }
            for label in ("B0", "B1")
        },
        "execution_authority": "none",
        "disposition": "NO CANDIDATE PROMOTION",
        "limitations": ECONOMIC_LIMITATIONS,
    }
