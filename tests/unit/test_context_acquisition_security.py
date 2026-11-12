"""RIC-154 adversarial regressions. Fake credentials and providers ONLY."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import traceback
from datetime import UTC, date, datetime, timedelta
from decimal import Inexact, localcontext
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.market_data import context_acquisition as ca
from spy_market_agent.market_data import context_transport as transport
from spy_market_agent.market_data.acquisition import AcquisitionRequest, MarketDataCredentials
from spy_market_agent.market_data.alpaca_provider import (
    AlpacaMarketDataProvider,
    _build_stock_bars_params,
)
from spy_market_agent.market_data.context_contracts import MAX_PAGES
from spy_market_agent.market_data.context_storage import ContextStore
from spy_market_agent.market_data.errors import (
    AtomicWriteFailure,
    CanonicalizationFailure,
    ExistingDatasetConflict,
    InvalidAcquisitionRequest,
    ManifestValidationFailure,
    MarketDataAcquisitionError,
    PaginationFailure,
    ProviderMalformedResponse,
    ProviderTimeoutFailure,
    UnsafeDataPath,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes
from unit.test_context_acquisition import NOW, FakeProvider, _page, _record, _request, _run


@pytest.fixture(autouse=True)
def ignored_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/raw/*\ndata/canonical/*\ndata/manifests/*\n")


class CountingProvider(AlpacaMarketDataProvider):
    def __init__(self, pages: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        self.constructions = 0
        self.calls: list[dict[str, Any]] = []
        self.pages = pages or [_page("QQQ")]
        super().__init__(client_factory=self.factory, sleep=lambda _: None, **kwargs)

    def factory(self, _credentials: Any, _timeout: float) -> Any:
        self.constructions += 1
        return self

    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        assert path == "/stocks/bars"
        self.calls.append(data)
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]


def acquire(
    root: Path,
    request: Any = None,
    provider: Any = None,
    clock: Any = None,
    credentials: Any = None,
) -> Any:
    return ca.acquire_historical_context_data(
        request if request is not None else _request(),
        provider=provider if provider is not None else CountingProvider(),
        credentials=credentials or MarketDataCredentials(api_key="fake", secret_key="fake"),
        clock=clock or (lambda: NOW),
        repository_root=root,
    )


@pytest.mark.parametrize("constructor", ["copy", "construct"])
@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "SPY"},
        {"symbol": "AAPL"},
        {"provider": "other"},
        {"feed": "iex"},
        {"timeframe": "1Hour"},
        {"adjustment_mode": "raw"},
        {"acknowledge_provider_terms": False},
        {"acknowledge_provider_terms": "true"},
        {"acknowledge_provider_terms": 1},
        {"asof": date(2024, 1, 2)},
        {"end_date": date(2025, 1, 1)},
        {"start_date": date(1900, 1, 1)},
        {"start_date": date(2024, 1, 6), "end_date": date(2024, 1, 7)},
        {"end_date": date(2024, 1, 8)},
        {"data_root": Path("exports")},
        {"extra_secret": "synthetic"},
    ],
)
def test_h02_revalidates_forged_requests_before_client(
    tmp_path: Path, constructor: str, change: dict[str, Any]
) -> None:
    request = _request()
    forged = (
        request.model_copy(update=change)
        if constructor == "copy"
        else ca.ContextAcquisitionRequest.model_construct(**{**request.__dict__, **change})
    )
    if constructor == "construct" and "extra_secret" in change:
        # model_construct(extra=forbid) discards unknown fields; inject a retained bypass.
        forged.__dict__["extra_secret"] = change["extra_secret"]
    provider = CountingProvider()
    with pytest.raises(InvalidAcquisitionRequest):
        acquire(tmp_path, request=forged, provider=provider, clock=lambda: NOW.replace(hour=12))
    assert provider.constructions == 0
    assert provider.calls == []


@pytest.mark.parametrize(
    "component",
    [
        "data",
        "data/raw",
        "data/canonical",
        "data/manifests",
        "data/raw/alpaca",
        "data/raw/alpaca/QQQ/1Day/sip/all",
    ],
)
@pytest.mark.parametrize("target_kind", ["internal", "external", "dangling"])
def test_h01_symlink_ancestry_denies_provider(
    tmp_path: Path, component: str, target_kind: str
) -> None:
    target = (
        tmp_path / "exports"
        if target_kind == "internal"
        else tmp_path.parent / (tmp_path.name + "-outside")
    )
    if target_kind != "dangling":
        target.mkdir(exist_ok=True)
    link = tmp_path / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0
    assert provider.calls == []
    if target_kind != "dangling":
        assert not list(target.iterdir())


@pytest.mark.parametrize("artifact", ["raw", "canonical", "manifest"])
def test_h01_dangling_final_artifacts_are_denied_before_fetch(
    tmp_path: Path, artifact: str
) -> None:
    manifest = _run(tmp_path).manifest
    path = tmp_path / manifest.generated_paths[artifact]
    path.unlink()
    path.symlink_to(tmp_path / "missing-target")
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0
    assert provider.calls == []
    assert not (tmp_path / "missing-target").exists()


def test_h01_ignored_layout_is_required_before_fetch(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("")
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0


def test_h01_symlink_swap_after_preflight_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    class SwapProvider(FakeProvider):
        def fetch_raw_snapshot(self, *args: Any, **kwargs: Any) -> Any:
            (tmp_path / "data").mkdir()
            (tmp_path / "data/raw").symlink_to(outside, target_is_directory=True)
            return super().fetch_raw_snapshot(*args, **kwargs)

    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=SwapProvider())
    assert not list(outside.iterdir())


@pytest.mark.parametrize("attack", ["headers", "unknown", "bar_secret", "nested", "token"])
def test_h03_secret_payloads_are_never_serialized(tmp_path: Path, attack: str) -> None:
    secret = "synthetic-sensitive-marker-154"
    page = _page("QQQ")
    changes: dict[str, dict[str, Any]] = {
        "headers": {"headers": {"Authorization": secret}},
        "unknown": {"arbitrary_secret": secret},
        "nested": {"metadata": {"nested": {"key": secret}}},
        "token": {"next_page_token": secret},
    }
    if attack == "bar_secret":
        page["bars"]["QQQ"][0]["password"] = secret  # type: ignore[index]
    else:
        page.update(changes[attack])
    with pytest.raises(MarketDataAcquisitionError) as caught:
        acquire(
            tmp_path,
            provider=CountingProvider([page]),
            credentials=MarketDataCredentials(api_key=secret, secret_key=secret),
        )
    assert secret not in "".join(traceback.format_exception(caught.value))
    assert not list(tmp_path.glob("data/**/*.json"))


@pytest.mark.parametrize("failure_type", [RuntimeError, TimeoutError, ProviderMalformedResponse])
def test_h03_public_tracebacks_have_no_raw_secret_or_cause(
    tmp_path: Path, failure_type: type[Exception]
) -> None:
    secret = "synthetic-sensitive-marker-154"

    class ErrorProvider(FakeProvider):
        def fetch_raw_snapshot(self, *_args: Any, **_kwargs: Any) -> Any:
            raise failure_type(secret)

    with pytest.raises(MarketDataAcquisitionError) as caught:
        acquire(tmp_path, provider=ErrorProvider())
    assert secret not in str(caught.value)
    assert secret not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_h03_retry_classification_and_bounds(tmp_path: Path) -> None:
    class TimeoutClient(CountingProvider):
        def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
            assert path == "/stocks/bars"
            self.calls.append(data)
            raise TimeoutError("synthetic-sensitive-marker-154")

    provider = TimeoutClient(max_retries=2)
    with pytest.raises(ProviderTimeoutFailure) as caught:
        acquire(tmp_path, provider=provider)
    assert len(provider.calls) == 3
    assert "synthetic-sensitive-marker-154" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("symbol", ["QQQ", "IWM"])
def test_m02_actual_adapter_multi_page_round_trip(tmp_path: Path, symbol: str) -> None:
    page = _page(symbol)
    records = page["bars"][symbol]  # type: ignore[index]
    pages: list[dict[str, Any]] = [
        {"bars": {symbol: records[:2]}, "next_page_token": "next-1"},
        {"bars": {symbol: records[2:]}, "next_page_token": None},
    ]
    provider = CountingProvider(pages)
    manifest = acquire(tmp_path, request=_request(symbol), provider=provider).manifest
    assert [p["page_token"] for p in provider.calls] == [None, "next-1"]
    assert (
        ca.verify_context_artifacts(
            tmp_path / manifest.generated_paths["manifest"], repository_root=tmp_path
        )
        == manifest
    )


@pytest.mark.parametrize("mode", ["repeated", "unlimited", "elapsed", "rows"])
def test_m02_global_budgets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    class Endless(CountingProvider):
        def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
            assert path == "/stocks/bars"
            self.calls.append(data)
            page = _page("QQQ")
            if mode == "rows":
                page["bars"]["QQQ"] *= 2500  # type: ignore[index]
            page["next_page_token"] = "same" if mode == "repeated" else f"token-{len(self.calls)}"
            return page

    provider = Endless()
    if mode == "elapsed":
        ticks = iter([0.0, 0.0, 121.0])
        provider._context_monotonic = lambda: next(ticks)
    if mode == "rows":
        monkeypatch.setattr(transport, "MAX_ROWS", 10)
    with pytest.raises(PaginationFailure):
        acquire(tmp_path, provider=provider)
    assert len(provider.calls) <= MAX_PAGES
    assert not list(tmp_path.glob("data/**/*.json"))


def rewrite(
    root: Path,
    manifest: Any,
    *,
    raw_change: Any = None,
    manifest_change: dict[str, Any] | None = None,
) -> Path:
    raw_path = root / manifest.generated_paths["raw"]
    payload = json.loads(raw_path.read_bytes())
    if raw_change:
        raw_change(payload)
        raw_path.write_bytes(canonical_json_bytes(payload))
    altered = manifest.model_copy(
        update={"raw_sha256": sha256_bytes(raw_path.read_bytes()), **(manifest_change or {})}
    )
    altered = altered.model_copy(update={"manifest_sha256": None})
    altered = altered.model_copy(
        update={"manifest_sha256": sha256_bytes(canonical_json_bytes(altered))}
    )
    path = root / str(manifest.generated_paths["manifest"])
    path.write_bytes(canonical_json_bytes(altered))
    return path


@pytest.mark.parametrize(
    "attack",
    [
        "page_number",
        "request_token",
        "row_count",
        "terminal",
        "count",
        "api",
        "sdk_name",
        "sdk_version",
        "access",
        "timezone",
        "corporate_actions",
        "request",
        "retrieval",
    ],
)
def test_m02_m05_recomputed_raw_metadata_is_rejected(tmp_path: Path, attack: str) -> None:
    manifest = _run(tmp_path).manifest

    def mutate(raw: dict[str, Any]) -> None:
        if attack in ("page_number", "request_token", "row_count"):
            key = {
                "page_number": "page_number",
                "request_token": "request_page_token",
                "row_count": "row_count",
            }[attack]
            raw["pagination"][0][key] = "invalid" if attack == "request_token" else 99
        elif attack == "terminal":
            raw["provider_response_payload"]["pages"][0]["next_page_token"] = "not-terminal"
            raw["pagination"][0]["next_page_token"] = "not-terminal"
        elif attack == "count":
            raw["response_page_count"] = 2
        elif attack in ("api", "sdk_name", "sdk_version", "access"):
            key = {
                "api": "api_version",
                "sdk_name": "sdk_package_name",
                "sdk_version": "sdk_package_version",
                "access": "access_method",
            }[attack]
            raw["provider_identity"][key] = "malicious"
        elif attack == "timezone":
            raw["source_timezone"] = "America/New_York"
        elif attack == "corporate_actions":
            raw["corporate_actions_payload"] = {"unexpected": "value"}
        elif attack == "request":
            raw["sanitized_request"]["asof"] = "2024-01-01"
        else:
            raw["retrieval_timestamp"] = "2025-01-01T00:00:00Z"

    path = rewrite(tmp_path, manifest, raw_change=mutate)
    with pytest.raises(MarketDataAcquisitionError):
        ca.verify_context_artifacts(path, repository_root=tmp_path)


@pytest.mark.parametrize(
    "attack",
    ["wrong_id", "traversal", "swapped", "lineage", "api", "sdk", "chronology", "old_schema"],
)
def test_m01_m05_malicious_recomputed_manifests(tmp_path: Path, attack: str) -> None:
    manifest = _run(tmp_path).manifest
    changes_by_attack: dict[str, dict[str, Any]] = {
        "wrong_id": {"dataset_id": "aq-ctx-v2-" + "1" * 24},
        "traversal": {"dataset_id": "../../escape"},
        "swapped": {
            "generated_paths": {
                **manifest.generated_paths,
                "raw": manifest.generated_paths["canonical"],
                "canonical": manifest.generated_paths["raw"],
            }
        },
        "lineage": {"lineage": manifest.lineage.model_copy(update={"git_commit": "0" * 40})},
        "api": {"provider_api_version": "v3"},
        "sdk": {"provider_sdk_version": "malicious"},
        "chronology": {"request_started_at": NOW + timedelta(seconds=1)},
        "old_schema": {"schema_version": "aq-qqq-iwm-context-acquisition-v1"},
    }
    changes = changes_by_attack[attack]
    path = rewrite(tmp_path, manifest, manifest_change=changes)
    with pytest.raises(MarketDataAcquisitionError):
        ca.verify_context_artifacts(path, repository_root=tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        "1e9999999",
        "1e-99999",
        "9" * 129,
        True,
        "-1",
        "0",
        "1.123456789012345678901234567890123456789",
    ],
)
def test_m03_invalid_prices_are_bounded(tmp_path: Path, value: Any) -> None:
    page = _page("QQQ")
    page["bars"]["QQQ"][0]["c"] = value  # type: ignore[index]
    with pytest.raises(CanonicalizationFailure):
        acquire(tmp_path, provider=CountingProvider([page]))
    assert not list(tmp_path.glob("data/**/*.json"))


@pytest.mark.parametrize("value", [True, 1.25, "1.5", -1, "9" * 50, 2**63])
def test_m03_invalid_volumes(tmp_path: Path, value: Any) -> None:
    page = _page("QQQ")
    page["bars"]["QQQ"][0]["v"] = value  # type: ignore[index]
    with pytest.raises(CanonicalizationFailure):
        acquire(tmp_path, provider=CountingProvider([page]))


def test_m03_decimal_context_cannot_round_prices_or_hide_ohlc(tmp_path: Path) -> None:
    for precision in (2, 50):
        with localcontext() as ctx:
            ctx.prec = precision
            ctx.traps[Inexact] = True
            assert (
                transport.decimal_text("100.123456789012345678", "close")
                == "100.123456789012345678"
            )
            page = _page("QQQ")
            row = page["bars"]["QQQ"][0]  # type: ignore[index]
            row.update({"o": "100", "c": "100", "l": "100.000000000000000001"})
            with pytest.raises(CanonicalizationFailure, match="OHLC"):
                acquire(tmp_path, provider=CountingProvider([page]))


def test_m04_distinct_start_and_completion_timestamps(tmp_path: Path) -> None:
    ticks = iter([NOW, NOW + timedelta(seconds=3), NOW + timedelta(seconds=5)])
    manifest = acquire(tmp_path, clock=lambda: next(ticks)).manifest
    assert manifest.request_started_at == NOW
    assert manifest.retrieval_completed_at == NOW + timedelta(seconds=5)
    assert (
        ca.verify_context_artifacts(
            tmp_path / manifest.generated_paths["manifest"], repository_root=tmp_path
        )
        == manifest
    )


@pytest.mark.parametrize(
    ("start", "end", "stamps", "now"),
    [
        (
            date(2024, 3, 8),
            date(2024, 3, 11),
            ["2024-03-08T05:00:00Z", "2024-03-11T04:00:00Z"],
            datetime(2024, 3, 12, tzinfo=UTC),
        ),
        (
            date(2024, 11, 27),
            date(2024, 11, 29),
            ["2024-11-27T05:00:00Z", "2024-11-29T05:00:00Z"],
            datetime(2024, 11, 29, 18, 1, tzinfo=UTC),
        ),
    ],
)
def test_calendar_dst_holiday_early_close(
    tmp_path: Path, start: date, end: date, stamps: list[str], now: datetime
) -> None:
    request = ca.ContextAcquisitionRequest(
        symbol="QQQ", start_date=start, end_date=end, acknowledge_provider_terms=True
    )
    page = {"bars": {"QQQ": [_record(stamp, 100) for stamp in stamps]}, "next_page_token": None}
    manifest = acquire(
        tmp_path, request=request, provider=CountingProvider([page]), clock=lambda: now
    ).manifest
    assert manifest.row_count == 2


@pytest.mark.parametrize("asof", [None, date(2024, 1, 2)])
@pytest.mark.parametrize(("feed", "adjustment"), [("sip", "all"), ("iex", "raw")])
def test_l02_exact_spy_outbound_parameters(asof: date | None, feed: str, adjustment: str) -> None:
    request = AcquisitionRequest(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        feed=feed,
        adjustment_mode=adjustment,
        acknowledge_provider_terms=True,
        asof=asof,
    )
    expected = {
        "symbols": "SPY",
        "start": "2024-01-02",
        "end": "2024-01-05",
        "timeframe": "1Day",
        "feed": feed,
        "adjustment": adjustment,
        "sort": "asc",
        "limit": 10000,
    }
    if asof:
        expected["asof"] = "2024-01-02"
    assert _build_stock_bars_params(request) == expected


def _writer(root: str, barrier: Any, output: Any, offset: int) -> None:
    try:

        class WaitingProvider(FakeProvider):
            def fetch_raw_snapshot(self, *args: Any, **kwargs: Any) -> Any:
                barrier.wait(timeout=20)
                return super().fetch_raw_snapshot(*args, **kwargs)

        result = acquire(
            Path(root), provider=WaitingProvider(), clock=lambda: NOW + timedelta(seconds=offset)
        )
        output.put(("success", result.manifest.raw_sha256))
    except ExistingDatasetConflict:
        output.put(("conflict", ""))


def test_h04_synchronized_process_writers_cannot_clobber(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    barrier, output = ctx.Barrier(2), ctx.Queue()
    writers = [
        ctx.Process(target=_writer, args=(str(tmp_path), barrier, output, i)) for i in range(2)
    ]
    for worker in writers:
        worker.start()
    for worker in writers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    records = [output.get(timeout=3) for _ in writers]
    assert sorted(record[0] for record in records) == ["conflict", "success"]
    manifests = list(tmp_path.glob("data/manifests/**/*.manifest.json"))
    assert len(manifests) == 1
    manifest = ca.verify_context_artifacts(manifests[0], repository_root=tmp_path)
    assert manifest.raw_sha256 == next(value for status, value in records if status == "success")


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_h04_each_write_failure_rolls_back_only_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: int
) -> None:
    original = ContextStore._publish
    calls = 0

    def publish(self: ContextStore, parent: int, path: Path, data: bytes, owned: Any) -> None:
        nonlocal calls
        original(self, parent, path, data, owned)
        calls += 1
        if calls == stage:
            raise OSError("injected synthetic write failure")

    monkeypatch.setattr(ContextStore, "_publish", publish)
    with pytest.raises(AtomicWriteFailure):
        acquire(tmp_path)
    assert not list(tmp_path.glob("data/**/*.raw.json"))
    assert not list(tmp_path.glob("data/**/*.canonical.csv"))
    assert not list(tmp_path.glob("data/**/*.manifest.json"))


def test_h04_rollback_cannot_remove_unowned_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ContextStore._publish
    replaced: list[Path] = []

    def publish(self: ContextStore, parent: int, path: Path, data: bytes, owned: Any) -> None:
        original(self, parent, path, data, owned)
        full = self.root / path
        foreign = full.with_suffix(".unrelated")
        foreign.write_bytes(b"unrelated-owner")
        foreign.replace(full)
        replaced.append(full)
        raise OSError("injected synthetic concurrent replacement")

    monkeypatch.setattr(ContextStore, "_publish", publish)
    with pytest.raises(AtomicWriteFailure):
        acquire(tmp_path)
    assert replaced[0].read_bytes() == b"unrelated-owner"


def _interrupted(root: str) -> None:
    original = ContextStore._publish

    def publish(self: ContextStore, parent: int, path: Path, data: bytes, owned: Any) -> None:
        original(self, parent, path, data, owned)
        os._exit(17)

    ContextStore._publish = publish  # type: ignore[method-assign,assignment]
    acquire(Path(root))


def test_h04_interrupted_set_requires_manual_recovery(tmp_path: Path) -> None:
    worker = multiprocessing.get_context("spawn").Process(
        target=_interrupted, args=(str(tmp_path),)
    )
    worker.start()
    worker.join(timeout=30)
    assert worker.exitcode == 17
    raw = next(tmp_path.glob("data/raw/**/*.raw.json"))
    before = raw.read_bytes()
    with pytest.raises(ExistingDatasetConflict, match="recovery_required"):
        acquire(tmp_path)
    assert raw.read_bytes() == before
    manifest_path = Path(
        str(raw).replace("/raw/", "/manifests/").replace(".raw.json", ".manifest.json")
    )
    with pytest.raises(ManifestValidationFailure):
        ca.verify_context_artifacts(manifest_path, repository_root=tmp_path)


def test_l02_fresh_process_cannot_acquire_on_import_or_synthetic_research() -> None:
    script = """
