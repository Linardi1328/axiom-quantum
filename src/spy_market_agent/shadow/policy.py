from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from spy_market_agent.shadow.freshness import evaluate_market_data_freshness
from spy_market_agent.shadow.identity import shadow_run_identity
from spy_market_agent.shadow.model_gate import evaluate_model_admission
from spy_market_agent.shadow.types import (
    BLOCKED_NO_APPROVED_MODEL,
    DailyMarketDataStatus,
    FreshnessDecision,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
    ShadowRunDecision,
    ShadowRunRequest,
    ShadowRunStatus,
)


@dataclass(frozen=True, slots=True)
class ShadowPolicyIssue:
    code: str
    message: str


class ShadowPolicyError(ValueError):
    """Raised when Phase 4 shadow policy fails closed."""

    def __init__(self, issue: ShadowPolicyIssue) -> None:
        self.issue = issue
        super().__init__(f"{issue.code}: {issue.message}")


class DuplicateShadowRunError(ShadowPolicyError):
    """Raised when a logical shadow run identity has already been processed."""


def evaluate_shadow_run(
    request: ShadowRunRequest,
    freshness: FreshnessDecision,
    *,
    existing_run_ids: tuple[str, ...] = (),
) -> ShadowRunDecision:
    shadow_run_id = shadow_run_identity(request)
    if shadow_run_id in existing_run_ids:
        _raise_shadow_policy_error(
            DuplicateShadowRunError,
            "duplicate_run",
            "shadow run identity has already been processed.",
        )

    admission = evaluate_model_admission(request.configuration.mode, request.model_metadata)
    refusal_reasons = tuple(dict.fromkeys((*freshness.reasons, *admission.reasons)))

    if not freshness.eligible:
        return ShadowRunDecision(
            shadow_run_id=shadow_run_id,
            mode=request.configuration.mode,
            run_status=ShadowRunStatus.BLOCKED,
            observation_allowed=False,
            model_inference_allowed=False,
            admission_status=admission.status,
            freshness_status=freshness.status,
            monitoring_status=ShadowHealthStatus.BLOCKED,
            refusal_reasons=refusal_reasons,
        )

    if request.configuration.mode == ShadowMode.OBSERVATION_ONLY_NO_MODEL:
        return ShadowRunDecision(
            shadow_run_id=shadow_run_id,
            mode=request.configuration.mode,
            run_status=ShadowRunStatus.OBSERVATION_READY,
            observation_allowed=True,
            model_inference_allowed=False,
            admission_status=ModelAdmissionStatus.NOT_REQUIRED_OBSERVATION_ONLY,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            refusal_reasons=(BLOCKED_NO_APPROVED_MODEL,),
        )

    return ShadowRunDecision(
        shadow_run_id=shadow_run_id,
        mode=request.configuration.mode,
        run_status=ShadowRunStatus.MODEL_INFERENCE_READY,
        observation_allowed=True,
        model_inference_allowed=admission.inference_allowed,
        admission_status=admission.status,
        freshness_status=FreshnessStatus.FRESH,
        monitoring_status=ShadowHealthStatus.HEALTHY,
        refusal_reasons=(),
    )


def evaluate_observation_only_run(
    request: ShadowRunRequest,
    market_data_status: DailyMarketDataStatus,
    *,
    existing_run_ids: tuple[str, ...] = (),
) -> ShadowRunDecision:
    if request.configuration.mode != ShadowMode.OBSERVATION_ONLY_NO_MODEL:
        _raise_shadow_policy_error(
            ShadowPolicyError,
            "observation_only_mode_required",
            "evaluate_observation_only_run requires observation-only mode.",
        )
    freshness = evaluate_market_data_freshness(market_data_status)
    return evaluate_shadow_run(request, freshness, existing_run_ids=existing_run_ids)


def _raise_shadow_policy_error(
    error_type: type[ShadowPolicyError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type(ShadowPolicyIssue(code=code, message=message))
