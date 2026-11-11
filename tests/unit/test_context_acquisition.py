"""Offline-only tests for explicit historical ETF context acquisition."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.market_data.acquisition import (
    AcquisitionRequest,
    MarketDataCredentials,
    PaginationMetadata,
    ProviderIdentity,
    RawAcquisitionSnapshot,
    StockBarsRequestProtocol,
)
from spy_market_agent.market_data.alpaca_provider import AlpacaMarketDataProvider
from spy_market_agent.market_data.context_acquisition import (
    ContextAcquisitionArtifacts,
    ContextAcquisitionRequest,
    acquire_historical_context_data,
    verify_context_artifacts,
)
from spy_market_agent.market_data.errors import (
    CanonicalizationFailure,
    ChecksumMismatch,
    ExistingDatasetConflict,
    InvalidAcquisitionRequest,
    ManifestValidationFailure,
    ProviderMalformedResponse,
    UnsafeDataPath,
    UnsupportedMarketSymbol,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes

NOW = datetime(2024, 1, 8, 22, 0, tzinfo=UTC)
DATES = (
    ("2024-01-02T05:00:00Z", 100),
    ("2024-01-03T05:00:00Z", 101),
    ("2024-01-04T05:00:00Z", 102),
    ("2024-01-05T05:00:00Z", 103),
)


def _clock() -> datetime:
    """Return one frozen time without a provider or system-clock dependency."""
    return NOW


def _request(symbol: str = "QQQ") -> ContextAcquisitionRequest:
    """Build an explicitly authorized *synthetic-test* request."""
    return ContextAcquisitionRequest(
        symbol=symbol,
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        acknowledge_provider_terms=True,
    )


def _record(stamp: str, close: int) -> dict[str, object]:
    """Create one valid, fake adjusted daily bar."""
    return {
        "t": stamp,
        "o": close,
        "h": close + 2,
        "l": close - 2,
        "c": close + 1,
        "v": 1000,
    }


def _page(symbol: str) -> dict[str, object]:
    """Supply an offline one-page response."""
    return {"bars": {symbol: [_record(t, p) for t, p in DATES]}, "next_page_token": None}


class FakeProvider:
    """Snapshot-only fake; intentionally has no network or SDK client."""

    def __init__(self, page: dict[str, object] | None = None) -> None:
        self.page = page
        self.calls = 0

    def fetch_raw_snapshot(
        self,
        request: StockBarsRequestProtocol,
        *,
        credentials: MarketDataCredentials,
        clock: Callable[[], datetime],
    ) -> RawAcquisitionSnapshot:
        """Return a credential-free response with consistent request identity."""
        del credentials
        self.calls += 1
        page = self.page if self.page is not None else _page(request.symbol)
        bars = page["bars"]
        assert isinstance(bars, dict)
        symbol_rows = bars.get(request.symbol, [])
        assert isinstance(symbol_rows, list)
        return RawAcquisitionSnapshot(
            sanitized_request=request.sanitized_parameters(),
            provider_identity=ProviderIdentity(
                provider_name="alpaca",
                api_version="v2",
                sdk_package_name="alpaca-py",
                sdk_package_version="test",
                feed=request.feed,
                adjustment_mode=request.adjustment_mode,
                access_method="synthetic-page-fixture",
            ),
            retrieval_timestamp=clock(),
            source_timezone="UTC",
            provider_response_payload={"pages": [page]},
            pagination=(
                PaginationMetadata(
                    page_number=1,
                    request_page_token=None,
                    next_page_token=None,
                    row_count=len(symbol_rows),
                ),
            ),
            response_page_count=1,
        )


def _run(
    tmp_path: Path,
    *,
    request: ContextAcquisitionRequest | None = None,
    provider: FakeProvider | None = None,
    clock: Callable[[], datetime] = _clock,
) -> ContextAcquisitionArtifacts:
    """Run fake provider only inside a test's isolated local repository."""
    return acquire_historical_context_data(
        request or _request(),
        provider=provider or FakeProvider(),
        credentials=MarketDataCredentials(api_key="fake", secret_key="fake"),
        clock=clock,
        repository_root=tmp_path,
    )


