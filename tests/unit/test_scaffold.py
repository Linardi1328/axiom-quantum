from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path

import spy_market_agent
from spy_market_agent.backtesting import BACKTEST_SCHEMA_VERSION
from spy_market_agent.datasets import LABEL_SCHEMA_VERSION
from spy_market_agent.execution import PAPER_EXECUTION_SCHEMA_VERSION
from spy_market_agent.features import FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data import (
    PHASE1_MANIFEST_SCHEMA_VERSION,
    PHASE1_SCHEMA_VERSION,
)
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.modeling import MODEL_SCHEMA_VERSION
from spy_market_agent.persistence.schema import PERSISTENCE_SCHEMA_VERSION
from spy_market_agent.risk import RISK_SCHEMA_VERSION
from spy_market_agent.strategies import STRATEGY_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _allows_version(requirement: str, version: tuple[int, int]) -> bool:
    major, minor = version

    for raw_constraint in requirement.split(","):
        constraint = raw_constraint.strip()
        if constraint.startswith(">="):
            minimum = tuple(int(part) for part in constraint[2:].split("."))
            if (major, minor) < minimum:
                return False
        elif constraint.startswith("<"):
            maximum = tuple(int(part) for part in constraint[1:].split("."))
            if (major, minor) >= maximum:
                return False
        else:
            msg = f"Unsupported version constraint in test: {constraint}"
            raise AssertionError(msg)

    return True


def test_package_can_be_imported() -> None:
    assert spy_market_agent is not None


def test_package_version_is_non_empty_string() -> None:
    assert isinstance(spy_market_agent.__version__, str)
    assert spy_market_agent.__version__


def test_package_version_matches_installed_metadata() -> None:
    assert spy_market_agent.__version__ == version("spy-market-agent")


def test_version_2_beta1_release_candidate_version_matches_pyproject_and_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert pyproject["project"]["version"] == "2.0.0b1"
    assert version("spy-market-agent") == "2.0.0b1"
    assert spy_market_agent.__version__ == "2.0.0b1"


def test_required_top_level_documentation_files_exist() -> None:
    assert (ROOT / "PROJECT_SPEC.md").is_file()
    assert (ROOT / "AGENTS.md").is_file()
    assert (ROOT / "README.md").is_file()


def test_release_preparation_keeps_data_api_and_database_schema_versions_stable() -> None:
    assert MARKET_DATA_SCHEMA_VERSION == "spy-daily-ohlcv-v1"
    assert FEATURE_SCHEMA_VERSION == "spy-daily-features-v1"
    assert LABEL_SCHEMA_VERSION == "spy-open-t1-to-open-t6-net-positive-v1"
    assert MODEL_SCHEMA_VERSION == "spy-binary-models-v1"
    assert STRATEGY_SCHEMA_VERSION == "spy-long-cash-strategy-v1"
    assert RISK_SCHEMA_VERSION == "spy-long-only-risk-v1"
    assert BACKTEST_SCHEMA_VERSION == "spy-daily-next-open-backtest-v1"
    assert PAPER_EXECUTION_SCHEMA_VERSION == "spy-paper-execution-v1"
    assert PERSISTENCE_SCHEMA_VERSION == "spy-sqlite-persistence-v2"
    assert PHASE1_SCHEMA_VERSION == "spy-v2-phase1-canonical-daily-bars-v1"
    assert PHASE1_MANIFEST_SCHEMA_VERSION == "spy-v2-phase1-dataset-manifest-v1"


def test_beta1_release_preparation_does_not_add_scheduler_dependencies_or_runtime_tag_logic() -> (
    None
):
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    dependencies = tuple(pyproject["project"]["dependencies"]) + tuple(
        pyproject["project"]["optional-dependencies"]["dev"]
    )
    dependency_text = "\n".join(dependencies).lower()

    for forbidden_dependency in ("apscheduler", "celery", "rq"):
        assert forbidden_dependency not in dependency_text

    release_identifier_hits: list[Path] = []
    for path in (ROOT / "src/spy_market_agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "v2.0.0-beta.1" in text:
            release_identifier_hits.append(path.relative_to(ROOT))
        assert "git tag" not in text, path
        assert "tag already exists" not in text.lower(), path

    assert release_identifier_hits == [Path("src/spy_market_agent/shadow/types.py")]


def test_real_env_file_is_not_tracked_or_required() -> None:
    assert (ROOT / ".env.example").is_file()

    if not (ROOT / ".git").exists():
        return

    result = subprocess.run(
        ["git", "ls-files", "--", ".env"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout == ""


def test_declared_python_version_range_supports_only_python_3_12() -> None:
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    requires_python = pyproject["project"]["requires-python"]

    assert _allows_version(requires_python, (3, 12))
    assert not _allows_version(requires_python, (3, 13))
    assert not _allows_version(requires_python, (sys.version_info.major, 14))