from unittest.mock import patch
from spy_market_agent.market_data import context_acquisition

from typing import NoReturn

def denied(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError('unexpected acquisition')
with patch.object(context_acquisition, 'acquire_historical_context_data', denied), \\
     patch('socket.socket.connect', denied), \\
     patch('spy_market_agent.market_data.acquisition.require_market_data_credentials', denied):
    from spy_market_agent.research.phase3c import run_synthetic_experiment
    result = run_synthetic_experiment()
    assert result.execution_authority == 'none'
    assert result.disposition == 'NO CANDIDATE PROMOTION'
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)


@pytest.mark.parametrize("mode", ["symlink", "missing"])
def test_h01_ancestor_relocation_before_stage_does_not_write_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    original = ContextStore._publish
    outside = tmp_path / "exports"
    moved: list[Path] = []

    def publish(self: ContextStore, parent: int, path: Path, data: bytes, owned: Any) -> None:
        full_parent = self.root / path.parent
        full_parent.rename(outside)
        if mode == "symlink":
            full_parent.symlink_to(outside, target_is_directory=True)
        moved.append(outside)
        original(self, parent, path, data, owned)

    monkeypatch.setattr(ContextStore, "_publish", publish)
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path)
    assert moved
    assert not list(outside.iterdir())


def test_h01_untrusted_writable_directory_is_rejected(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o777)
    data.chmod(0o777)
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0