@pytest.mark.parametrize("symbol", ["QQQ", "IWM"])
def test_qqq_iwm_manifest_is_immutable_and_explicitly_unverified(
    tmp_path: Path,
    symbol: str,
) -> None:
    """Persist all three artifacts with truthful rights/as-of statuses."""
    result = _run(tmp_path, request=_request(symbol))
    manifest = result.manifest
    assert manifest.symbol == symbol
    assert manifest.provider == "alpaca"
    assert manifest.feed == "sip"
    assert manifest.adjustment_mode == "all"
    assert manifest.row_count == 4
    assert manifest.point_in_time_status == "UNVERIFIED"
    assert manifest.usage_rights_status == "UNVERIFIED"
    assert manifest.execution_authority == "none"
    assert manifest.manifest_sha256 is not None
    assert manifest.manifest_sha256 == sha256_bytes(
        canonical_json_bytes(manifest.model_copy(update={"manifest_sha256": None}))
    )
    raw = tmp_path / manifest.generated_paths["raw"]
    canonical = tmp_path / manifest.generated_paths["canonical"]
    summary = tmp_path / manifest.generated_paths["manifest"]
    assert all(path.is_file() for path in (raw, canonical, summary))
    assert sha256_bytes(raw.read_bytes()) == manifest.raw_sha256
    assert sha256_bytes(canonical.read_bytes()) == manifest.canonical_sha256
    assert json.loads(summary.read_text())["dataset_id"] == manifest.dataset_id
    assert symbol in canonical.read_text()
    assert verify_context_artifacts(summary, repository_root=tmp_path) == manifest
    assert not (tmp_path / "data" / "canonical" / "alpaca" / "SPY").exists()
    again = _run(tmp_path, request=_request(symbol))
    assert again.manifest == manifest
    assert not again.raw_artifact_created
    assert not again.canonical_artifact_created
    assert not again.manifest_artifact_created


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "SPY"},
        {"symbol": "AAPL"},
        {"feed": "iex"},
        {"adjustment_mode": "raw"},
        {"provider": "other"},
        {"timeframe": "1Hour"},
        {"acknowledge_provider_terms": False},
        {"asof": date(2024, 1, 2)},
        {"start_date": date(2024, 1, 5), "end_date": date(2024, 1, 2)},
    ],
)
def test_request_scope_refuses_unsupported_source_policy(overrides: dict[str, object]) -> None:
    """No unapproved symbol, feed, adjustment or asof can reach provider."""
    payload: dict[str, object] = {
        "symbol": "QQQ",
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 1, 5),
        "acknowledge_provider_terms": True,
    }
    payload.update(overrides)
    with pytest.raises((InvalidAcquisitionRequest, ValueError)):
        ContextAcquisitionRequest.model_validate(payload)


@pytest.mark.parametrize(
    "root",
    [Path("../outside"), Path("/tmp/outside"), Path("."), Path("other"), Path("data/sub")],
)
def test_unsafe_storage_root_is_denied(root: Path) -> None:
    """Reject traversal and absolute writes before any network client exists."""
    with pytest.raises(UnsafeDataPath):
        ContextAcquisitionRequest(
            symbol="QQQ",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            data_root=root,
            acknowledge_provider_terms=True,
        )


def test_original_phase1_cannot_acquire_context_symbols() -> None:
    """Frozen SPY Phase 1 request and manifest contract remain untouched."""
    with pytest.raises(UnsupportedMarketSymbol):
        AcquisitionRequest(
            symbol="QQQ",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            feed="sip",
            adjustment_mode="all",
            acknowledge_provider_terms=True,
        )


def test_duplicate_or_missing_context_sessions_rejected_before_storage(tmp_path: Path) -> None:
    """The complete XNYS interval must match the response exactly."""
    page = _page("QQQ")
    bars = page["bars"]
    assert isinstance(bars, dict)
    records = bars["QQQ"]
    assert isinstance(records, list)
    records.pop()
    with pytest.raises(CanonicalizationFailure, match="missing, extra"):
        _run(tmp_path, provider=FakeProvider(page))
    assert not (tmp_path / "data").exists()

    page = _page("QQQ")
    bars = page["bars"]
    assert isinstance(bars, dict)
    records = bars["QQQ"]
    assert isinstance(records, list)
    records.insert(1, dict(records[0]))
    with pytest.raises(CanonicalizationFailure, match="duplicated"):
        _run(tmp_path, provider=FakeProvider(page))
    assert not (tmp_path / "data").exists()


