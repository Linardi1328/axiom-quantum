from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as datetime_time
from importlib.metadata import version
from typing import Any, Protocol, cast

from alpaca.common.enums import Sort
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from requests import Response, Session

from spy_market_agent.market_data.acquisition import (
    AcquisitionRequest,
    Clock,
    MarketDataCredentials,
    PaginationMetadata,
    ProviderIdentity,
    RawAcquisitionSnapshot,
)
from spy_market_agent.market_data.errors import (
    PaginationFailure,
    ProviderAuthenticationFailure,
    ProviderAuthorizationFailure,
    ProviderMalformedResponse,
    ProviderRateLimitFailure,
    ProviderTimeoutFailure,
    ProviderUnavailableFailure,
    redact_secret_text,
)

ALPACA_DATA_API_VERSION = "v2"
ALPACA_STOCK_BARS_PATH = "/stocks/bars"
DEFAULT_PAGE_LIMIT = 10_000
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 30.0


class AlpacaPageClient(Protocol):
    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Return one raw Alpaca response page."""


ClientFactory = Callable[[MarketDataCredentials, float], AlpacaPageClient]
Sleep = Callable[[float], None]


class AlpacaMarketDataProvider:
    """Explicit Alpaca historical market-data adapter for Phase 1 acquisition."""

    name = "alpaca"

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Sleep = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be nonnegative.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._client_factory = client_factory or _default_client_factory
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep

    def fetch_raw_snapshot(
        self,
        request: AcquisitionRequest,
        *,
        credentials: MarketDataCredentials,
        clock: Clock,
    ) -> RawAcquisitionSnapshot:
        client = self._client_factory(credentials, self._timeout_seconds)
        params = _build_stock_bars_params(request)
        pages: list[dict[str, Any]] = []
        pagination: list[PaginationMetadata] = []
        seen_tokens: set[str] = set()
        page_token: str | None = None
        page_number = 1

        while True:
            if page_token is not None:
                if page_token in seen_tokens:
                    raise PaginationFailure("provider returned a repeated pagination token.")
                seen_tokens.add(page_token)
            page_params = dict(params)
            page_params["page_token"] = page_token
            page = self._fetch_page_with_retries(client, page_params)
            if not isinstance(page, dict):
                raise ProviderMalformedResponse("Alpaca page response must be an object.")
            bars = page.get("bars")
            if not isinstance(bars, dict):
                raise ProviderMalformedResponse("Alpaca page response must contain bars.")
            symbol_bars = bars.get(request.symbol, [])
            if symbol_bars is None:
                symbol_bars = []
            if not isinstance(symbol_bars, list):
                raise ProviderMalformedResponse("Alpaca bars for SPY must be a list.")
            next_page_token = page.get("next_page_token")
            if next_page_token is not None and not isinstance(next_page_token, str):
                raise ProviderMalformedResponse("Alpaca next_page_token must be a string or null.")
            pages.append(_sanitized_page(page))
            pagination.append(
                PaginationMetadata(
                    page_number=page_number,
                    request_page_token=page_token,
                    next_page_token=next_page_token,
                    row_count=len(symbol_bars),
                )
            )
            if next_page_token is None:
                break
            page_token = next_page_token
            page_number += 1

        return RawAcquisitionSnapshot(
            sanitized_request=request.sanitized_parameters(),
            provider_identity=ProviderIdentity(
                provider_name=self.name,
                api_version=ALPACA_DATA_API_VERSION,
                sdk_package_name="alpaca-py",
                sdk_package_version=version("alpaca-py"),
                feed=request.feed,
                adjustment_mode=request.adjustment_mode,
                access_method=(
                    "alpaca-py StockHistoricalDataClient low-level get /v2/stocks/bars "
                    "with bounded requests session timeout"
                ),
            ),
            retrieval_timestamp=clock().astimezone(UTC),
            source_timezone="UTC",
            provider_response_payload={"pages": pages},
            pagination=tuple(pagination),
            response_page_count=len(pages),
            corporate_actions_payload=None,
        )

    def _fetch_page_with_retries(
        self,
        client: AlpacaPageClient,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        attempts_allowed = self._max_retries + 1
        for attempt in range(1, attempts_allowed + 1):
            try:
                return client.get(path=ALPACA_STOCK_BARS_PATH, data=params)
            except APIError as exc:
                mapped = _map_api_error(exc)
                if not _is_retryable_exception(mapped) or attempt >= attempts_allowed:
                    raise mapped from exc
            except Exception as exc:
                mapped = _map_transport_error(exc)
                if not _is_retryable_exception(mapped) or attempt >= attempts_allowed:
                    raise mapped from exc
            self._sleep(float(attempt))
        raise ProviderUnavailableFailure("provider retry loop exhausted unexpectedly.")


class _TimeoutSession:
    """Requests session that enforces the Phase 1 per-request timeout."""

    def __init__(self, timeout_seconds: float) -> None:
        self._session = Session()
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, url: str | bytes, **kwargs: Any) -> Response:
        kwargs.setdefault("timeout", self.timeout_seconds)
        return self._session.request(method, url, **kwargs)


class _AlpacaStockBarsPageAdapter:
    """Small adapter around alpaca-py's raw page request boundary."""

    def __init__(self, client: StockHistoricalDataClient) -> None:
        self._client = client

    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], self._client.get(path=path, data=data))


