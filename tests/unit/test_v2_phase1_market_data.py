from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from alpaca.data.historical import StockHistoricalDataClient
from pydantic import SecretStr
from requests import Timeout

from spy_market_agent.config import Settings
from spy_market_agent.market_data import alpaca_provider as alpaca_provider_module
from spy_market_agent.market_data.acquisition import (
    AcquisitionRequest,
    MarketDataCredentials,
    utc_now,
)
from spy_market_agent.market_data.alpaca_provider import AlpacaMarketDataProvider
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.canonicalization import canonicalize_snapshot
from spy_market_agent.market_data.cli import main as market_data_cli_main
from spy_market_agent.market_data.errors import (
    AtomicWriteFailure,
    CanonicalizationFailure,
    ChecksumMismatch,
    ExistingDatasetConflict,
    InvalidAcquisitionRequest,
    ManifestValidationFailure,
    MissingMarketDataCredentials,
    PaginationFailure,
    ProviderIncompleteResponse,
    ProviderMalformedResponse,
    ProviderTimeoutFailure,
    SessionValidationFailure,
    UnsafeDataPath,
    UnsupportedMarketSymbol,
    UnsupportedTimeframe,
    redact_secret_text,
)
from spy_market_agent.market_data.manifest import (
    build_manifest,
    canonical_bars_from_csv_bytes,
    canonical_content_checksum,
    canonical_csv_bytes,
    canonical_csv_header,
    dataset_identity,
    finalized_manifest_with_checksum,
    load_manifest_bytes,
    load_raw_snapshot_bytes,
    sha256_bytes,
    source_checksum,
)
from spy_market_agent.market_data.pipeline import acquire_historical_spy_data
from spy_market_agent.market_data.storage import DatasetStore, raw_snapshot_json_bytes

FIXED_NOW = datetime(2024, 1, 8, 22, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


def make_request(
    *,
    adjustment_mode: str = "raw",
    feed: str = "sip",
    data_root: Path = Path("data"),
    acknowledge_provider_terms: bool = True,
    start_date: date = date(2024, 1, 2),
    end_date: date = date(2024, 1, 5),
) -> AcquisitionRequest:
    return AcquisitionRequest(
        symbol="SPY",
        start_date=start_date,
        end_date=end_date,
        timeframe="1Day",
        provider="alpaca",
        feed=feed,
        adjustment_mode=adjustment_mode,
        data_root=data_root,
        acknowledge_provider_terms=acknowledge_provider_terms,
    )


def valid_page_1(next_page_token: str | None = "page-2") -> dict[str, Any]:
    return {
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
        "next_page_token": next_page_token,
    }


def valid_page_2() -> dict[str, Any]:
    return {
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
                    "c": "103.50",
                    "v": "1000300",
                },
            ]
        },
        "next_page_token": None,
    }


class FakePageClient:
    def __init__(self, pages: list[dict[str, Any] | Exception]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"path": path, "data": dict(data)})
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def valid_page_client_factory() -> Callable[[MarketDataCredentials, float], FakePageClient]:
    return lambda _credentials, _timeout_seconds: FakePageClient([valid_page_1(), valid_page_2()])


def test_acquisition_request_accepts_phase1_contract() -> None:
    request = make_request(adjustment_mode="all-adjusted")

    assert request.symbol == "SPY"
    assert request.timeframe == "1Day"
    assert request.provider == "alpaca"
    assert request.adjustment_mode == "all"
    assert "data_root" not in request.sanitized_parameters()


