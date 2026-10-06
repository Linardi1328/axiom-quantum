from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from fastapi.routing import APIRoute

from spy_market_agent.api import create_app

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCUMENTS = (
    "docs/ARCHITECTURE.md",
    "docs/REPRODUCIBILITY.md",
    "docs/WORKFLOWS.md",
    "docs/SECURITY_AND_SAFETY.md",
    "docs/DEMO_GUIDE.md",
    "docs/PORTFOLIO_OVERVIEW.md",
    "docs/V2_PHASE_02_REAL_HISTORICAL_BENCHMARK_SPEC.md",
    "docs/V2_PHASE_02_BENCHMARK_POLICY.md",
    "docs/V2_PHASE_02_DATA_CARD_TEMPLATE.md",
    "docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md",
    "docs/V2_PHASE_03_ALPHA3_RELEASE_EVIDENCE.md",
    "docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md",
    "docs/V2_PHASE_04_BETA1_RELEASE_EVIDENCE.md",
    "CHANGELOG.md",
    "RELEASE_NOTES_V1.0.0.md",
    "RELEASE_NOTES_V2.0.0_ALPHA_1.md",
    "RELEASE_NOTES_V2.0.0_ALPHA_2.md",
    "RELEASE_NOTES_V2.0.0_BETA_1.md",
    "VERSION_1_RELEASE_CHECKLIST.md",
    "VERSION_2_PHASE_01_RELEASE_CHECKLIST.md",
    "VERSION_2_PHASE_02_RELEASE_CHECKLIST.md",
    "reviews/PHASE_09_REVIEW.md",
    "reviews/VERSION_1_FINAL_REVIEW.md",
    "reviews/V2_PHASE_01_REVIEW.md",
    "reviews/V2_PHASE_02_REVIEW.md",
)

DOCUMENTED_MODULE_PATHS = (
    "src/spy_market_agent/config",
    "src/spy_market_agent/market_data",
    "src/spy_market_agent/validation",
    "src/spy_market_agent/features",
    "src/spy_market_agent/datasets",
    "src/spy_market_agent/modeling",
    "src/spy_market_agent/strategies",
    "src/spy_market_agent/risk",
    "src/spy_market_agent/backtesting",
    "src/spy_market_agent/benchmark",
    "src/spy_market_agent/research",
    "src/spy_market_agent/shadow",
    "src/spy_market_agent/persistence",
    "src/spy_market_agent/api",
    "src/spy_market_agent/dashboard",
    "src/spy_market_agent/execution",
    "src/spy_market_agent/execution/alpaca_paper.py",
)

EXPECTED_GET_ROUTES = {
    "/health",
    "/api/v1/data/status",
    "/api/v1/model-runs",
    "/api/v1/model-runs/{run_id}",
    "/api/v1/model-runs/{run_id}/predictions",
    "/api/v1/backtests",
    "/api/v1/backtests/{run_id}",
    "/api/v1/backtests/{run_id}/equity",
    "/api/v1/backtests/{run_id}/orders",
    "/api/v1/backtests/{run_id}/risk-decisions",
    "/api/v1/backtests/{run_id}/fills",
    "/api/v1/paper-trading/status",
    "/api/v1/paper-orders",
    "/api/v1/paper-orders/{client_order_id}",
}


def _application_get_routes() -> set[str]:
    app = create_app()
    route_inventory: set[str] = set()
    excluded_routes = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods or set()
        if "GET" in methods and route.path not in excluded_routes:
            route_inventory.add(route.path)

    return route_inventory


def _repo_document_texts() -> dict[str, str]:
    paths = ("README.md", *REQUIRED_DOCUMENTS)
    return {path: (ROOT / path).read_text(encoding="utf-8") for path in paths}


def _fenced_blocks(text: str) -> Iterator[tuple[str, str]]:
    pattern = re.compile(r"```(?P<language>[^\n]*)\n(?P<body>.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        yield match.group("language").strip().lower(), match.group("body")


def test_phase9_documentation_files_exist() -> None:
    for path in REQUIRED_DOCUMENTS:
        assert (ROOT / path).is_file(), path


def test_readme_internal_links_resolve_to_repository_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)

    assert links
    for link in links:
        if "://" in link or link.startswith("#"):
            continue
        target = ROOT / link.split("#", 1)[0]
        assert target.exists(), link


