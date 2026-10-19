"""Module: tests.helpers
Purpose: Shared builders for the showcase tests: tiny PNGs, content folders, a temporary
    repository copy, a live server on a free port, and raw HTTP requests.
Inputs: Temporary folders created by the tests.
Outputs: Paths, servers and (status, headers, body) tuples.
"""

from __future__ import annotations

import http.client
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from vicar_showcase.server import ServerState, make_server  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
CAPTIONS_HEADER = "title,caption,credit,chapter\n"


def make_png(width: int = 2, height: int = 2) -> bytes:
    """Return the bytes of a valid solid-colour PNG.

    Parameters:
        width: Pixels across.
        height: Pixels down.
    Returns:
        PNG file bytes.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x80\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_video(path: Path) -> bool:
    """Make a 1 second test video with ffmpeg; return False when ffmpeg is missing."""
    if not FFMPEG:
        return False
    result = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1",
         "-pix_fmt", "yuv420p", str(path)], capture_output=True)
    return result.returncode == 0 and path.is_file()


def make_content(folder: Path, captions: str | None = None) -> Path:
    """Fill a folder with three PNG slides and a captions file.

    Parameters:
        folder: Existing empty folder.
        captions: CSV text (default: rows for the three slides).
    Returns:
        The folder.
    """
    for name in ("01_alpha.png", "02-beta.png", "10_gamma.png"):
        (folder / name).write_bytes(make_png())
    text = captions if captions is not None else (
        CAPTIONS_HEADER + "alpha,First caption,Credit A,Intro\nbeta,Second,Credit B,Reefs\ngamma,Third,,\n")
    (folder / "captions.csv").write_text(text, encoding="utf-8")
    return folder


def make_repo(root: Path, content_dir: Path) -> Path:
    """Make a temporary repository copy that runs the real code against `content_dir`.

    Parameters:
        root: Existing empty folder.
        content_dir: Folder to put in settings.json.
    Returns:
        The repository folder.
    """
    repo = root / "repo"
    (repo / "slideshow").mkdir(parents=True)
    shutil.copytree(REPO / "vicar_showcase", repo / "vicar_showcase",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO / "showcase.py", repo / "showcase.py")
    for name in ("screensaver.html", "slideshow.html", "vicar-icon.png"):
        source = REPO / "slideshow" / name
        if source.is_file():
            shutil.copy(source, repo / "slideshow" / name)
    (repo / "settings.json").write_text(json.dumps({"content_dir": str(content_dir)}), encoding="utf-8")
    return repo


def request(port: int, method: str, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """Send one raw HTTP request (the path is sent exactly as given).

    Parameters:
        port: Server port on 127.0.0.1.
        method: GET or HEAD (or others for refusal tests).
        path: Request target, not normalised.
        headers: Extra request headers.
    Returns:
        (status, lower-cased response headers, body).
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


class TempDirTestCase(unittest.TestCase):
    """TestCase with a temporary folder (self.tmp) removed after each test."""

    def setUp(self) -> None:
        """Create the temporary folder."""
        self._tmp = tempfile.TemporaryDirectory(prefix="showcase-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class LiveServerTestCase(TempDirTestCase):
    """TestCase with content (self.content), a repo (self.repo) and a live server (self.port)."""

    view = "slideshow"

    def setUp(self) -> None:
        """Start a server on a free port against a fresh content folder."""
        super().setUp()
        self.content = self.tmp / "content"
        self.content.mkdir()
        make_content(self.content)
        (self.tmp / "secret.txt").write_text("secret")
        self.repo = make_repo(self.tmp, self.content)
        self.state = ServerState(self.content, self.repo, self.view, thumbs_dir=self.tmp / "thumbs")
        self.server = make_server(self.state, 0)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        """Stop the server."""
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    """Return a TCP port on 127.0.0.1 that is free right now."""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