@pytest.mark.parametrize(
    ("kwargs", "exc_type"),
    [
        ({"symbol": "AAPL"}, UnsupportedMarketSymbol),
        ({"timeframe": "1Hour"}, UnsupportedTimeframe),
        ({"provider": "other"}, InvalidAcquisitionRequest),
        ({"feed": "boats"}, InvalidAcquisitionRequest),
        ({"adjustment_mode": "split"}, InvalidAcquisitionRequest),
        ({"acknowledge_provider_terms": False}, InvalidAcquisitionRequest),
        ({"data_root": Path("../data")}, UnsafeDataPath),
        ({"data_root": Path("/tmp/data")}, UnsafeDataPath),
        ({"start_date": date(2024, 1, 5), "end_date": date(2024, 1, 2)}, InvalidAcquisitionRequest),
        ({"start_date": date(2099, 1, 2), "end_date": date(2099, 1, 3)}, InvalidAcquisitionRequest),
    ],
)
def test_acquisition_request_rejects_invalid_inputs(
    kwargs: dict[str, object],
    exc_type: type[Exception],
) -> None:
    values: dict[str, object] = {
        "symbol": "SPY",
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 1, 5),
        "timeframe": "1Day",
        "provider": "alpaca",
        "feed": "sip",
        "adjustment_mode": "raw",
        "data_root": Path("data"),
        "acknowledge_provider_terms": True,
    }
    values.update(kwargs)

    with pytest.raises(exc_type):
        AcquisitionRequest.model_validate(values)


def test_market_data_credentials_are_separate_from_paper_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_MARKET_DATA_SECRET_KEY", raising=False)

    with pytest.raises(MissingMarketDataCredentials):
        MarketDataCredentials.from_environment()

    monkeypatch.setenv("ALPACA_MARKET_DATA_API_KEY", "market-key")
    monkeypatch.setenv("ALPACA_MARKET_DATA_SECRET_KEY", "market-secret")

    credentials = MarketDataCredentials.from_environment()

    assert credentials.api_key == "market-key"
    assert credentials.secret_key == "market-secret"


def test_settings_display_safe_values_redact_market_data_credentials() -> None:
    settings = Settings(
        alpaca_market_data_api_key=SecretStr("market-key"),
        alpaca_market_data_secret_key=SecretStr("market-secret"),
    )

    safe = settings.display_safe_dict()

    assert safe["alpaca_market_data_api_key_present"] is True
    assert safe["alpaca_market_data_secret_key_present"] is True
    assert "market-key" not in json.dumps(safe)
    assert "market-secret" not in json.dumps(safe)


def test_alpaca_provider_uses_explicit_params_and_paginates() -> None:
    client = FakePageClient([valid_page_1(), valid_page_2()])
    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: client,
        sleep=lambda _seconds: None,
    )

    snapshot = provider.fetch_raw_snapshot(
        make_request(),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )

    assert snapshot.provider_identity.provider_name == "alpaca"
    assert snapshot.provider_identity.sdk_package_name == "alpaca-py"
    assert snapshot.response_page_count == 2
    assert tuple(item.row_count for item in snapshot.pagination) == (2, 2)
    assert client.calls[0]["data"]["feed"] == "sip"
    assert client.calls[0]["data"]["adjustment"] == "raw"
    assert client.calls[0]["data"]["timeframe"] == "1Day"
    assert client.calls[0]["data"]["sort"] == "asc"
    assert client.calls[1]["data"]["page_token"] == "page-2"


def test_installed_alpaca_sdk_contract_for_phase1_page_adapter() -> None:
    public_method_source = inspect.getsource(StockHistoricalDataClient.get_stock_bars)
    client_init_source = inspect.getsource(StockHistoricalDataClient.__init__)
    request = make_request()

    params = alpaca_provider_module._build_stock_bars_params(request)

    assert 'path="/stocks/bars"' in public_method_source
    assert "page_size=10_000" in public_method_source
    assert "raw_data" in client_init_source
    assert "has not been implemented yet" in client_init_source
    assert params["symbols"] == "SPY"
    assert params["start"] == "2024-01-02"
    assert params["end"] == "2024-01-05"
    assert params["timeframe"] == "1Day"
    assert params["feed"] == "sip"
    assert params["adjustment"] == "raw"
    assert params["sort"] == "asc"
    assert params["limit"] == 10_000
    assert "page_token" not in params