def test_documented_module_paths_exist() -> None:
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    for path in DOCUMENTED_MODULE_PATHS:
        assert path in architecture
        assert (ROOT / path).exists(), path


def test_documented_fastapi_routes_match_application_inventory() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert _application_get_routes() == EXPECTED_GET_ROUTES
    for route in sorted(EXPECTED_GET_ROUTES):
        assert f"GET {route}" in readme


def test_readme_local_dashboard_quick_start_is_complete_and_safe() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lower = readme.lower()

    required_snippets = (
        "## Quick Start — Open the Local Dashboard",
        "git clone https://github.com/Linardi1328/spy-market-agent.git",
        "git rev-parse --show-toplevel",
        'test -f pyproject.toml && echo "Repository root confirmed"',
        "python3.12 -m venv .venv",
        'python -m pip install -e ".[dev]"',
        "initialize_database('./spy_market_agent.sqlite3')",
        "SQLITE_DATABASE_PATH=./spy_market_agent.sqlite3",
        'python -m uvicorn "spy_market_agent.api.main:create_app"',
        "curl http://127.0.0.1:8000/health",
        "DASHBOARD_API_BASE_URL=http://127.0.0.1:8000",
        "streamlit run src/spy_market_agent/dashboard/streamlit_app.py",
        "--server.headless true",
        "--browser.gatherUsageStats false",
        "separate terminal",
        "http://127.0.0.1:8501",
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/docs",
        "No Alpaca credentials are required",
        "no order is submitted",
        "empty dashboard is expected",
        "neither setup.py nor pyproject.toml found",
        "docs/DEMO_GUIDE.md",
        "docs/WORKFLOWS.md",
        "docs/REPRODUCIBILITY.md",
        "docs/SECURITY_AND_SAFETY.md",
    )

    for snippet in required_snippets:
        assert snippet in readme

    forbidden = (
        "historical market-data acquisition is unimplemented",
        "phase 2 real benchmark results already exist",
        "dashboard submits orders",
        "local startup constitutes internet deployment",
    )
    for phrase in forbidden:
        assert phrase not in lower


def test_api_route_versions_remain_v1() -> None:
    routes = _application_get_routes()

    assert "/health" in routes
    assert all(route == "/health" or route.startswith("/api/v1/") for route in routes)
    assert not any(route.startswith("/api/v2/") for route in routes)


def test_documented_code_examples_do_not_submit_paper_orders() -> None:
    forbidden_executable_terms = (
        "submit_approved_order",
        "submit_market_day_order",
        "AlpacaPaperBroker",
        "TradingClient",
    )
    executable_languages = {"bash", "sh", "shell", "python", "py"}

    for path, text in _repo_document_texts().items():
        for language, body in _fenced_blocks(text):
            if language not in executable_languages:
                continue
            assert "paper=False" not in body, path
            assert "https://api.alpaca.markets" not in body, path
            for term in forbidden_executable_terms:
                assert term not in body, f"{path}: {term}"


def test_version_1_safety_statements_remain_documented() -> None:
    docs = _repo_document_texts()
    combined = "\n".join(docs.values()).lower()

    assert "not investment advice" in combined
    assert "no live trading" in combined
    assert "no market-data downloader" in combined
    assert "no automatic paper-order submission" in combined
    assert "dual kill switches" in combined
    assert "lookup-only reconciliation" in combined
    assert "at most one spy paper-execution attempt" in combined