def test_h04_stale_staging_is_not_automatically_deleted(tmp_path: Path) -> None:
    leaf = tmp_path / "data/raw/alpaca/QQQ/1Day/sip/all"
    leaf.mkdir(parents=True)
    staged = leaf / ".context-interrupted.tmp"
    staged.write_bytes(b"unrelated-interrupted-owner")
    with pytest.raises(ExistingDatasetConflict, match="recovery_required"):
        acquire(tmp_path)
    assert staged.read_bytes() == b"unrelated-interrupted-owner"


def test_m02_context_budget_clamps_each_sdk_request_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from requests import Response

    from spy_market_agent.market_data.alpaca_provider import _AlpacaStockBarsPageAdapter

    elapsed = [0.0]
    timeouts: list[float] = []

    class FakeSDK:
        _session: Any = None

        def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
            assert path == "/stocks/bars"
            assert data["symbols"] == "QQQ"
            elapsed[0] = 119.0
            return self._session.request("GET", "https://synthetic.invalid").json()  # type: ignore[no-any-return]

    def response(_self: Any, _method: str, _url: Any, **kwargs: Any) -> Response:
        timeouts.append(kwargs["timeout"])
        result = Response()
        result.status_code = 200
        result._content = json.dumps(_page("QQQ")).encode()
        return result

    monkeypatch.setattr("requests.sessions.Session.request", response)
    provider = AlpacaMarketDataProvider(
        client_factory=lambda *_: _AlpacaStockBarsPageAdapter(FakeSDK()),  # type: ignore[arg-type]
        context_monotonic=lambda: elapsed[0],
    )
    acquire(tmp_path, provider=provider)
    assert timeouts == [1.0]


