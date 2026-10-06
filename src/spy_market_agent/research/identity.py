from __future__ import annotations

from typing import Any

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.constants import (
    PHASE3_EXPERIMENT_ID_VERSION,
    PHASE3_FOLD_ID_VERSION,
    PHASE3_FOLD_MANIFEST_ID_VERSION,
)
from spy_market_agent.research.models import (
    ExperimentManifest,
    WalkForwardFold,
    WalkForwardManifest,
)


def research_fold_identity_payload(fold: WalkForwardFold) -> dict[str, Any]:
    payload = fold.model_dump(mode="python")
    payload.pop("fold_id", None)
    payload["fold_id_version"] = PHASE3_FOLD_ID_VERSION
    return payload


def research_fold_identity(fold: WalkForwardFold) -> str:
    return f"spy-v2p3-fold-{sha256_json(research_fold_identity_payload(fold))[:20]}"


def fold_manifest_identity_payload(manifest: WalkForwardManifest) -> dict[str, Any]:
    payload = manifest.model_dump(mode="python")
    payload.pop("fold_manifest_id", None)
    payload["fold_manifest_id_version"] = PHASE3_FOLD_MANIFEST_ID_VERSION
    return payload


def fold_manifest_identity(manifest: WalkForwardManifest) -> str:
    return f"spy-v2p3-folds-{sha256_json(fold_manifest_identity_payload(manifest))[:20]}"


def experiment_identity_payload(manifest: ExperimentManifest) -> dict[str, Any]:
    payload = manifest.model_dump(mode="python")
    payload.pop("experiment_id", None)
    payload.pop("creation_timestamp", None)
    payload.pop("owner_operator_notes", None)
    payload["experiment_id_version"] = PHASE3_EXPERIMENT_ID_VERSION
    return payload


def experiment_identity(manifest: ExperimentManifest) -> str:
    return f"spy-v2p3-{sha256_json(experiment_identity_payload(manifest))[:24]}"