def test_version_2_alpha_release_documents_are_consistent() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    phase1_spec = (ROOT / "docs/V2_PHASE_01_REAL_SPY_DATA_SPEC.md").read_text(encoding="utf-8")
    phase2_spec = (ROOT / "docs/V2_PHASE_02_REAL_HISTORICAL_BENCHMARK_SPEC.md").read_text(
        encoding="utf-8"
    )
    phase3_spec = (ROOT / "docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md").read_text(
        encoding="utf-8"
    )
    phase1_release_notes = (ROOT / "RELEASE_NOTES_V2.0.0_ALPHA_1.md").read_text(encoding="utf-8")
    phase2_release_notes = (ROOT / "RELEASE_NOTES_V2.0.0_ALPHA_2.md").read_text(encoding="utf-8")
    phase1_checklist = (ROOT / "VERSION_2_PHASE_01_RELEASE_CHECKLIST.md").read_text(
        encoding="utf-8"
    )
    phase2_checklist = (ROOT / "VERSION_2_PHASE_02_RELEASE_CHECKLIST.md").read_text(
        encoding="utf-8"
    )
    phase1_review = (ROOT / "reviews/V2_PHASE_01_REVIEW.md").read_text(encoding="utf-8")
    phase2_review = (ROOT / "reviews/V2_PHASE_02_REVIEW.md").read_text(encoding="utf-8")
    combined = "\n".join(
        (
            readme,
            changelog,
            roadmap,
            phase1_spec,
            phase2_spec,
            phase3_spec,
            phase1_release_notes,
            phase2_release_notes,
            phase1_checklist,
            phase2_checklist,
            phase1_review,
            phase2_review,
        )
    )
    combined_lower = combined.lower()
    phase2_completion = (
        "Owner-run real SIP benchmark and one controlled final-test execution completed"
    )

    assert "Current package/runtime version: `2.0.0b1`" in readme
    assert "Current released identifier: `v2.0.0-beta.1`" in readme
    assert "Target Phase 5 release: `v2.0.0-beta.2`" in readme
    assert "V2 Phase 1: accepted and complete - Real SPY Data Foundation" in readme
    assert "V2 Phase 2: accepted and released - Real Historical Benchmark" in readme
    assert "V2 Phase 3: accepted and released as `v2.0.0-alpha.3`" in readme
    assert "V2 Phase 3 model outcome: `NO CANDIDATE PROMOTION`" in readme
    assert "V2 Phase 4: accepted and released as `v2.0.0-beta.1`" in readme
    assert "V2 Phase 5 specification/scaffold: active" in readme
    assert "Gate B: locked" in readme
    assert "Public `v2.0.0-beta.1` release/tag: created" in readme
    assert "Public `v2.0.0-beta.2` release/tag: not yet created" in readme
    assert phase2_completion in readme
    assert "Began Version 2 Phase 4 Real-Time Shadow Mode" in changelog
    assert "Corresponding Python package version: `2.0.0b1`" in changelog
    assert "Corresponding Python package version: `2.0.0a3`" in changelog
    assert "Added the Version 2 Phase 3 Walk-Forward Model Research specification" in changelog
    assert "Implemented initial `spy_market_agent.research` scaffolding" in changelog
    assert "## [2.0.0-alpha.1] - 2026-08-05" in changelog
    assert "## [2.0.0-alpha.2] - 2026-08-09" in changelog
    assert "## [2.0.0-alpha.3] - 2026-08-11" in changelog
    assert "Corresponding Python package version: `2.0.0a1`" in changelog
    assert "Corresponding Python package version: `2.0.0a2`" in changelog
    assert "Status: Accepted for v2.0.0-alpha.1 release" in phase1_spec
    assert "Status: Accepted and released as `v2.0.0-alpha.2`" in phase2_spec
    assert "Status: Accepted and released as `v2.0.0-alpha.3`" in phase3_spec
    assert "Accepted - Version 2 Real SPY Data Foundation" in roadmap
    assert "Accepted and released after owner-run real SIP benchmark" in roadmap
    assert "V2 Phase 4 / `v2.0.0-beta.1`: Accepted and released" in roadmap
    assert "V2 Phase 5 / `v2.0.0-beta.2`: Production Paper Operation" in roadmap
    assert "Git release identifier: `v2.0.0-alpha.1`" in phase1_release_notes
    assert "Git release identifier: `v2.0.0-alpha.2`" in phase2_release_notes
    assert "does not evaluate model accuracy" in phase1_release_notes
    assert "does not claim profitability" in phase1_release_notes
    assert "does not add live-money execution" in phase1_release_notes
    assert "does not claim profitability" in phase2_release_notes
    assert "model superiority" in phase2_release_notes
    assert "No Git tag created on the review branch" in phase1_checklist
    assert "No Git tag created on the review branch" in phase2_checklist
    assert "Create annotated `v2.0.0-alpha.1` tag" in phase1_checklist
    assert "Create annotated `v2.0.0-alpha.2` tag" in phase2_checklist
    assert "Phase 1 accepted; release metadata verified" in phase1_review
    assert (
        "Phase 2 benchmark infrastructure and controlled evaluation workflow passed"
    ) in phase2_review
    assert "preparing the tag" not in combined_lower
    assert "Planned Git tag" not in combined
    assert "tag already exists" not in combined.lower()


