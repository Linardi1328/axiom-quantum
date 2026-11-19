"""Synthetic-only sandbox for the AQ-P3C-R adapter: no network, credentials or real data."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from spy_market_agent.market_data.acquisition import (
    MarketDataCredentials,
    PaginationMetadata,
    ProviderIdentity,
    RawAcquisitionSnapshot,
    StockBarsRequestProtocol,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.context_acquisition import (
    ContextAcquisitionRequest,
    acquire_historical_context_data,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.candidates import development_model_registry
from spy_market_agent.research.constants import PHASE3_ARTIFACT_SCHEMA_VERSION
from spy_market_agent.research.phase2_isolation import apply_phase2_final_test_session_isolation
from spy_market_agent.research.retrospective_adapter import FrozenParent
from spy_market_agent.research.runner import _load_verified_research_market_data
from unit.v2_phase2_helpers import write_synthetic_phase1_dataset

NOW = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
NEW_YORK = ZoneInfo("America/New_York")
COMMIT = "a" * 40
CAMPAIGN_ID = "spy-v2p3-dev-" + "0" * 24
GITIGNORE = "data/raw/*\ndata/canonical/*\ndata/manifests/*\nartifacts/research/*\n.local/\n"
APPROVED_START = date(2018, 1, 2)
APPROVED_END = date(2024, 10, 15)


def _stamp(session: date) -> str:
    midnight = datetime(session.year, session.month, session.day, tzinfo=NEW_YORK)
    return midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeContextProvider:
    """Snapshot-only fake with no network client."""

    def __init__(self, sessions: tuple[date, ...], seed: int) -> None:
        rng = np.random.default_rng(seed)
        close = 50.0 * np.exp(np.cumsum(0.0002 + 0.011 * rng.standard_normal(len(sessions))))
        self.records = [
            {
                "t": _stamp(s),
                "o": f"{c:.2f}",
                "h": f"{c + 0.5:.2f}",
                "l": f"{c - 0.5:.2f}",
                "c": f"{c:.2f}",
                "v": 1_000_000 + i,
            }
            for i, (s, c) in enumerate(zip(sessions, close, strict=True))
        ]

    def fetch_raw_snapshot(
        self,
        request: StockBarsRequestProtocol,
        *,
        credentials: MarketDataCredentials,
        clock: Callable[[], datetime],
    ) -> RawAcquisitionSnapshot:
        del credentials
        page = {"bars": {request.symbol: self.records}, "next_page_token": None}
        return RawAcquisitionSnapshot(
            sanitized_request=request.sanitized_parameters(),
            provider_identity=ProviderIdentity(
                provider_name="alpaca",
                api_version="v2",
                sdk_package_name="alpaca-py",
                sdk_package_version=version("alpaca-py"),
                feed=request.feed,
                adjustment_mode=request.adjustment_mode,
                access_method=(
                    "alpaca-py StockHistoricalDataClient low-level get /v2/stocks/bars "
                    "with bounded requests session timeout"
                ),
            ),
            retrieval_timestamp=clock(),
            source_timezone="UTC",
            provider_response_payload={"pages": [page]},
            pagination=(
                PaginationMetadata(
                    page_number=1,
                    request_page_token=None,
                    next_page_token=None,
                    row_count=len(self.records),
                ),
            ),
            response_page_count=1,
        )


def acquire_context(root: Path, symbol: str, start: date, end: date, seed: int) -> Path:
    """Create real v2 artifacts through the existing acquisition code with a fake provider."""
    sessions = XNYSCalendar().sessions_between(start, end)
    artifacts = acquire_historical_context_data(
        ContextAcquisitionRequest(
            symbol=symbol, start_date=start, end_date=end, acknowledge_provider_terms=True
        ),
        provider=FakeContextProvider(sessions, seed),
        credentials=MarketDataCredentials(api_key="fake", secret_key="fake"),
        clock=lambda: NOW,
        repository_root=root,
    )
    return Path(artifacts.manifest.generated_paths["manifest"])


def build_campaign(
    root: Path,
    manifest: Any,
    parent: Any,
    *,
    campaign_id: str = CAMPAIGN_ID,
    boundary_edit: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    store = ResearchArtifactStore(root / "artifacts/research", repository_root=root)
    _, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest, market_data=parent, global_feature_warmup_rows=60
    )
    boundary_json = boundary.model_dump(mode="json")
    if boundary_edit:
        boundary_edit(boundary_json)
    store.write_json(
        campaign_id,
        "experiment_manifest.json",
        {
            "campaign_id": campaign_id,
            "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
            "parent_phase1_dataset_id": manifest.dataset_id,
            "parent_phase1_canonical_content_checksum": manifest.canonical_content_checksum,
            "parent_canonical_market_data_checksum": parent.metadata.dataset_checksum,
            "phase1_manifest_artifact_checksum": manifest.manifest_artifact_checksum,
            "protected_evaluation_authorized": False,
            "phase2_final_test_available_for_tuning": False,
            "phase2_final_test_exclusion_boundary": boundary_json,
        },
        allow_replace=True,
    )
    store.write_json(
        campaign_id,
        "model_registry.json",
        {
            "models": [
                m.model_dump(mode="json") for m in development_model_registry(random_seed=42).models
            ]
        },
    )
    names = ("experiment_manifest.json", "model_registry.json")
    store.write_json(
        campaign_id,
        "artifact_index.json",
        {
            "campaign_id": campaign_id,
            "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
            "artifacts": [
                {
                    "relative_path": store.relative_path(store.artifact_path(campaign_id, name)),
                    "stable_identity": campaign_id,
                    "sha256_checksum": store.checksum(campaign_id, name),
                }
                for name in names
            ],
        },
        allow_replace=True,
    )


@dataclass(frozen=True)
class Sandbox:
    root: Path
    qqq_manifest: Path
    iwm_manifest: Path
    fixture_parent: FrozenParent


def build_sandbox(root: Path) -> Sandbox:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(GITIGNORE)
    manifest_path = write_synthetic_phase1_dataset(
        root, start=APPROVED_START, end=date(2025, 12, 31)
    )
    manifest, parent = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=Path("data"),
        repository_root=root,
        config=ResearchCampaignConfig(),
    )
    build_campaign(root, manifest, parent)
    qqq = acquire_context(root, "QQQ", APPROVED_START, APPROVED_END, 11)
    iwm = acquire_context(root, "IWM", APPROVED_START, APPROVED_END, 12)
    fixture_parent = FrozenParent(
        dataset_id=manifest.dataset_id,
        canonical_checksum=manifest.canonical_content_checksum,
        market_data_checksum=compute_market_data_checksum(parent.data),
        manifest_self_checksum=str(manifest.manifest_artifact_checksum),
        campaign_id=CAMPAIGN_ID,
    )
    return Sandbox(root, qqq, iwm, fixture_parent)


def clone(sandbox: Sandbox, destination: Path) -> Sandbox:
    shutil.copytree(sandbox.root, destination, symlinks=True)
    return Sandbox(destination, sandbox.qqq_manifest, sandbox.iwm_manifest, sandbox.fixture_parent)


def write_authorization(root: Path, digest: str, **overrides: Any) -> Path:
    document: dict[str, Any] = {
        "schema_version": "aq-p3cr-experiment-authorization-v1",
        "authorization_id": "auth-20260105-0001",
        "study_id": "aq-p3cr-retrospective-study-v1",
        "evidence_kind": "synthetic_fixture",
        "git_commit": COMMIT,
        "report_digest": digest,
        "owner_rights_reference_id": "owner-local-entitlement-check-0001",
        "output_destination": "artifacts/research/phase3cr_synthetic",
        "execution_authority": "none",
        "disposition": "NO CANDIDATE PROMOTION",
    }
    document.update(overrides)
    directory = root / ".local"
    directory.mkdir(exist_ok=True)
    path = directory / "authorization.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    return path
