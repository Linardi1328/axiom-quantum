from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from spy_market_agent import __version__
from spy_market_agent.benchmark.errors import BenchmarkLockError, raise_benchmark_error
from spy_market_agent.benchmark.locks import BenchmarkLock

LINEAGE_DEPENDENCIES: tuple[str, ...] = (
    "pandas",
    "pydantic",
    "scikit-learn",
    "exchange-calendars",
    "alpaca-py",
)


@dataclass(frozen=True, slots=True)
class RuntimeLineage:
    git_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]


def current_runtime_lineage(
    *,
    repository_root: Path | None = None,
    dependency_names: tuple[str, ...] = LINEAGE_DEPENDENCIES,
) -> RuntimeLineage:
    package_version = _installed_package_version()
    if package_version != __version__:
        raise_benchmark_error(
            BenchmarkLockError,
            "package_runtime_version_mismatch",
            "installed package version must match spy_market_agent.__version__.",
        )
    return RuntimeLineage(
        git_commit_sha=_current_git_commit(repository_root),
        python_version=platform.python_version(),
        package_version=package_version,
        dependency_versions=_dependency_versions(dependency_names),
    )


def require_runtime_lineage(
    lock: BenchmarkLock,
    *,
    repository_root: Path | None = None,
    current: RuntimeLineage | None = None,
) -> RuntimeLineage:
    observed = current or current_runtime_lineage(
        repository_root=repository_root,
        dependency_names=_locked_dependency_names(lock),
    )
    mismatches: list[str] = []
    if observed.git_commit_sha != lock.code_commit_sha:
        mismatches.append("Git commit SHA")
    if observed.package_version != lock.package_version:
        mismatches.append("package/runtime version")
    if observed.python_version != lock.python_version:
        mismatches.append("Python version")
    for package_name, locked_version in lock.dependency_versions.items():
        if observed.dependency_versions.get(package_name) != locked_version:
            mismatches.append(f"dependency {package_name}")
    for package_name, locked_version in lock.identity_input.dependency_versions.items():
        if observed.dependency_versions.get(package_name) != locked_version:
            mismatches.append(f"identity dependency {package_name}")
    if lock.identity_input.package_version != lock.package_version:
        mismatches.append("identity package/runtime version")
    identity_python_version = getattr(lock.identity_input, "python_version", lock.python_version)
    if identity_python_version != lock.python_version:
        mismatches.append("identity Python version")
    if lock.identity_input.code_commit_sha != lock.code_commit_sha:
        mismatches.append("identity Git commit SHA")
    if mismatches:
        raise_benchmark_error(
            BenchmarkLockError,
            "runtime_lineage_mismatch",
            "current runtime does not match frozen benchmark lock: "
            + ", ".join(dict.fromkeys(mismatches)),
        )
    return observed


def dependency_versions() -> dict[str, str]:
    return _dependency_versions(LINEAGE_DEPENDENCIES)


def _locked_dependency_names(lock: BenchmarkLock) -> tuple[str, ...]:
    names = set(LINEAGE_DEPENDENCIES)
    names.update(lock.dependency_versions)
    names.update(lock.identity_input.dependency_versions)
    return tuple(sorted(names))


def _dependency_versions(package_names: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name in package_names:
        try:
            versions[package_name] = version(package_name)
        except PackageNotFoundError:
            versions[package_name] = "not-installed"
    return versions


def _installed_package_version() -> str:
    try:
        return version("spy-market-agent")
    except PackageNotFoundError:
        return __version__


def _current_git_commit(repository_root: Path | None) -> str | None:
    command = ["git"]
    if repository_root is not None:
        command.extend(["-C", str(repository_root)])
    command.extend(["rev-parse", "HEAD"])
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None
