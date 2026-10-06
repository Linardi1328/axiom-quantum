from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from spy_market_agent.market_data.acquisition import (
    PHASE1_CANONICAL_CHECKSUM_VERSION,
    PHASE1_CORPORATE_ACTION_POLICY,
    PHASE1_DATASET_ID_VERSION,
    PHASE1_MANIFEST_SCHEMA_VERSION,
    PHASE1_SCHEMA_VERSION,
    PHASE1_SOURCE_CHECKSUM_VERSION,
    AcquisitionRequest,
    CanonicalDailyBar,
    DatasetManifest,
    GeneratedFileLocations,
    MissingSessionSummary,
    RawAcquisitionSnapshot,
    runtime_lineage,
)
from spy_market_agent.market_data.calendar import MARKET_TIMEZONE, TradingCalendar
from spy_market_agent.market_data.errors import ManifestValidationFailure

JSON_SEPARATOR_PAIR = (",", ":")


def canonical_json_bytes(payload: object) -> bytes:
    """Serialize JSON deterministically for checksums and durable artifacts."""

    text = json.dumps(
        _jsonable(payload),
        allow_nan=False,
        separators=JSON_SEPARATOR_PAIR,
        sort_keys=True,
    )
    return f"{text}\n".encode()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(payload: object) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | bytearray):
        return [_jsonable(item) for item in value]
    return value


def source_checksum(snapshot: RawAcquisitionSnapshot) -> str:
    """Checksum stable raw source content, excluding volatile retrieval timestamp."""

    payload = {
        "version": PHASE1_SOURCE_CHECKSUM_VERSION,
        "sanitized_request": snapshot.sanitized_request,
        "provider_identity": snapshot.provider_identity,
        "source_timezone": snapshot.source_timezone,
        "provider_response_payload": snapshot.provider_response_payload,
        "pagination": snapshot.pagination,
        "response_page_count": snapshot.response_page_count,
        "corporate_actions_payload": snapshot.corporate_actions_payload,
    }
    return sha256_json(payload)


def canonical_content_checksum(
    *,
    bars: tuple[CanonicalDailyBar, ...],
    provider: str,
    feed: str,
    timeframe: str,
    adjustment_mode: str,
    corporate_action_policy: str = PHASE1_CORPORATE_ACTION_POLICY,
) -> str:
    payload = {
        "version": PHASE1_CANONICAL_CHECKSUM_VERSION,
        "canonical_schema_version": PHASE1_SCHEMA_VERSION,
        "provider": provider,
        "feed": feed,
        "timeframe": timeframe,
        "adjustment_mode": adjustment_mode,
        "corporate_action_policy": corporate_action_policy,
        "columns": canonical_csv_header(),
        "rows": [_stable_bar_payload(bar) for bar in bars],
    }
    return sha256_json(payload)


def dataset_identity(
    *,
    request: AcquisitionRequest,
    canonical_checksum: str,
    corporate_action_policy: str = PHASE1_CORPORATE_ACTION_POLICY,
) -> str:
    payload = {
        "version": PHASE1_DATASET_ID_VERSION,
        "symbol": request.symbol,
        "provider": request.provider,
        "feed": request.feed,
        "timeframe": request.timeframe,
        "adjustment_mode": request.adjustment_mode,
        "requested_start_date": request.start_date,
        "requested_end_date": request.end_date,
        "canonical_schema_version": PHASE1_SCHEMA_VERSION,
        "canonical_content_checksum": canonical_checksum,
        "corporate_action_policy": corporate_action_policy,
    }
    return f"spy-v2p1-{sha256_json(payload)[:24]}"


def canonical_csv_header() -> tuple[str, ...]:
    return (
        "symbol",
        "session_date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
        "provider",
        "feed",
        "adjustment_mode",
        "source_timezone",
        "canonical_timezone",
        "lineage_identifier",
    )


