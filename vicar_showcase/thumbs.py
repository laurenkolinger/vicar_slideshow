"""Module: vicar_showcase.thumbs
Purpose: Make and cache video first-frame thumbnails with ffmpeg, when it is installed.
Inputs: Video files and a thumbnail cache folder (default <repo>/.cache/thumbs).
Outputs: JPEG files named by a hash of the video's real path, size and modification
    time; None when ffmpeg is missing or fails.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from .content import ContentReport

THUMBS_SUBDIR = Path(".cache") / "thumbs"
FFMPEG_SEEK_SECONDS = "0.5"
THUMB_WIDTH_FILTER = "scale=480:-2"
THUMB_QUALITY = "4"
FFMPEG_TIMEOUT_SECONDS = 60

Runner = Callable[..., "subprocess.CompletedProcess"]

_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
_failed_keys: set[str] = set()


def default_thumbs_dir(repo_dir: Path) -> Path:
    """Return the cache folder inside the repository (gitignored)."""
    return Path(repo_dir) / THUMBS_SUBDIR


def _cache_key(video: Path) -> str:
    """Hash a video's real path, size and modification time.

    Parameters:
        video: The video file.
    Returns:
        A hex digest, so an edited or replaced file gets a new thumbnail.
    Raises:
        OSError: The file cannot be read.
    """
    real = os.path.realpath(video)
    info = os.stat(real)
    text = f"{real}|{info.st_size}|{info.st_mtime_ns}"
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def thumb_cache_path(thumbs_dir: Path, video: Path) -> Path:
    """Return where the thumbnail of a video is (or would be) cached.

    Parameters:
        thumbs_dir: The cache folder.
        video: The video file.
    Returns:
        The .jpg path; the file may not exist yet.
    Raises:
        OSError: The video cannot be read.
    """
    return Path(thumbs_dir) / f"{_cache_key(video)}.jpg"


def cached_thumb(thumbs_dir: Path, video: Path) -> Optional[Path]:
    """Return the cached thumbnail without generating one.

    Parameters:
        thumbs_dir: The cache folder.
        video: The video file.
    Returns:
        The path when a non-empty thumbnail exists, otherwise None.
    """
    try:
        path = thumb_cache_path(thumbs_dir, video)
        return path if path.is_file() and path.stat().st_size > 0 else None
    except OSError:
        return None


def _lock_for(key: str) -> threading.Lock:
    """Return the one lock that guards a cache key."""
    with _locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _ffmpeg_command(ffmpeg: str, video: Path, out: Path) -> list[str]:
    """Build the ffmpeg command: one frame at 0.5 s, 480 px wide.

    Parameters:
        ffmpeg: The ffmpeg executable.
        video: The input video.
        out: The output .jpg path.
    Returns:
        The argument list.
    """
    return [
        ffmpeg, "-y", "-loglevel", "error", "-ss", FFMPEG_SEEK_SECONDS, "-i", str(video),
        "-vframes", "1", "-vf", THUMB_WIDTH_FILTER, "-q:v", THUMB_QUALITY, str(out),
    ]


def _run_ffmpeg(command: list[str], out: Path, runner: Runner, timeout: int) -> bool:
    """Run ffmpeg and report whether it produced a non-empty file.

    Parameters:
        command: The ffmpeg argument list.
        out: The expected output file.
        runner: subprocess.run or a stand-in.
        timeout: Seconds before giving up.
    Returns:
        True on success.
    """
    try:
        result = runner(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        # ffmpeg missing, not executable or timed out: no thumbnail, the page falls back.
        return False
    return result.returncode == 0 and out.is_file() and out.stat().st_size > 0


def ensure_thumb(
    thumbs_dir: Path,
    video: Path,
    runner: Runner = subprocess.run,
    timeout: int = FFMPEG_TIMEOUT_SECONDS,
    ffmpeg: Optional[str] = None,
) -> Optional[Path]:
    """Return the thumbnail of a video, making it when needed.

    Two callers asking for the same video never run ffmpeg twice: the second waits
    and finds the first one's file.

    Parameters:
        thumbs_dir: The cache folder (created when missing).
        video: The video file.
        runner: Runs ffmpeg (subprocess.run by default; tests pass a stub).
        timeout: Seconds before ffmpeg is abandoned.
        ffmpeg: The ffmpeg executable; found on PATH when None.
    Returns:
        The thumbnail path, or None when ffmpeg is missing or fails.
    """
    exe = ffmpeg or shutil.which("ffmpeg")
    if exe is None:
        return None
    try:
        key = _cache_key(video)
    except OSError:
        return None
    final = Path(thumbs_dir) / f"{key}.jpg"
    with _lock_for(key):
        if cached_thumb(thumbs_dir, video) is not None:
            return final
        if key in _failed_keys:
            return None
        return _generate(exe, video, final, key, runner, timeout)


def _generate(exe: str, video: Path, final: Path, key: str, runner: Runner, timeout: int) -> Optional[Path]:
    """Make a thumbnail through a temporary file, then move it into place.

    Parameters:
        exe: The ffmpeg executable.
        video: The input video.
        final: The cache path.
        key: The cache key (remembered when generation fails).
        runner: Runs ffmpeg.
        timeout: Seconds before ffmpeg is abandoned.
    Returns:
        The cache path, or None on failure.
    """
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    partial = final.parent / f".{key}.{threading.get_ident()}.partial.jpg"
    ok = _run_ffmpeg(_ffmpeg_command(exe, video, partial), partial, runner, timeout)
    try:
        if ok:
            os.replace(partial, final)
            return final
    except OSError:
        ok = False
    finally:
        try:
            partial.unlink()
        except OSError:
            # The partial is already gone (moved into place or never written).
            pass
    _failed_keys.add(key)
    return None


def make_all_thumbs(report: ContentReport, thumbs_dir: Path, runner: Runner = subprocess.run) -> dict[str, int]:
    """Make a thumbnail for every video slide in a report.

    Parameters:
        report: The scanned content folder.
        thumbs_dir: The cache folder.
        runner: Runs ffmpeg.
    Returns:
        Counts: videos, cached (already there), made, failed, ffmpeg_missing (0 or 1).
    """
    counts = {"videos": 0, "cached": 0, "made": 0, "failed": 0, "ffmpeg_missing": 0}
    if shutil.which("ffmpeg") is None:
        counts["ffmpeg_missing"] = 1
    for slide in report.slides:
        if slide.kind != "video":
            continue
        counts["videos"] += 1
        video = report.content_dir / slide.file
        if cached_thumb(thumbs_dir, video) is not None:
            counts["cached"] += 1
        elif ensure_thumb(thumbs_dir, video, runner=runner) is not None:
            counts["made"] += 1
        else:
            counts["failed"] += 1
    return counts
