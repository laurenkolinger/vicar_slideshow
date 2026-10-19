"""Module: vicar_showcase.copy_content
Purpose: Copy the slides and the captions file from one folder to another, verifying
    every copy by SHA-256, never overwriting a different file, and logging each action.
Inputs: A source folder, an existing destination folder, an optional captions file path.
Outputs: Verified copies in the destination, a CSV log (_showcase_copy_log.csv) there,
    and moved-aside old versions under _superseded/<stamp>/ when replacing.

Placing a file never overwrites one that appeared after planning: the verified temporary
copy is hard-linked to its final name (os.link fails if the name exists, also on a
case-insensitive folder where a.jpg and A.jpg are the same name). Where hard links are not
supported (some network and exFAT folders) an exclusive create (O_CREAT | O_EXCL) of the
final name guards the move; a file created in the instant between that check and the
move is the one case the fallback cannot see.
"""

from __future__ import annotations

import csv
import getpass
import hashlib
import io
import itertools
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .common import BYTES_PER_MB, EXIT_FAILED, EXIT_NO_CONTENT, EXIT_REFUSED
from .content import scan_content
from .settings import DEFAULT_CAPTIONS_FILE

try:
    import fcntl
except ImportError:  # Windows has no fcntl
    fcntl = None

LOG_NAME = "_showcase_copy_log.csv"
SUPERSEDED_DIR = "_superseded"
LOG_COLUMNS = ("timestamp", "action", "file", "bytes", "sha256", "source_dir", "run_by")
SPACE_MARGIN = 1.05
CHUNK_BYTES = 1024 * 1024
AST_OFFSET_HOURS = -4
PARTIAL_PREFIX = ".showcase-"  # short on purpose: a long file name must not overflow the 255-byte limit
PARTIAL_SUFFIX = ".partial"

_LOG_LOCK = threading.Lock()
_PARTIAL_COUNTER = itertools.count(1)
_LOGGER = logging.getLogger(__name__)

ACTION_COPIED = "copied"
ACTION_PRESENT = "already there"
ACTION_REPLACED = "replaced"
ACTION_SKIPPED = "skipped"
ACTION_FAILED = "failed"
ACTION_CONFLICT = "conflict"


class PlacementConflict(Exception):
    """The final name already exists, so the file was not placed."""


class CopyError(Exception):
    """A copy that cannot start or finish; `exit_code` is the process exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        """Keep the message and the exit code the command line should return."""
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class PlanItem:
    """One file to place in the destination."""

    name: str
    source: Path
    size: int
    status: str  # "copy", "present" or "conflict"


@dataclass
class CopyPlan:
    """What a copy run will do."""

    source_dir: Path
    dest_dir: Path
    replace: bool
    items: list[PlanItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def conflicts(self) -> list[PlanItem]:
        """Items whose destination file differs from the source."""
        return [i for i in self.items if i.status == "conflict"]

    @property
    def bytes_to_copy(self) -> int:
        """Total size of the files that will be written."""
        return sum(i.size for i in self.items if i.status in ("copy", "conflict"))


@dataclass
class CopyResult:
    """Counts from a finished run."""

    copied: int = 0
    present: int = 0
    replaced: int = 0
    skipped: int = 0
    failed: int = 0
    conflicts: int = 0
    failures: list[str] = field(default_factory=list)


def now_ast() -> datetime:
    """Return the current time in Atlantic Standard Time (UTC-4)."""
    return datetime.now(timezone(timedelta(hours=AST_OFFSET_HOURS)))


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file.

    Parameters:
        path: The file to hash.
    Returns:
        The digest.
    Raises:
        OSError: The file cannot be read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _real(path: Path) -> Path:
    """Return a path with symlinks resolved."""
    return Path(os.path.realpath(path))


def _check_folders(source: Path, dest: Path) -> None:
    """Check the preconditions on the two folders.

    Parameters:
        source: The source folder.
        dest: The destination folder (never created).
    Raises:
        CopyError: A folder is missing, or one holds the other.
    """
    for label, folder in (("source", source), ("destination", dest)):
        if not folder.is_dir():
            note = " (the destination is never created; make it first)" if label == "destination" else ""
            raise CopyError(f"{label} folder not found or not a folder: {folder}{note}", EXIT_NO_CONTENT)
    a, b = _real(source), _real(dest)
    if a == b or a in b.parents or b in a.parents:
        raise CopyError(
            f"source {source} and destination {dest} are the same folder or one is inside the other",
            EXIT_REFUSED,
        )


def _same_content(src: Path, dst: Path, size: int) -> bool:
    """Return True when the destination file has the source's size and SHA-256."""
    try:
        return dst.is_file() and dst.stat().st_size == size and sha256_file(dst) == sha256_file(src)
    except OSError:
        return False


