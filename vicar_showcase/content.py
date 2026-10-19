"""Module: vicar_showcase.content
Purpose: Read a content folder: decide which files are slides, in what order,
    read the captions spreadsheet, and report everything that is not shown and why.
Inputs: The content folder path and the captions file name.
Outputs: A ContentReport (slides, skipped files with reasons, warnings, error)
    and a safe file lookup for the web server.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".m4v"})
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

CAPTIONS_MAX_BYTES = 5 * 1024 * 1024
CONFLICT_MARKER = "conflicted copy"
IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini", "icon\r"})
CAPTION_FIELDS = ("caption", "credit", "chapter")
NUMBERED_NAME = re.compile(r"^(\d+)[_-](.+)$")
# Extensions a contributor is likely to try, with the fix to suggest.
IMAGE_LIKE = frozenset({".heic", ".heif", ".tif", ".tiff", ".bmp", ".raw", ".cr2", ".dng"})
VIDEO_LIKE = frozenset({".avi", ".mkv", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp"})
MAX_LISTED = 12


@dataclass(frozen=True)
class Slide:
    """One file the showcase shows."""

    file: str
    kind: str
    number: Optional[int]
    title: str
    caption: str
    credit: str
    chapter: str
    size_bytes: int

    def to_json(self) -> dict:
        """Return the slide as the JSON the pages read (media and thumbnail URLs included)."""
        url = "media/" + quote(self.file, safe="")
        thumb = url
        if self.kind == "video":
            thumb = "_thumbs/" + quote(Path(self.file).stem, safe="") + ".jpg"
        return {
            "file": self.file, "url": url, "kind": self.kind, "number": self.number,
            "title": self.title, "caption": self.caption, "credit": self.credit,
            "chapter": self.chapter, "size_bytes": self.size_bytes, "thumb": thumb,
        }


@dataclass(frozen=True)
class Skipped:
    """A file the showcase does not show, with a plain reason."""

    file: str
    reason: str


@dataclass
class ContentReport:
    """Everything known about a content folder."""

    content_dir: Path
    captions_path: Path
    exists: bool = False
    slides: list[Slide] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_json(self) -> dict:
        """Return the report as plain JSON-ready data."""
        return {
            "content_dir": str(self.content_dir),
            "content_name": self.content_dir.name,
            "captions_path": str(self.captions_path),
            "exists": self.exists,
            "error": self.error,
            "slides": [s.to_json() for s in self.slides],
            "skipped": [{"file": s.file, "reason": s.reason} for s in self.skipped],
            "warnings": list(self.warnings),
        }


def parse_media_name(name: str) -> tuple[Optional[int], str]:
    """Split a file name into its number prefix and its title.

    Parameters:
        name: A file name such as "05_reef-survey.mp4".
    Returns:
        (number or None, title). The title is the name without the number prefix
        and without the extension.
    Example:
        parse_media_name("05-reef.mp4") -> (5, "reef"); parse_media_name("x.jpg") -> (None, "x")
    """
    stem = name[: len(name) - len(Path(name).suffix)] if Path(name).suffix else name
    match = NUMBERED_NAME.match(stem)
    if match:
        return int(match.group(1)), match.group(2)
    return None, stem


def _clean_value(text: object) -> str:
    """Strip a caption cell: line breaks and tabs become a space, control characters go.

    Parameters:
        text: The raw cell value (None becomes an empty string).
    Returns:
        The cleaned text.
    """
    if text is None:
        return ""
    value = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Cc")
    return value.strip()


def _decode_captions(raw: bytes, warnings: list[str], name: str) -> str:
    """Decode the captions bytes as UTF-8 (BOM allowed), else Windows-1252 with a warning.

    Parameters:
        raw: The file bytes.
        warnings: Appended to when the fallback is used.
        name: The file name for the warning.
    Returns:
        The decoded text.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.append(f"{name} is not UTF-8; it was read as Windows-1252. Save it as CSV UTF-8.")
        return raw.decode("cp1252", errors="replace")


def _parse_rows(text: str, warnings: list[str], name: str) -> list[dict[str, str]]:
    """Parse captions text into rows keyed by lower-case header.

    Parameters:
        text: Decoded CSV text.
        warnings: Appended to for parse problems.
        name: The file name for warnings.
    Returns:
        One dict per data row; empty when the file has no title column.
    """
    reader = csv.reader(io.StringIO(text.replace("\x00", ""), newline=""))
    try:
        header = next(reader, None)
        if header is None:
            return []
        keys = [_clean_value(h).lower() for h in header]
        if "title" not in keys:
            warnings.append(f"{name} has no title column; no captions were read.")
            return []
        return [
            {k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)}
            for row in reader if any(cell.strip() for cell in row)
        ]
    except csv.Error as exc:
        warnings.append(f"{name} could not be read as a spreadsheet ({exc}); no captions were read.")
        return []


