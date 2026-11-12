"""POSIX descriptor-relative, no-follow, no-clobber context-only persistence.

A persistent global context flock deliberately rejects simultaneous operations.
No rollback guesses ownership from an initial exists() observation. Interrupted
partial sets are recovery_required and must be inspected manually, never deleted.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from spy_market_agent.market_data.context_contracts import DATASET_PATTERN, MAX_BYTES
from spy_market_agent.market_data.errors import (
    ExistingDatasetConflict,
    ManifestValidationFailure,
    UnsafeDataPath,
)

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ContextStore:
    def __init__(self, repository_root: Path | None = None) -> None:
        candidate = repository_root or Path.cwd()
        if candidate.is_symlink():
            raise UnsafeDataPath("context repository root cannot be a symlink.")
        self.root = candidate.resolve(strict=True)

    def paths(self, symbol: str, dataset_id: str) -> tuple[Path, Path, Path]:
        if symbol not in ("QQQ", "IWM") or not DATASET_PATTERN.fullmatch(dataset_id):
            raise UnsafeDataPath("invalid context artifact identity.")
        return tuple(
            Path("data", kind, "alpaca", symbol, "1Day", "sip", "all", f"{dataset_id}.{suffix}")
            for kind, suffix in (
                ("raw", "raw.json"),
                ("canonical", "canonical.csv"),
                ("manifests", "manifest.json"),
            )
        )  # type: ignore[return-value]

    def _parts(self, path: Path) -> tuple[str, ...]:
        relative = path.relative_to(self.root) if path.is_absolute() else path
        parts = relative.parts
        if (
            len(parts) < 3
            or parts[0] != "data"
            or parts[1] not in ("raw", "canonical", "manifests")
            or any(item in (".", "..") for item in parts)
        ):
            raise UnsafeDataPath("context path is outside the approved ignored layout.")
        return parts

    @contextmanager
    def _directory(self, parts: tuple[str, ...], *, create: bool = False) -> Iterator[int | None]:
        descriptor = os.open(self.root, DIR_FLAGS)
        try:
            root_info = os.fstat(descriptor)
            if (
                root_info.st_uid != os.geteuid()
                or root_info.st_mode & 0o022
                or not root_info.st_mode & 0o200
                or not root_info.st_mode & 0o100
            ):
                raise UnsafeDataPath(
                    "context repository root must be owner-controlled and writable."
                )
            for part in parts:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                try:
                    child = os.open(part, DIR_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    yield None
                    return
                except OSError:
                    raise UnsafeDataPath(
                        "context ancestry must contain only real directories."
                    ) from None
                info = os.fstat(child)
                if (
                    info.st_uid != os.geteuid()
                    or info.st_mode & 0o022
                    or not info.st_mode & 0o200
                    or not info.st_mode & 0o100
                ):
                    os.close(child)
                    raise UnsafeDataPath(
                        "context directories must be owner-controlled and not group/world writable."
                    )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def _ignored(self, path: Path) -> None:
        relative = Path(*self._parts(path))
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", "--", relative.as_posix()],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or tracked.returncode == 0:
            raise UnsafeDataPath("context artifact destination must be ignored and untracked.")

    @staticmethod
    def _stat(descriptor: int, name: str) -> os.stat_result | None:
        try:
            result = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise UnsafeDataPath("context artifact must be a regular single-link file.")
        return result

    def preflight(self, symbol: str) -> None:
        # Walk existing directories only: invalid source data must not create data/.
        with self._directory(("data", "manifests")) as lock_parent:
            if lock_parent is not None:
                self._stat(lock_parent, ".context-acquisition-v2.lock")
        for path in self.paths(symbol, "aq-ctx-v2-" + "0" * 24):
            self._ignored(path.parent)
            self._ignored(path)
            parts = self._parts(path)
            with self._directory(parts[:-1]) as descriptor:
                if descriptor is not None:
                    # Reject symlinks in any existing final artifact, including dangling.
                    for name in os.listdir(descriptor):  # noqa: PTH208 - pinned dir_fd, never re-resolve paths
                        self._stat(descriptor, name)
                        if name.startswith(".context-") and name.endswith(".tmp"):
                            raise ExistingDatasetConflict(
                                "interrupted context staging file; recovery_required."
                            )

    def _check_pinned(self, path: Path, descriptor: int) -> None:
        self._ignored(path)
        with self._directory(self._parts(path)[:-1]) as current:
            if current is None or self._inode(os.fstat(current)) != self._inode(
                os.fstat(descriptor)
            ):
                raise UnsafeDataPath("context ancestry changed during operation.")

    @staticmethod
    def _inode(info: os.stat_result) -> tuple[int, int]:
        return info.st_dev, info.st_ino

    @contextmanager
    def locked(self, *, create: bool) -> Iterator[None]:
        lock = Path("data/manifests/.context-acquisition-v2.lock")
        self._ignored(lock)
        with self._directory(("data", "manifests"), create=create) as parent:
            if parent is None:
                raise ManifestValidationFailure("context manifest storage is missing.")
            try:
                if create:
                    try:
                        descriptor = os.open(
                            lock.name,
                            os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=parent,
                        )
                    except FileExistsError:
                        descriptor = os.open(
                            lock.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                        )
                else:
                    descriptor = os.open(
                        lock.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                    )
            except FileNotFoundError:
                if not create:
                    raise ManifestValidationFailure(
                        "context coordination lock is missing; recovery_required."
                    ) from None
                raise ExistingDatasetConflict(
                    "context lock creation raced; retry manually."
                ) from None
            except OSError:
                raise UnsafeDataPath("context lock must be a stable regular file.") from None
            try:
                info = self._stat(parent, lock.name)
                if info is None or self._inode(info) != self._inode(os.fstat(descriptor)):
                    raise UnsafeDataPath("context lock identity mismatch.")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ExistingDatasetConflict(
                        "context operation already in progress; retry manually."
                    ) from None
                self._check_pinned(lock, parent)
                yield
            finally:
                os.close(descriptor)

    def read(self, path: Path) -> bytes:
        parts = self._parts(path)
        self._ignored(path)
        with self._directory(parts[:-1]) as parent:
            if parent is None or self._stat(parent, parts[-1]) is None:
                raise ManifestValidationFailure(
                    "context artifact set is incomplete; recovery_required."
                )
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            try:
                info = self._stat(parent, parts[-1])
                opened = os.fstat(descriptor)
                if (
                    info is None
                    or self._inode(info) != self._inode(opened)
                    or not stat.S_ISREG(opened.st_mode)
                    or opened.st_size > MAX_BYTES
                ):
                    raise UnsafeDataPath("invalid context artifact size or identity.")
                self._check_pinned(path, parent)
                chunks: list[bytes] = []
                total = 0
                while chunk := os.read(descriptor, 65536):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise UnsafeDataPath("context artifact exceeds byte budget.")
                    chunks.append(chunk)
                self._check_pinned(path, parent)
                return b"".join(chunks)
            finally:
                os.close(descriptor)

    def _publish(
        self, parent: int, path: Path, payload: bytes, owned: dict[Path, tuple[int, int]]
    ) -> None:
        self._check_pinned(path, parent)
        stage = f".context-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(descriptor)
            owned[path] = self._inode(os.fstat(descriptor))
            self._check_pinned(path, parent)
            # Atomic link, not replace: an existing final path is never overwritten.
            os.link(stage, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.unlink(stage, dir_fd=parent)
            os.fsync(parent)
            self._check_pinned(path, parent)
        finally:
            os.close(descriptor)
            with suppress(FileNotFoundError):
                os.unlink(stage, dir_fd=parent)

    def write(
        self, paths: tuple[Path, Path, Path], payloads: tuple[bytes, bytes, bytes]
    ) -> tuple[bool, bool, bool]:
        if any(len(payload) > MAX_BYTES for payload in payloads):
            raise UnsafeDataPath("context payload exceeds byte budget.")
        with self.locked(create=True):
            present = []
            for path in paths:
                with self._directory(self._parts(path)[:-1]) as parent:
                    present.append(parent is not None and self._stat(parent, path.name) is not None)
            if any(present):
                if all(present) and all(
                    self.read(path) == data for path, data in zip(paths, payloads, strict=True)
                ):
                    return False, False, False
                raise ExistingDatasetConflict(
                    "conflicting or incomplete context set; recovery_required."
                )
            owned: dict[Path, tuple[int, int]] = {}
            parents: dict[Path, int] = {}
            try:
                for path, data in zip(paths, payloads, strict=True):
                    with self._directory(self._parts(path)[:-1], create=True) as parent:
                        if parent is None:
                            raise UnsafeDataPath("context parent is missing.")
                        parents[path] = os.dup(parent)
                        self._publish(parent, path, data, owned)
                return True, True, True  # manifest was published LAST: completion marker
            except BaseException:
                for path, inode in owned.items():
                    parent = parents[path]
                    try:
                        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if info is not None and self._inode(info) == inode:
                        os.unlink(path.name, dir_fd=parent)
                raise
            finally:
                for descriptor in parents.values():
                    os.close(descriptor)
