"""Module: tests.helpers
Purpose: Shared builders for the showcase tests: tiny PNGs, content folders, a temporary
    repository copy, a live server on a free port, and raw HTTP requests.
Inputs: Temporary folders created by the tests.
Outputs: Paths, servers and (status, headers, body) tuples.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import webbrowser
import zlib
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from vicar_showcase.server import ServerState, make_server  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
# The real functions, taken before any guard replaces them: make_video builds test media
# with them, and the guard hands an allowed call on to them.
_REAL_SUBPROCESS_FUNCTIONS = {name: getattr(subprocess, name)
                              for name in ("Popen", "run", "call", "check_call", "check_output")}
_REAL_OS_FUNCTIONS = {name: getattr(os, name)
                      for name in ("system", "execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp")
                      if hasattr(os, name)}
_REAL_WEBBROWSER_FUNCTIONS = {name: getattr(webbrowser, name) for name in ("open", "open_new", "open_new_tab")}
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
    # The real Popen, not subprocess.run: run() looks Popen up at call time and would meet the guard.
    process = _REAL_SUBPROCESS_FUNCTIONS["Popen"](
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1",
         "-pix_fmt", "yuv420p", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.communicate()
    return process.returncode == 0 and path.is_file()


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


class LaunchGuard:
    """Makes any program start or browser open fail the test unless it was allowed.

    A launcher test that forgets to stub one outside effect would otherwise start a real
    browser on the desktop. While the guard is installed every subprocess function, every
    webbrowser opener and the os-level exec and spawn calls raise AssertionError naming the
    call, except a start whose program is in `allowed_programs` or is an absolute path that
    does not exist (the OS refuses it, so nothing runs).
    """

    def __init__(self, allowed_programs: tuple[str, ...] = ()) -> None:
        """Remember what may start.

        Parameters:
            allowed_programs: Exact program names or paths that may really run (a test
                that deliberately runs this Python lists sys.executable here).
        """
        self.allowed_programs = tuple(allowed_programs)
        self.blocked: list[str] = []
        self.started: list[str] = []

    @staticmethod
    def _program(args: tuple, kwargs: dict) -> str:
        """Return the program a call would start ("" when it cannot be told)."""
        command = args[0] if args else kwargs.get("args", kwargs.get("path", kwargs.get("file", "")))
        if isinstance(command, (str, bytes, os.PathLike)):
            text = os.fsdecode(command)
            return text.split()[0] if text.split() else ""
        parts = list(command or [])
        return os.fsdecode(parts[0]) if parts else ""

    def _permitted(self, program: str) -> bool:
        """Return True when this program may really start."""
        if program in self.allowed_programs:
            return True
        return os.path.isabs(program) and not os.path.exists(program)

    def wrap(self, label: str, real):
        """Return a stand-in for `real` that raises unless the program is permitted.

        Parameters:
            label: Dotted name shown in the failure ("subprocess.Popen").
            real: The function to hand an allowed call to.
        """
        def guarded(*args, **kwargs):
            program = self._program(args, kwargs)
            if not self._permitted(program):
                self.blocked.append(f"{label}({program!r})")
                raise AssertionError(f"{label}({program!r}) was called without a stub: a test would have started "
                                     f"a real program or browser")
            self.started.append(f"{label}({program!r})")
            return real(*args, **kwargs)
        return guarded

    def wrap_opener(self, label: str):
        """Return a stand-in for a webbrowser opener that always raises."""
        def blocked(*args, **kwargs):
            self.blocked.append(f"{label}({args[0] if args else ''!r})")
            raise AssertionError(f"{label}({args[0] if args else ''!r}) was called without a stub: a test would "
                                 f"have opened a real browser")
        return blocked


def install_launch_guard(case: unittest.TestCase, allowed_programs: tuple[str, ...] = ()) -> LaunchGuard:
    """Install a LaunchGuard for one test case and remove it when the test ends.

    Parameters:
        case: The running test (the patches are undone through addCleanup).
        allowed_programs: Programs that may really run (see LaunchGuard).
    Returns:
        The guard, so a test can read what it blocked or let through.
    """
    from vicar_showcase import launch  # imported here: helpers must stay importable on its own

    guard = LaunchGuard(allowed_programs)
    patches = [mock.patch.object(launch.subprocess, name, guard.wrap(f"subprocess.{name}", real))
               for name, real in _REAL_SUBPROCESS_FUNCTIONS.items()]
    patches += [mock.patch.object(launch.os, name, guard.wrap(f"os.{name}", real))
                for name, real in _REAL_OS_FUNCTIONS.items()]
    patches += [mock.patch.object(launch.webbrowser, name, guard.wrap_opener(f"webbrowser.{name}"))
                for name in _REAL_WEBBROWSER_FUNCTIONS]
    for patch in patches:
        patch.start()
        case.addCleanup(patch.stop)
    return guard


class StrictRunner:
    """A launcher Runner stand-in whose every method fails the test unless a subclass overrides it.

    It does not inherit the real Runner on purpose: a method a test forgot to override must
    fail, never fall through to a real program.
    """

    def which(self, name):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.which({name!r}) was not stubbed")

    def exists(self, path):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.exists({path!r}) was not stubbed")

    def run(self, args, timeout=None):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.run({list(args)!r}) was not stubbed")

    def run_foreground(self, args):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.run_foreground({list(args)!r}) was not stubbed")

    def popen(self, args, **kwargs):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.popen({list(args)!r}) was not stubbed")

    def open_url(self, url):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.open_url({url!r}) was not stubbed")

    def sleep(self, seconds):
        """Fail: not overridden."""
        raise AssertionError(f"Runner.sleep({seconds!r}) was not stubbed")


class LaunchGuardMixin:
    """Put first in a test class's bases to install the LaunchGuard around every test.

    A class that deliberately runs this same Python (or the server) lists sys.executable in
    `allowed_programs`; every other class lets nothing start.
    """

    allowed_programs: tuple[str, ...] = ()

    def setUp(self) -> None:
        """Install the guard, then continue with the next setUp."""
        super().setUp()
        self.guard = install_launch_guard(self, self.allowed_programs)


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
