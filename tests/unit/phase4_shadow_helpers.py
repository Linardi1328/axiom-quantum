from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from spy_market_agent.market_data.acquisition import (
    AcquisitionRequest,
    CanonicalDailyBar,
    DatasetManifest,
    PaginationMetadata,
    ProviderIdentity,
    RawAcquisitionSnapshot,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.canonicalization import canonicalize_snapshot
from spy_market_agent.market_data.manifest import (
    build_manifest,
    canonical_content_checksum,
    canonical_csv_bytes,
    dataset_identity,
    finalized_manifest_with_checksum,
    manifest_json_bytes,
    sha256_bytes,
)
from spy_market_agent.market_data.storage import DatasetStore, raw_snapshot_json_bytes


@dataclass(frozen=True, slots=True)
class SyntheticPhase1Dataset:
    data_root: Path
    manifest_path: Path
    manifest: DatasetManifest
    canonical_bars: tuple[CanonicalDailyBar, ...]


def write_synthetic_phase1_dataset(
    repository_root: Path,
    *,
    start_session: date = date(2025, 1, 2),
    end_session: date = date(2025, 1, 2),
    retrieval_timestamp: datetime = datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
    adjustment_mode: str = "all",
) -> SyntheticPhase1Dataset:
    data_root = Path("data")
    request = AcquisitionRequest(
        symbol="SPY",
        start_date=start_session,
        end_date=end_session,
        timeframe="1Day",
        provider="alpaca",
        feed="sip",
        adjustment_mode=adjustment_mode,
        data_root=data_root,
        acknowledge_provider_terms=True,
    )
    calendar = XNYSCalendar()
    raw_records = tuple(
        _raw_bar_for_session(session, index=index)
        for index, session in enumerate(
            calendar.sessions_between(start_session, end_session),
            start=0,
        )
    )
    snapshot = RawAcquisitionSnapshot(
        sanitized_request=request.sanitized_parameters(),
        provider_identity=ProviderIdentity(
            provider_name="alpaca",
            api_version="v2",
            sdk_package_name="alpaca-py",
            sdk_package_version="0.43.5",
            feed="sip",
            adjustment_mode=request.adjustment_mode,
            access_method="synthetic-phase4-shadow-fixture",
        ),
        retrieval_timestamp=retrieval_timestamp,
        source_timezone="UTC",
        provider_response_payload={
            "pages": [
                {
                    "bars": {"SPY": list(raw_records)},
                    "next_page_token": None,
                }
            ]
        },
        pagination=(PaginationMetadata(page_number=1, row_count=len(raw_records)),),
        response_page_count=1,
        corporate_actions_payload=None,
    )
    bars = canonicalize_snapshot(
        request=request,
        snapshot=snapshot,
        calendar=calendar,
        as_of=retrieval_timestamp,
    )
    canonical_checksum = canonical_content_checksum(
        bars=bars,
        provider=request.provider,
        feed=request.feed,
        timeframe=request.timeframe,
        adjustment_mode=request.adjustment_mode,
    )
    dataset_id = dataset_identity(request=request, canonical_checksum=canonical_checksum)
    store = DatasetStore(data_root, repository_root=repository_root)
    paths = store.artifact_paths(request=request, dataset_id=dataset_id)
    raw_bytes = raw_snapshot_json_bytes(snapshot)
    canonical_bytes = canonical_csv_bytes(bars)
    manifest = build_manifest(
        request=request,
        snapshot=snapshot,
        bars=bars,
        calendar=calendar,
        relative_raw_path=store.relative_path(paths.raw_snapshot_path),
        relative_canonical_path=store.relative_path(paths.canonical_path),
        relative_manifest_path=store.relative_path(paths.manifest_path),
        canonical_artifact_checksum=sha256_bytes(canonical_bytes),
        raw_artifact_checksum=sha256_bytes(raw_bytes),
    )
    manifest = finalized_manifest_with_checksum(manifest)
    manifest_bytes = manifest_json_bytes(manifest)
    store.write_dataset(
        paths=paths,
        raw_bytes=raw_bytes,
        canonical_bytes=canonical_bytes,
        manifest_bytes=manifest_bytes,
        expected_raw_checksum=sha256_bytes(raw_bytes),
        expected_canonical_checksum=sha256_bytes(canonical_bytes),
        expected_manifest_checksum=sha256_bytes(manifest_bytes),
    )
    return SyntheticPhase1Dataset(
        data_root=data_root,
        manifest_path=paths.manifest_path,
        manifest=manifest,
        canonical_bars=bars,
    )


def _raw_bar_for_session(session: date, *, index: int) -> dict[str, str]:
    open_price = 100 + index
    close_price = open_price + 1
    return {
        "t": f"{session.isoformat()}T05:00:00Z",
        "o": f"{open_price}.00",
        "h": f"{close_price + 1}.00",
        "l": f"{open_price - 1}.00",
        "c": f"{close_price}.00",
        "v": str(1_000_000 + index),
    }