def plan_copy(
    source_dir: Path,
    dest_dir: Path,
    captions_path: Optional[Path] = None,
    captions_file: str = DEFAULT_CAPTIONS_FILE,
    replace: bool = False,
) -> CopyPlan:
    """Decide what a copy will do, without writing anything.

    Parameters:
        source_dir: Folder to copy from.
        dest_dir: Existing folder to copy into.
        captions_path: Captions spreadsheet to copy; defaults to source_dir/captions_file.
            It is written to the destination under the name `captions_file`.
        captions_file: Name of the captions file in the destination.
        replace: When True, differing destination files are replaced (old ones are
            moved to _superseded/); otherwise they are conflicts.
    Returns:
        The CopyPlan: items (copy, present or conflict), skipped files, warnings.
    Raises:
        CopyError: A folder is missing, `captions_path` is given but is not a file,
            one folder holds the other, or free space is too small.
    """
    source, dest = Path(source_dir), Path(dest_dir)
    _check_folders(source, dest)
    if captions_path is not None and not Path(captions_path).is_file():
        raise CopyError(f"captions file not found or not a file: {captions_path}", EXIT_NO_CONTENT)
    report = scan_content(source, captions_file)
    if report.error:
        raise CopyError(report.error, EXIT_NO_CONTENT)
    plan = CopyPlan(source, dest, replace, skipped=[(s.file, s.reason) for s in report.skipped])
    for slide in report.slides:
        plan.items.append(_plan_item(source / slide.file, dest, slide.file, slide.size_bytes))
    captions = Path(captions_path) if captions_path else source / captions_file
    if captions.is_file():
        plan.items.append(_plan_item(captions, dest, captions_file, captions.stat().st_size))
    else:
        plan.warnings.append(f"no captions file found at {captions}; only the slides are copied")
    _check_space(plan)
    return plan


def _plan_item(src: Path, dest: Path, name: str, size: int) -> PlanItem:
    """Classify one file against the destination.

    Parameters:
        src: The source file.
        dest: The destination folder.
        name: The file name to write.
        size: The source size in bytes.
    Returns:
        A PlanItem with status copy, present or conflict.
    """
    target = dest / name
    if not os.path.lexists(target):
        return PlanItem(name, src, size, "copy")
    return PlanItem(name, src, size, "present" if _same_content(src, target, size) else "conflict")


def _check_space(plan: CopyPlan) -> None:
    """Require free space for the bytes to copy plus a 5 percent margin.

    Parameters:
        plan: The plan to check.
    Raises:
        CopyError: Not enough free space on the destination.
    """
    need = int(plan.bytes_to_copy * SPACE_MARGIN)
    free = shutil.disk_usage(plan.dest_dir).free
    if free < need:
        raise CopyError(
            f"not enough free space in {plan.dest_dir}: need {need / BYTES_PER_MB:.0f} MB "
            f"(copy plus {round((SPACE_MARGIN - 1) * 100)} percent margin), free {free / BYTES_PER_MB:.0f} MB",
            EXIT_REFUSED,
        )


def describe_plan(plan: CopyPlan) -> list[str]:
    """Return the plan as printable lines.

    Parameters:
        plan: The plan.
    Returns:
        One line per file and per skipped file, then a totals line.
    """
    lines = []
    for item in plan.items:
        verb = {"copy": "will copy", "present": "already there", "conflict": "DIFFERS in destination"}[item.status]
        if item.status == "conflict" and plan.replace:
            verb = "will replace (old one moved to _superseded)"
        lines.append(f"  {verb}: {item.name} ({item.size / BYTES_PER_MB:.1f} MB)")
    lines.extend(f"  not copied: {name} ({reason})" for name, reason in plan.skipped)
    lines.extend(f"  warning: {w}" for w in plan.warnings)
    lines.append(f"  total to write: {plan.bytes_to_copy / BYTES_PER_MB:.1f} MB")
    return lines


