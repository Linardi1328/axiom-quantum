from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import load_json_file, write_loose_json
from spy_market_agent.benchmark.errors import (
    BenchmarkEligibilityError,
    BenchmarkInputError,
    raise_benchmark_error,
)
from spy_market_agent.benchmark.locks import (
    BenchmarkRole,
    DatasetEligibilityReport,
    FeedAvailabilityRecord,
    FeedLimitationDecision,
)
from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions, build_supervised_dataset
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.market_data.acquisition import DatasetManifest
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.manifest import canonical_bars_from_csv_bytes, load_manifest_bytes
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.market_data.storage import DatasetStore
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

PANDEMIC_PERIOD = (date(2020, 2, 19), date(2020, 4, 30))
RATE_HIKE_PERIOD = (date(2022, 1, 3), date(2022, 12, 30))
LOWER_VOL_EXPANSION_YEARS = (2017, 2019, 2021)


def record_feed_availability(
    *,
    provider: str,
    requested_feed: str,
    symbol: str,
    timeframe: str,
    adjustment_mode: str,
    requested_start: date,
    requested_end: date,
    probe_timestamp: datetime,
    success: bool,
    owner_acknowledgement: bool,
    evidence_source_description: str,
    output: Path,
    entitlement_or_subscription_limitation: str | None = None,
    sanitized_failure_category: str | None = None,
) -> FeedAvailabilityRecord:
    record = FeedAvailabilityRecord(
        provider=provider.strip().lower(),
        requested_feed=requested_feed.strip().lower(),
        symbol=symbol.strip().upper(),
        timeframe=timeframe.strip(),
        adjustment_mode=adjustment_mode.strip().lower(),
        requested_start=requested_start,
        requested_end=requested_end,
        probe_timestamp=_require_aware_utc(probe_timestamp, field_name="probe_timestamp"),
        success=success,
        entitlement_or_subscription_limitation=entitlement_or_subscription_limitation,
        sanitized_failure_category=sanitized_failure_category,
        owner_acknowledgement=owner_acknowledgement,
        evidence_source_description=evidence_source_description.strip(),
    )
    _validate_feed_record(record)
    write_loose_json(output, record)
    return record


def load_feed_record(path: Path) -> FeedAvailabilityRecord:
    try:
        record = FeedAvailabilityRecord.model_validate(load_json_file(path))
    except ValidationError:
        raise_benchmark_error(
            BenchmarkInputError,
            "invalid_feed_record",
            "feed availability record failed validation.",
        )
    _validate_feed_record(record)
    return record


def load_verified_phase1_dataset(
    manifest_path: Path,
    *,
    repository_root: Path | None = None,
) -> tuple[DatasetManifest, MarketDataBatch]:
    """Deep-verify a Phase 1 manifest before loading canonical bars for benchmarks."""

    repo_root = (repository_root or Path.cwd()).resolve()
    if manifest_path.is_absolute():
        candidate_manifest = manifest_path.resolve(strict=False)
    else:
        candidate_manifest = (repo_root / manifest_path).resolve(strict=False)
    try:
        candidate_manifest.relative_to(repo_root)
    except ValueError:
        raise_benchmark_error(
            BenchmarkInputError,
            "manifest_path_escape",
            "Phase 2 manifest path must stay inside the repository.",
        )
    try:
        provisional = load_manifest_bytes(candidate_manifest.read_bytes())
    except OSError:
        raise_benchmark_error(
            BenchmarkInputError,
            "manifest_missing",
            "Phase 2 requires an existing Phase 1 manifest path.",
        )

    data_root_value = provisional.relevant_configuration.get("data_root", "data")
    data_root = Path(data_root_value) if isinstance(data_root_value, str) else Path("data")
    store = DatasetStore(data_root, repository_root=repo_root)
    manifest = store.verify_manifest_artifacts(candidate_manifest)
    canonical_path = repo_root / manifest.generated_file_locations.canonical_path
    bars = canonical_bars_from_csv_bytes(canonical_path.read_bytes())
    frame = pd.DataFrame(
        {
            "session": [bar.session_date for bar in bars],
            "open": [bar.open for bar in bars],
            "high": [bar.high for bar in bars],
            "low": [bar.low for bar in bars],
            "close": [bar.close for bar in bars],
            "volume": [bar.volume for bar in bars],
        },
        columns=["session", "open", "high", "low", "close", "volume"],
    )
    batch = validate_daily_spy_data(
        frame,
        provider_name=manifest.provider,
        downloaded_at=manifest.retrieval_timestamp,
        created_at=manifest.retrieval_timestamp,
        as_of=manifest.retrieval_timestamp,
        calendar=XNYSCalendar(),
        source_description=f"Phase 1 manifest dataset_id={manifest.dataset_id}",
    )
    return manifest, batch


