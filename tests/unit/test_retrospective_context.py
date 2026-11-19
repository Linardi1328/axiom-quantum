from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.market_data.context_acquisition import _canonical_csv, _context_dataset_id
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionManifest,
    ContextAcquisitionRequest,
)
from spy_market_agent.research import retrospective_context as ctx
from spy_market_agent.research.retrospective_context import (
    RetrospectiveContextSeries,
    RetrospectiveInputError,
    bind_context_series,
    derive_context_dataset_id,
)
from unit.retrospective_helpers import (
    LINEAGE,
    canonical_csv,
    make_artifact,
    sealed_manifest,
    synthetic_context_bars,
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


def test_independent_fixture_writer_matches_the_acquisition_writer() -> None:
    bars = synthetic_context_bars("QQQ", SESSIONS, 7)
    assert canonical_csv(bars) == _canonical_csv(tuple(bars))


def test_identity_rule_matches_the_acquisition_module() -> None:
    manifest, canonical = make_artifact("IWM", SESSIONS, 8)
    request = ContextAcquisitionRequest(
        symbol="IWM", start_date=SESSIONS[0], end_date=SESSIONS[-1], acknowledge_provider_terms=True
    )
    expected = _context_dataset_id(request, sha256_bytes(canonical), LINEAGE)
    assert derive_context_dataset_id(manifest, sha256_bytes(canonical)) == expected
    assert manifest.dataset_id == expected


def test_series_stores_no_derived_values_that_could_be_substituted() -> None:
    fields = {f.name for f in dataclasses.fields(RetrospectiveContextSeries)}
    assert fields == {"manifest", "canonical_bytes"}
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    series = bind_context_series(manifest, canonical)
    for forged in ("closes", "sessions", "dataset_id", "point_in_time_status", "_origin"):
        bad: dict[str, Any] = {forged: object()}
        with pytest.raises(TypeError):
            dataclasses.replace(series, **bad)


def test_replace_cannot_pair_other_bytes_or_manifests_with_the_original_identity() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    series = bind_context_series(manifest, canonical)
    other_manifest, other_bytes = make_artifact("QQQ", SESSIONS, 8)
    with pytest.raises(RetrospectiveInputError, match="canonical_checksum_mismatch"):
        dataclasses.replace(series, canonical_bytes=other_bytes)
    with pytest.raises(RetrospectiveInputError):
        dataclasses.replace(series, manifest=other_manifest)
    # A fully re-sealed pair is simply a different artifact with a different identity.
    legitimate = dataclasses.replace(series, manifest=other_manifest, canonical_bytes=other_bytes)
    assert legitimate.dataset_id != series.dataset_id
    assert legitimate.canonical_sha256 != series.canonical_sha256
    assert legitimate.derived().effective_data_checksum != series.derived().effective_data_checksum
    assert legitimate.closes != series.closes


def test_post_construction_tampering_is_caught_when_values_are_rederived() -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    series = bind_context_series(manifest, canonical)
    _, other_bytes = make_artifact("QQQ", SESSIONS, 8)
    object.__setattr__(series, "canonical_bytes", other_bytes)  # bypasses frozen dataclass
    with pytest.raises(RetrospectiveInputError, match="canonical_checksum_mismatch"):
        _ = series.closes
    with pytest.raises(RetrospectiveInputError):
        series.derived()
    forged = ContextAcquisitionManifest.model_construct(
        **{**manifest.model_dump(), "point_in_time_status": "VERIFIED"}
    )
    clean = bind_context_series(manifest, canonical)
    object.__setattr__(clean, "manifest", forged)
    with pytest.raises(RetrospectiveInputError):
        clean.derived()


def test_effective_data_checksum_distinguishes_different_data() -> None:
    first = bind_context_series(*make_artifact("QQQ", SESSIONS, 7)).derived()
    second = bind_context_series(*make_artifact("QQQ", SESSIONS, 8)).derived()
    again = bind_context_series(*make_artifact("QQQ", SESSIONS, 7)).derived()
    assert first.effective_data_checksum == again.effective_data_checksum
    assert first.effective_data_checksum != second.effective_data_checksum


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


def _row_edit(column: int, value: str, row: int = 4) -> bytes:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    lines = canonical.decode().split("\n")
    parts = lines[1 + row].split(",")
    parts[column] = value
    lines[1 + row] = ",".join(parts)
    return "\n".join(lines).encode()


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        (5, "50.12340", "noncanonical_price_text"),  # trailing zero
        (5, "050.1234", "noncanonical_price_text"),  # leading zero
        (5, "+50.1234", "noncanonical_price_text"),
        (5, "5.01234e1", "noncanonical_price_text"),
        (5, ".5", "noncanonical_price_text"),
        (5, "50.", "noncanonical_price_text"),
        (5, "0", "invalid_price_text"),
        (5, "-1", "invalid_price_text"),
        (5, "NaN", "invalid_price_text"),
        (5, "1e13", "invalid_price_text"),  # numeric bound
        (5, "9" * 14, "invalid_price_text"),  # adjusted exponent beyond bound
        (5, "0." + "0" * 12 + "1", "invalid_price_text"),
        (5, "1." + "1" * 20, "invalid_price_text"),  # excessive digits
        (6, "0100", "noncanonical_volume_text"),
        (6, "-5", "invalid_volume"),
        (6, "1.0", "invalid_volume"),
        (6, "", "invalid_volume"),
        (6, "9223372036854775808", "invalid_volume"),  # above int64
        (6, "1" * 20, "invalid_volume"),
        (1, "20210301", "noncanonical_session_text"),
        (0, "IWM", "invalid_canonical_row"),
    ],
)
def test_resealed_noncanonical_values_match_verifier_rejections(
    column: int, value: str, code: str
) -> None:
    body = _row_edit(column, value)
    resealed = sealed_manifest("QQQ", body, SESSIONS[0], SESSIONS[-1], len(SESSIONS))
    with pytest.raises(RetrospectiveInputError, match=code):
        bind_context_series(resealed, body)