class CopyLog:
    """Appends one CSV row per action to the destination's copy log."""

    def __init__(self, dest: Path, source: Path, run_by: str) -> None:
        """Remember where to write.

        Parameters:
            dest: Destination folder holding the log.
            source: Source folder (recorded in each row).
            run_by: The account name recorded in each row.
        """
        self.path = dest / LOG_NAME
        self.source = str(source)
        self.run_by = run_by

    def write(self, action: str, file: str, size: int = 0, sha: str = "") -> None:
        """Append one row.

        Parameters:
            action: copied, already there, replaced, skipped, conflict or failed.
            file: The file name.
            size: Size in bytes.
            sha: SHA-256 hex digest, or empty.
        Raises:
            CopyError: The log cannot be written.
        """
        row = io.StringIO()
        csv.writer(row).writerow([now_ast().isoformat(timespec="seconds"), action, file, size, sha,
                                  self.source, self.run_by])
        try:
            with _LOG_LOCK, open(self.path, "a", encoding="utf-8", newline="") as handle:
                _lock_file(handle)
                if handle.tell() == 0:
                    header = io.StringIO()
                    csv.writer(header).writerow(LOG_COLUMNS)
                    handle.write(header.getvalue())
                handle.write(row.getvalue())
                handle.flush()
        except OSError as exc:
            raise CopyError(f"cannot write the copy log {self.path}: {exc}", EXIT_FAILED) from exc


def _lock_file(handle) -> None:
    """Take an exclusive lock on an open file where the platform supports it.

    Parameters:
        handle: The open log file; the lock is released when it closes.
    """
    if fcntl is None:
        # Windows has no fcntl; the in-process lock still orders this tool's own threads.
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _copy_hashing(src: Path, partial: Path) -> str:
    """Copy a file to a partial name, hashing while copying, and fsync it.

    Parameters:
        src: Source file.
        partial: Temporary destination path.
    Returns:
        The SHA-256 of the bytes read from the source.
    Raises:
        OSError: Reading or writing failed.
    """
    digest = hashlib.sha256()
    with open(src, "rb") as reader, open(partial, "wb") as writer:
        for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _supersede(dest: Path, name: str, stamp: str) -> None:
    """Move an existing destination file to _superseded/<stamp>/ (never deleted).

    A file that has already vanished needs no moving.

    Parameters:
        dest: Destination folder.
        name: File name to move aside.
        stamp: Folder name, YYYYMMDD-HHMMSS.
    Raises:
        OSError: The move failed.
    """
    folder = dest / SUPERSEDED_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / name, 1
    while os.path.lexists(target):
        n += 1
        target = folder / f"{name}.{n}"
    try:
        os.replace(dest / name, target)
    except FileNotFoundError:
        # Removed by someone else since planning: nothing to set aside.
        return


def _partial_path(dest: Path) -> Path:
    """Return a short, unique temporary name inside the destination folder."""
    return dest / f"{PARTIAL_PREFIX}{os.getpid()}-{next(_PARTIAL_COUNTER)}{PARTIAL_SUFFIX}"


