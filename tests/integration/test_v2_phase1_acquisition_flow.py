from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.market_data.acquisition import AcquisitionRequest, MarketDataCredentials
from spy_market_agent.market_data.alpaca_provider import AlpacaMarketDataProvider
from spy_market_agent.market_data.errors import (
    ChecksumMismatch,
    ExistingDatasetConflict,
    MarketDataAcquisitionError,
)
from spy_market_agent.market_data.manifest import (
    canonical_bars_from_csv_bytes,
    canonical_content_checksum,
    canonical_json_bytes,
    load_raw_snapshot_bytes,
    sha256_bytes,
    source_checksum,
)
from spy_market_agent.market_data.pipeline import acquire_historical_spy_data
from spy_market_agent.market_data.storage import DatasetStore

FIXED_NOW = datetime(2024, 1, 8, 22, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


def make_request(
    *, data_root: Path = Path("data"), adjustment_mode: str = "raw"
) -> AcquisitionRequest:
    return AcquisitionRequest(
        symbol="SPY",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        timeframe="1Day",
        provider="alpaca",
        feed="sip",
        adjustment_mode=adjustment_mode,
        data_root=data_root,
        acknowledge_provider_terms=True,
    )


def valid_pages(*, final_close: str = "103.50") -> list[dict[str, Any]]:
    return [
        {
            "bars": {
                "SPY": [
                    {
                        "t": "2024-01-02T05:00:00Z",
                        "o": "100.00",
                        "h": "101.00",
                        "l": "99.50",
                        "c": "100.50",
                        "v": "1000000",
                    },
                    {
                        "t": "2024-01-03T05:00:00Z",
                        "o": "100.50",
                        "h": "102.00",
                        "l": "100.00",
                        "c": "101.25",
                        "v": "1000100",
                    },
                ]
            },
            "next_page_token": "page-2",
        },
        {
            "bars": {
                "SPY": [
                    {
                        "t": "2024-01-04T05:00:00Z",
                        "o": "101.25",
                        "h": "103.00",
                        "l": "101.00",
                        "c": "102.75",
                        "v": "1000200",
                    },
                    {
                        "t": "2024-01-05T05:00:00Z",
                        "o": "102.75",
                        "h": "104.00",
                        "l": "102.50",
                        "c": final_close,
                        "v": "1000300",
                    },
                ]
            },
            "next_page_token": None,
        },
    ]


def missing_session_pages() -> list[dict[str, Any]]:
    pages = valid_pages()
    pages[0]["bars"]["SPY"] = [pages[0]["bars"]["SPY"][0]]
    return pages


class FakePageClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = [dict(page) for page in pages]

    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        assert path == "/stocks/bars"
        assert data["symbols"] == "SPY"
        return self.pages.pop(0)


def make_provider(pages: list[dict[str, Any]]) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: FakePageClient(pages),
        sleep=lambda _seconds: None,
    )


def test_fake_provider_pages_write_raw_canonical_manifest_and_verify(tmp_path: Path) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )

    assert artifacts.manifest.row_count == 4
    assert artifacts.manifest.missing_session_summary.count == 0
    assert artifacts.manifest.source_checksum
    assert artifacts.manifest.canonical_content_checksum
    assert artifacts.manifest.generated_file_locations.raw_snapshot_path.startswith("data/raw/")
    assert artifacts.manifest.generated_file_locations.canonical_path.startswith("data/canonical/")
    assert artifacts.manifest.generated_file_locations.manifest_path.startswith("data/manifests/")

    store = DatasetStore(Path("data"), repository_root=tmp_path)
    manifest = store.verify_manifest_artifacts(
        tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    )
    assert manifest.dataset_id == artifacts.manifest.dataset_id


def test_repeated_unchanged_acquisition_is_idempotent(tmp_path: Path) -> None:
    request = make_request()
    credentials = MarketDataCredentials(api_key="key", secret_key="secret")
    first = acquire_historical_spy_data(
        request,
        provider=make_provider(valid_pages()),
        credentials=credentials,
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    second = acquire_historical_spy_data(
        request,
        provider=make_provider(valid_pages()),
        credentials=credentials,
        clock=fixed_clock,
        repository_root=tmp_path,
    )

    assert first.manifest.dataset_id == second.manifest.dataset_id
    assert first.manifest.canonical_content_checksum == second.manifest.canonical_content_checksum
    assert second.reused_existing is True
    assert second.created_files == ()


def test_acquisition_uses_one_captured_clock_value(tmp_path: Path) -> None:
    calls: list[datetime] = []

    def moving_clock() -> datetime:
        value = datetime(2024, 1, 8, 22, len(calls), tzinfo=UTC)
        calls.append(value)
        return value

    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=moving_clock,
        repository_root=tmp_path,
    )

    assert calls == [datetime(2024, 1, 8, 22, 0, tzinfo=UTC)]
    assert artifacts.manifest.retrieval_timestamp == calls[0]


