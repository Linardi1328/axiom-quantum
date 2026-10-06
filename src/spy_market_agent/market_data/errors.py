from __future__ import annotations

import re


class MarketDataAcquisitionError(RuntimeError):
    """Base class for explicit historical market-data acquisition failures."""


class InvalidAcquisitionRequest(MarketDataAcquisitionError):
    """Raised when an acquisition request is invalid before provider access."""


class UnsupportedMarketSymbol(InvalidAcquisitionRequest):
    """Raised when a request targets a symbol outside the approved Phase 1 scope."""


class UnsupportedTimeframe(InvalidAcquisitionRequest):
    """Raised when a request targets a timeframe outside the approved Phase 1 scope."""


class UnsafeDataPath(InvalidAcquisitionRequest):
    """Raised when a data-root or artifact path could escape the approved storage root."""


class MissingMarketDataCredentials(MarketDataAcquisitionError):
    """Raised when explicit acquisition is requested without market-data credentials."""


class ProviderAuthenticationFailure(MarketDataAcquisitionError):
    """Raised when provider credentials are missing or invalid at the provider boundary."""


class ProviderAuthorizationFailure(MarketDataAcquisitionError):
    """Raised when provider credentials do not authorize the requested data."""


class ProviderRateLimitFailure(MarketDataAcquisitionError):
    """Raised when the provider rate limit remains exhausted after bounded retries."""


class ProviderUnavailableFailure(MarketDataAcquisitionError):
    """Raised when the provider returns a retryable server-side failure."""


class ProviderTimeoutFailure(MarketDataAcquisitionError):
    """Raised when provider access times out after bounded retries."""


class ProviderMalformedResponse(MarketDataAcquisitionError):
    """Raised when the provider response cannot be parsed into the expected contract."""


class ProviderIncompleteResponse(MarketDataAcquisitionError):
    """Raised when a provider response is structurally valid but incomplete."""


class PaginationFailure(MarketDataAcquisitionError):
    """Raised when provider pagination cannot be completed safely."""


class RawSnapshotWriteFailure(MarketDataAcquisitionError):
    """Raised when a raw snapshot cannot be written or verified."""


class CanonicalizationFailure(MarketDataAcquisitionError):
    """Raised when provider records cannot be converted into canonical daily bars."""


class SessionValidationFailure(CanonicalizationFailure):
    """Raised when canonical bars fail XNYS session validation."""


class ManifestValidationFailure(MarketDataAcquisitionError):
    """Raised when a dataset manifest cannot be constructed or verified."""


class ChecksumMismatch(MarketDataAcquisitionError):
    """Raised when a generated or existing artifact checksum does not match expectations."""


class ExistingDatasetConflict(MarketDataAcquisitionError):
    """Raised when a dataset path exists with conflicting content or metadata."""


class AtomicWriteFailure(MarketDataAcquisitionError):
    """Raised when an atomic write cannot be completed or cleaned up."""


_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(APCA-API-KEY-ID\s*[:=]\s*)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(APCA-API-SECRET-KEY\s*[:=]\s*)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(Authorization\s*[:=]\s*Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(secret[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]+", re.IGNORECASE),
)


def redact_secret_text(value: object) -> str:
    """Return a sanitized message suitable for exceptions, logs, and reviews."""

    text = str(value)
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(r"\1<redacted>", text)
    return text
