from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.market_data.context_acquisition import _context_dataset_id
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionManifest,
    ContextAcquisitionRequest,
)
from spy_market_agent.research.retrospective_context import (
    RetrospectiveContextSeries,
    RetrospectiveInputError,
    bind_context_series,
    derive_context_dataset_id,
)
from unit.retrospective_helpers import (
    LINEAGE,
    make_artifact,
    sealed_manifest,
    xnys_sessions,
)

SESSIONS = xnys_sessions(date(2021, 3, 1), date(2021, 6, 30))


def test_series_is_derived_only_from_canonical_bytes() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    series = bind_context_series(manifest, canonical)
    assert series.sessions == SESSIONS
    assert len(series.closes) == len(SESSIONS)
    assert series.dataset_id == manifest.dataset_id
    assert series.canonical_sha256 == sha256_bytes(canonical)
    assert series.data_class == "retrospective_corrected"
    assert (series.point_in_time_status, series.usage_rights_status) == (
        "UNVERIFIED",
        "UNVERIFIED",
    )
    assert series.execution_authority == "none"


def test_identity_rule_matches_the_acquisition_module() -> None:
    manifest, canonical = make_artifact("IWM", SESSIONS, 8)
    request = ContextAcquisitionRequest(
        symbol="IWM", start_date=SESSIONS[0], end_date=SESSIONS[-1], acknowledge_provider_terms=True
    )
    expected = _context_dataset_id(request, sha256_bytes(canonical), LINEAGE)
    assert derive_context_dataset_id(manifest, sha256_bytes(canonical)) == expected
    assert manifest.dataset_id == expected


def test_direct_construction_with_caller_arrays_is_refused() -> None:
    with pytest.raises(RetrospectiveInputError):
        RetrospectiveContextSeries("QQQ", "x", "y", "z", SESSIONS, (1.0,) * len(SESSIONS))
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    series = bind_context_series(manifest, canonical)
    with pytest.raises(RetrospectiveInputError):
        dataclasses.replace(series, closes=(2.0,) * len(SESSIONS), _origin=object())


def test_modified_bytes_fail_checksum_or_identity() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    tampered = canonical[:-3] + b"999"
    with pytest.raises(RetrospectiveInputError, match="canonical_checksum_mismatch"):
        bind_context_series(manifest, tampered)
    # Attacker reseals the checksum and manifest but cannot recompute the lineage-bound ID.
    resealed = sealed_manifest(
        "QQQ",
        tampered,
        SESSIONS[0],
        SESSIONS[-1],
        len(SESSIONS),
        dataset_id=manifest.dataset_id,
    )
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(resealed, tampered)


def test_manifest_forgery_is_rejected() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    stale = manifest.model_copy(update={"row_count": manifest.row_count - 1})
    with pytest.raises(RetrospectiveInputError, match="manifest_checksum_mismatch"):
        bind_context_series(stale, canonical)
    forged = ContextAcquisitionManifest.model_construct(
        **{**manifest.model_dump(), "point_in_time_status": "VERIFIED"}
    )
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(forged, canonical)
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(manifest.model_dump(), canonical)  # type: ignore[arg-type]
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(manifest, canonical.decode())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    ["crlf", "missing_session", "duplicate_session", "zero_price", "ohlc", "foreign_symbol"],
)
def test_noncanonical_or_invalid_rows_fail_closed(mutation: str) -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    lines = canonical.decode().split("\n")
    header, rows = lines[0], [r for r in lines[1:] if r]
    count = len(rows)
    if mutation == "crlf":
        body = canonical.replace(b"\n", b"\r\n")
    else:
        if mutation == "missing_session":
            del rows[5]
            count -= 1
        elif mutation == "duplicate_session":
            rows[6] = rows[5]
        elif mutation == "zero_price":
            parts = rows[4].split(",")
            parts[5] = "0.0000"
            rows[4] = ",".join(parts)
        elif mutation == "ohlc":
            parts = rows[4].split(",")
            parts[3] = "0.0100"  # high below open/close
            rows[4] = ",".join(parts)
        else:
            rows[4] = rows[4].replace("QQQ", "IWM", 1)
        body = ("\n".join([header, *rows]) + "\n").encode()
    resealed = sealed_manifest(
        "QQQ", body, SESSIONS[0], SESSIONS[-1], count if mutation != "crlf" else len(SESSIONS)
    )
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(resealed, body)


@pytest.mark.parametrize(
    "override",
    [{"first_session": date(2021, 3, 2)}, {"row_count": 3}, {"end_date": date(2021, 5, 1)}],
)
def test_manifest_must_describe_the_bytes(override: dict[str, Any]) -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    manifest = sealed_manifest(
        "QQQ", canonical, SESSIONS[0], SESSIONS[-1], len(SESSIONS), **override
    )
    with pytest.raises(RetrospectiveInputError):
        bind_context_series(manifest, canonical)


def test_unverified_flags_cannot_be_upgraded() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    with pytest.raises(ValidationError):
        sealed_manifest(
            "QQQ",
            canonical,
            SESSIONS[0],
            SESSIONS[-1],
            len(SESSIONS),
            point_in_time_status="VERIFIED",
        )
    assert manifest.point_in_time_status == "UNVERIFIED"
