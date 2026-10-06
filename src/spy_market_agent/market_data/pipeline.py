from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from spy_market_agent.market_data.acquisition import (
    AcquisitionArtifacts,
    AcquisitionRequest,
    Clock,
    HistoricalMarketDataProviderProtocol,
    MarketDataCredentials,
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


def acquire_historical_spy_data(
    request: AcquisitionRequest,
    *,
    provider: HistoricalMarketDataProviderProtocol,
    credentials: MarketDataCredentials,
    clock: Clock,
    repository_root: Path | None = None,
) -> AcquisitionArtifacts:
    """Run explicit Phase 1 acquisition and persist raw/canonical/manifest artifacts."""

    calendar = XNYSCalendar()
    captured_now = clock()
    if captured_now.tzinfo is None or captured_now.utcoffset() is None:
        raise ValueError("acquisition clock must return a timezone-aware timestamp.")
    acquisition_timestamp = captured_now.astimezone(UTC)

    def acquisition_clock() -> datetime:
        return acquisition_timestamp

    snapshot = provider.fetch_raw_snapshot(
        request,
        credentials=credentials,
        clock=acquisition_clock,
    )
    canonical_bars = canonicalize_snapshot(
        request=request,
        snapshot=snapshot,
        calendar=calendar,
        as_of=acquisition_timestamp,
    )
    canonical_checksum = canonical_content_checksum(
        bars=canonical_bars,
        provider=request.provider,
        feed=request.feed,
        timeframe=request.timeframe,
        adjustment_mode=request.adjustment_mode,
    )
    dataset_id = dataset_identity(request=request, canonical_checksum=canonical_checksum)
    store = DatasetStore(request.data_root, repository_root=repository_root)
    paths = store.artifact_paths(request=request, dataset_id=dataset_id)

    raw_bytes = raw_snapshot_json_bytes(snapshot)
    canonical_bytes = canonical_csv_bytes(canonical_bars)
    raw_artifact_checksum = sha256_bytes(raw_bytes)
    canonical_artifact_checksum = sha256_bytes(canonical_bytes)

    manifest = build_manifest(
        request=request,
        snapshot=snapshot,
        bars=canonical_bars,
        calendar=calendar,
        relative_raw_path=store.relative_path(paths.raw_snapshot_path),
        relative_canonical_path=store.relative_path(paths.canonical_path),
        relative_manifest_path=store.relative_path(paths.manifest_path),
        canonical_artifact_checksum=canonical_artifact_checksum,
        raw_artifact_checksum=raw_artifact_checksum,
    )
    manifest = finalized_manifest_with_checksum(manifest)
    manifest_bytes = manifest_json_bytes(manifest)
    manifest_artifact_checksum = sha256_bytes(manifest_bytes)

    raw_result, canonical_result, manifest_result = store.write_dataset(
        paths=paths,
        raw_bytes=raw_bytes,
        canonical_bytes=canonical_bytes,
        manifest_bytes=manifest_bytes,
        expected_raw_checksum=raw_artifact_checksum,
        expected_canonical_checksum=canonical_artifact_checksum,
        expected_manifest_checksum=manifest_artifact_checksum,
    )
    reused_existing = not (
        raw_result.created or canonical_result.created or manifest_result.created
    )
    created_files = tuple(
        result.path for result in (raw_result, canonical_result, manifest_result) if result.created
    )
    if reused_existing:
        manifest = store.load_existing_manifest(paths.manifest_path)
    return AcquisitionArtifacts(
        manifest=manifest,
        raw_snapshot=snapshot,
        canonical_bars=canonical_bars,
        created_files=created_files,
        reused_existing=reused_existing,
    )