@pytest.mark.parametrize("position", [0, 1, 2])
def test_m04_naive_clock_at_any_boundary_is_denied(tmp_path: Path, position: int) -> None:
    values = [NOW, NOW, NOW]
    values[position] = NOW.replace(tzinfo=None)
    ticks = iter(values)
    with pytest.raises(MarketDataAcquisitionError):
        acquire(tmp_path, clock=lambda: next(ticks))
    assert not list(tmp_path.glob("data/**/*.json"))


@pytest.mark.parametrize("count", [True, 1.0])
def test_m02_forged_response_count_type_is_rejected(tmp_path: Path, count: Any) -> None:
    class Forged(FakeProvider):
        def fetch_raw_snapshot(self, *args: Any, **kwargs: Any) -> Any:
            return (
                super()
                .fetch_raw_snapshot(*args, **kwargs)
                .model_copy(update={"response_page_count": count})
            )

    with pytest.raises(PaginationFailure):
        acquire(tmp_path, provider=Forged())


@pytest.mark.parametrize("kwargs", [{"max_retries": 4}, {"timeout_seconds": float("inf")}])
def test_m02_unsupported_context_retry_configuration_does_not_construct_client(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    provider = CountingProvider(**kwargs)
    with pytest.raises(InvalidAcquisitionRequest):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0


@pytest.mark.parametrize("attack", ["request", "provider", "retrieval", "timezone", "timestamp"])
def test_m05_acquisition_rejects_source_identity_and_time_mismatch(
    tmp_path: Path, attack: str
) -> None:
    class WrongIdentity(FakeProvider):
        def fetch_raw_snapshot(self, *args: Any, **kwargs: Any) -> Any:
            snapshot = super().fetch_raw_snapshot(*args, **kwargs)
            changes: dict[str, dict[str, Any]] = {
                "request": {"sanitized_request": {**snapshot.sanitized_request, "symbol": "IWM"}},
                "provider": {
                    "provider_identity": snapshot.provider_identity.model_copy(
                        update={"sdk_package_name": "wrong"}
                    )
                },
                "retrieval": {"retrieval_timestamp": NOW + timedelta(hours=1)},
                "timezone": {"source_timezone": "other"},
            }
            if attack == "timestamp":
                snapshot.provider_response_payload["pages"][0]["bars"]["QQQ"][0]["t"] = (
                    "invalid-timestamp"
                )
                return snapshot
            return snapshot.model_copy(update=changes[attack])

    with pytest.raises((ProviderMalformedResponse, CanonicalizationFailure)):
        acquire(tmp_path, provider=WrongIdentity())
    assert not list(tmp_path.glob("data/**/*.json"))


def test_m01_lineage_is_present_and_identity_bound(tmp_path: Path) -> None:
    manifest = _run(tmp_path).manifest
    lineage = manifest.lineage
    assert len(lineage.git_commit) == 40
    assert lineage.python_version.startswith("3.12.")
    assert lineage.package_version == "2.0.0b1"
    assert set(lineage.dependency_versions) == {
        "alpaca-py",
        "exchange-calendars",
        "pandas",
        "pydantic",
        "pydantic-settings",
    }
    assert {
        "market_data/context_storage.py",
        "market_data/context_transport.py",
        "market_data/context_acquisition.py",
        "market_data/context_contracts.py",
    } <= set(lineage.source_checksums)
    request = _request()
    changed = lineage.model_copy(update={"git_commit": "0" * 40})
    assert (
        ca._context_dataset_id(request, manifest.canonical_sha256, changed) != manifest.dataset_id
    )


def test_h02_incomplete_early_close_denies_client(tmp_path: Path) -> None:
    request = ca.ContextAcquisitionRequest(
        symbol="QQQ",
        start_date=date(2024, 11, 29),
        end_date=date(2024, 11, 29),
        acknowledge_provider_terms=True,
    )
    provider = CountingProvider()
    with pytest.raises(InvalidAcquisitionRequest):
        acquire(
            tmp_path,
            request=request,
            provider=provider,
            clock=lambda: datetime(2024, 11, 29, 17, 59, tzinfo=UTC),
        )
    assert provider.constructions == 0


def test_h01_specific_filename_ignore_is_not_an_approved_directory(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "**/aq-ctx-v2-000000000000000000000000.*\ndata/manifests/.context-acquisition-v2.lock\n"
    )
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0


@pytest.mark.parametrize(
    ("status", "expected", "calls"),
    [
        (401, "ProviderAuthenticationFailure", 1),
        (403, "ProviderAuthorizationFailure", 1),
        (429, "ProviderRateLimitFailure", 2),
        (503, "ProviderUnavailableFailure", 2),
        (400, "ProviderMalformedResponse", 1),
    ],
)
def test_h03_sdk_errors_keep_classification_not_sensitive_tracebacks(
    tmp_path: Path, status: int, expected: str, calls: int
) -> None:
    from alpaca.common.exceptions import APIError
    from requests import HTTPError, Response

    response = Response()
    response.status_code = status
    http_error = HTTPError("synthetic-sensitive-marker-154", response=response)
    error = APIError(
        json.dumps({"code": status, "message": "synthetic-sensitive-marker-154"}), http_error
    )

    class SDKFailure(CountingProvider):
        def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
            assert path == "/stocks/bars"
            self.calls.append(data)
            raise error

    provider = SDKFailure(max_retries=1)
    with pytest.raises(MarketDataAcquisitionError) as caught:
        acquire(tmp_path, provider=provider)
    assert type(caught.value).__name__ == expected
    assert len(provider.calls) == calls
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "synthetic-sensitive-marker-154" not in "".join(traceback.format_exception(caught.value))


def test_h03_client_factory_exception_is_sanitized(tmp_path: Path) -> None:
    def failure(*_args: Any) -> Any:
        raise ConnectionError("synthetic-sensitive-marker-154")

    with pytest.raises(MarketDataAcquisitionError) as caught:
        acquire(tmp_path, provider=AlpacaMarketDataProvider(client_factory=failure))
    assert type(caught.value).__name__ == "ProviderUnavailableFailure"
    assert "synthetic-sensitive-marker-154" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "attack",
    ["records", "token", "timestamp", "n", "envelope", "decimal_exponent", "empty", "naive", "nat"],
)
def test_strict_context_wire_schema_negative_paths(tmp_path: Path, attack: str) -> None:
    page: dict[str, Any] = _page("QQQ")
    if attack == "records":
        page["bars"]["QQQ"] = "not-a-list"
    elif attack == "token":
        page["next_page_token"] = {"secret": "synthetic-marker"}
    elif attack == "timestamp":
        page["bars"]["QQQ"][0]["t"] = 123
    elif attack == "n":
        page["bars"]["QQQ"][0]["n"] = False
    elif attack == "envelope":
        page["bars"] = []
    elif attack == "decimal_exponent":
        page["bars"]["QQQ"][0]["c"] = "1e" + "9" * 50
    elif attack == "empty":
        page["bars"]["QQQ"] = []
    elif attack == "naive":
        page["bars"]["QQQ"][0]["t"] = "2024-01-02"
    else:
        page["bars"]["QQQ"][0]["t"] = "NaT"
    with pytest.raises(MarketDataAcquisitionError):
        acquire(tmp_path, provider=CountingProvider([page]))
    assert not list(tmp_path.glob("data/**/*.json"))


