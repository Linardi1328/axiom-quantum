from __future__ import annotations

from datetime import datetime

import pandas as pd

from spy_market_agent.datasets.models import (
    ENTRY_OFFSET_SESSIONS,
    EXIT_OFFSET_SESSIONS,
    LABEL_COLUMNS,
    LABEL_SCHEMA_VERSION,
    LabelConstructionError,
    LabelSet,
    TradingCostAssumptions,
    dataset_issue,
    is_finite_float,
)
from spy_market_agent.market_data.models import MarketDataBatch


def build_forward_label_set(
    market_data: MarketDataBatch,
    *,
    cost_assumptions: TradingCostAssumptions,
    created_at: datetime,
) -> LabelSet:
    """Build the t+1 entry to t+6 exit open-to-open net-positive label."""

    source = market_data.data.copy(deep=True).reset_index(drop=True)
    if len(source) <= EXIT_OFFSET_SESSIONS:
        raise LabelConstructionError(
            [
                dataset_issue(
                    "insufficient_source_rows",
                    "at least seven source rows are required for Phase 4 labels.",
                )
            ]
        )

    side_cost_rate = float(cost_assumptions.side_cost_rate)
    records: list[dict[str, object]] = []
    max_label_index = len(source) - EXIT_OFFSET_SESSIONS
    for index in range(max_label_index):
        session = source.at[index, "session"]
        entry_session = source.at[index + ENTRY_OFFSET_SESSIONS, "session"]
        exit_session = source.at[index + EXIT_OFFSET_SESSIONS, "session"]
        entry_open = float(source.at[index + ENTRY_OFFSET_SESSIONS, "open"])
        exit_open = float(source.at[index + EXIT_OFFSET_SESSIONS, "open"])
        effective_entry_price = entry_open * (1.0 + side_cost_rate)
        effective_exit_price = exit_open * (1.0 - side_cost_rate)
        gross_forward_return = exit_open / entry_open - 1.0
        net_forward_return = effective_exit_price / effective_entry_price - 1.0
        if not is_finite_float(gross_forward_return) or not is_finite_float(net_forward_return):
            raise LabelConstructionError(
                [
                    dataset_issue(
                        "non_finite_forward_return",
                        "forward return calculation produced a non-finite value.",
                    )
                ]
            )
        records.append(
            {
                "session": session,
                "entry_session": entry_session,
                "exit_session": exit_session,
                "gross_forward_return": gross_forward_return,
                "net_forward_return": net_forward_return,
                "target": 1 if net_forward_return > 0.0 else 0,
            }
        )

    labels = pd.DataFrame.from_records(records, columns=list(LABEL_COLUMNS))
    labels["gross_forward_return"] = labels["gross_forward_return"].astype("float64")
    labels["net_forward_return"] = labels["net_forward_return"].astype("float64")
    labels["target"] = labels["target"].astype("int64")

    return LabelSet(
        data=labels,
        source_market_data_checksum=market_data.metadata.dataset_checksum,
        source_schema_version=market_data.metadata.schema_version,
        label_schema_version=LABEL_SCHEMA_VERSION,
        entry_offset_sessions=ENTRY_OFFSET_SESSIONS,
        exit_offset_sessions=EXIT_OFFSET_SESSIONS,
        cost_assumptions=cost_assumptions,
        first_label_session=labels.iloc[0]["session"],
        last_label_session=labels.iloc[-1]["session"],
        row_count=len(labels),
        source_rows_excluded_after_label_horizon=EXIT_OFFSET_SESSIONS,
        created_at=created_at,
    )
