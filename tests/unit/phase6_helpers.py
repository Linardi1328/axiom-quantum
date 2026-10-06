from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions, build_supervised_dataset
from spy_market_agent.datasets.splits import (
    ChronologicalPartitions,
    ChronologicalSplitSpec,
    split_supervised_dataset,
)
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.modeling import (
    FinalModelBundle,
    FinalTestEvaluation,
    ModelTrainingConfig,
    PredictionSet,
    evaluate_locked_model_on_test,
    fit_locked_model_on_train_validation,
    train_candidate_models,
)
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)


def make_market_batch(row_count: int = 170) -> MarketDataBatch:
    calendar = XNYSCalendar()
    sessions = list(calendar.sessions_between(date(2024, 1, 2), date(2024, 12, 31)))[:row_count]
    opens = [100.0 + 5.0 * math.sin(index * 0.45) + 0.02 * index for index in range(row_count)]
    closes = [open_ + 0.2 * math.cos(index * 0.31) for index, open_ in enumerate(opens)]
    frame = pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": [max(open_, close) + 1.0 for open_, close in zip(opens, closes, strict=True)],
            "low": [min(open_, close) - 1.0 for open_, close in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": [1_000_000 + index * 1_000 for index in range(len(sessions))],
        },
        columns=list(CANONICAL_COLUMNS),
    )
    return validate_daily_spy_data(
        frame,
        provider_name="phase6-fixture",
        downloaded_at=CREATED_AT,
        created_at=CREATED_AT,
        as_of=CREATED_AT,
        calendar=calendar,
        source_description="deterministic Phase 6 fixture",
    )


def make_partitions_from_batch(batch: MarketDataBatch) -> ChronologicalPartitions:
    sessions = batch.data["session"].to_list()
    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)
    label_set = build_forward_label_set(
        batch,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
        ),
        created_at=CREATED_AT,
    )
    supervised = build_supervised_dataset(feature_set, label_set, created_at=CREATED_AT)
    spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[65],
        validation_start_session=sessions[72],
        validation_end_session=sessions[117],
        test_start_session=sessions[124],
        test_end_session=sessions[169],
    )
    return split_supervised_dataset(supervised, spec)


def force_strategy_probabilities(evaluation: FinalTestEvaluation) -> FinalTestEvaluation:
    data = evaluation.prediction_set.data.copy(deep=True)
    probabilities = [0.60 if index % 4 in (0, 1) else 0.40 for index in range(len(data))]
    if len(probabilities) >= 4:
        probabilities[1] = 0.50
    data["probability_positive"] = pd.Series(probabilities, dtype="float64")
    data["predicted_class"] = (data["probability_positive"] >= 0.5).astype("int64")
    prediction_set = PredictionSet(
        model_name=evaluation.prediction_set.model_name,
        partition_name=evaluation.prediction_set.partition_name,
        data=data,
        diagnostic_classification_threshold=evaluation.prediction_set.diagnostic_classification_threshold,
        row_count=evaluation.prediction_set.row_count,
        first_session=evaluation.prediction_set.first_session,
        last_session=evaluation.prediction_set.last_session,
        created_at=evaluation.prediction_set.created_at,
    )
    return FinalTestEvaluation(
        selected_model_name=evaluation.selected_model_name,
        locked_selection=evaluation.locked_selection,
        prediction_set=prediction_set,
        metrics=evaluation.metrics,
        source_market_data_checksum=evaluation.source_market_data_checksum,
        source_schema_version=evaluation.source_schema_version,
        feature_schema_version=evaluation.feature_schema_version,
        label_schema_version=evaluation.label_schema_version,
        feature_columns=evaluation.feature_columns,
        split_spec=evaluation.split_spec,
        test_row_count=evaluation.test_row_count,
        test_first_session=evaluation.test_first_session,
        test_last_session=evaluation.test_last_session,
        random_seed=evaluation.random_seed,
        diagnostic_classification_threshold=evaluation.diagnostic_classification_threshold,
        sklearn_version=evaluation.sklearn_version,
        model_schema_version=evaluation.model_schema_version,
        created_at=evaluation.created_at,
    )


def make_phase6_inputs() -> tuple[
    MarketDataBatch,
    ChronologicalPartitions,
    FinalModelBundle,
    FinalTestEvaluation,
]:
    batch = make_market_batch()
    partitions = make_partitions_from_batch(batch)
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=42),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    evaluation = evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)
    return batch, partitions, final_model, force_strategy_probabilities(evaluation)