def test_alpaca_timeout_seconds_reaches_request_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = json.dumps(valid_page_1(None))

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return valid_page_1(None)

    def fake_request(_session: object, method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, "kwargs": dict(kwargs)})
        return FakeResponse()

    monkeypatch.setattr("requests.sessions.Session.request", fake_request)

    client = alpaca_provider_module._default_client_factory(
        MarketDataCredentials(api_key="key", secret_key="secret"),
        12.5,
    )
    page = client.get(path="/stocks/bars", data={"symbols": "SPY"})

    assert page["bars"]["SPY"]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/v2/stocks/bars")
    assert calls[0]["kwargs"]["timeout"] == 12.5


def test_alpaca_request_timeout_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def timeout_request(_session: object, _method: str, _url: str, **kwargs: Any) -> None:
        calls.append(dict(kwargs))
        raise Timeout("read timed out APCA-API-SECRET-KEY=secret-value")

    monkeypatch.setattr("requests.sessions.Session.request", timeout_request)
    provider = AlpacaMarketDataProvider(
        max_retries=0,
        timeout_seconds=0.25,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ProviderTimeoutFailure) as exc_info:
        provider.fetch_raw_snapshot(
            make_request(),
            credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
            clock=fixed_clock,
        )

    assert len(calls) == 1
    assert calls[0]["timeout"] == 0.25
    assert "secret-value" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)


def test_timeout_and_retry_settings_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    class FakeResponse:
        status_code = 200
        text = json.dumps(valid_page_1(None))

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return valid_page_1(None)

    def flaky_request(_session: object, _method: str, _url: str, **kwargs: Any) -> FakeResponse:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise Timeout("temporary timeout")
        return FakeResponse()

    monkeypatch.setattr("requests.sessions.Session.request", flaky_request)
    provider = AlpacaMarketDataProvider(
        max_retries=1,
        timeout_seconds=0.25,
        sleep=sleeps.append,
    )

    snapshot = provider.fetch_raw_snapshot(
        make_request(start_date=date(2024, 1, 2), end_date=date(2024, 1, 3)),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )

    assert snapshot.response_page_count == 1
    assert [call["timeout"] for call in calls] == [0.25, 0.25]
    assert sleeps == [1.0]


def test_default_market_data_client_does_not_construct_trading_client(tmp_path: Path) -> None:
    code = (
        "import alpaca.trading.client as trading_client\n"
        "def fail(*args, **kwargs):\n"
        "    raise AssertionError('TradingClient constructed')\n"
        "trading_client.TradingClient = fail\n"
        "from spy_market_agent.market_data.alpaca_provider import _default_client_factory\n"
        "from spy_market_agent.market_data.acquisition import MarketDataCredentials\n"
        "_default_client_factory(MarketDataCredentials(api_key='key', secret_key='secret'), 1.0)\n"
        "print('not constructed')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "not constructed"


def test_alpaca_provider_rejects_repeated_pagination_token() -> None:
    client = FakePageClient([valid_page_1("same-token"), valid_page_1("same-token")])
    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: client,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(PaginationFailure):
        provider.fetch_raw_snapshot(
            make_request(),
            credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
            clock=fixed_clock,
        )


def test_alpaca_provider_retries_timeouts_without_real_sleep() -> None:
    client = FakePageClient([TimeoutError("temporary timeout"), valid_page_1(None)])
    sleeps: list[float] = []
    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: client,
        max_retries=1,
        sleep=sleeps.append,
    )

    snapshot = provider.fetch_raw_snapshot(
        make_request(start_date=date(2024, 1, 2), end_date=date(2024, 1, 3)),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )

    assert snapshot.response_page_count == 1
    assert len(client.calls) == 2
    assert sleeps == [1.0]


def test_alpaca_provider_stops_after_bounded_retries() -> None:
    client = FakePageClient([TimeoutError("timeout APCA-API-SECRET-KEY=secret")])
    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: client,
        max_retries=0,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ProviderTimeoutFailure) as exc_info:
        provider.fetch_raw_snapshot(
            make_request(),
            credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
            clock=fixed_clock,
        )

    assert "secret" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)