def test_supported_optional_trade_count_and_vwap_are_valid(tmp_path: Path) -> None:
    page: dict[str, Any] = _page("QQQ")
    page["bars"]["QQQ"][0].update(n="12", vw="100.50", v="1000")
    result = acquire(tmp_path, provider=CountingProvider([page]))
    assert result.manifest.row_count == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_schema", "v1"),
        ("extra", "secret"),
        ("raw_whitespace", ""),
        ("manifest_whitespace", ""),
        ("rows", 99),
    ],
)
def test_m05_recomputed_wire_and_summary_tampering(tmp_path: Path, field: str, value: Any) -> None:
    manifest = _run(tmp_path).manifest

    def mutate(raw: dict[str, Any]) -> None:
        raw["schema_version" if field == "raw_schema" else "unknown"] = value

    if field in ("raw_schema", "extra"):
        path = rewrite(tmp_path, manifest, raw_change=mutate)
    elif field == "rows":
        path = rewrite(tmp_path, manifest, manifest_change={"row_count": value})
    else:
        path = tmp_path / manifest.generated_paths["manifest"]
        if field == "manifest_whitespace":
            path.write_bytes(b" " + path.read_bytes())
        else:
            raw_path = tmp_path / manifest.generated_paths["raw"]
            raw_path.write_bytes(b" " + raw_path.read_bytes())
            path = rewrite(tmp_path, manifest)
    with pytest.raises(MarketDataAcquisitionError):
        ca.verify_context_artifacts(path, repository_root=tmp_path)