def test_changed_provider_content_changes_dataset_identity(tmp_path: Path) -> None:
    request = make_request()
    credentials = MarketDataCredentials(api_key="key", secret_key="secret")
    first = acquire_historical_spy_data(
        request,
        provider=make_provider(valid_pages()),
        credentials=credentials,
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    second = acquire_historical_spy_data(
        request,
        provider=make_provider(valid_pages(final_close="103.75")),
        credentials=credentials,
        clock=fixed_clock,
        repository_root=tmp_path,
    )

    assert first.manifest.canonical_content_checksum != second.manifest.canonical_content_checksum
    assert first.manifest.dataset_id != second.manifest.dataset_id


def test_corrupted_artifact_fails_closed_on_repeat_and_verify(tmp_path: Path) -> None:
    request = make_request()
    credentials = MarketDataCredentials(api_key="key", secret_key="secret")
    artifacts = acquire_historical_spy_data(
        request,
        provider=make_provider(valid_pages()),
        credentials=credentials,
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    canonical_path = tmp_path / artifacts.manifest.generated_file_locations.canonical_path
    canonical_path.write_text("corrupted\n", encoding="utf-8")
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(ChecksumMismatch):
        store.verify_manifest_artifacts(
            tmp_path / artifacts.manifest.generated_file_locations.manifest_path
        )
    with pytest.raises(ExistingDatasetConflict):
        acquire_historical_spy_data(
            request,
            provider=make_provider(valid_pages()),
            credentials=credentials,
            clock=fixed_clock,
            repository_root=tmp_path,
        )


def test_corrupted_manifest_fails_verification(tmp_path: Path) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    original_package_version = artifacts.manifest.package_version
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(original_package_version, "9.9.9"),
        encoding="utf-8",
    )
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(ChecksumMismatch):
        store.verify_manifest_artifacts(manifest_path)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"source_checksum": "0" * 64}),
        lambda payload: payload.update({"canonical_content_checksum": "1" * 64}),
        lambda payload: payload.update({"dataset_id": "spy-v2p1-" + "2" * 24}),
        lambda payload: payload.update({"row_count": payload["row_count"] + 1}),
        lambda payload: payload.update({"actual_first_session": "2024-01-03"}),
        lambda payload: payload.update({"actual_last_session": "2024-01-04"}),
        lambda payload: payload.update({"feed": "iex"}),
        lambda payload: payload.update({"adjustment_mode": "all"}),
        lambda payload: payload["generated_file_locations"].update(
            {"canonical_path": "data/canonical/wrong.csv"}
        ),
        lambda payload: payload.update({"lineage_identifier": "lineage-" + "3" * 24}),
    ],
)
def test_deep_verification_rejects_manifest_semantic_tampering(
    tmp_path: Path,
    mutator: Any,
) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    payload = _manifest_payload(manifest_path)
    mutator(payload)
    _write_manifest_payload(manifest_path, payload)
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(MarketDataAcquisitionError):
        store.verify_manifest_artifacts(manifest_path)


def test_deep_verification_rejects_raw_source_tampering_with_recomputed_manifest(
    tmp_path: Path,
) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    raw_path = tmp_path / artifacts.manifest.generated_file_locations.raw_snapshot_path
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_payload["provider_response_payload"]["pages"][1]["bars"]["SPY"][1]["c"] = "103.55"
    raw_path.write_bytes(canonical_json_bytes(raw_payload))
    payload = _manifest_payload(manifest_path)
    payload["raw_artifact_checksum"] = sha256_bytes(raw_path.read_bytes())
    _write_manifest_payload(manifest_path, payload)
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(MarketDataAcquisitionError):
        store.verify_manifest_artifacts(manifest_path)


