from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import spy_market_agent.api as api
import spy_market_agent.dashboard as dashboard
import spy_market_agent.execution as execution
import spy_market_agent.persistence as persistence
from spy_market_agent.api import (
    DEFAULT_SQLITE_DATABASE_PATH,
    EDUCATIONAL_WARNING,
    MAX_PAGE_LIMIT,
    BacktestDetailResponse,
    BacktestRunListResponse,
    DataStatusResponse,
    ExecutionReadRepository,
    HealthResponse,
    ModelRunDetailResponse,
    ModelRunListResponse,
    PaperOrderAttemptResponse,
    PaperOrderListResponse,
    PaperTradingStatusResponse,
    ReadService,
    create_app,
)
from spy_market_agent.dashboard import (
    DASHBOARD_WARNING,
    DashboardApiClient,
    DashboardApiError,
    DashboardState,
    create_default_client,
    load_dashboard_state,
    main,
    render_dashboard,
)
from spy_market_agent.execution import (
    PAPER_EXECUTION_SCHEMA_VERSION,
    PaperBrokerProtocol,
    PaperExecutionService,
    PaperOrderApproval,
    PaperOrderInstruction,
    SQLitePaperExecutionRepository,
)
from spy_market_agent.persistence import (
    PERSISTENCE_SCHEMA_VERSION,
    BacktestRunSummary,
    ModelRunSummary,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
    PersistenceIntegrityError,
    PersistenceNotFoundError,
    PersistenceSchemaError,
    RuntimeSnapshot,
    SQLiteArtifactRepository,
    connect_database,
    initialize_database,
)


def test_public_phase7_exports_are_explicit_and_available() -> None:
    expected_persistence = {
        "PERSISTENCE_SCHEMA_VERSION": PERSISTENCE_SCHEMA_VERSION,
        "BacktestRunSummary": BacktestRunSummary,
        "ModelRunSummary": ModelRunSummary,
        "PersistenceConflictError": PersistenceConflictError,
        "PersistenceError": PersistenceError,
        "PersistenceInputError": PersistenceInputError,
        "PersistenceIntegrityError": PersistenceIntegrityError,
        "PersistenceNotFoundError": PersistenceNotFoundError,
        "PersistenceSchemaError": PersistenceSchemaError,
        "RuntimeSnapshot": RuntimeSnapshot,
        "SQLiteArtifactRepository": SQLiteArtifactRepository,
        "connect_database": connect_database,
        "initialize_database": initialize_database,
    }
    expected_api = {
        "DEFAULT_SQLITE_DATABASE_PATH": DEFAULT_SQLITE_DATABASE_PATH,
        "EDUCATIONAL_WARNING": EDUCATIONAL_WARNING,
        "MAX_PAGE_LIMIT": MAX_PAGE_LIMIT,
        "BacktestDetailResponse": BacktestDetailResponse,
        "BacktestRunListResponse": BacktestRunListResponse,
        "DataStatusResponse": DataStatusResponse,
        "ExecutionReadRepository": ExecutionReadRepository,
        "HealthResponse": HealthResponse,
        "ModelRunDetailResponse": ModelRunDetailResponse,
        "ModelRunListResponse": ModelRunListResponse,
        "PaperOrderAttemptResponse": PaperOrderAttemptResponse,
        "PaperOrderListResponse": PaperOrderListResponse,
        "PaperTradingStatusResponse": PaperTradingStatusResponse,
        "ReadService": ReadService,
        "create_app": create_app,
    }
    expected_dashboard = {
        "DASHBOARD_WARNING": DASHBOARD_WARNING,
        "DashboardApiClient": DashboardApiClient,
        "DashboardApiError": DashboardApiError,
        "DashboardState": DashboardState,
        "create_default_client": create_default_client,
        "load_dashboard_state": load_dashboard_state,
        "main": main,
        "render_dashboard": render_dashboard,
    }

    for module, expected in (
        (persistence, expected_persistence),
        (api, expected_api),
        (dashboard, expected_dashboard),
    ):
        assert set(module.__all__) == set(expected)
        for name, imported_value in expected.items():
            assert getattr(module, name) is imported_value


def test_public_phase8_execution_exports_are_explicit_and_available() -> None:
    expected_execution_subset = {
        "PAPER_EXECUTION_SCHEMA_VERSION": PAPER_EXECUTION_SCHEMA_VERSION,
        "PaperBrokerProtocol": PaperBrokerProtocol,
        "PaperExecutionService": PaperExecutionService,
        "PaperOrderApproval": PaperOrderApproval,
        "PaperOrderInstruction": PaperOrderInstruction,
        "SQLitePaperExecutionRepository": SQLitePaperExecutionRepository,
    }

    for name, imported_value in expected_execution_subset.items():
        assert name in execution.__all__
        assert getattr(execution, name) is imported_value
    assert "AlpacaPaperBroker" not in execution.__all__


def test_importing_phase7_packages_has_no_database_or_network_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert importlib.import_module("spy_market_agent.persistence") is persistence
    assert importlib.import_module("spy_market_agent.api") is api
    assert importlib.import_module("spy_market_agent.dashboard") is dashboard
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "module_name",
    [
        "spy_market_agent",
        "spy_market_agent.modeling",
        "spy_market_agent.strategies",
        "spy_market_agent.risk",
        "spy_market_agent.execution",
        "spy_market_agent.api",
        "spy_market_agent.dashboard",
    ],
)
def test_import_boundaries_do_not_import_alpaca_or_create_files(
    tmp_path: Path,
    module_name: str,
) -> None:
    code = (
        "import importlib, pathlib, sys; "
        f"importlib.import_module({module_name!r}); "
        "print(any(name == 'alpaca' or name.startswith('alpaca.') for name in sys.modules)); "
        "print(list(pathlib.Path('.').iterdir()))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["False", "[]"]
