from __future__ import annotations

from typing import Any

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.shadow.types import (
    PHASE4_SHADOW_RUN_ID_VERSION,
    ShadowModelMetadata,
    ShadowRunConfiguration,
    ShadowRunRequest,
)


def shadow_run_identity_payload(request: ShadowRunRequest) -> dict[str, Any]:
    model_metadata = request.model_metadata
    model_payload = _model_identity_payload(model_metadata) if model_metadata is not None else None
    configuration_payload = request.configuration.model_dump(mode="python")

    return {
        "run_id_version": PHASE4_SHADOW_RUN_ID_VERSION,
        "symbol": request.configuration.symbol,
        "session": request.signal_session,
        "mode": request.configuration.mode.value,
        "configuration": _configuration_identity_payload(request.configuration),
        "data_lineage": request.data_lineage.model_dump(mode="python"),
        "feature_schema": request.feature_schema,
        "model": model_payload,
        "provider_finalization_policy_id": configuration_payload["provider_finalization_policy_id"],
    }


def shadow_run_identity(request: ShadowRunRequest) -> str:
    return f"spy-v2p4-shadow-{sha256_json(shadow_run_identity_payload(request))[:24]}"


def _configuration_identity_payload(configuration: ShadowRunConfiguration) -> dict[str, Any]:
    payload = configuration.model_dump(mode="python")
    payload.pop("artifact_schema_version", None)
    return payload


def _model_identity_payload(model_metadata: ShadowModelMetadata) -> dict[str, Any]:
    payload = model_metadata.model_dump(mode="python")
    payload.pop("artifact_schema_version", None)
    return {
        "model_id": payload["model_id"],
        "experiment_id": payload["experiment_id"],
        "campaign_id": payload["campaign_id"],
        "model_artifact_checksum": payload["model_artifact_checksum"],
        "feature_schema": payload["feature_schema"],
        "label_schema": payload["label_schema"],
        "git_commit_sha": payload["git_commit_sha"],
        "approval_status": payload["approval_status"],
        "approved_for_shadow": payload["approved_for_shadow"],
    }
