from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from spy_market_agent.backtesting import (
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestResult,
    run_long_or_cash_backtest,
)
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling import FinalTestEvaluation
from spy_market_agent.persistence import (
    RuntimeSnapshot,
    SQLiteArtifactRepository,
    initialize_database,
)
from spy_market_agent.risk import RiskConfig
from unit.phase6_helpers import CREATED_AT, make_phase6_inputs

MARKET_RUN_ID = "market-data-status-1"
MODEL_RUN_ID = "model-run-1"
BACKTEST_RUN_ID = "backtest-run-1"
TEST_RUNTIME = RuntimeSnapshot(
    git_commit_hash="d773fbb3b86b73dccd1644ab885f8c6f79c58574",
    python_version="3.12.13",
    dependency_versions=(("phase7-test", "1.0"),),
)


@dataclass(frozen=True, slots=True)
class Phase7Artifacts:
    market_data: MarketDataBatch
    evaluation: FinalTestEvaluation
    backtest: BacktestResult


def make_phase7_artifacts() -> Phase7Artifacts:
    batch, _partitions, _final_model, evaluation = make_phase6_inputs()
    backtest = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=BacktestConfig(
            cost_assumptions=BacktestCostAssumptions(
                commission_bps_per_side=Decimal("0.125"),
                slippage_bps_per_side=Decimal("0.25"),
            )
        ),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    return Phase7Artifacts(market_data=batch, evaluation=evaluation, backtest=backtest)


def initialized_repository(database_path: Path) -> SQLiteArtifactRepository:
    initialize_database(database_path)
    return SQLiteArtifactRepository(database_path, runtime_snapshot=TEST_RUNTIME)


def persist_phase7_artifacts(database_path: Path) -> Phase7Artifacts:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(database_path)
    repository.save_market_data_batch(MARKET_RUN_ID, artifacts.market_data)
    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)
    repository.save_backtest_result(BACKTEST_RUN_ID, artifacts.backtest)
    return artifacts