def test_redact_secret_text_removes_authorization_headers() -> None:
    message = "APCA-API-KEY-ID: KEY123 APCA-API-SECRET-KEY=SECRET123 Authorization: Bearer TOKEN123"

    redacted = redact_secret_text(message)

    assert "KEY123" not in redacted
    assert "SECRET123" not in redacted
    assert "TOKEN123" not in redacted
    assert redacted.count("<redacted>") == 3


def make_snapshot(page: dict[str, Any]) -> Any:
    provider = AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: FakePageClient([page]),
        sleep=lambda _seconds: None,
    )
    return provider.fetch_raw_snapshot(
        make_request(start_date=date(2024, 1, 2), end_date=date(2024, 1, 5)),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )


def canonicalize_page(page: dict[str, Any], *, as_of: datetime = FIXED_NOW) -> tuple[Any, ...]:
    return canonicalize_snapshot(
        request=make_request(start_date=date(2024, 1, 2), end_date=date(2024, 1, 5)),
        snapshot=make_snapshot(page),
        calendar=XNYSCalendar(),
        as_of=as_of,
    )


def test_canonicalization_accepts_valid_raw_and_all_adjusted_modes() -> None:
    raw_provider = AlpacaMarketDataProvider(
        client_factory=valid_page_client_factory(),
        sleep=lambda _seconds: None,
    )
    raw_request = make_request()
    raw_snapshot = raw_provider.fetch_raw_snapshot(
        raw_request,
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )
    raw_bars = canonicalize_snapshot(
        request=raw_request,
        snapshot=raw_snapshot,
        calendar=XNYSCalendar(),
        as_of=FIXED_NOW,
    )
    request = make_request(adjustment_mode="all")
    provider = AlpacaMarketDataProvider(
        client_factory=valid_page_client_factory(),
        sleep=lambda _seconds: None,
    )
    adjusted_snapshot = provider.fetch_raw_snapshot(
        request,
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )
    adjusted_bars = canonicalize_snapshot(
        request=request,
        snapshot=adjusted_snapshot,
        calendar=XNYSCalendar(),
        as_of=FIXED_NOW,
    )

    assert raw_bars[0].session_date == date(2024, 1, 2)
    assert raw_bars[0].adjusted_close is None
    assert adjusted_bars[0].adjusted_close == adjusted_bars[0].close
    assert adjusted_bars[0].adjustment_mode == "all"


@pytest.mark.parametrize(
    ("page", "exc_type"),
    [
        ({"unexpected": "shape"}, ProviderMalformedResponse),
        (
            {"bars": {"SPY": [{"t": "2024-01-02T05:00:00Z"}]}, "next_page_token": None},
            ProviderIncompleteResponse,
        ),
        ({"bars": {"AAPL": []}, "next_page_token": None}, CanonicalizationFailure),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                    ]
                },
                "next_page_token": None,
            },
            SessionValidationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-03T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                    ]
                },
                "next_page_token": None,
            },
            SessionValidationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                        {
                            "t": "2024-01-04T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                        {
                            "t": "2024-01-05T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        },
                    ]
                },
                "next_page_token": None,
            },
            SessionValidationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-06T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            SessionValidationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-01T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            SessionValidationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "0",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "-1",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "NaN",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "-1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "101",
                            "l": "99",
                            "c": "100",
                            "v": "1.5",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
        (
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2024-01-02T05:00:00Z",
                            "o": "100",
                            "h": "99",
                            "l": "101",
                            "c": "100",
                            "v": "1",
                        }
                    ]
                },
                "next_page_token": None,
            },
            CanonicalizationFailure,
        ),
    ],
)
def test_canonicalization_rejects_malformed_or_invalid_bars(
    page: dict[str, Any],
    exc_type: type[Exception],
) -> None:
    with pytest.raises(exc_type):
        canonicalize_page(page)