def test_version_2_phase2_release_preparation_status_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/V2_PHASE_02_REAL_HISTORICAL_BENCHMARK_SPEC.md").read_text(encoding="utf-8")
    policy = (ROOT / "docs/V2_PHASE_02_BENCHMARK_POLICY.md").read_text(encoding="utf-8")
    release_notes = (ROOT / "RELEASE_NOTES_V2.0.0_ALPHA_2.md").read_text(encoding="utf-8")
    checklist = (ROOT / "VERSION_2_PHASE_02_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    review = (ROOT / "reviews/V2_PHASE_02_REVIEW.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, changelog, roadmap, spec)).lower()
    release_combined = "\n".join(
        (readme, changelog, roadmap, spec, policy, release_notes, checklist, review)
    )
    release_lower = release_combined.lower()
    normalized_combined = " ".join(combined.split())
    normalized_spec = " ".join(spec.split())
    phase2_completion = (
        "Owner-run real SIP benchmark and one controlled final-test execution completed"
    )

    assert "Current package/runtime version: `2.0.0b1`" in readme
    assert "Current released identifier: `v2.0.0-beta.1`" in readme
    assert "Target Phase 5 release: `v2.0.0-beta.2`" in readme
    assert "v2.0.0-alpha.2" in spec
    assert "2.0.0a2" in spec
    assert "Status: Accepted and released as `v2.0.0-alpha.2`" in spec
    assert (
        "Phase 2 benchmark infrastructure and controlled evaluation workflow passed acceptance"
    ) in normalized_spec
    assert "Implementation PR #20 was merged at main commit `1155c3c`" in changelog
    assert "Corresponding Python package version: `2.0.0a2`" in changelog
    assert "Owner-run real SIP Phase 2 benchmark acceptance completed" in changelog
    assert "Release-preparation package/runtime version: `2.0.0a2`" in spec
    assert "owner-run real SIP benchmark acceptance gates completed" in spec
    assert "did not run or reopen the real Phase 2 benchmark or final test" in spec
    assert "Accepted and released after owner-run real SIP benchmark" in roadmap
    assert phase2_completion in readme
    assert "weak predictive discrimination" in release_lower
    assert "did not establish a reliable predictive edge" in release_lower
    assert "do not tune against the already-opened phase 2 final test" in release_lower
    assert "Package version: `2.0.0a2`" in checklist
    assert "Package/runtime version is prepared as `2.0.0a2`" in review
    assert "does not authorize new model research, live execution, shadow" in normalized_combined
    agents_lower = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    normalized_agents = " ".join(agents_lower.split())
    assert "version 2 phase 3 pr #24 merged" in normalized_agents
    assert "pr #25 merged" in normalized_agents
    assert "version 2 phase 4 is accepted and released" in normalized_agents
    assert "scheduled observation operations v1" in normalized_agents
    assert "phase 2 is accepted" not in combined
    assert "profitability is guaranteed" not in combined
    assert "live-money readiness: approved" not in combined


