from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from spy_market_agent.intelligence.context import (
    MI2A_CONTEXT_POLICY_ID,
    MI2A_REQUIRED_CONTEXT_IDS,
    ContextBundleStatus,
    ContextSeriesDefinition,
    ContextSeriesRole,
    ContextTransformKind,
    SPYContextBundleAssessment,
)
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID

_AS_OF = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)


def _assessment(**overrides: object) -> SPYContextBundleAssessment:
    payload: dict[str, object] = {
        "policy_id": MI2A_CONTEXT_POLICY_ID,
        "as_of": _AS_OF,
        "target_series_id": LEGACY_SPY_SERIES_ID,
        "target_snapshot_id": "snapshot-spy",
        "present_context_ids": MI2A_REQUIRED_CONTEXT_IDS,
        "missing_context_ids": (),
        "unverified_context_ids": (),
        "future_available_context_ids": (),
        "status": ContextBundleStatus.VERIFIED_COMPLETE,
        "eligible_for_complete_context_analysis": True,
        "reasons": (),
    }
    payload.update(overrides)
    return SPYContextBundleAssessment(**cast(Any, payload))


def _missing_last_overrides() -> dict[str, object]:
    missing_id = MI2A_REQUIRED_CONTEXT_IDS[-1]
    return {
        "present_context_ids": MI2A_REQUIRED_CONTEXT_IDS[:-1],
        "missing_context_ids": (missing_id,),
        "status": ContextBundleStatus.INCOMPLETE,
        "eligible_for_complete_context_analysis": False,
        "reasons": (f"missing context: {missing_id}",),
    }


def _with_overrides(base: dict[str, object], **overrides: object) -> dict[str, object]:
    payload = dict(base)
    payload.update(overrides)
    return payload


def test_context_definition_contract_rejects_invalid_types() -> None:
    with pytest.raises(ValueError, match="path-safe"):
        ContextSeriesDefinition(
            series_id="../unsafe",
            role=ContextSeriesRole.LARGE_CAP_GROWTH_CONTEXT,
            transform_kind=ContextTransformKind.PRICE_LEVEL,
        )
    with pytest.raises(ValueError, match="ContextSeriesRole"):
        ContextSeriesDefinition(
            series_id="context",
            role=cast(ContextSeriesRole, "wrong"),
            transform_kind=ContextTransformKind.PRICE_LEVEL,
        )
    with pytest.raises(ValueError, match="ContextTransformKind"):
        ContextSeriesDefinition(
            series_id="context",
            role=ContextSeriesRole.LARGE_CAP_GROWTH_CONTEXT,
            transform_kind=cast(ContextTransformKind, "wrong"),
        )


def test_context_assessment_contract_rejects_invalid_identity_and_types() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        _assessment(policy_id="wrong")
    with pytest.raises(ValueError, match="timezone-aware"):
        _assessment(as_of=datetime(2026, 9, 4, 22, 0))
    with pytest.raises(ValueError, match="approved legacy SPY"):
        _assessment(target_series_id="qqq-daily")
    with pytest.raises(ValueError, match="path-safe"):
        _assessment(target_snapshot_id="../unsafe")
    with pytest.raises(ValueError, match="ContextBundleStatus"):
        _assessment(status=cast(ContextBundleStatus, "wrong"))


def test_context_assessment_contract_enforces_context_sets() -> None:
    first, second, *_ = MI2A_REQUIRED_CONTEXT_IDS
    with pytest.raises(ValueError, match="duplicates"):
        _assessment(present_context_ids=(first, first))
    with pytest.raises(ValueError, match="undeclared context"):
        _assessment(present_context_ids=("undeclared-context",))
    with pytest.raises(ValueError, match="canonical order"):
        _assessment(present_context_ids=(second, first))

    with pytest.raises(ValueError, match="complement missing_context_ids"):
        _assessment(present_context_ids=MI2A_REQUIRED_CONTEXT_IDS[:-1])

    missing_id = MI2A_REQUIRED_CONTEXT_IDS[-1]
    missing = _missing_last_overrides()
    with pytest.raises(ValueError, match="subset of present_context_ids"):
        _assessment(**_with_overrides(missing, unverified_context_ids=(missing_id,)))
    with pytest.raises(ValueError, match="subset of present_context_ids"):
        _assessment(**_with_overrides(missing, future_available_context_ids=(missing_id,)))


def test_context_assessment_contract_enforces_reason_lineage() -> None:
    missing = _missing_last_overrides()
    missing_id = MI2A_REQUIRED_CONTEXT_IDS[-1]

    duplicate_reason = f"missing context: {missing_id}"
    with pytest.raises(ValueError, match="duplicates"):
        _assessment(
            **_with_overrides(
                missing,
                reasons=(duplicate_reason, duplicate_reason),
            )
        )
    with pytest.raises(ValueError, match="undeclared MI-2A readiness failure"):
        _assessment(
            status=ContextBundleStatus.INELIGIBLE,
            eligible_for_complete_context_analysis=False,
            reasons=("invented readiness failure",),
        )
    with pytest.raises(ValueError, match="include every structural"):
        _assessment(**_with_overrides(missing, reasons=()))
    with pytest.raises(ValueError, match="canonical order"):
        _assessment(
            **_with_overrides(
                missing,
                status=ContextBundleStatus.INELIGIBLE,
                reasons=(f"missing context: {missing_id}", "target data not verified"),
            )
        )

    unverified_id = MI2A_REQUIRED_CONTEXT_IDS[0]
    with pytest.raises(ValueError, match="include every structural"):
        _assessment(
            unverified_context_ids=(unverified_id,),
            status=ContextBundleStatus.INELIGIBLE,
            eligible_for_complete_context_analysis=False,
            reasons=("target data not verified",),
        )

    future_id = MI2A_REQUIRED_CONTEXT_IDS[1]
    with pytest.raises(ValueError, match="include every structural"):
        _assessment(
            future_available_context_ids=(future_id,),
            status=ContextBundleStatus.INELIGIBLE,
            eligible_for_complete_context_analysis=False,
            reasons=("target snapshot not point-in-time available",),
        )


def test_context_assessment_status_is_determined_by_fail_closed_rule() -> None:
    missing = _missing_last_overrides()
    with pytest.raises(ValueError, match="status must match"):
        _assessment(**_with_overrides(missing, status=ContextBundleStatus.INELIGIBLE))

    missing_id = MI2A_REQUIRED_CONTEXT_IDS[-1]
    with pytest.raises(ValueError, match="status must match"):
        _assessment(
            **_with_overrides(
                missing,
                status=ContextBundleStatus.INCOMPLETE,
                reasons=("target data not verified", f"missing context: {missing_id}"),
            )
        )

    with pytest.raises(ValueError, match="eligibility must match"):
        _assessment(eligible_for_complete_context_analysis=False)

    valid_ineligible = _assessment(
        status=ContextBundleStatus.INELIGIBLE,
        eligible_for_complete_context_analysis=False,
        reasons=("target data not verified",),
    )
    assert valid_ineligible.status == ContextBundleStatus.INELIGIBLE

    valid_missing = _assessment(**missing)
    assert valid_missing.status == ContextBundleStatus.INCOMPLETE