def test_resealed_session_changes_that_break_the_requested_xnys_coverage() -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    lines = canonical.decode().split("\n")
    header, rows = lines[0], [r for r in lines[1:] if r]

    def seal(edited: list[str]) -> tuple[ContextAcquisitionManifest, bytes]:
        body = ("\n".join([header, *edited]) + "\n").encode()
        return sealed_manifest("QQQ", body, SESSIONS[0], SESSIONS[-1], len(edited)), body

    weekend = rows[5].replace(SESSIONS[5].isoformat(), "2021-03-06")  # a Saturday
    cases = {
        "missing": [r for i, r in enumerate(rows) if i != 5],
        "weekend": [*rows[:5], weekend, *rows[6:]],
        "duplicate": [*rows[:6], rows[5], *rows[6:]],
        "reordered": [*rows[:5], rows[6], rows[5], *rows[7:]],
    }
    for edited in cases.values():
        manifest, body = seal(edited)
        with pytest.raises(RetrospectiveInputError):
            bind_context_series(manifest, body)


def test_requested_range_must_equal_returned_sessions_exactly() -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    for override in (
        {"start_date": date(2021, 2, 26)},  # earlier XNYS session was requested but absent
        {"end_date": date(2021, 7, 1)},
    ):
        manifest = sealed_manifest(
            "QQQ", canonical, SESSIONS[0], SESSIONS[-1], len(SESSIONS), **override
        )
        with pytest.raises(
            RetrospectiveInputError, match="sessions_differ_from_requested_xnys_coverage"
        ):
            bind_context_series(manifest, canonical)


def test_final_session_must_be_complete_at_retrieval() -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    manifest = sealed_manifest(
        "QQQ",
        canonical,
        SESSIONS[0],
        SESSIONS[-1],
        len(SESSIONS),
        request_started_at=datetime(2021, 6, 30, 15, 0, tzinfo=UTC),
        retrieval_completed_at=datetime(2021, 6, 30, 15, 1, tzinfo=UTC),
    )
    with pytest.raises(RetrospectiveInputError, match="final_session_incomplete_at_retrieval"):
        bind_context_series(manifest, canonical)
    after_close = sealed_manifest(
        "QQQ",
        canonical,
        SESSIONS[0],
        SESSIONS[-1],
        len(SESSIONS),
        request_started_at=datetime(2021, 6, 30, 21, 0, tzinfo=UTC),
        retrieval_completed_at=datetime(2021, 6, 30, 21, 1, tzinfo=UTC),
    )
    assert bind_context_series(after_close, canonical).sessions == SESSIONS


def test_retrieval_window_is_bounded_like_the_verifier() -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    manifest = sealed_manifest(
        "QQQ",
        canonical,
        SESSIONS[0],
        SESSIONS[-1],
        len(SESSIONS),
        request_started_at=datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
        retrieval_completed_at=datetime(2026, 1, 5, 22, 3, tzinfo=UTC),
    )
    with pytest.raises(RetrospectiveInputError, match="manifest_session_mismatch"):
        bind_context_series(manifest, canonical)


def test_input_bytes_and_row_counts_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, canonical = make_artifact("QQQ", SESSIONS, 7)
    monkeypatch.setattr(ctx, "MAX_BYTES", len(canonical) - 1)
    with pytest.raises(RetrospectiveInputError, match="canonical_bytes_out_of_bounds"):
        bind_context_series(manifest, canonical)
    monkeypatch.setattr(ctx, "MAX_BYTES", len(canonical))
    monkeypatch.setattr(ctx, "MAX_ROWS", len(SESSIONS) - 1)
    with pytest.raises(RetrospectiveInputError, match="invalid_canonical_layout"):
        bind_context_series(manifest, canonical)
    monkeypatch.setattr(ctx, "MAX_ROWS", len(SESSIONS))
    assert bind_context_series(manifest, canonical).sessions == SESSIONS
    with pytest.raises(RetrospectiveInputError, match="canonical_bytes_out_of_bounds"):
        bind_context_series(manifest, b"")


@pytest.mark.parametrize(
    "mutation",
    ["crlf", "zero_price", "ohlc", "bom", "extra_column", "blank_line", "no_final_newline"],
)
def test_noncanonical_layout_or_ohlc_fails_closed(mutation: str) -> None:
    _, canonical = make_artifact("QQQ", SESSIONS, 7)
    text = canonical.decode()
    if mutation == "crlf":
        body = text.replace("\n", "\r\n").encode()
    elif mutation == "bom":
        body = b"\xef\xbb\xbf" + canonical
    elif mutation == "extra_column":
        body = text.replace("volume", "volume,extra", 1).encode()
    elif mutation == "blank_line":
        body = (text + "\n").encode()
    elif mutation == "no_final_newline":
        body = text.rstrip("\n").encode()
    elif mutation == "zero_price":
        body = _row_edit(5, "0")
    else:
        body = _row_edit(3, "0.01")  # high below open/close
    resealed = sealed_manifest("QQQ", body, SESSIONS[0], SESSIONS[-1], len(SESSIONS))
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