def _publish_without_overwrite(partial: Path, final: Path) -> None:
    """Give a verified temporary file its final name, never replacing an existing file.

    Parameters:
        partial: The verified temporary file.
        final: The name to publish under.
    Raises:
        PlacementConflict: `final` already exists (any case variant on a case-insensitive folder).
        OSError: A file operation failed.
    """
    try:
        os.link(partial, final)
        return
    except FileExistsError as exc:
        raise PlacementConflict(f"{final.name} appeared in the destination during the copy") from exc
    except (OSError, NotImplementedError, AttributeError):
        # Hard links are not supported here: use the exclusive-create guard below.
        pass
    try:
        os.close(os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError as exc:
        raise PlacementConflict(f"{final.name} appeared in the destination during the copy") from exc
    os.replace(partial, final)


def _remove_partial(partial: Path) -> None:
    """Remove a temporary file; a failure is logged and never replaces the real result."""
    try:
        partial.unlink()
    except FileNotFoundError:
        # Already gone (never created, or moved into place): nothing to clean.
        pass
    except OSError as exc:
        _LOGGER.warning("could not remove the temporary file %s: %s", partial, exc)


def _place_one(item: PlanItem, plan: CopyPlan, stamp: str) -> tuple[str, str]:
    """Copy one file through a temporary name, verify it, and publish it without clobbering.

    Parameters:
        item: The item to place.
        plan: The plan (destination, replace flag).
        stamp: Folder name for superseded files.
    Returns:
        (action, sha256) where action is copied or replaced.
    Raises:
        PlacementConflict: The final name exists now (it appeared after planning).
        OSError: A file operation failed.
        ValueError: The written file does not match the source hash.
    """
    final = plan.dest_dir / item.name
    partial = _partial_path(plan.dest_dir)
    try:
        digest = _copy_hashing(item.source, partial)
        if sha256_file(partial) != digest:
            raise ValueError(f"verification failed: {item.name} was not written correctly")
        action = ACTION_COPIED
        if item.status == "conflict":
            _supersede(plan.dest_dir, item.name, stamp)
            action = ACTION_REPLACED
        _publish_without_overwrite(partial, final)
        return action, digest
    finally:
        _remove_partial(partial)


def _refuse_conflicts(plan: CopyPlan) -> None:
    """Refuse the whole run when files differ and replace was not asked for.

    Parameters:
        plan: The plan.
    Raises:
        CopyError: Listing every conflicting file (exit 3).
    """
    if plan.conflicts and not plan.replace:
        names = "\n".join(f"  {i.name}" for i in plan.conflicts)
        raise CopyError(
            f"{len(plan.conflicts)} file(s) in {plan.dest_dir} differ from the source; nothing was "
            f"copied. Remove them or run again with --replace (old versions go to _superseded/):\n{names}",
            EXIT_REFUSED,
        )


def execute_plan(plan: CopyPlan, run_by: Optional[str] = None, out: Callable[[str], None] = print) -> CopyResult:
    """Carry out a plan.

    Parameters:
        plan: A plan from plan_copy.
        run_by: Account name for the log (default: the signed-in user).
        out: Prints progress lines.
    Returns:
        The CopyResult counts (failures list the files that failed or conflicted).
    Raises:
        CopyError: Conflicts without replace (nothing is copied), or the log is unwritable.
        (A file that appears in the destination after planning is counted in
        `conflicts` and left alone; the caller exits 3.)
    """
    _refuse_conflicts(plan)
    log = CopyLog(plan.dest_dir, plan.source_dir, run_by or getpass.getuser())
    stamp = now_ast().strftime("%Y%m%d-%H%M%S")
    result = CopyResult()
    for name, _reason in plan.skipped:
        log.write(ACTION_SKIPPED, name)
        result.skipped += 1
    for item in plan.items:
        _execute_item(item, plan, log, result, stamp, out)
    return result


def _record_present(item: PlanItem, plan: CopyPlan, log: CopyLog, result: CopyResult,
                    out: Callable[[str], None]) -> None:
    """Log a file that was already in the destination, or count it failed if it vanished.

    Parameters:
        item: The item planned as present.
        plan: The plan.
        log: The copy log.
        result: Counts, updated in place.
        out: Prints a progress line.
    """
    try:
        digest = sha256_file(plan.dest_dir / item.name)
    except OSError as exc:
        log.write(ACTION_FAILED, item.name, item.size)
        result.failed += 1
        result.failures.append(f"{item.name}: it was in the destination at planning but cannot be read now ({exc})")
        out(f"  FAILED: {item.name}: it was in the destination at planning but cannot be read now ({exc})")
        return
    log.write(ACTION_PRESENT, item.name, item.size, digest)
    result.present += 1
    out(f"  already there: {item.name}")


def _execute_item(item: PlanItem, plan: CopyPlan, log: CopyLog, result: CopyResult, stamp: str,
                  out: Callable[[str], None]) -> None:
    """Place one item, log it and update the counts.

    Parameters:
        item: The item.
        plan: The plan.
        log: The copy log.
        result: Counts, updated in place.
        stamp: Folder name for superseded files.
        out: Prints a progress line.
    """
    if item.status == "present":
        _record_present(item, plan, log, result, out)
        return
    try:
        action, digest = _place_one(item, plan, stamp)
    except PlacementConflict as exc:
        log.write(ACTION_CONFLICT, item.name, item.size)
        result.conflicts += 1
        result.failures.append(f"{item.name}: {exc}; it was left as it is")
        out(f"  CONFLICT: {item.name}: {exc}; it was left as it is")
        return
    except (OSError, ValueError) as exc:
        log.write(ACTION_FAILED, item.name, item.size)
        result.failed += 1
        result.failures.append(f"{item.name}: {exc}")
        out(f"  FAILED: {item.name}: {exc}")
        return
    log.write(action, item.name, item.size, digest)
    if action == ACTION_REPLACED:
        result.replaced += 1
    else:
        result.copied += 1
    out(f"  {action}: {item.name} ({item.size / BYTES_PER_MB:.1f} MB, verified)")