def canonical_csv_bytes(bars: tuple[CanonicalDailyBar, ...]) -> bytes:
    lines = [",".join(canonical_csv_header())]
    for bar in bars:
        values = (
            bar.symbol,
            bar.session_date.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.adjusted_close or "",
            str(bar.volume),
            bar.provider,
            bar.feed,
            bar.adjustment_mode,
            bar.source_timezone,
            bar.canonical_timezone,
            bar.lineage_identifier,
        )
        lines.append(",".join(_csv_escape(value) for value in values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _csv_escape(value: str) -> str:
    if any(character in value for character in (",", '"', "\n", "\r")):
        return '"' + value.replace('"', '""') + '"'
    return value


def _stable_bar_payload(bar: CanonicalDailyBar) -> dict[str, object]:
    payload = bar.model_dump(mode="python")
    payload.pop("lineage_identifier", None)
    return payload


def build_manifest(
    *,
    request: AcquisitionRequest,
    snapshot: RawAcquisitionSnapshot,
    bars: tuple[CanonicalDailyBar, ...],
    calendar: TradingCalendar,
    relative_raw_path: str,
    relative_canonical_path: str,
    relative_manifest_path: str,
    canonical_artifact_checksum: str,
    raw_artifact_checksum: str,
) -> DatasetManifest:
    if not bars:
        raise ManifestValidationFailure("cannot build a manifest for an empty dataset.")

    canonical_checksum = canonical_content_checksum(
        bars=bars,
        provider=request.provider,
        feed=request.feed,
        timeframe=request.timeframe,
        adjustment_mode=request.adjustment_mode,
    )
    dataset_id = dataset_identity(request=request, canonical_checksum=canonical_checksum)
    expected_sessions = calendar.sessions_between(request.start_date, request.end_date)
    observed_sessions = tuple(bar.session_date for bar in bars)
    missing_sessions = tuple(
        session.isoformat() for session in expected_sessions if session not in observed_sessions
    )
    duplicate_count = len(observed_sessions) - len(set(observed_sessions))
    git_commit_sha, python_version, package_version, dependencies = runtime_lineage()

    try:
        return DatasetManifest(
            dataset_id=dataset_id,
            symbol="SPY",
            provider=request.provider,
            provider_api_version=snapshot.provider_identity.api_version,
            sdk_package_name=snapshot.provider_identity.sdk_package_name,
            sdk_package_version=snapshot.provider_identity.sdk_package_version,
            feed=request.feed,
            timeframe="1Day",
            requested_start_date=request.start_date,
            requested_end_date=request.end_date,
            actual_first_session=bars[0].session_date,
            actual_last_session=bars[-1].session_date,
            retrieval_timestamp=snapshot.retrieval_timestamp,
            adjustment_mode=request.adjustment_mode,
            canonical_schema_version=PHASE1_SCHEMA_VERSION,
            manifest_schema_version=PHASE1_MANIFEST_SCHEMA_VERSION,
            row_count=len(bars),
            expected_session_count=len(expected_sessions),
            missing_session_summary=MissingSessionSummary(
                count=len(missing_sessions),
                sessions=missing_sessions,
            ),
            duplicate_session_count=duplicate_count,
            incomplete_session_policy="reject latest session until XNYS close has passed",
            corporate_action_policy=PHASE1_CORPORATE_ACTION_POLICY,
            corporate_action_evidence=(
                "Alpaca bar adjustment parameter recorded; no separate corporate-action "
                "snapshot is acquired in this implementation candidate."
            ),
            source_checksum=source_checksum(snapshot),
            canonical_content_checksum=canonical_checksum,
            artifact_checksum=canonical_artifact_checksum,
            raw_artifact_checksum=raw_artifact_checksum,
            manifest_artifact_checksum=None,
            relevant_configuration={
                "calendar": getattr(calendar, "calendar_code", "XNYS"),
                "market_timezone": MARKET_TIMEZONE,
                "source_timezone": snapshot.source_timezone,
                "provider": request.provider,
                "feed": request.feed,
                "timeframe": request.timeframe,
                "adjustment_mode": request.adjustment_mode,
                "asof": request.asof.isoformat() if request.asof else None,
                "data_root": request.data_root.as_posix(),
            },
            lineage_identifier=bars[0].lineage_identifier,
            git_commit_sha=git_commit_sha,
            python_version=python_version,
            package_version=package_version,
            dependency_versions=dependencies,
            licensing_classification=(
                "local-use-only-restricted-provider-data-not-for-redistribution"
            ),
            generated_file_locations=GeneratedFileLocations(
                raw_snapshot_path=relative_raw_path,
                canonical_path=relative_canonical_path,
                manifest_path=relative_manifest_path,
            ),
        )
    except ValidationError as exc:
        raise ManifestValidationFailure(f"manifest validation failed: {exc}") from exc


def manifest_json_bytes(manifest: DatasetManifest) -> bytes:
    return canonical_json_bytes(manifest)


def finalized_manifest_with_checksum(manifest: DatasetManifest) -> DatasetManifest:
    interim_bytes = manifest_json_bytes(manifest)
    artifact_checksum = sha256_bytes(interim_bytes)
    try:
        return manifest.model_copy(update={"manifest_artifact_checksum": artifact_checksum})
    except ValidationError as exc:
        raise ManifestValidationFailure(f"manifest checksum finalization failed: {exc}") from exc


def load_manifest_bytes(payload: bytes) -> DatasetManifest:
    try:
        raw = _load_json_object(payload, artifact_name="manifest")
        _require_exact_keys(raw, DatasetManifest.model_fields.keys(), artifact_name="manifest")
        return DatasetManifest.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ManifestValidationFailure(f"manifest cannot be loaded: {exc}") from exc


def load_raw_snapshot_bytes(payload: bytes) -> RawAcquisitionSnapshot:
    try:
        raw = _load_json_object(payload, artifact_name="raw snapshot")
        _require_exact_keys(
            raw,
            RawAcquisitionSnapshot.model_fields.keys(),
            artifact_name="raw snapshot",
        )
        return RawAcquisitionSnapshot.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ManifestValidationFailure(f"raw snapshot cannot be loaded: {exc}") from exc


def canonical_bars_from_csv_bytes(payload: bytes) -> tuple[CanonicalDailyBar, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationFailure(f"canonical CSV is not valid UTF-8: {exc}") from exc
    if not text.endswith("\n"):
        raise ManifestValidationFailure("canonical CSV must end with a newline.")

    reader = csv.DictReader(io.StringIO(text), restkey="__extra__", restval=None)
    fieldnames = tuple(reader.fieldnames or ())
    expected_fieldnames = canonical_csv_header()
    if fieldnames != expected_fieldnames:
        raise ManifestValidationFailure("canonical CSV header does not match schema.")

    bars: list[CanonicalDailyBar] = []
    try:
        for row_number, row in enumerate(reader, start=2):
            extras = row.pop("__extra__", None)
            if extras:
                raise ManifestValidationFailure(
                    f"canonical CSV row {row_number} has extra columns."
                )
            missing_fields = [field for field in expected_fieldnames if row.get(field) is None]
            if missing_fields:
                raise ManifestValidationFailure(
                    f"canonical CSV row {row_number} is missing fields: {missing_fields}."
                )
            if row["symbol"] != "SPY":
                raise ManifestValidationFailure(
                    f"canonical CSV row {row_number} has unsupported symbol."
                )
            bars.append(
                CanonicalDailyBar(
                    symbol="SPY",
                    session_date=date.fromisoformat(row["session_date"]),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    adjusted_close=row["adjusted_close"] or None,
                    volume=int(row["volume"]),
                    provider=row["provider"],
                    feed=row["feed"],
                    adjustment_mode=row["adjustment_mode"],
                    source_timezone=row["source_timezone"],
                    canonical_timezone=row["canonical_timezone"],
                    lineage_identifier=row["lineage_identifier"],
                )
            )
    except (KeyError, ValueError, ValidationError) as exc:
        raise ManifestValidationFailure(f"canonical CSV cannot be loaded: {exc}") from exc
    if not bars:
        raise ManifestValidationFailure("canonical CSV must contain at least one row.")
    return tuple(bars)


def _load_json_object(payload: bytes, *, artifact_name: str) -> dict[str, object]:
    raw = json.loads(payload.decode("utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{artifact_name} JSON must be an object.")
    return raw


def _require_exact_keys(
    raw: Mapping[str, object],
    expected_keys: Iterable[str],
    *,
    artifact_name: str,
) -> None:
    actual = set(raw)
    expected = set(expected_keys)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise TypeError(f"{artifact_name} keys mismatch; missing={missing}; extra={extra}.")
