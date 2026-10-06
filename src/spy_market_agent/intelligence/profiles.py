from __future__ import annotations

from spy_market_agent.intelligence.contracts import AnalysisHorizon, AnalysisProfile, HorizonUnit
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_INSTRUMENT_PROFILE

MI1_QQQ_DAILY_SERIES_ID = "qqq-daily"
MI1_IWM_DAILY_SERIES_ID = "iwm-daily"
MI1_VIX_DAILY_SERIES_ID = "vix-daily"
MI1_US_10Y_YIELD_DAILY_SERIES_ID = "us-10y-yield-daily"
MI1_SPY_SCENARIO_SCHEMA_ID = "downside-range-upside-v1"

MI1_SPY_ANALYSIS_PROFILE = AnalysisProfile(
    profile_id="mi1-spy-market-intelligence",
    profile_version="1",
    target_instrument_id=LEGACY_SPY_INSTRUMENT_PROFILE.instrument_id,
    horizons=(
        AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5),
        AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=20),
    ),
    feature_families=(
        "drawdown",
        "rates",
        "relative-strength",
        "trend",
        "volatility",
    ),
    context_series_ids=(
        MI1_QQQ_DAILY_SERIES_ID,
        MI1_IWM_DAILY_SERIES_ID,
        MI1_VIX_DAILY_SERIES_ID,
        MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    ),
    scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
)