def test_version_2_phase3_walk_forward_research_framework_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    workflows = (ROOT / "docs/WORKFLOWS.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    reproducibility = (ROOT / "docs/REPRODUCIBILITY.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/SECURITY_AND_SAFETY.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    normalized_spec = " ".join(spec.split())
    normalized_workflows = " ".join(workflows.split())
    combined = "\n".join(
        (readme, roadmap, workflows, architecture, reproducibility, safety, changelog, agents, spec)
    )
    normalized_combined = " ".join(combined.split())
    combined_lower = combined.lower()

    assert "Target release identifier: `v2.0.0-alpha.3`" in spec
    assert "Release-preparation branch: `review/v2-phase-03-alpha3-release-preparation`" in spec
    assert "Released package/runtime version: `2.0.0a3`" in spec
    assert "Current released identifier: `v2.0.0-beta.1`" in readme
    assert "Target Phase 5 release: `v2.0.0-beta.2`" in readme
    assert "Version 2 Phase 3 Walk-Forward Model Research Specification" in readme
    assert "`spy_market_agent.research` package now provides a manual, offline" in readme
    assert "`spy_market_agent.shadow` package supports `observation_only_no_model`" in readme
    assert "src/spy_market_agent/research" in architecture
    assert "src/spy_market_agent/shadow" in architecture
    assert "python -m spy_market_agent.research.cli run-development" in normalized_workflows
    assert "v2.0.0-alpha.3" in roadmap
    assert "review/v2-phase-03-development-research" in roadmap
    assert "review/v2-phase-03-alpha3-release-preparation" in roadmap
    assert "docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md" in agents

    required_spec_sections = (
        "## 6. Primary Walk-Forward Protocol",
        "## 7. Fold Design and Chronology Rules",
        "## 8. Leakage Protections",
        "## 9. Allowable Feature Research",
        "## 10. Feature Ablation Policy",
        "## 11. Allowable Model Research",
        "## 12. Hyperparameter Research Policy",
        "## 13. Calibration Policy",
        "## 14. Threshold Research Policy",
        "## 15. Regime and Drift Analysis",
        "## 16. Experiment Registry and Lineage",
        "## 17. Required Metrics",
        "## 18. Baselines",
        "## 19. Candidate Selection Rules",
        "## 20. Protected Evaluation Policy",
        "## 21. Classification Versus Strategy Evaluation",
        "## 24. Acceptance Criteria for Alpha 3",
    )
    for section in required_spec_sections:
        assert section in spec

    required_protocol_terms = (
        "Minimum initial training rows: `756`",
        "Default assessment window: `126` supervised rows",
        "Default step size: `63` supervised rows",
        "`BOUNDARY_EXCLUSION_SESSIONS = 6`",
        "expanding-window walk-forward validation",
        "six-row boundary exclusion",
        "feature warm-up",
        "deterministic fold identities",
        "deterministic experiment identities",
    )
    for term in required_protocol_terms:
        assert term in normalized_combined

    required_boundaries = (
        "must not tune against the already-opened Phase 2 final test",
        "Phase 2 final-test row-level labels",
        "must not be used for Phase 3 tuning",
        "no live trading",
        "no automatic paper-order submission",
        "API write routes",
        "dashboard execution controls",
        "protected evaluation",
        "strategy optimization",
        "NO CANDIDATE PROMOTION",
        "NO APPROVED SHADOW MODEL",
        "artifacts/research/<experiment_id>/",
    )
    for boundary in required_boundaries:
        assert boundary.lower() in combined_lower

    assert "strategy performance alone as evidence of a reliable predictive edge" in normalized_spec
    assert (
        "Alpha 3 acceptance does not require a candidate model to beat baselines" in normalized_spec
    )
    assert "artifacts/research/*" in gitignore
    assert "!artifacts/research/.gitkeep" in gitignore
    assert "live-money readiness: approved" not in combined_lower
    assert "profitability is guaranteed" not in combined_lower


def test_version_2_phase3_alpha3_release_preparation_evidence_is_documented() -> None:
    evidence = (ROOT / "docs/V2_PHASE_03_ALPHA3_RELEASE_EVIDENCE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md").read_text(encoding="utf-8")
    workflows = (ROOT / "docs/WORKFLOWS.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/SECURITY_AND_SAFETY.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    combined = "\n".join((evidence, readme, changelog, roadmap, spec, workflows, safety))
    combined_lower = combined.lower()
    normalized_combined_lower = " ".join(combined_lower.split())
    normalized_evidence = " ".join(evidence.split())

    assert "Released package/runtime version: `2.0.0a3`" in evidence
    assert "Public release identifier: `v2.0.0-alpha.3` has been tagged and released" in evidence
    assert "campaign ID: `spy-v2p3-dev-3741349b8aa34020b8425af5`" in evidence
    assert "parent Phase 1 dataset ID: `spy-v2p1-825930b0a2bcab20c733b867`" in evidence
    assert "research-slice dataset ID: `spy-v2p3-dev-slice-d026d0f30a0e378b7c0b6b9d`" in evidence
    assert "exclusion policy: `phase2-final-test-session-exclusion-v1`" in evidence
    assert "Phase 2 final-test available for tuning: `false`" in evidence
    assert "eligible development prediction range: 2018-03-29 through 2024-10-07" in evidence
    assert "fold count: `13`" in evidence
    assert "Selected development feature set: `baseline_plus_drawdown_position`" in evidence
    assert "**NO CANDIDATE PROMOTION**" in evidence
    assert "Protected evaluation status: `scaffolded_locked_no_access`" in evidence
    assert "strategy_results_artifact` as `not_generated_classification_first_branch" in evidence
    assert "all indexed SHA-256 checksums were recomputed and matched" in normalized_evidence
    assert "artifact_index.json` checksum" in evidence

    required_boundaries = (
        "protected evaluation was not executed",
        "strategy optimization was not authorized and was not executed",
        "no predictive edge is claimed",
        "public `v2.0.0-alpha.3` tag was created only after release-preparation merge",
        "phase 4 shadow mode",
        "phase 2 final-test rows were unavailable for phase 3 tuning",
    )
    for boundary in required_boundaries:
        assert boundary in normalized_combined_lower

    prohibited_payload_markers = (
        '"fold_evaluations"',
        '"y_true"',
        '"y_score"',
        '"probabilities"',
        '"prediction_sessions"',
        "raw provider response",
        "account id",
        "api key",
    )
    evidence_lower = evidence.lower()
    for marker in prohibited_payload_markers:
        assert marker not in evidence_lower

    assert "artifacts/research/*" in gitignore
    assert "!artifacts/research/.gitkeep" in gitignore


def test_version_2_phase4_beta1_release_preparation_is_documented() -> None:
    evidence = (ROOT / "docs/V2_PHASE_04_BETA1_RELEASE_EVIDENCE.md").read_text(encoding="utf-8")
    release_notes = (ROOT / "RELEASE_NOTES_V2.0.0_BETA_1.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md").read_text(encoding="utf-8")
    workflows = (ROOT / "docs/WORKFLOWS.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/SECURITY_AND_SAFETY.md").read_text(encoding="utf-8")
    combined = "\n".join(
        (evidence, release_notes, readme, changelog, roadmap, spec, workflows, safety)
    )
    combined_lower = combined.lower()

    assert "Released package/runtime version: `2.0.0b1`" in evidence
    assert "Package version: `2.0.0b1`" in release_notes
    assert "Git release identifier: `v2.0.0-beta.1`" in release_notes
    assert "Public release identifier: `v2.0.0-beta.1` has been tagged" in evidence
    assert "Public `v2.0.0-beta.1` release/tag: created" in readme
    assert "Release commit: `1c8feae478c0f5536b2193eeb408e580f3f7e33c`" in roadmap
    assert "Public tag: `v2.0.0-beta.1` CREATED" in spec
    assert "PR #27 merged" in evidence
    assert "PR #28 merged" in evidence
    assert "PR #29 merged" in evidence
    assert "owner acceptance is complete" in roadmap
    assert "shadow run ID: `spy-v2p4-shadow-09c5104d1614fe7902a3fadf`" in evidence
    assert "session: 2025-12-31" in evidence
    assert "model gate status: `blocked_no_approved_model`" in evidence
    assert "alerts: `none`" in evidence
    assert "NO CANDIDATE PROMOTION" in release_notes
    assert "Beta 1 contains no approved model-connected shadow inference" in release_notes
    assert "Gate B = BLOCKED" in evidence
    assert "approved shadow model = none" in evidence
    assert "model inference = unavailable" in evidence
    assert "ShadowProposal operational generation = unavailable" in evidence
    assert "broker communication = unavailable" in evidence
    assert "paper execution = unauthorized" in evidence
    assert "unattended scheduling = unauthorized" in evidence
    assert "live trading = prohibited" in evidence
    assert "ACTIVE - specification + non-submitting safety/recovery scaffold" in roadmap
    assert (
        "review/v2-phase-05-production-paper` (active specification + non-submitting"
    ) in roadmap

    required_boundaries = (
        "no model loading",
        "no model inference",
        "no operational `ShadowProposal` generation",
        "no broker communication",
        "no paper execution",
        "no unattended scheduler",
        "no market-data acquisition",
        "no protected evaluation",
        "no Phase 5 production paper submission",
        "no live trading",
    )
    for boundary in required_boundaries:
        assert boundary.lower() in combined_lower

    prohibited_release_claims = (
        "profitability is guaranteed",
        "predictive edge is claimed",
        "paper-trading readiness is approved",
        "live readiness is approved",
        "beta.2 tag already exists",
    )
    for claim in prohibited_release_claims:
        assert claim not in combined_lower

    sanitized_markers = (
        "/Users/",
        "C:\\",
        "localhost",
        "raw provider payload",
        '"bars"',
        '"y_true"',
        '"y_score"',
    )
    for marker in sanitized_markers:
        assert marker not in evidence


def test_version_2_phase2_benchmark_integrity_governance_is_documented() -> None:
    spec = (ROOT / "docs/V2_PHASE_02_REAL_HISTORICAL_BENCHMARK_SPEC.md").read_text(encoding="utf-8")
    lower_spec = spec.lower()
    normalized_spec = " ".join(spec.split())

    assert "adjustment mode `all`" in spec
    assert "Raw, split-only, dividend-only" in spec
    assert "SIP is the preferred primary benchmark feed" in spec
    assert "IEX is a single-exchange feed" in spec
    assert "must not silently substitute IEX for SIP" in spec
    assert "Changing the feed creates a new dataset ID and a new benchmark ID" in spec
    assert "whether the dataset qualifies as the primary benchmark" in spec

    expected_cost_rows = (
        "| `idealized` | `0` | `0` | `0` | `0` |",
        "| `base` | `0.125` | `0.25` | `0.375` | `0.75` |",
        "| `adverse` | `1` | `2` | `3` | `6` |",
        "| `severe` | `10` | `20` | `30` | `60` |",
    )
    for row in expected_cost_rows:
        assert row in spec
    assert "Risk-free-rate assumption: `0.0%` annualized" in spec
    assert "Whole-share constraints apply" in spec
    assert 'Initial cash: `Decimal("10000")`' in spec
    assert "Cost rounding policy: use the existing `Decimal` arithmetic" in spec
    assert "meaningfully higher" not in spec
    assert "conservative stress value" not in spec

    assert "`BOUNDARY_EXCLUSION_SESSIONS = 6`" in spec
    assert "train_rows = floor(assignable_rows * 70 / 100)" in spec
    assert "validation_rows = floor(assignable_rows * 15 / 100)" in spec
    assert "final-test partition receives every remainder" in lower_spec
    assert "| Training | 756 | 120 | 120 |" in spec
    assert "| Validation | 252 | 40 | 40 |" in spec
    assert "| Final test | 252 | 40 | 40 |" in spec
    assert "Boundaries must not be moved after inspecting model metrics" in spec
    assert "prohibition on outcome-driven boundary adjustment" in spec

    assert "train candidate models on the training partition only" in spec
    assert "freshly refit the selected model on the combined training and validation" in spec
    assert "exclude gap observations and final-test observations from fitting" in spec
    assert "Before the final-test lock and explicit owner acknowledgement" in spec
    assert "selected-model final-test metrics" in spec
    assert "final-test cost-sensitivity results" in spec
    assert "Stage B must calculate all final-test model, baseline, regime, strategy" in spec
    assert "not a separate independent classification baseline" in spec

    assert (
        "Phase 2 benchmark infrastructure and controlled evaluation workflow passed acceptance"
    ) in normalized_spec
    assert "owner-run real SIP benchmark acceptance gates completed" in normalized_spec
    assert "did not establish strong directional predictive discrimination" in normalized_spec
    assert "phase 2 benchmark passed" not in lower_spec
    assert "phase 2 implementation complete" not in lower_spec