def test_deep_verification_rejects_canonical_value_tampering_with_updated_artifact_hash(
    tmp_path: Path,
) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    canonical_path = tmp_path / artifacts.manifest.generated_file_locations.canonical_path
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace("103.5", "103.55"),
        encoding="utf-8",
    )
    payload = _manifest_payload(manifest_path)
    payload["artifact_checksum"] = sha256_bytes(canonical_path.read_bytes())
    _write_manifest_payload(manifest_path, payload)
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(MarketDataAcquisitionError):
        store.verify_manifest_artifacts(manifest_path)


def test_deep_verification_rejects_filename_dataset_id_mismatch(tmp_path: Path) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    raw_path = tmp_path / artifacts.manifest.generated_file_locations.raw_snapshot_path
    wrong_raw_path = raw_path.with_name("spy-v2p1-wrongfilename.raw.json")
    wrong_raw_path.write_bytes(raw_path.read_bytes())
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    payload = _manifest_payload(manifest_path)
    payload["generated_file_locations"]["raw_snapshot_path"] = wrong_raw_path.relative_to(
        tmp_path
    ).as_posix()
    _write_manifest_payload(manifest_path, payload)
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(MarketDataAcquisitionError):
        store.verify_manifest_artifacts(manifest_path)


def test_deep_verification_rejects_invalid_xnys_session_after_hash_updates(
    tmp_path: Path,
) -> None:
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    raw_path = tmp_path / artifacts.manifest.generated_file_locations.raw_snapshot_path
    canonical_path = tmp_path / artifacts.manifest.generated_file_locations.canonical_path
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_payload["provider_response_payload"]["pages"][0]["bars"]["SPY"][0]["t"] = (
        "2024-01-06T05:00:00Z"
    )
    raw_path.write_bytes(canonical_json_bytes(raw_payload))
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace("2024-01-02", "2024-01-06"),
        encoding="utf-8",
    )
    raw_snapshot = load_raw_snapshot_bytes(raw_path.read_bytes())
    canonical_bars = canonical_bars_from_csv_bytes(canonical_path.read_bytes())
    payload = _manifest_payload(manifest_path)
    payload["raw_artifact_checksum"] = sha256_bytes(raw_path.read_bytes())
    payload["artifact_checksum"] = sha256_bytes(canonical_path.read_bytes())
    payload["source_checksum"] = source_checksum(raw_snapshot)
    payload["canonical_content_checksum"] = canonical_content_checksum(
        bars=canonical_bars,
        provider=payload["provider"],
        feed=payload["feed"],
        timeframe=payload["timeframe"],
        adjustment_mode=payload["adjustment_mode"],
        corporate_action_policy=payload["corporate_action_policy"],
    )
    _write_manifest_payload(manifest_path, payload)
    store = DatasetStore(Path("data"), repository_root=tmp_path)

    with pytest.raises(MarketDataAcquisitionError):
        store.verify_manifest_artifacts(manifest_path)


def test_cli_verify_returns_nonzero_for_semantic_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    artifacts = acquire_historical_spy_data(
        make_request(),
        provider=make_provider(valid_pages()),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    manifest_path = tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    payload = _manifest_payload(manifest_path)
    payload["row_count"] = payload["row_count"] + 1
    _write_manifest_payload(manifest_path, payload)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "spy_market_agent.market_data.cli",
            "verify",
            "--manifest",
            artifacts.manifest.generated_file_locations.manifest_path,
            "--data-root",
            "data",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "verification failed" in result.stderr


def test_missing_xnys_session_rejects_pipeline(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="expected XNYS sessions are missing"):
        acquire_historical_spy_data(
            make_request(),
            provider=make_provider(missing_session_pages()),
            credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
            clock=fixed_clock,
            repository_root=tmp_path,
        )


def test_api_and_dashboard_startup_do_not_acquire_market_data(tmp_path: Path) -> None:
    code = (
        "from spy_market_agent.api import create_app; "
        "import spy_market_agent.dashboard.streamlit_app as app; "
        "create_app(); "
        "print(hasattr(app, 'main')); "
        "import pathlib; print(list(pathlib.Path('.').iterdir()))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["True", "[]"]


def _manifest_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_manifest_payload(path: Path, payload: dict[str, Any]) -> None:
    payload["manifest_artifact_checksum"] = None
    payload["manifest_artifact_checksum"] = sha256_bytes(canonical_json_bytes(payload))
    path.write_bytes(canonical_json_bytes(payload))