def _default_client_factory(
    credentials: MarketDataCredentials,
    timeout_seconds: float,
) -> AlpacaPageClient:
    client = StockHistoricalDataClient(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        raw_data=True,
    )
    _install_timeout_session(client, timeout_seconds=timeout_seconds)
    return _AlpacaStockBarsPageAdapter(client)


def _install_timeout_session(
    client: StockHistoricalDataClient,
    *,
    timeout_seconds: float,
) -> None:
    client._session = cast(Session, _TimeoutSession(timeout_seconds))


def _build_stock_bars_params(request: AcquisitionRequest) -> dict[str, Any]:
    stock_request = StockBarsRequest(
        symbol_or_symbols=request.symbol,
        start=datetime.combine(request.start_date, datetime_time.min, tzinfo=UTC),
        end=datetime.combine(request.end_date, datetime_time.max, tzinfo=UTC),
        timeframe=TimeFrame.Day,
        adjustment=_sdk_adjustment(request.adjustment_mode),
        feed=_sdk_feed(request.feed),
        sort=Sort.ASC,
        asof=request.asof.isoformat() if request.asof else None,
    )
    params = stock_request.to_request_fields()
    params["start"] = request.start_date.isoformat()
    params["end"] = request.end_date.isoformat()
    params["timeframe"] = "1Day"
    params["adjustment"] = request.adjustment_mode
    params["feed"] = request.feed
    params["sort"] = "asc"
    params["limit"] = DEFAULT_PAGE_LIMIT
    if request.asof is None:
        params.pop("asof", None)
    return params


def _sdk_adjustment(adjustment_mode: str) -> Adjustment:
    if adjustment_mode == "raw":
        return Adjustment.RAW
    if adjustment_mode == "all":
        return Adjustment.ALL
    raise ProviderMalformedResponse(f"unsupported Alpaca adjustment mode {adjustment_mode!r}.")


def _sdk_feed(feed: str) -> DataFeed:
    if feed == "sip":
        return DataFeed.SIP
    if feed == "iex":
        return DataFeed.IEX
    raise ProviderMalformedResponse(f"unsupported Alpaca feed {feed!r}.")


def _map_api_error(exc: APIError) -> Exception:
    status_code = exc.status_code
    message = redact_secret_text(_api_error_message(exc))
    if status_code == 401:
        return ProviderAuthenticationFailure(message)
    if status_code == 403:
        return ProviderAuthorizationFailure(message)
    if status_code == 429:
        return ProviderRateLimitFailure(message)
    if status_code is not None and 500 <= status_code <= 599:
        return ProviderUnavailableFailure(message)
    return ProviderMalformedResponse(message)


def _api_error_message(exc: APIError) -> str:
    try:
        return f"Alpaca API error {exc.status_code}: {exc.message}"
    except (KeyError, ValueError, TypeError):
        return f"Alpaca API error {exc.status_code}: {exc}"


def _map_transport_error(exc: Exception) -> Exception:
    class_name = exc.__class__.__name__.lower()
    message = redact_secret_text(exc)
    if "timeout" in class_name or "timeout" in message.lower():
        return ProviderTimeoutFailure(message)
    if "connection" in class_name or "temporar" in message.lower():
        return ProviderUnavailableFailure(message)
    return ProviderMalformedResponse(message)


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(
        exc,
        (ProviderRateLimitFailure, ProviderUnavailableFailure, ProviderTimeoutFailure),
    )


def _sanitized_page(page: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    for key, value in page.items():
        if key.lower() in {"authorization", "apca-api-key-id", "apca-api-secret-key"}:
            allowed[key] = "<redacted>"
        else:
            allowed[key] = value
    return allowed