def test_canonicalization_rejects_incomplete_final_session() -> None:
    page = {
        "bars": {
            "SPY": [
                {
                    "t": "2024-01-02T05:00:00Z",
                    "o": "100",
                    "h": "101",
                    "l": "99",
                    "c": "100",
                    "v": "1",
                }
            ]
        },
        "next_page_token": None,
    }

    with pytest.raises(SessionValidationFailure):
        canonicalize_snapshot(
            request=make_request(start_date=date(2024, 1, 2), end_date=date(2024, 1, 2)),
            snapshot=make_snapshot(page),
            calendar=XNYSCalendar(),
            as_of=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        )


def test_manifest_checksums_and_dataset_identity_are_deterministic(tmp_path: Path) -> None:
    request = make_request(data_root=Path("data"), adjustment_mode="raw")
    provider = AlpacaMarketDataProvider(
        client_factory=valid_page_client_factory(),
        sleep=lambda _seconds: None,
    )
    snapshot = provider.fetch_raw_snapshot(
        request,
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
    )
    bars = canonicalize_snapshot(
        request=request,
        snapshot=snapshot,
        calendar=XNYSCalendar(),
        as_of=FIXED_NOW,
    )
    canonical_bytes = canonical_csv_bytes(bars)
    raw_bytes = raw_snapshot_json_bytes(snapshot)
    canonical_checksum = canonical_content_checksum(
        bars=bars,
        provider=request.provider,
        feed=request.feed,
        timeframe=request.timeframe,
        adjustment_mode=request.adjustment_mode,
    )
    dataset_id = dataset_identity(request=request, canonical_checksum=canonical_checksum)
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id=dataset_id)
    manifest = build_manifest(
        request=request,
        snapshot=snapshot,
        bars=bars,
        calendar=XNYSCalendar(),
        relative_raw_path=store.relative_path(paths.raw_snapshot_path),
        relative_canonical_path=store.relative_path(paths.canonical_path),
        relative_manifest_path=store.relative_path(paths.manifest_path),
        canonical_artifact_checksum=sha256_bytes(canonical_bytes),
        raw_artifact_checksum=sha256_bytes(raw_bytes),
    )
    manifest = finalized_manifest_with_checksum(manifest)

    assert source_checksum(snapshot) == source_checksum(snapshot)
    assert manifest.dataset_id == dataset_id
    assert manifest.canonical_content_checksum == canonical_checksum
    assert manifest.package_version == "2.0.0b1"
    changed_request = make_request(feed="iex")
    changed_id = dataset_identity(request=changed_request, canonical_checksum=canonical_checksum)
    assert changed_id != dataset_id


def test_deep_verification_parsers_reject_malformed_artifacts() -> None:
    header = ",".join(canonical_csv_header())
    valid_row = ",".join(
        (
            "SPY",
            "2024-01-02",
            "100",
            "101",
            "99",
            "100.5",
            "",
            "1000000",
            "alpaca",
            "sip",
            "raw",
            "UTC",
            "America/New_York",
            "lineage-" + "a" * 24,
        )
    )

    malformed_payloads = (
        lambda: load_manifest_bytes(b'{"extra":true}\n'),
        lambda: load_raw_snapshot_bytes(b'{"extra":true}\n'),
        lambda: canonical_bars_from_csv_bytes(f"{header}\n{valid_row}".encode()),
        lambda: canonical_bars_from_csv_bytes(b"wrong,header\nvalue,value\n"),
        lambda: canonical_bars_from_csv_bytes(f"{header}\n{valid_row},extra\n".encode()),
        lambda: canonical_bars_from_csv_bytes(
            f"{header}\n{valid_row.rsplit(',', maxsplit=1)[0]}\n".encode()
        ),
        lambda: canonical_bars_from_csv_bytes(
            f"{header}\n{valid_row.replace('SPY', 'AAPL', 1)}\n".encode()
        ),
        lambda: canonical_bars_from_csv_bytes(f"{header}\n".encode()),
    )

    for parse in malformed_payloads:
        with pytest.raises(ManifestValidationFailure):
            parse()


