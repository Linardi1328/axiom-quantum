"""Allowlisted context transport and bounded pagination; SPY uses its old path."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from typing import Any, cast

from alpaca.common.exceptions import APIError
from requests import Response, Session

from spy_market_agent.market_data.acquisition import (
    Clock,
    MarketDataCredentials,
    PaginationMetadata,
    ProviderIdentity,
    RawAcquisitionSnapshot,
)
from spy_market_agent.market_data.context_contracts import (
    ACCESS_METHOD,
    MAX_BYTES,
    MAX_PAGES,
    MAX_ROWS,
    MAX_SECONDS,
    ContextAcquisitionRequest,
    aware,
)
from spy_market_agent.market_data.errors import (
    CanonicalizationFailure,
    InvalidAcquisitionRequest,
    PaginationFailure,
    ProviderAuthenticationFailure,
    ProviderAuthorizationFailure,
    ProviderMalformedResponse,
    ProviderRateLimitFailure,
    ProviderTimeoutFailure,
    ProviderUnavailableFailure,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes

_permit: ContextVar[ContextAcquisitionRequest | None] = ContextVar(
    "context_request_permit", default=None
)


@contextmanager
def context_permit(request: ContextAcquisitionRequest) -> Iterator[None]:
    token = _permit.set(request)
    try:
        yield
    finally:
        _permit.reset(token)


def decimal_price(value: object, name: str) -> Decimal:
    if type(value) not in (str, int, float):
        raise CanonicalizationFailure(f"invalid {name} in context bar.")
    if (isinstance(value, str) and len(value) > 128) or (
        isinstance(value, int) and value.bit_length() > 128
    ):
        raise CanonicalizationFailure(f"excessive {name} in context bar.")
    text = str(value)
    if len(text) > 128 or not re.fullmatch(r"\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        raise CanonicalizationFailure(f"invalid {name} in context bar.")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise CanonicalizationFailure(f"invalid {name} in context bar.") from None
    digits = number.as_tuple()
    if (
        not number.is_finite()
        or number <= 0
        or len(digits.digits) > 38
        or not -18 <= int(digits.exponent) <= 18
        or not -12 <= number.adjusted() <= 12
    ):
        raise CanonicalizationFailure(f"nonpositive, nonfinite or excessive {name} in context bar.")
    return number


def decimal_text(value: object, name: str) -> str:
    text = format(decimal_price(value, name), "f")  # exact, no normalize()/context rounding
    return text.rstrip("0").rstrip(".") if "." in text else text


def integer_volume(value: object) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and len(value) <= 19 and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise CanonicalizationFailure("invalid context bar volume.")
    if not 0 <= result <= 2**63 - 1:
        raise CanonicalizationFailure("invalid context bar volume.")
    return result


def validate_page(
    page: object, symbol: str, credentials: MarketDataCredentials | None = None
) -> int:
    if not isinstance(page, dict) or set(page) != {"bars", "next_page_token"}:
        raise ProviderMalformedResponse("context page contains unexpected or sensitive metadata.")
    bars = page["bars"]
    if not isinstance(bars, dict) or set(bars) != {symbol}:
        raise ProviderMalformedResponse("context page contains an unexpected symbol.")
    records = bars[symbol]
    if not isinstance(records, list) or len(records) > 10_000:
        raise ProviderMalformedResponse("invalid context page bar count.")
    token = page["next_page_token"]
    if token is not None and (
        not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_+=/.-]{1,512}", token)
    ):
        raise PaginationFailure("invalid context pagination token.")
    for record in records:
        if (
            not isinstance(record, dict)
            or not {"t", "o", "h", "l", "c", "v"} <= set(record)
            or set(record) - {"t", "o", "h", "l", "c", "v", "n", "vw"}
        ):
            raise ProviderMalformedResponse(
                "context bar contains incomplete or unexpected metadata."
            )
        if not isinstance(record["t"], str) or len(record["t"]) > 64:
            raise CanonicalizationFailure("invalid context bar timestamp.")
        for key in ("o", "h", "l", "c", "vw"):
            if key in record:
                decimal_price(record[key], key)
        integer_volume(record["v"])
        if "n" in record:
            integer_volume(record["n"])
    # The schema above permits scalars only, so nested secrets cannot survive.
    if credentials is not None:
        encoded = canonical_json_bytes(page)
        for secret in (credentials.api_key, credentials.secret_key):
            if (
                secret.encode() in encoded
                or any(secret in str(value) for record in records for value in record.values())
                or (token and secret in token)
            ):
                raise ProviderMalformedResponse(
                    "context response contains supplied credential material."
                )
    if len(canonical_json_bytes(page)) > MAX_BYTES:
        raise ProviderMalformedResponse("context page exceeds byte budget.")
    return len(records)


def validate_snapshot(
    snapshot: RawAcquisitionSnapshot,
    request: ContextAcquisitionRequest,
    credentials: MarketDataCredentials | None = None,
) -> None:
    if (
        set(snapshot.__dict__) != set(RawAcquisitionSnapshot.model_fields)
        or snapshot.__pydantic_extra__
        or snapshot.corporate_actions_payload is not None
        or snapshot.source_timezone != "UTC"
        or snapshot.sanitized_request != request.sanitized_parameters()
    ):
        raise ProviderMalformedResponse("context source request or envelope identity mismatch.")
    if type(snapshot) is not RawAcquisitionSnapshot:
        raise ProviderMalformedResponse("context snapshot must use the supported raw model.")
    identity = snapshot.provider_identity
    if type(identity) is not ProviderIdentity or any(
        type(item) is not PaginationMetadata for item in snapshot.pagination
    ):
        raise ProviderMalformedResponse("context source metadata types are unsupported.")
    if (
        snapshot.retrieval_timestamp.tzinfo is None
        or snapshot.retrieval_timestamp.utcoffset() != timedelta(0)
    ):
        raise ProviderMalformedResponse("context raw retrieval timestamp must be UTC-aware.")
    expected = ProviderIdentity(
        provider_name="alpaca",
        api_version="v2",
        sdk_package_name="alpaca-py",
        sdk_package_version=version("alpaca-py"),
        feed="sip",
        adjustment_mode="all",
        access_method=ACCESS_METHOD,
    )
    if identity.__dict__ != expected.__dict__ or identity.__pydantic_extra__:
        raise ProviderMalformedResponse("context provider identity mismatch.")
    payload = snapshot.provider_response_payload
    if not isinstance(payload, dict) or set(payload) != {"pages"}:
        raise ProviderMalformedResponse("context raw envelope contains unexpected metadata.")
    pages = payload["pages"]
    if (
        not isinstance(pages, list)
        or not 1 <= len(pages) <= MAX_PAGES
        or type(snapshot.response_page_count) is not int
        or snapshot.response_page_count != len(pages)
        or len(snapshot.pagination) != len(pages)
    ):
        raise PaginationFailure("context page counts exceed budget or disagree.")
    prior: str | None = None
    seen: set[str] = set()
    rows = 0
    for index, (page, metadata) in enumerate(zip(pages, snapshot.pagination, strict=True), start=1):
        count = validate_page(page, request.symbol, credentials)
        next_token = page["next_page_token"]
        if (
            type(metadata.page_number) is not int
            or metadata.page_number != index
            or type(metadata.row_count) is not int
            or metadata.row_count != count
            or metadata.request_page_token != prior
            or metadata.next_page_token != next_token
            or (index < len(pages) and next_token is None)
            or (index == len(pages) and next_token is not None)
            or (next_token is not None and next_token in seen)
        ):
            raise PaginationFailure("context pagination chain or raw payload is inconsistent.")
        if next_token:
            seen.add(next_token)
        prior = next_token
        rows += count
    if rows > MAX_ROWS:
        raise PaginationFailure("context row budget exceeded.")
    if credentials is not None:
        encoded = canonical_json_bytes(snapshot)
        if any(
            secret.encode() in encoded for secret in (credentials.api_key, credentials.secret_key)
        ):
            raise ProviderMalformedResponse(
                "context snapshot contains supplied credential material."
            )


def _classification(exc: Exception) -> type[Exception]:
    if isinstance(exc, APIError):
        try:
            status = exc.status_code
        except Exception:
            status = None
        if status == 401:
            return ProviderAuthenticationFailure
        if status == 403:
            return ProviderAuthorizationFailure
        if status == 429:
            return ProviderRateLimitFailure
        if status is not None and 500 <= status <= 599:
            return ProviderUnavailableFailure
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ProviderTimeoutFailure
    if "connection" in name:
        return ProviderUnavailableFailure
    return ProviderMalformedResponse


def fetch_context_snapshot(
    adapter: Any,
    request: ContextAcquisitionRequest,
    *,
    credentials: MarketDataCredentials,
    clock: Clock,
) -> RawAcquisitionSnapshot:
    from spy_market_agent.market_data.alpaca_provider import (
        _AlpacaStockBarsPageAdapter,
        _build_stock_bars_params,
        _TimeoutSession,
    )

    if _permit.get() is not request:
        raise InvalidAcquisitionRequest(
            "context transport requires the acquisition preflight boundary."
        )
    if (
        type(adapter._max_retries) is not int
        or not 0 <= adapter._max_retries <= 3
        or not math.isfinite(adapter._timeout_seconds)
        or not 0 < adapter._timeout_seconds <= 30
    ):
        raise InvalidAcquisitionRequest("context provider retry or timeout policy is unsupported.")
    monotonic: Callable[[], float] = adapter._context_monotonic
    started = monotonic()

    def remaining() -> float:
        elapsed = monotonic() - started
        if not math.isfinite(elapsed) or elapsed < 0 or elapsed >= MAX_SECONDS:
            raise PaginationFailure("context elapsed-time budget exhausted.")
        return MAX_SECONDS - elapsed

    failure: type[Exception] | None = None
    try:
        client = adapter._client_factory(credentials, min(adapter._timeout_seconds, remaining()))
    except Exception as exc:
        failure = _classification(exc)
    if failure:
        raise failure("context provider client construction failed.") from None
    if isinstance(client, _AlpacaStockBarsPageAdapter):

        class ContextDeadlineSession(_TimeoutSession):
            def request(self, method: str, url: str | bytes, **kwargs: Any) -> Response:
                # Recompute on EVERY SDK request/retry, not just client construction.
                kwargs["timeout"] = min(
                    float(kwargs.get("timeout", self.timeout_seconds)), remaining()
                )
                return super().request(method, url, **kwargs)

        client._client._session = cast(Session, ContextDeadlineSession(adapter._timeout_seconds))
    params = _build_stock_bars_params(request)
    pages: list[dict[str, Any]] = []
    pagination: list[PaginationMetadata] = []
    prior: str | None = None
    seen: set[str] = set()
    rows = 0
    for index in range(1, MAX_PAGES + 1):
        remaining()
        page_params = {**params, "page_token": prior}
        page: Any = None
        for attempt in range(adapter._max_retries + 1):
            remaining()
            failure = None
            try:
                page = client.get(path="/stocks/bars", data=page_params)
            except Exception as exc:
                failure = _classification(exc)
            remaining()
            if failure is None:
                break
            if (
                failure
                not in (
                    ProviderRateLimitFailure,
                    ProviderUnavailableFailure,
                    ProviderTimeoutFailure,
                )
                or attempt == adapter._max_retries
            ):
                raise failure("context provider request failed.") from None
            adapter._sleep(min(float(attempt + 1), remaining()))
        rows += validate_page(page, request.symbol, credentials)
        if rows > MAX_ROWS:
            raise PaginationFailure("context row budget exceeded.")
        next_token = page["next_page_token"]
        if next_token is not None and next_token in seen:
            raise PaginationFailure("context provider repeated pagination token.")
        pages.append(page)
        if sum(len(canonical_json_bytes(item)) for item in pages) > MAX_BYTES:
            raise PaginationFailure("context raw byte budget exceeded.")
        pagination.append(
            PaginationMetadata(
                page_number=index,
                request_page_token=prior,
                next_page_token=next_token,
                row_count=len(page["bars"][request.symbol]),
            )
        )
        if next_token is None:
            break
        seen.add(next_token)
        prior = next_token
    else:
        raise PaginationFailure("context page budget exhausted.")
    remaining()
    return RawAcquisitionSnapshot(
        sanitized_request=request.sanitized_parameters(),
        provider_identity=ProviderIdentity(
            provider_name="alpaca",
            api_version="v2",
            sdk_package_name="alpaca-py",
            sdk_package_version=version("alpaca-py"),
            feed="sip",
            adjustment_mode="all",
            access_method=ACCESS_METHOD,
        ),
        retrieval_timestamp=aware(clock()),
        source_timezone="UTC",
        provider_response_payload={"pages": pages},
        pagination=tuple(pagination),
        response_page_count=len(pages),
    )
