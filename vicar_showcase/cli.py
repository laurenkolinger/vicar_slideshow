"""Module: vicar_showcase.cli
Purpose: The command line: screensaver, slideshow, serve, check, copy-content, make-thumbs.
Inputs: Command-line arguments, settings files, the environment.
Outputs: Text or JSON on stdout, errors on stderr as "showcase: error: <message>",
    and an exit code: 0 ok, 1 settings or usage error, 2 content folder missing,
    3 copy refused (or a file appeared in the destination during the copy), 4 copy failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .common import BYTES_PER_MB, EXIT_FAILED, EXIT_NO_CONTENT, EXIT_OK, EXIT_REFUSED, EXIT_USAGE
from .content import MEDIA_EXTENSIONS, ContentReport, scan_content
from .copy_content import CopyError, describe_plan, execute_plan, plan_copy
from .launch import run_view
from .server import ServerState, run_server
from .settings import VIEW_PORTS, VIEWS, Settings, SettingsError, load_settings
from .thumbs import cached_thumb, default_thumbs_dir, make_all_thumbs

REPO_DIR = Path(__file__).resolve().parent.parent
MIN_PORT, MAX_PORT = 1, 65535


class ShowcaseParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors use the tool's message style and exit 1."""

    def error(self, message: str):  # type: ignore[override]
        """Print `showcase: error: <message>` and exit with the usage code."""
        print(f"showcase: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _port(text: str) -> int:
    """Parse a TCP port argument.

    Parameters:
        text: The argument text.
    Returns:
        The port number.
    Raises:
        argparse.ArgumentTypeError: Not an integer from 1 to 65535.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"port must be a whole number, got {text!r}") from None
    if not MIN_PORT <= value <= MAX_PORT:
        raise argparse.ArgumentTypeError(f"port must be between {MIN_PORT} and {MAX_PORT}, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser with all subcommands.
    """
    parser = ShowcaseParser(prog="showcase.py", description="VICAR Lab showcase: screensaver and slideshow.")
    parser.add_argument("--version", action="version", version=f"showcase {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=ShowcaseParser)
    common = ShowcaseParser(add_help=False)
    common.add_argument("--content", metavar="DIR", help="Content folder; overrides every setting.")
    for view in VIEWS:
        p = sub.add_parser(view, parents=[common], help=f"Open the {view}.")
        p.add_argument("--port", type=_port, default=VIEW_PORTS[view], help="Server port (default %(default)s).")
    serve = sub.add_parser("serve", parents=[common], help="Run the server only (VICARIUS uses this).")
    serve.add_argument("--view", required=True, choices=VIEWS, help="Which page `/` redirects to.")
    serve.add_argument("--port", type=_port, default=None, help="Server port (default: the view's port).")
    check = sub.add_parser("check", parents=[common], help="Report what the content folder shows.")
    check.add_argument("--json", action="store_true", help="Print the report as JSON.")
    copy = sub.add_parser("copy-content", parents=[common], help="Copy slides and captions into a folder.")
    copy.add_argument("--from", dest="source", required=True, metavar="DIR", help="Folder to copy from.")
    copy.add_argument("--to", metavar="DIR", help="Existing folder to copy into (default: the content setting).")
    copy.add_argument("--captions", metavar="FILE", help="Captions file (default: captions file in --from).")
    copy.add_argument("--replace", action="store_true", help="Replace differing files; old ones go to _superseded/.")
    copy.add_argument("--dry-run", action="store_true", help="Print the plan and write nothing.")
    sub.add_parser("make-thumbs", parents=[common], help="Make video thumbnails now.")
    return parser


def _thumb_paths(report: ContentReport, thumbs_dir: Path) -> dict[str, Optional[str]]:
    """Map each video file to the path of its cached thumbnail, or None."""
    paths: dict[str, Optional[str]] = {}
    for slide in report.slides:
        if slide.kind == "video":
            found = cached_thumb(thumbs_dir, report.content_dir / slide.file)
            paths[slide.file] = str(found) if found else None
    return paths


def check_json(settings: Settings, report: ContentReport) -> dict:
    """Build the `check --json` document.

    Parameters:
        settings: Resolved settings.
        report: The scanned folder.
    Returns:
        The report JSON plus repo_dir, content_dir_source, thumbs_dir, supported_extensions
        and a thumb_path per slide.
    """
    thumbs_dir = default_thumbs_dir(settings.repo_dir)
    data = report.to_json()
    paths = _thumb_paths(report, thumbs_dir)
    for slide in data["slides"]:
        slide["thumb_path"] = paths.get(slide["file"])
    data.update({
        "repo_dir": str(settings.repo_dir), "content_dir_source": settings.content_dir_source,
        "thumbs_dir": str(thumbs_dir), "supported_extensions": sorted(MEDIA_EXTENSIONS),
    })
    return data


def check_text(settings: Settings, report: ContentReport) -> str:
    """Build the human `check` output.

    Parameters:
        settings: Resolved settings.
        report: The scanned folder.
    Returns:
        Multi-line text: folder and source, counts, slides, not shown, warnings.
    """
    lines = [f"Content folder: {report.content_dir}", f"Chosen by: {settings.content_dir_source}"]
    if report.error:
        return "\n".join(lines + [f"Problem: {report.error}"])
    videos = sum(1 for s in report.slides if s.kind == "video")
    megabytes = sum(s.size_bytes for s in report.slides) / BYTES_PER_MB
    lines += [f"Videos: {videos}", f"Images: {len(report.slides) - videos}",
              f"Slides: {len(report.slides)} ({megabytes:.1f} MB)", ""]
    lines.append("Slides:")
    for slide in report.slides:
        number = f"{slide.number:>3}" if slide.number else "  -"
        caption = "caption" if slide.caption else "NO CAPTION"
        lines.append(f"  {number}  {slide.title}  [{slide.kind}, {caption}]")
    if not report.slides:
        lines.append("  none")
    lines += ["", "Not shown:"] + ([f"  {s.file}: {s.reason}" for s in report.skipped] or ["  none"])
    lines += ["", "Warnings:"] + ([f"  {w}" for w in report.warnings] or ["  none"])
    return "\n".join(lines)


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    """Run `check`: print the report; exit 2 when the folder is missing or unreadable."""
    report = scan_content(settings.content_dir, settings.captions_file)
    if args.json:
        print(json.dumps(check_json(settings, report), indent=2))
    else:
        print(check_text(settings, report))
    return EXIT_NO_CONTENT if (not report.exists or report.error) else EXIT_OK


def _require_folder(settings: Settings) -> Optional[ContentReport]:
    """Scan the content folder; print the problem and return None when it is unusable."""
    report = scan_content(settings.content_dir, settings.captions_file)
    if report.error:
        print(f"showcase: error: {report.error}. Set content_dir in settings.local.json "
              f"or pass --content.", file=sys.stderr)
        return None
    return report


def cmd_view(settings: Settings, args: argparse.Namespace) -> int:
    """Run `screensaver` or `slideshow`: check the folder, then open the view."""
    if _require_folder(settings) is None:
        return EXIT_NO_CONTENT
    return run_view(settings, args.command, args.port)


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    """Run `serve`: serve until stopped."""
    port = args.port if args.port is not None else VIEW_PORTS[args.view]
    state = ServerState(settings.content_dir, settings.repo_dir, args.view, settings.captions_file)
    try:
        run_server(state, port)
    except OSError as exc:
        print(f"showcase: error: cannot serve on port {port}: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def cmd_make_thumbs(settings: Settings, args: argparse.Namespace) -> int:
    """Run `make-thumbs`: make every missing video thumbnail."""
    report = _require_folder(settings)
    if report is None:
        return EXIT_NO_CONTENT
    counts = make_all_thumbs(report, default_thumbs_dir(settings.repo_dir))
    if counts["ffmpeg_missing"]:
        print("ffmpeg is not installed; no thumbnails were made. The slideshow grid shows an empty cell for each video.")
    print(f"Videos: {counts['videos']}; already cached: {counts['cached']}; "
          f"made: {counts['made']}; failed: {counts['failed']}")
    print("Step 1 complete: thumbnails")
    return EXIT_OK


def _count_slides(folder: Path, captions_file: str) -> int:
    """Return how many slides a folder shows."""
    return len(scan_content(folder, captions_file).slides)


def cmd_copy(settings: Settings, args: argparse.Namespace) -> int:
    """Run `copy-content`: plan, then copy, verify, log, and compare slide counts."""
    source = Path(args.source).expanduser()
    dest = Path(args.to).expanduser() if args.to else settings.content_dir
    try:
        plan = plan_copy(source, dest, Path(args.captions).expanduser() if args.captions else None,
                         settings.captions_file, args.replace)
        print(f"Copy from {source} to {dest}")
        print("\n".join(describe_plan(plan)))
        print("Step 1 complete: plan")
        if args.dry_run:
            print("Dry run: nothing was written.")
            return EXIT_OK
        result = execute_plan(plan)
    except CopyError as exc:
        print(f"showcase: error: {exc}", file=sys.stderr)
        return exc.exit_code
    if result.failed or result.conflicts:
        print(f"Step 2 incomplete: {result.failed} file(s) failed, {result.conflicts} left alone because "
              f"the name appeared in the destination during the copy")
    else:
        print("Step 2 complete: files copied and verified")
    print(f"Copied: {result.copied}; replaced: {result.replaced}; already there: {result.present}; "
          f"not copied: {result.skipped}; failed: {result.failed}; conflicts: {result.conflicts}")
    print(f"Slides shown: source {_count_slides(source, settings.captions_file)}, "
          f"destination {_count_slides(dest, settings.captions_file)}")
    print("Step 3 complete: destination re-scanned")
    for failure in result.failures:
        print(f"showcase: error: {failure}", file=sys.stderr)
    if result.failed:
        return EXIT_FAILED
    return EXIT_REFUSED if result.conflicts else EXIT_OK


COMMANDS = {
    "screensaver": cmd_view, "slideshow": cmd_view, "serve": cmd_serve, "check": cmd_check,
    "copy-content": cmd_copy, "make-thumbs": cmd_make_thumbs,
}


def main(argv: Optional[Sequence[str]] = None, repo_dir: Path = REPO_DIR) -> int:
    """Run the command line.

    Parameters:
        argv: Arguments after the program name (default sys.argv[1:]).
        repo_dir: The repository folder (tests pass a temporary one).
    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(repo_dir, content_override=args.content)
    except SettingsError as exc:
        print(f"showcase: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return COMMANDS[args.command](settings, args)