def test_storage_missing_manifest_paths_fail_closed(tmp_path: Path) -> None:
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    missing_manifest = tmp_path / "data/manifests/missing.manifest.json"

    with pytest.raises(ChecksumMismatch):
        store.load_existing_manifest(missing_manifest)
    with pytest.raises(ChecksumMismatch):
        store.verify_manifest_artifacts(missing_manifest)


def test_storage_rejects_unsafe_roots_and_conflicting_artifacts(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDataPath):
        DatasetStore(Path("../data"), repository_root=tmp_path)
    with pytest.raises(UnsafeDataPath):
        DatasetStore(Path("src/data"), repository_root=tmp_path)

    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-test")
    payload = b'{"ok":true}\n'
    result = store.write_dataset(
        paths=paths,
        raw_bytes=payload,
        canonical_bytes=payload,
        manifest_bytes=payload,
        expected_raw_checksum=sha256_bytes(payload),
        expected_canonical_checksum=sha256_bytes(payload),
        expected_manifest_checksum=sha256_bytes(payload),
    )

    assert all(write_result.created for write_result in result)

    repeated = store.write_dataset(
        paths=paths,
        raw_bytes=payload,
        canonical_bytes=payload,
        manifest_bytes=payload,
        expected_raw_checksum=sha256_bytes(payload),
        expected_canonical_checksum=sha256_bytes(payload),
        expected_manifest_checksum=sha256_bytes(payload),
    )

    assert not any(write_result.created for write_result in repeated)

    with pytest.raises(ExistingDatasetConflict):
        store.write_dataset(
            paths=paths,
            raw_bytes=b"different\n",
            canonical_bytes=payload,
            manifest_bytes=payload,
            expected_raw_checksum=sha256_bytes(b"different\n"),
            expected_canonical_checksum=sha256_bytes(payload),
            expected_manifest_checksum=sha256_bytes(payload),
        )


def test_write_dataset_rolls_back_raw_when_canonical_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-rollback-canonical")
    payload = b'{"ok":true}\n'
    original_write = store._write_atomic_if_needed

    def fail_canonical(
        path: Path,
        payload_bytes: bytes,
        *,
        expected_checksum: str,
    ) -> Any:
        if path == paths.canonical_path:
            raise AtomicWriteFailure("canonical write failed")
        return original_write(path, payload_bytes, expected_checksum=expected_checksum)

    monkeypatch.setattr(store, "_write_atomic_if_needed", fail_canonical)

    with pytest.raises(AtomicWriteFailure):
        store.write_dataset(
            paths=paths,
            raw_bytes=payload,
            canonical_bytes=payload,
            manifest_bytes=payload,
            expected_raw_checksum=sha256_bytes(payload),
            expected_canonical_checksum=sha256_bytes(payload),
            expected_manifest_checksum=sha256_bytes(payload),
        )

    assert not paths.raw_snapshot_path.exists()
    assert not paths.canonical_path.exists()
    assert not paths.manifest_path.exists()


def test_write_dataset_rolls_back_raw_and_canonical_when_manifest_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-rollback-manifest")
    payload = b'{"ok":true}\n'
    original_write = store._write_atomic_if_needed

    def fail_manifest(
        path: Path,
        payload_bytes: bytes,
        *,
        expected_checksum: str,
    ) -> Any:
        if path == paths.manifest_path:
            raise AtomicWriteFailure("manifest write failed")
        return original_write(path, payload_bytes, expected_checksum=expected_checksum)

    monkeypatch.setattr(store, "_write_atomic_if_needed", fail_manifest)

    with pytest.raises(AtomicWriteFailure):
        store.write_dataset(
            paths=paths,
            raw_bytes=payload,
            canonical_bytes=payload,
            manifest_bytes=payload,
            expected_raw_checksum=sha256_bytes(payload),
            expected_canonical_checksum=sha256_bytes(payload),
            expected_manifest_checksum=sha256_bytes(payload),
        )

    assert not paths.raw_snapshot_path.exists()
    assert not paths.canonical_path.exists()
    assert not paths.manifest_path.exists()


