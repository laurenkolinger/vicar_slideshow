"""Module: vicar_showcase.server
Purpose: Serve the showcase pages, a live manifest, media (with Range support) and
    thumbnails on 127.0.0.1. Read-only: GET and HEAD only, no directory listings.
Inputs: A ServerState (content folder, repository, view, thumbnail cache).
Outputs: HTTP responses; a daemon thread that pre-makes video thumbnails.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from .content import VIDEO_EXTENSIONS, resolve_media_file, scan_content
from .thumbs import cached_thumb, default_thumbs_dir, ensure_thumb, make_all_thumbs

BIND_HOST = "127.0.0.1"
APP_NAME = "vicar-showcase"
MANIFEST_SCHEMA = 2
PAGES_SUBDIR = "slideshow"
STATIC_FILES = {
    "/screensaver.html": ("screensaver.html", "text/html; charset=utf-8"),
    "/slideshow.html": ("slideshow.html", "text/html; charset=utf-8"),
    "/vicar-icon.png": ("vicar-icon.png", "image/png"),
}
CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".mp4": "video/mp4", ".m4v": "video/x-m4v",
    ".mov": "video/quicktime", ".webm": "video/webm",
}
STREAM_CHUNK_BYTES = 256 * 1024
RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
CONNECTION_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


@dataclass
class ServerState:
    """What the request handlers need to know."""

    content_dir: Path
    repo_dir: Path
    view: str
    captions_file: str = "captions.csv"
    thumbs_dir: Optional[Path] = None

    def thumbs(self) -> Path:
        """Return the thumbnail cache folder."""
        return self.thumbs_dir or default_thumbs_dir(self.repo_dir)


def safe_name(raw: str) -> Optional[str]:
    """Decode a URL file name and reject unsafe ones.

    Parameters:
        raw: The URL segment after /media/ or /_thumbs/ (still percent-encoded).
    Returns:
        The decoded bare name, or None when it holds a slash, backslash, NUL, or
        starts with a dot or underscore, or is empty.
    Example:
        safe_name("%2e%2e%2fsettings.json") -> None
    """
    name = unquote(raw)
    if not name or any(ch in name for ch in "/\\\0") or name.startswith((".", "_")):
        return None
    return name


def parse_range(header: Optional[str], size: int) -> tuple[str, int, int]:
    """Interpret a Range header against a file size.

    Parameters:
        header: The Range header value, or None.
        size: File size in bytes.
    Returns:
        (kind, start, end) where kind is "whole" (send 200), "partial" (send 206 with
        bytes start..end inclusive) or "unsatisfiable" (send 416). Multiple ranges and
        malformed headers give "whole".
    Example:
        parse_range("bytes=-10", 100) -> ("partial", 90, 99)
    """
    match = RANGE_PATTERN.match(header.strip()) if header else None
    if not match or (not match.group(1) and not match.group(2)):
        return "whole", 0, max(size - 1, 0)
    first, last = match.group(1), match.group(2)
    if not first:  # suffix range: last n bytes
        count = int(last)
        if count == 0 or size == 0:
            return "unsatisfiable", 0, 0
        return "partial", max(size - count, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return "whole", 0, max(size - 1, 0)  # invalid spec is ignored
    if start >= size:
        return "unsatisfiable", 0, 0
    return "partial", start, min(end, size - 1)


def build_manifest(state: ServerState) -> dict:
    """Scan the content folder and return the manifest the pages read.

    Parameters:
        state: The server state.
    Returns:
        {"schema", "content_dir", "content_name", "error", "slides", "warnings"}.
    """
    data = scan_content(state.content_dir, state.captions_file).to_json()
    return {
        "schema": MANIFEST_SCHEMA, "content_dir": data["content_dir"],
        "content_name": data["content_name"], "error": data["error"],
        "slides": data["slides"], "warnings": data["warnings"],
    }


def find_video_by_stem(content_dir: Path, stem: str) -> Optional[Path]:
    """Find the first video file whose name without extension is `stem`.

    Parameters:
        content_dir: The content folder.
        stem: File name without extension.
    Returns:
        A safe path from resolve_media_file, or None.
    """
    try:
        names = sorted(os.listdir(content_dir))
    except OSError:
        return None
    for name in names:
        suffix = Path(name).suffix
        if suffix.lower() in VIDEO_EXTENSIONS and name[: len(name) - len(suffix)] == stem:
            found = resolve_media_file(content_dir, name)
            if found is not None:
                return found
    return None


def make_handler(state: ServerState) -> type[BaseHTTPRequestHandler]:
    """Build the request handler class bound to a state.

    Parameters:
        state: The server state.
    Returns:
        A BaseHTTPRequestHandler subclass.
    """

    class ShowcaseHandler(BaseHTTPRequestHandler):
        """Read-only handler for the showcase routes."""

        server_version = "VicarShowcase"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Stay quiet: the launcher terminal is not a request log."""

        def handle(self) -> None:
            """Handle a connection, ignoring browsers that cancel (video seeks do this)."""
            try:
                super().handle()
            except CONNECTION_ERRORS:
                # The browser closed the connection mid-response; nothing to report.
                pass

        def do_GET(self) -> None:  # noqa: N802
            """Answer a GET."""
            self._route(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            """Answer a HEAD (headers only)."""
            self._route(send_body=False)

        def _method_not_allowed(self) -> None:
            """Refuse anything but GET and HEAD."""
            self._send_bytes(HTTPStatus.METHOD_NOT_ALLOWED, b"GET and HEAD only\n", "text/plain; charset=utf-8",
                             True, {"Allow": "GET, HEAD"})

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed  # noqa: N815

        def _route(self, send_body: bool) -> None:
            """Pick the handler for the request path.

            Parameters:
                send_body: False for HEAD.
            """
            path = urlsplit(self.path).path
            if path == "/":
                self._redirect(f"/{state.view}.html")
            elif path in STATIC_FILES:
                self._send_static(path, send_body)
            elif path == "/_manifest.json":
                self._send_json(build_manifest(state), send_body)
            elif path == "/_showcase_info.json":
                self._send_json(self._info(), send_body)
            elif path.startswith("/media/"):
                self._send_media(path[len("/media/"):], send_body)
            elif path.startswith("/_thumbs/"):
                self._send_thumb(path[len("/_thumbs/"):], send_body)
            else:
                self._not_found()

        def _info(self) -> dict:
            """Return what a launcher needs to recognise this server."""
            return {"app": APP_NAME, "view": state.view, "content_dir": str(state.content_dir),
                    "repo_dir": str(state.repo_dir), "pid": os.getpid()}

        def _not_found(self) -> None:
            """Send a plain 404 (never a listing)."""
            self._send_bytes(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8", True)

        def _redirect(self, location: str) -> None:
            """Send a 302 to a page."""
            self._send_bytes(HTTPStatus.FOUND, b"", "text/plain; charset=utf-8", False, {"Location": location})

        def _send_json(self, data: dict, send_body: bool) -> None:
            """Send JSON that must not be cached."""
            body = json.dumps(data).encode("utf-8")
            self._send_bytes(HTTPStatus.OK, body, "application/json; charset=utf-8", send_body,
                             {"Cache-Control": "no-store"})

        def _send_bytes(self, status: int, body: bytes, ctype: str, send_body: bool,
                        extra: Optional[dict] = None) -> None:
            """Send a small in-memory response.

            Parameters:
                status: HTTP status.
                body: Response bytes.
                ctype: Content-Type.
                send_body: False for HEAD.
                extra: Additional headers.
            """
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if send_body and body and self.command != "HEAD":
                self.wfile.write(body)

        def _send_static(self, route: str, send_body: bool) -> None:
            """Send one of the fixed page files from slideshow/."""
            filename, ctype = STATIC_FILES[route]
            path = state.repo_dir / PAGES_SUBDIR / filename
            if not path.is_file():
                self._not_found()
                return
            self._send_file(path, ctype, send_body, {"Cache-Control": "no-cache"})

        def _send_media(self, raw: str, send_body: bool) -> None:
            """Send a slide file, honouring one Range."""
            name = safe_name(raw)
            path = resolve_media_file(state.content_dir, name) if name else None
            if path is None:
                self._not_found()
                return
            ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self._send_file(path, ctype, send_body, {}, self.headers.get("Range"))

        def _send_thumb(self, raw: str, send_body: bool) -> None:
            """Send (making it first if needed) a video's thumbnail."""
            name = safe_name(raw)
            if not name or not name.endswith(".jpg"):
                self._not_found()
                return
            video = find_video_by_stem(state.content_dir, name[: -len(".jpg")])
            thumb = None
            if video is not None:
                thumb = cached_thumb(state.thumbs(), video) or ensure_thumb(state.thumbs(), video)
            if thumb is None:
                self._not_found()
                return
            self._send_file(thumb, "image/jpeg", send_body, {})

        def _send_file(self, path: Path, ctype: str, send_body: bool, extra: dict,
                       range_header: Optional[str] = None) -> None:
            """Stream a file as 200, 206 or 416.

            Parameters:
                path: File to send.
                ctype: Content-Type.
                send_body: False for HEAD.
                extra: Additional headers.
                range_header: The request's Range header, if any.
            """
            try:
                size = path.stat().st_size
                handle = open(path, "rb")
            except OSError:
                self._not_found()
                return
            with handle:
                kind, start, end = parse_range(range_header, size)
                if kind == "unsatisfiable":
                    self._send_bytes(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", ctype, False,
                                     {"Content-Range": f"bytes */{size}"})
                    return
                status = HTTPStatus.PARTIAL_CONTENT if kind == "partial" else HTTPStatus.OK
                length = end - start + 1 if size else 0
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if kind == "partial":
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                if send_body:
                    self._stream(handle, start, length)

        def _stream(self, handle, start: int, length: int) -> None:
            """Write `length` bytes of an open file from `start`."""
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    return ShowcaseHandler


class QuietServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not print tracebacks for cancelled requests."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:  # noqa: D401
        """Ignore connection errors, report anything else to stderr."""
        import sys
        if isinstance(sys.exc_info()[1], CONNECTION_ERRORS):
            return
        super().handle_error(request, client_address)


def make_server(state: ServerState, port: int) -> QuietServer:
    """Create the server bound to 127.0.0.1.

    Parameters:
        state: The server state.
        port: TCP port; 0 picks a free one.
    Returns:
        A server that has not started serving.
    Raises:
        OSError: The port is in use or not allowed.
    """
    return QuietServer((BIND_HOST, port), make_handler(state))


def start_thumb_worker(state: ServerState) -> threading.Thread:
    """Pre-make video thumbnails in the background.

    Parameters:
        state: The server state.
    Returns:
        The started daemon thread.
    """
    def work() -> None:
        make_all_thumbs(scan_content(state.content_dir, state.captions_file), state.thumbs())

    thread = threading.Thread(target=work, name="showcase-thumbs", daemon=True)
    thread.start()
    return thread


def run_server(state: ServerState, port: int) -> None:
    """Serve until interrupted, pre-making thumbnails in the background.

    Parameters:
        state: The server state.
        port: TCP port.
    Raises:
        OSError: The port cannot be bound.
    """
    server = make_server(state, port)
    start_thumb_worker(state)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass  # Ctrl+C is the normal way to stop a foreground server.
    finally:
        server.server_close()