def test_h01_hardlinks_are_not_accepted(tmp_path: Path) -> None:
    manifest = _run(tmp_path).manifest
    raw = tmp_path / manifest.generated_paths["raw"]
    os.link(raw, raw.with_suffix(".linked"))
    with pytest.raises(UnsafeDataPath):
        ca.verify_context_artifacts(
            tmp_path / manifest.generated_paths["manifest"], repository_root=tmp_path
        )


def test_h01_read_byte_budget_enforced_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_market_agent.market_data import context_storage

    manifest = _run(tmp_path).manifest
    monkeypatch.setattr(context_storage, "MAX_BYTES", 4)
    with pytest.raises(UnsafeDataPath):
        ContextStore(tmp_path).read(Path(manifest.generated_paths["raw"]))


def test_h02_direct_context_transport_requires_acquisition_preflight() -> None:
    provider = CountingProvider()
    with pytest.raises(InvalidAcquisitionRequest, match="preflight"):
        provider.fetch_raw_snapshot(
            _request(),
            credentials=MarketDataCredentials(api_key="fake", secret_key="fake"),
            clock=lambda: NOW,
        )
    assert provider.constructions == 0


def test_h04_active_interprocess_lock_mechanically_blocks_other_writer(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    barrier = ctx.Barrier(1)
    with ContextStore(tmp_path).locked(create=True):
        worker = ctx.Process(target=_writer, args=(str(tmp_path), barrier, output, 0))
        worker.start()
        worker.join(timeout=30)
        assert worker.exitcode == 0
        assert output.get(timeout=3)[0] == "conflict"
        assert not list(tmp_path.glob("data/**/*.manifest.json"))


def test_h01_symlinked_repository_and_lock_are_rejected(tmp_path: Path) -> None:
    alias = tmp_path.parent / (tmp_path.name + "-alias")
    alias.symlink_to(tmp_path, target_is_directory=True)
    provider = CountingProvider()
    with pytest.raises(UnsafeDataPath):
        acquire(alias, provider=provider)
    assert provider.constructions == 0
    manifests = tmp_path / "data/manifests"
    manifests.mkdir(parents=True)
    (manifests / ".context-acquisition-v2.lock").symlink_to(tmp_path / "dangling-lock")
    with pytest.raises(UnsafeDataPath):
        acquire(tmp_path, provider=provider)
    assert provider.constructions == 0


def test_h04_verifier_never_creates_a_missing_lock(tmp_path: Path) -> None:
    manifest = _run(tmp_path).manifest
    lock = tmp_path / "data/manifests/.context-acquisition-v2.lock"
    lock.unlink()
    with pytest.raises(ManifestValidationFailure, match="recovery_required"):
        ca.verify_context_artifacts(
            tmp_path / manifest.generated_paths["manifest"], repository_root=tmp_path
        )
    assert not lock.exists()


def test_h01_file_growth_cannot_bypass_read_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_market_agent.market_data import context_storage

    manifest = _run(tmp_path).manifest
    raw = Path(manifest.generated_paths["raw"])
    before = (tmp_path / raw).read_bytes()
    monkeypatch.setattr(context_storage, "MAX_BYTES", len(before) + 1)
    original = os.read

    def growing(descriptor: int, length: int) -> bytes:
        return original(descriptor, length) * 2

    monkeypatch.setattr(os, "read", growing)
    with pytest.raises(UnsafeDataPath, match="byte budget"):
        ContextStore(tmp_path).read(raw)


def test_h04_write_byte_budget_denies_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_market_agent.market_data import context_storage

    monkeypatch.setattr(context_storage, "MAX_BYTES", 4)
    with pytest.raises(UnsafeDataPath, match="byte budget"):
        acquire(tmp_path)
    assert not list(tmp_path.glob("data/**/*.json"))


@pytest.mark.parametrize("attack", ["envelope", "row_budget", "credential_metadata", "non_model"])
def test_h03_m02_untrusted_snapshot_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    class BadSnapshot(FakeProvider):
        def fetch_raw_snapshot(self, *args: Any, **kwargs: Any) -> Any:
            snapshot = super().fetch_raw_snapshot(*args, **kwargs)
            if attack == "non_model":
                return None
            if attack == "envelope":
                return snapshot.model_copy(
                    update={
                        "provider_response_payload": {
                            **snapshot.provider_response_payload,
                            "headers": {"Authorization": "synthetic"},
                        }
                    }
                )
            return snapshot

    if attack == "row_budget":
        monkeypatch.setattr(transport, "MAX_ROWS", 1)
    credentials = MarketDataCredentials(
        api_key="alpaca" if attack == "credential_metadata" else "fake", secret_key="fake"
    )
    with pytest.raises(MarketDataAcquisitionError):
        acquire(tmp_path, provider=BadSnapshot(), credentials=credentials)
    assert not list(tmp_path.glob("data/**/*.json"))


def test_m02_wire_byte_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "MAX_BYTES", 4)
    with pytest.raises(ProviderMalformedResponse, match="sanitized classification"):
        acquire(tmp_path)
    assert not list(tmp_path.glob("data/**/*.json"))


@pytest.mark.parametrize("mode", [0o777, 0o500])
def test_h01_repository_root_permissions_fail_before_provider(tmp_path: Path, mode: int) -> None:
    tmp_path.chmod(mode)
    provider = CountingProvider()
    try:
        with pytest.raises(UnsafeDataPath):
            acquire(tmp_path, provider=provider)
        assert provider.constructions == 0
    finally:
        tmp_path.chmod(0o700)
