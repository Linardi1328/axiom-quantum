from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions, build_supervised_dataset
from spy_market_agent.datasets.splits import ChronologicalSplitSpec, split_supervised_dataset
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.features.models import FEATURE_COLUMNS
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS
from spy_market_agent.modeling import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SCHEMA_VERSION,
    ModelTrainingConfig,
    evaluate_locked_model_on_test,
    fit_locked_model_on_train_validation,
    train_candidate_models,
)
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)


def test_phase5_modeling_flow_is_deterministic_and_test_locked() -> None:
    calendar = XNYSCalendar()
    sessions = list(calendar.sessions_between(date(2024, 1, 2), date(2024, 12, 31)))[:170]
    opens = [100.0 + 5.0 * math.sin(index * 0.45) + 0.02 * index for index in range(170)]
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
    batch = validate_daily_spy_data(
        frame,
        provider_name="phase5-integration-fixture",
        downloaded_at=CREATED_AT,
        created_at=CREATED_AT,
        as_of=CREATED_AT,
        calendar=calendar,
        source_description="deterministic Phase 5 modeling flow fixture",
    )
    batch_before = batch.data.copy(deep=True)
    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)
    feature_before = feature_set.data.copy(deep=True)
    label_set = build_forward_label_set(
        batch,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
        ),
        created_at=CREATED_AT,
    )
    label_before = label_set.data.copy(deep=True)
    supervised = build_supervised_dataset(feature_set, label_set, created_at=CREATED_AT)
    supervised_features_before = supervised.features.copy(deep=True)
    supervised_labels_before = supervised.labels.copy(deep=True)
    spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[65],
        validation_start_session=sessions[72],
        validation_end_session=sessions[117],
        test_start_session=sessions[124],
        test_end_session=sessions[169],
    )
    partitions = split_supervised_dataset(supervised, spec)
    train_before = partitions.train.features.copy(deep=True)
    validation_before = partitions.validation.features.copy(deep=True)
    test_before = partitions.test.features.copy(deep=True)
    config = ModelTrainingConfig(random_seed=42)

    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=config,
        created_at=CREATED_AT,
    )
    repeated_comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=config,
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    locked_before_test = comparison.locked_selection
    test_evaluation = evaluate_locked_model_on_test(
        final_model,
        partitions.test,
        created_at=CREATED_AT,
    )

    assert comparison.source_market_data_checksum == batch.metadata.dataset_checksum
    assert comparison.feature_schema_version == feature_set.feature_schema_version
    assert comparison.label_schema_version == label_set.label_schema_version
    assert comparison.model_schema_version == MODEL_SCHEMA_VERSION
    assert comparison.feature_columns == FEATURE_COLUMNS
    assert comparison.locked_selection.selected_model_name in {
        LOGISTIC_REGRESSION_MODEL,
        GRADIENT_BOOSTING_MODEL,
    }
    assert comparison.locked_selection.selected_model_name == (
        repeated_comparison.locked_selection.selected_model_name
    )
    assert comparison.locked_selection.candidate_parameters == (
        repeated_comparison.locked_selection.candidate_parameters
    )
    assert final_model.combined_row_count == (
        partitions.train.metadata.included_row_count
        + partitions.validation.metadata.included_row_count
    )
    assert final_model.validation_last_session < test_evaluation.test_first_session
    assert test_evaluation.selected_model_name == comparison.locked_selection.selected_model_name
    assert test_evaluation.locked_selection == locked_before_test
    assert (
        test_evaluation.prediction_set.data["session"].to_list()
        == partitions.test.labels["session"].to_list()
    )
    assert set(partitions.train.labels["target"].to_list()) == {0, 1}
    assert set(partitions.validation.labels["target"].to_list()) == {0, 1}
    assert set(partitions.test.labels["target"].to_list()) == {0, 1}
    assert list(supervised.X.columns) == list(FEATURE_COLUMNS)
    assert "target" not in supervised.X.columns
    assert "net_forward_return" not in supervised.X.columns
    pd.testing.assert_frame_equal(
        comparison.logistic_regression.validation_predictions.data,
        repeated_comparison.logistic_regression.validation_predictions.data,
    )
    pd.testing.assert_frame_equal(
        comparison.gradient_boosting.validation_predictions.data,
        repeated_comparison.gradient_boosting.validation_predictions.data,
    )
    assert comparison.logistic_regression.validation_metrics == (
        repeated_comparison.logistic_regression.validation_metrics
    )
    assert comparison.gradient_boosting.validation_metrics == (
        repeated_comparison.gradient_boosting.validation_metrics
    )
    pd.testing.assert_frame_equal(batch.data, batch_before)
    pd.testing.assert_frame_equal(feature_set.data, feature_before)
    pd.testing.assert_frame_equal(label_set.data, label_before)
    pd.testing.assert_frame_equal(supervised.features, supervised_features_before)
    pd.testing.assert_frame_equal(supervised.labels, supervised_labels_before)
    pd.testing.assert_frame_equal(partitions.train.features, train_before)
    pd.testing.assert_frame_equal(partitions.validation.features, validation_before)
    pd.testing.assert_frame_equal(partitions.test.features, test_before)