def build_supervised_phase2_dataset(
    market_data: MarketDataBatch,
    *,
    created_at: datetime,
) -> Any:
    base_cost = TradingCostAssumptions(
        commission_bps_per_side=Decimal("0.125"),
        slippage_bps_per_side=Decimal("0.25"),
    )
    features = build_trailing_feature_set(market_data, created_at=created_at)
    labels = build_forward_label_set(
        market_data,
        cost_assumptions=base_cost,
        created_at=created_at,
    )
    return build_supervised_dataset(features, labels, created_at=created_at)


def evaluate_dataset_eligibility(
    *,
    benchmark_id: str,
    manifest: DatasetManifest,
    market_data: MarketDataBatch,
    feed_record: FeedAvailabilityRecord,
    benchmark_role: BenchmarkRole,
    latest_complete_research_year: int,
) -> DatasetEligibilityReport:
    calendar = XNYSCalendar()
    sessions = tuple(market_data.data["session"].to_list())
    session_set = set(sessions)
    reasons: list[str] = []
    included: list[str] = []
    missing: list[str] = []
    limitations: list[str] = []

    _check_manifest_feed_consistency(manifest, feed_record, reasons)
    manifest_symbol = str(manifest.symbol)
    manifest_timeframe = str(manifest.timeframe)
    if manifest_symbol != "SPY":
        reasons.append("symbol must be exactly SPY")
    if manifest_timeframe != "1Day":
        reasons.append("timeframe must be exactly 1Day")
    if manifest.adjustment_mode != "all":
        reasons.append("adjustment mode must be exactly all")
    if manifest.provider != feed_record.provider:
        reasons.append("provider must match feed availability record")
    if manifest.feed != feed_record.requested_feed:
        reasons.append("feed must match feed availability record")
    if not feed_record.owner_acknowledgement:
        reasons.append("owner feed acknowledgement is required")
    if not feed_record.success:
        reasons.append("feed availability record must be successful for the locked feed")

    if manifest.feed == "iex":
        limitations.append(
            "IEX is limited single-exchange coverage and is not consolidated SIP data"
        )
    if benchmark_role == BenchmarkRole.PRIMARY and manifest.feed != "sip":
        reasons.append(
            "primary benchmark requires SIP unless a specification amendment is approved"
        )
    if benchmark_role == BenchmarkRole.DIAGNOSTIC and manifest.feed not in {"sip", "iex"}:
        reasons.append("diagnostic benchmark feed must be SIP or IEX")

    complete_years = _complete_calendar_years(sessions, calendar)
    if len(complete_years) < 8:
        reasons.append("at least eight complete calendar years are required")
    if latest_complete_research_year not in complete_years:
        reasons.append("latest_complete_research_year must be complete in the dataset")
    final_expected = calendar.sessions_between(
        date(latest_complete_research_year, 1, 1),
        date(latest_complete_research_year, 12, 31),
    )[-1]
    if sessions[-1] < final_expected:
        reasons.append(
            "dataset must contain the final XNYS session of latest_complete_research_year"
        )

    for name, start, end in (
        ("2020_pandemic_crash_recovery", *PANDEMIC_PERIOD),
        ("2022_rate_hike_bear", *RATE_HIKE_PERIOD),
    ):
        expected = calendar.sessions_between(start, end)
        if all(session in session_set for session in expected):
            included.append(name)
        else:
            missing.append(name)
    if any(year in complete_years for year in LOWER_VOL_EXPANSION_YEARS):
        included.append("lower_volatility_expansion")
    else:
        missing.append("lower_volatility_expansion")
    if "2020_pandemic_crash_recovery" in included:
        included.append("high_volatility_period")
    else:
        missing.append("high_volatility_period")
    reasons.extend(f"missing required period: {name}" for name in missing)

    passed = not reasons
    return DatasetEligibilityReport(
        benchmark_id=benchmark_id,
        dataset_id=manifest.dataset_id,
        requested_start=manifest.requested_start_date,
        requested_end=manifest.requested_end_date,
        actual_start=manifest.actual_first_session,
        actual_end=manifest.actual_last_session,
        row_count=manifest.row_count,
        expected_session_count=manifest.expected_session_count,
        complete_calendar_years=complete_years,
        included_required_periods=tuple(sorted(set(included))),
        missing_required_periods=tuple(sorted(set(missing))),
        provider=manifest.provider,
        feed=manifest.feed,
        provider_feed_limitations=tuple(limitations),
        adjustment_mode=manifest.adjustment_mode,
        benchmark_role=benchmark_role,
        passed=passed,
        reasons=tuple(reasons),
        latest_complete_research_year=latest_complete_research_year,
    )