def test_bad_ohlc_or_foreign_symbol_never_persisted(tmp_path: Path) -> None:
    """Reject malformed bars and provider cross-symbol contamination."""
    page = _page("QQQ")
    bars = page["bars"]
    assert isinstance(bars, dict)
    records = bars["QQQ"]
    assert isinstance(records, list)
    first = records[0]
    assert isinstance(first, dict)
    first["h"] = 1
    with pytest.raises(CanonicalizationFailure, match="OHLC"):
        _run(tmp_path, provider=FakeProvider(page))
    page = _page("QQQ")
    bars = page["bars"]
    assert isinstance(bars, dict)
    bars["IWM"] = []
    with pytest.raises(ProviderMalformedResponse, match="unexpected symbol"):
        _run(tmp_path, provider=FakeProvider(page))


def test_snapshot_conflict_cannot_rewrite_existing_dataset(tmp_path: Path) -> None:
    """Identical adjusted bars with a later retrieval timestamp must conflict."""
    first = _run(tmp_path)
    raw = tmp_path / first.manifest.generated_paths["raw"]
    before = sha256_bytes(raw.read_bytes())

    def later() -> datetime:
        return NOW + timedelta(minutes=1)

    with pytest.raises(ExistingDatasetConflict):
        _run(tmp_path, clock=later)
    assert sha256_bytes(raw.read_bytes()) == before


def test_real_alpaca_adapter_is_injectable_without_network(tmp_path: Path) -> None:
    """Share the existing pagination/credential provider using an offline page client."""

    class FakePageClient:
        def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
            assert path == "/stocks/bars"
            assert data["symbols"] == "QQQ"
            assert data["feed"] == "sip"
            assert data["adjustment"] == "all"
            assert data["timeframe"] == "1Day"
            assert data["asof"] == "-"
            return _page("QQQ")

    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout: FakePageClient()
    )
    result = acquire_historical_context_data(
        _request(),
        provider=provider,
        credentials=MarketDataCredentials(api_key="fake", secret_key="fake"),
        clock=_clock,
        repository_root=tmp_path,
    )
    assert result.manifest.row_count == 4
    assert result.manifest.point_in_time_status == "UNVERIFIED"


@pytest.mark.parametrize("artifact", ["raw", "canonical", "manifest"])
def test_context_verifier_rejects_modified_bytes(tmp_path: Path, artifact: str) -> None:
    """Detect tampering after the original write without any provider contact."""
    manifest = _run(tmp_path).manifest
    path = tmp_path / manifest.generated_paths[artifact]
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ChecksumMismatch):
        verify_context_artifacts(
            tmp_path / manifest.generated_paths["manifest"], repository_root=tmp_path
        )


def test_context_verifier_rejects_wrong_identity_or_missing_artifacts(tmp_path: Path) -> None:
    """Do not load an absent manifest or trust swapped path identities."""
    manifest = _run(tmp_path).manifest
    path = tmp_path / manifest.generated_paths["manifest"]
    with pytest.raises(ManifestValidationFailure):
        verify_context_artifacts(
            tmp_path / "data" / "manifests" / "other.json", repository_root=tmp_path
        )
    path.unlink()
    with pytest.raises(ManifestValidationFailure):
        verify_context_artifacts(path, repository_root=tmp_path)


def test_context_daily_bars_reject_intraday_timestamp(tmp_path: Path) -> None:
    """A valid session date alone does not turn an intraday candle into 1Day."""
    page = _page("QQQ")
    bars = page["bars"]
    assert isinstance(bars, dict)
    records = bars["QQQ"]
    assert isinstance(records, list)
    first = records[0]
    assert isinstance(first, dict)
    first["t"] = "2024-01-02T15:30:00Z"
    with pytest.raises(CanonicalizationFailure, match="New York midnight"):
        _run(tmp_path, provider=FakeProvider(page))
    assert not (tmp_path / "data").exists()


def test_alpaca_context_does_not_remap_symbols_but_spy_keeps_default() -> None:
    """No-remapping applies only to QQQ/IWM, never frozen Phase 1 SPY."""
    from spy_market_agent.market_data.alpaca_provider import _build_stock_bars_params

    assert _build_stock_bars_params(_request())["asof"] == "-"
    spy = AcquisitionRequest(
        symbol="SPY",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        feed="sip",
        adjustment_mode="all",
        acknowledge_provider_terms=True,
    )
    assert "asof" not in _build_stock_bars_params(spy)