def test_write_dataset_rolls_back_after_checksum_failure(tmp_path: Path) -> None:
    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-rollback-checksum")
    payload = b'{"ok":true}\n'

    with pytest.raises(ChecksumMismatch):
        store.write_dataset(
            paths=paths,
            raw_bytes=payload,
            canonical_bytes=payload,
            manifest_bytes=payload,
            expected_raw_checksum=sha256_bytes(payload),
            expected_canonical_checksum=sha256_bytes(payload),
            expected_manifest_checksum="0" * 64,
        )

    assert not paths.raw_snapshot_path.exists()
    assert not paths.canonical_path.exists()
    assert not paths.manifest_path.exists()


def test_write_dataset_preserves_matching_existing_artifacts_when_later_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-preserve-existing")
    payload = b'{"ok":true}\n'
    checksum = sha256_bytes(payload)
    store.write_dataset(
        paths=paths,
        raw_bytes=payload,
        canonical_bytes=payload,
        manifest_bytes=payload,
        expected_raw_checksum=checksum,
        expected_canonical_checksum=checksum,
        expected_manifest_checksum=checksum,
    )
    paths.manifest_path.unlink()
    original_write = store._write_atomic_if_needed

    def fail_manifest(
        path: Path,
        payload_bytes: bytes,
        *,
        expected_checksum: str,
    ) -> Any:
        if path == paths.manifest_path:
            raise AtomicWriteFailure("manifest write failed")
        return original_write(path, payload_bytes, expected_checksum=expected_checksum)

    monkeypatch.setattr(store, "_write_atomic_if_needed", fail_manifest)

    with pytest.raises(AtomicWriteFailure):
        store.write_dataset(
            paths=paths,
            raw_bytes=payload,
            canonical_bytes=payload,
            manifest_bytes=payload,
            expected_raw_checksum=checksum,
            expected_canonical_checksum=checksum,
            expected_manifest_checksum=checksum,
        )

    assert paths.raw_snapshot_path.exists()
    assert paths.canonical_path.exists()
    assert not paths.manifest_path.exists()


def test_storage_rejects_symlink_artifact_path(tmp_path: Path) -> None:
    request = make_request()
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    paths = store.artifact_paths(request=request, dataset_id="spy-v2p1-symlink")
    paths.raw_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        paths.raw_snapshot_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not supported on this filesystem")

    with pytest.raises(UnsafeDataPath):
        store.write_dataset(
            paths=paths,
            raw_bytes=b"raw\n",
            canonical_bytes=b"canonical\n",
            manifest_bytes=b"manifest\n",
            expected_raw_checksum=sha256_bytes(b"raw\n"),
            expected_canonical_checksum=sha256_bytes(b"canonical\n"),
            expected_manifest_checksum=sha256_bytes(b"manifest\n"),
        )


def test_cli_help_and_invalid_inputs_do_not_require_credentials() -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "spy_market_agent.market_data.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Explicit Phase 1 historical SPY market-data commands" in help_result.stdout

    invalid_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "spy_market_agent.market_data.cli",
            "acquire",
            "--provider",
            "alpaca",
            "--symbol",
            "SPY",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-05",
            "--timeframe",
            "1Day",
            "--adjustment",
            "raw",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid_result.returncode == 1
    assert "provider terms" in invalid_result.stderr