def feed_limitation_decision(feed: str) -> FeedLimitationDecision:
    if feed == "sip":
        return FeedLimitationDecision(
            feed="sip",
            limitation_label="consolidated_us_market_feed",
            primary_allowed=True,
            diagnostic_allowed=True,
            reason="SIP is the required primary benchmark feed when available.",
        )
    if feed == "iex":
        return FeedLimitationDecision(
            feed="iex",
            limitation_label="limited_single_exchange_coverage",
            primary_allowed=False,
            diagnostic_allowed=True,
            reason="IEX is not consolidated SIP data and is diagnostic-only without amendment.",
        )
    raise_benchmark_error(
        BenchmarkEligibilityError,
        "unsupported_feed",
        "Phase 2 feed must be SIP or IEX.",
    )


def require_eligible(report: DatasetEligibilityReport) -> None:
    if not report.passed:
        raise_benchmark_error(
            BenchmarkEligibilityError,
            "dataset_ineligible",
            "; ".join(report.reasons),
        )


def _check_manifest_feed_consistency(
    manifest: DatasetManifest,
    feed_record: FeedAvailabilityRecord,
    reasons: list[str],
) -> None:
    expected = {
        "provider": manifest.provider,
        "requested_feed": manifest.feed,
        "symbol": manifest.symbol,
        "timeframe": manifest.timeframe,
        "adjustment_mode": manifest.adjustment_mode,
        "requested_start": manifest.requested_start_date,
        "requested_end": manifest.requested_end_date,
    }
    for field_name, expected_value in expected.items():
        if getattr(feed_record, field_name) != expected_value:
            reasons.append(f"feed record {field_name} must match manifest")


def _complete_calendar_years(sessions: tuple[date, ...], calendar: XNYSCalendar) -> tuple[int, ...]:
    session_set = set(sessions)
    complete: list[int] = []
    for year in range(sessions[0].year, sessions[-1].year + 1):
        expected = calendar.sessions_between(date(year, 1, 1), date(year, 12, 31))
        if expected and all(session in session_set for session in expected):
            complete.append(year)
    return tuple(complete)


def _validate_feed_record(record: FeedAvailabilityRecord) -> None:
    if record.provider != "alpaca":
        raise_benchmark_error(
            BenchmarkInputError,
            "unsupported_feed_provider",
            "provider must be alpaca.",
        )
    if record.requested_feed not in {"sip", "iex"}:
        raise_benchmark_error(BenchmarkInputError, "unsupported_feed", "feed must be sip or iex.")
    if record.symbol != "SPY" or record.timeframe != "1Day" or record.adjustment_mode != "all":
        raise_benchmark_error(
            BenchmarkInputError,
            "invalid_feed_contract",
            "feed record must use SPY, 1Day, and all adjustment mode.",
        )
    if record.requested_start > record.requested_end:
        raise_benchmark_error(
            BenchmarkInputError,
            "invalid_feed_date_range",
            "feed record start must not be after end.",
        )
    if not record.owner_acknowledgement:
        raise_benchmark_error(
            BenchmarkInputError,
            "missing_owner_feed_acknowledgement",
            "owner acknowledgement is required for feed records.",
        )
    if not record.evidence_source_description:
        raise_benchmark_error(
            BenchmarkInputError,
            "missing_feed_evidence_source",
            "feed record evidence source description is required.",
        )


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise_benchmark_error(
            BenchmarkInputError,
            f"naive_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return value.astimezone(UTC)