def load_captions(path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Read the captions spreadsheet.

    Parameters:
        path: The captions.csv path; a missing file gives no captions and no warning
            (scan_content reports a missing file).
    Returns:
        (captions keyed by title, warnings). The first row wins for a repeated title.
    """
    warnings: list[str] = []
    path = Path(path)
    if not path.is_file():
        return {}, warnings
    try:
        if path.stat().st_size > CAPTIONS_MAX_BYTES:
            warnings.append(f"{path.name} is over 5 MB and was ignored.")
            return {}, warnings
        raw = path.read_bytes()
    except OSError as exc:
        warnings.append(f"{path.name} could not be read ({exc.strerror or exc}); no captions were read.")
        return {}, warnings
    rows = _parse_rows(_decode_captions(raw, warnings, path.name), warnings, path.name)
    captions: dict[str, dict[str, str]] = {}
    repeated: list[str] = []
    for row in rows:
        title = _norm(_clean_value(row.get("title")))
        if not title:
            continue
        if title in captions:
            repeated.append(title)
            continue
        captions[title] = {k: _clean_value(row.get(k)) for k in CAPTION_FIELDS}
    if repeated:
        warnings.append(
            f"{path.name} has duplicate titles ({_listing(repeated)}); the first row of each is used."
        )
    return captions, warnings


def _norm(title: str) -> str:
    """Normalise a title to Unicode NFC so macOS (NFD) file names match typed captions."""
    return unicodedata.normalize("NFC", title)


def _listing(names: list[str]) -> str:
    """Join names for a warning, cutting a long list.

    Parameters:
        names: The names to list.
    Returns:
        A comma-separated string with "and N more" when long.
    """
    shown = ", ".join(names[:MAX_LISTED])
    extra = len(names) - MAX_LISTED
    return f"{shown} and {extra} more" if extra > 0 else shown


def _is_ignored(name: str) -> bool:
    """Return True for names that are never reported (hidden, underscore, system files)."""
    return name.startswith((".", "_")) or name.lower() in IGNORED_NAMES


def _unsupported_reason(name: str, suffix: str, captions_file: str) -> str:
    """Explain why a file type is not shown, with the fix when one is known.

    Parameters:
        name: The file name.
        suffix: Its lower-case extension (may be empty).
        captions_file: The one spreadsheet that is used.
    Returns:
        A short plain sentence.
    """
    if suffix == ".csv":
        return f"spreadsheet not used; captions come only from {captions_file}"
    if not suffix:
        return "file has no extension; rename it with .jpg, .png or .mp4"
    if suffix in IMAGE_LIKE:
        return f"file type {suffix} is not shown; save it as .jpg or .png"
    if suffix in VIDEO_LIKE:
        return f"file type {suffix} is not shown; export it as .mp4"
    return f"file type {suffix} is not shown; use .jpg, .png, .gif, .webp, .avif, .mp4, .mov, .webm or .m4v"


def _classify(folder: Path, real_root: str, name: str, captions_file: str) -> tuple[Optional[Path], Optional[str]]:
    """Decide whether a folder entry is a slide.

    Parameters:
        folder: The content folder.
        real_root: Its real path.
        name: The entry name.
        captions_file: The one spreadsheet that is used.
    Returns:
        (path, None) when it is a slide candidate, or (None, reason) when skipped.
    """
    path = folder / name
    try:
        if path.is_symlink():
            target = os.path.realpath(path)
            if not os.path.exists(target):
                return None, "link is broken"
            if os.path.commonpath([real_root, target]) != real_root:
                return None, "link points outside the content folder"
        if path.is_dir():
            return None, "subfolder; only files at the top level are shown"
        if not path.is_file():
            return None, "not a regular file"
        if CONFLICT_MARKER in name.lower():
            return None, "Dropbox conflicted copy; keep one version and remove the other"
        suffix = Path(name).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            return None, _unsupported_reason(name, suffix, captions_file)
        if path.stat().st_size == 0:
            return None, "empty file; it may still be syncing"
    except OSError as exc:
        return None, f"cannot be read ({exc.strerror or exc})"
    return path, None


def _slide_order(slide: Slide) -> tuple:
    """Sort key: numbered slides by number, then unnumbered alphabetically."""
    if slide.number is None:
        return (1, 0, slide.file.lower(), slide.file)
    return (0, slide.number, slide.file.lower(), slide.file)


def _build_slide(path: Path, captions: dict[str, dict[str, str]], lowered: dict[str, str]) -> Slide:
    """Make a Slide from a file and the captions.

    Parameters:
        path: The media file.
        captions: Captions keyed by exact title.
        lowered: Lower-case title to exact title (first wins).
    Returns:
        The Slide, with empty caption fields when no row matches.
    """
    number, title = parse_media_name(path.name)
    key = _norm(title)
    row = captions.get(key) or captions.get(lowered.get(key.lower(), ""), {})
    kind = "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image"
    return Slide(
        file=path.name, kind=kind, number=number, title=title,
        caption=row.get("caption", ""), credit=row.get("credit", ""),
        chapter=row.get("chapter", ""), size_bytes=path.stat().st_size,
    )


def _cross_check(report: ContentReport, captions: dict[str, dict[str, str]]) -> None:
    """Add warnings that compare slides with captions.

    Parameters:
        report: The report with slides filled in; warnings are appended.
        captions: Captions keyed by title.
    """
    name = report.captions_path.name
    numbers: dict[int, list[str]] = {}
    for slide in report.slides:
        if slide.number is not None:
            numbers.setdefault(slide.number, []).append(slide.file)
    for number, files in sorted(numbers.items()):
        if len(files) > 1:
            report.warnings.append(f"two slides share the number {number}: {', '.join(files)}")
    if not captions:
        return
    titles = {_norm(s.title).lower() for s in report.slides}
    captioned = {t.lower() for t in captions}
    bare = [s.file for s in report.slides if _norm(s.title).lower() not in captioned]
    orphans = [t for t in captions if t.lower() not in titles]
    if bare:
        report.warnings.append(f"slides with no row in {name}: {_listing(bare)}")
    if orphans:
        report.warnings.append(f"rows in {name} that name no slide in the folder: {_listing(orphans)}")


def scan_content(content_dir: Path, captions_file: str = "captions.csv") -> ContentReport:
    """Read a content folder.

    Parameters:
        content_dir: The folder holding slides and the captions file.
        captions_file: Bare name of the captions spreadsheet.
    Returns:
        A ContentReport. A missing or unreadable folder sets `error`; this never raises.
    """
    folder = Path(content_dir)
    report = ContentReport(content_dir=folder, captions_path=folder / captions_file)
    if not folder.exists():
        report.error = f"content folder not found: {folder}"
        return report
    if not folder.is_dir():
        report.error = f"content path is not a folder: {folder}"
        return report
    report.exists = True
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        report.error = f"cannot read the content folder {folder}: {exc.strerror or exc}"
        return report
    _scan_entries(report, folder, names, captions_file)
    return report


def _scan_entries(report: ContentReport, folder: Path, names: list[str], captions_file: str) -> None:
    """Classify every entry, then attach captions and cross-check.

    Parameters:
        report: Filled in place.
        folder: The content folder.
        names: Entry names in the folder.
        captions_file: Bare name of the captions spreadsheet.
    """
    real_root = os.path.realpath(folder)
    candidates: list[Path] = []
    for name in names:
        if _is_ignored(name) or name == captions_file:
            continue
        if CONFLICT_MARKER in name.lower() and name.lower().endswith(".csv") and \
                name.lower().startswith(Path(captions_file).stem.lower()):
            report.warnings.append(f"a conflicted copy of {captions_file} is present: {name}")
        path, reason = _classify(folder, real_root, name, captions_file)
        if path is None:
            report.skipped.append(Skipped(name, reason or "not shown"))
        else:
            candidates.append(path)
    if not report.captions_path.is_file():
        report.warnings.append(f"no captions file: {captions_file} was not found in the folder")
    captions, warnings = load_captions(report.captions_path)
    report.warnings.extend(warnings)
    lowered: dict[str, str] = {}
    for title in captions:
        lowered.setdefault(title.lower(), title)
    for path in candidates:
        try:
            report.slides.append(_build_slide(path, captions, lowered))
        except OSError as exc:
            report.skipped.append(Skipped(path.name, f"cannot be read ({exc.strerror or exc})"))
    report.slides.sort(key=_slide_order)
    _cross_check(report, captions)


def resolve_media_file(content_dir: Path, name: str) -> Optional[Path]:
    """Find a media file by bare name, refusing anything outside the folder.

    Parameters:
        content_dir: The content folder.
        name: A bare file name (no separators, not hidden, supported extension).
    Returns:
        The file path, or None when the name is unsafe, unsupported, missing, not a
        regular file, or resolves outside the folder's real path.
    Example:
        resolve_media_file(folder, "../secret.jpg") -> None
    """
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    if any(ch in name for ch in "/\\\0") or name.startswith((".", "_")):
        return None
    if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
        return None
    try:
        path = Path(content_dir) / name
        real = os.path.realpath(path)
        root = os.path.realpath(content_dir)
        if os.path.commonpath([root, real]) != root or not os.path.isfile(real):
            return None
    except (OSError, ValueError):
        return None
    return path