def test_cli_missing_credentials_fails_before_write(tmp_path: Path) -> None:

    env = os.environ.copy()
    env.pop("ALPACA_MARKET_DATA_API_KEY", None)
    env.pop("ALPACA_MARKET_DATA_SECRET_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "spy_market_agent.market_data.cli",
            "acquire",
            "--provider",
            "alpaca",
            "--symbol",
            "SPY",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-05",
            "--timeframe",
            "1Day",
            "--adjustment",
            "raw",
            "--data-root",
            "data",
            "--acknowledge-provider-terms",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "ALPACA_MARKET_DATA_API_KEY" in result.stderr
    assert not (tmp_path / "data").exists()


def test_cli_acquire_success_runs_in_process_without_trading_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_KEY", "market-key")
    monkeypatch.setenv("ALPACA_MARKET_DATA_SECRET_KEY", "market-secret")

    import spy_market_agent.market_data.alpaca_provider as alpaca_provider_module

    def fake_provider_factory(**_kwargs: object) -> AlpacaMarketDataProvider:
        return AlpacaMarketDataProvider(
            client_factory=valid_page_client_factory(),
            sleep=lambda _seconds: None,
        )

    monkeypatch.setattr(
        alpaca_provider_module,
        "AlpacaMarketDataProvider",
        fake_provider_factory,
    )

    exit_code = market_data_cli_main(
        [
            "acquire",
            "--provider",
            "alpaca",
            "--symbol",
            "SPY",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-05",
            "--timeframe",
            "1Day",
            "--feed",
            "sip",
            "--adjustment",
            "raw",
            "--data-root",
            "data",
            "--acknowledge-provider-terms",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "dataset_id=spy-v2p1-" in output.out
    assert "manifest_path=data/manifests/" in output.out
    assert "market-key" not in output.out + output.err
    assert not any(path.name.endswith(".tmp") for path in tmp_path.rglob("*"))


def test_cli_verify_success_and_failure_run_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    request = make_request(data_root=Path("data"))
    artifacts = acquire_historical_spy_data(
        request,
        provider=AlpacaMarketDataProvider(
            client_factory=valid_page_client_factory(),
            sleep=lambda _seconds: None,
        ),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )
    manifest_path = artifacts.manifest.generated_file_locations.manifest_path

    success = market_data_cli_main(["verify", "--manifest", manifest_path, "--data-root", "data"])
    success_output = capsys.readouterr()

    assert success == 0
    assert "verification=passed" in success_output.out

    canonical_path = tmp_path / artifacts.manifest.generated_file_locations.canonical_path
    canonical_path.write_text("corrupted\n", encoding="utf-8")
    failure = market_data_cli_main(["verify", "--manifest", manifest_path, "--data-root", "data"])
    failure_output = capsys.readouterr()

    assert failure == 1
    assert "verification failed" in failure_output.err


def test_importing_phase1_modules_has_no_files_or_alpaca_client_construction(
    tmp_path: Path,
) -> None:
    code = (
        "import importlib, pathlib, sys; "
        "importlib.import_module('spy_market_agent.market_data.acquisition'); "
        "importlib.import_module('spy_market_agent.market_data.pipeline'); "
        "importlib.import_module('spy_market_agent.api'); "
        "importlib.import_module('spy_market_agent.dashboard'); "
        "print('alpaca.trading.client' in sys.modules); "
        "print(list(pathlib.Path('.').iterdir()))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["False", "[]"]


def test_full_pipeline_with_fake_alpaca_pages_verifies_artifacts(tmp_path: Path) -> None:
    request = make_request(data_root=Path("data"))
    provider = AlpacaMarketDataProvider(
        client_factory=valid_page_client_factory(),
        sleep=lambda _seconds: None,
    )

    artifacts = acquire_historical_spy_data(
        request,
        provider=provider,
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=fixed_clock,
        repository_root=tmp_path,
    )

    assert artifacts.manifest.row_count == 4
    assert len(artifacts.created_files) == 3
    store = DatasetStore(Path("data"), repository_root=tmp_path)
    verified = store.verify_manifest_artifacts(
        tmp_path / artifacts.manifest.generated_file_locations.manifest_path
    )
    assert verified.dataset_id == artifacts.manifest.dataset_id

    canonical_path = tmp_path / artifacts.manifest.generated_file_locations.canonical_path
    canonical_path.write_text("corrupted\n", encoding="utf-8")
    with pytest.raises(ChecksumMismatch):
        store.verify_manifest_artifacts(
            tmp_path / artifacts.manifest.generated_file_locations.manifest_path
        )


def test_utc_now_returns_aware_utc_timestamp() -> None:
    value = utc_now()

    assert value.tzinfo is UTC
