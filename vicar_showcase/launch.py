"""Module: vicar_showcase.launch
Purpose: Open the screensaver (kiosk browser window) or the slideshow (default browser)
    against a showcase server, starting the server as its own child process and stopping
    it when the browser closes. Keeps the Linux extras: the dock toggle and the
    fullscreen helper.
Inputs: Resolved Settings, a view name, a port.
Outputs: A running server process for the length of the session; an exit code.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional, Sequence

from .server import APP_NAME
from .settings import SOURCE_ENV, SOURCE_FLAG, Settings

HOST = "127.0.0.1"
PROBE_TIMEOUT_SECONDS = 1.0
SERVER_WAIT_SECONDS = 3.0
SERVER_POLL_SECONDS = 0.1
CHILD_STOP_SECONDS = 5
WINDOW_TITLE = "VICAR Lab"
WINDOW_POLLS = 40
WINDOW_POLL_SECONDS = 0.25
DOCK_SCHEMA = "org.gnome.shell.extensions.dash-to-dock"
DOCK_KEY = "dock-fixed"
PROFILE_SUBDIR = Path(".cache") / "vicar-screensaver-profile"
LINUX_CHROME_NAMES = ("google-chrome", "chromium", "chromium-browser")
MAC_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
WINDOWS_CHROME_PARTS = (r"Google\Chrome\Application\chrome.exe", r"Chromium\Application\chrome.exe")
WINDOWS_ENV_ROOTS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
CHROME_KIOSK_FLAGS = (
    "--kiosk", "--noerrdialogs", "--disable-infobars", "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI", "--no-first-run", "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required", "--disable-pinch",
    "--overscroll-history-navigation=0",
)


class LaunchError(RuntimeError):
    """The showcase cannot be started; the message says what to do."""


class Runner:
    """Every outside effect (programs, browser, clock) in one place so tests can replace it."""

    def which(self, name: str) -> Optional[str]:
        """Return the full path of a program on PATH, or None."""
        import shutil
        return shutil.which(name)

    def exists(self, path: str) -> bool:
        """Return True when a file exists."""
        return os.path.isfile(path)

    def run(self, args: Sequence[str], timeout: Optional[float] = None) -> Optional[subprocess.CompletedProcess]:
        """Run a program to completion, capturing text output.

        Returns:
            The CompletedProcess, or None when it could not be started or timed out.
        """
        try:
            return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            # Program missing or hung: callers treat None as "not available".
            return None

    def popen(self, args: Sequence[str], **kwargs) -> subprocess.Popen:
        """Start a program in the background."""
        return subprocess.Popen(list(args), **kwargs)

    def open_url(self, url: str) -> bool:
        """Open a URL in the default browser; return True when one accepted it."""
        return webbrowser.open(url)

    def sleep(self, seconds: float) -> None:
        """Pause."""
        time.sleep(seconds)


def probe_existing(port: int, timeout: float = PROBE_TIMEOUT_SECONDS) -> Optional[dict]:
    """Ask a port whether a showcase server answers there.

    Parameters:
        port: TCP port on 127.0.0.1.
        timeout: Seconds to wait.
    Returns:
        The /_showcase_info.json object, or None when nothing (or another program) answers.
    """
    url = f"http://{HOST}:{port}/_showcase_info.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read(65536).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        # Closed port, other program, or not JSON: no showcase server here.
        return None
    return data if isinstance(data, dict) else None


def port_in_use(port: int, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Return True when something accepts connections on 127.0.0.1:port."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_browser(platform: str, runner: Runner, environ: Optional[dict] = None) -> Optional[str]:
    """Find a Chrome or Chromium executable.

    Parameters:
        platform: sys.platform value.
        runner: Provides which() and exists().
        environ: Environment for Windows folders (default os.environ).
    Returns:
        The executable path, or None.
    """
    env = os.environ if environ is None else environ
    if platform.startswith("linux"):
        for name in LINUX_CHROME_NAMES:
            if runner.which(name):
                return name
    elif platform == "darwin":
        return next((p for p in MAC_CHROME_PATHS if runner.exists(p)), None)
    elif platform.startswith("win"):
        for var in WINDOWS_ENV_ROOTS:
            for part in WINDOWS_CHROME_PARTS:
                candidate = os.path.join(env.get(var, ""), part) if env.get(var) else ""
                if candidate and runner.exists(candidate):
                    return candidate
    return None


def chrome_kiosk_command(browser: str, url: str, profile_dir: Path) -> list[str]:
    """Build the kiosk Chrome command (own profile so a running Chrome is not reused).

    Parameters:
        browser: Chrome executable.
        url: The page to open.
        profile_dir: Dedicated user-data folder.
    Returns:
        The argument list.
    """
    return [browser, f"--user-data-dir={profile_dir}", f"--app={url}", *CHROME_KIOSK_FLAGS]


def server_command(settings: Settings, view: str, port: int) -> list[str]:
    """Build the argv of the server child (VICARIUS looks for exactly this shape).

    Parameters:
        settings: Resolved settings.
        view: screensaver or slideshow.
        port: TCP port.
    Returns:
        [python, <repo>/showcase.py, serve, --view, view, --port, port] plus
        --content <dir> when a flag or environment override chose the folder.
    """
    argv = [sys.executable, str(settings.repo_dir / "showcase.py"), "serve",
            "--view", view, "--port", str(port)]
    if settings.content_dir_source in (SOURCE_FLAG, SOURCE_ENV):
        argv += ["--content", str(settings.content_dir)]
    return argv


class DockToggle:
    """Un-pin the Ubuntu dash-to-dock while the screensaver shows, then restore it."""

    def __init__(self, runner: Runner, platform: str) -> None:
        """Remember the services to use.

        Parameters:
            runner: Provides which() and run().
            platform: sys.platform value; only Linux does anything.
        """
        self.runner = runner
        self.active = platform.startswith("linux") and runner.which("gsettings") is not None
        self.previous = ""

    def apply(self) -> None:
        """Set dock-fixed to false, remembering the old value (when the setting exists)."""
        if not self.active:
            return
        writable = self.runner.run(["gsettings", "writable", DOCK_SCHEMA, DOCK_KEY], timeout=5)
        if writable is None or writable.returncode != 0:
            return
        current = self.runner.run(["gsettings", "get", DOCK_SCHEMA, DOCK_KEY], timeout=5)
        if current is None or current.returncode != 0 or not current.stdout.strip():
            return
        self.previous = current.stdout.strip()
        self.runner.run(["gsettings", "set", DOCK_SCHEMA, DOCK_KEY, "false"], timeout=5)

    def restore(self) -> None:
        """Put dock-fixed back to its earlier value."""
        if self.active and self.previous:
            self.runner.run(["gsettings", "set", DOCK_SCHEMA, DOCK_KEY, self.previous], timeout=5)
            self.previous = ""


def start_fullscreen_helper(runner: Runner, platform: str) -> Optional[threading.Event]:
    """Start the thread that forces the kiosk window fullscreen with wmctrl.

    Parameters:
        runner: Provides which(), run() and sleep().
        platform: sys.platform value; only Linux with wmctrl installed starts it.
    Returns:
        An Event that stops the helper when set, or None when nothing was started.
    """
    if not platform.startswith("linux") or runner.which("wmctrl") is None:
        return None
    stop = threading.Event()

    def work() -> None:
        for _ in range(WINDOW_POLLS):
            if stop.is_set():
                return
            listing = runner.run(["wmctrl", "-l"], timeout=5)
            if listing is not None and WINDOW_TITLE in listing.stdout:
                runner.run(["wmctrl", "-r", WINDOW_TITLE, "-b", "add,fullscreen"], timeout=5)
                runner.run(["wmctrl", "-r", WINDOW_TITLE, "-b", "add,above"], timeout=5)
                return
            runner.sleep(WINDOW_POLL_SECONDS)

    threading.Thread(target=work, name="showcase-fullscreen", daemon=True).start()
    return stop


def ensure_server(settings: Settings, view: str, port: int, runner: Runner,
                  probe: Callable[[int], Optional[dict]] = probe_existing,
                  in_use: Callable[[int], bool] = port_in_use) -> Optional[subprocess.Popen]:
    """Reuse the server on the port, or start our own.

    Parameters:
        settings: Resolved settings.
        view: screensaver or slideshow.
        port: TCP port.
        runner: Starts the child.
        probe: Reads /_showcase_info.json (tests stub it).
        in_use: Tells whether anything listens on the port (tests stub it).
    Returns:
        The child process we started, or None when an existing server is reused.
    Raises:
        LaunchError: The port is held by another program or by a server for another
            folder, or our server did not come up.
    """
    info = probe(port)
    if info is not None and info.get("app") == APP_NAME:
        if _same_folder(info.get("content_dir"), settings.content_dir):
            return None
        raise LaunchError(
            f"port {port} already serves another content folder ({info.get('content_dir')}). "
            f"Close that showcase first, or use --port to pick another port."
        )
    if in_use(port):
        raise LaunchError(
            f"port {port} is used by another program. Close it, or use --port to pick another port."
        )
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL,
              "cwd": str(settings.repo_dir)}
    if not sys.platform.startswith("win"):
        kwargs["start_new_session"] = True
    child = runner.popen(server_command(settings, view, port), **kwargs)
    _wait_for_server(child, port, runner, probe)
    return child


def _same_folder(reported: object, wanted: Path) -> bool:
    """Return True when a server's reported folder is the folder we want."""
    if not isinstance(reported, str):
        return False
    return os.path.realpath(reported) == os.path.realpath(wanted)


def _wait_for_server(child: subprocess.Popen, port: int, runner: Runner, probe: Callable[[int], Optional[dict]]) -> None:
    """Wait until the child's port answers.

    Parameters:
        child: The server process.
        port: TCP port.
        runner: Provides sleep().
        probe: Reads /_showcase_info.json.
    Raises:
        LaunchError: The child exited early or never answered; the child is stopped.
    """
    waited = 0.0
    while waited <= SERVER_WAIT_SECONDS:
        if child.poll() is not None:
            raise LaunchError(f"the showcase server stopped at start (exit code {child.returncode}); "
                              f"run `python3 showcase.py serve --port {port}` to see why")
        info = probe(port)
        if info is not None and info.get("app") == APP_NAME:
            return
        runner.sleep(SERVER_POLL_SECONDS)
        waited += SERVER_POLL_SECONDS
    stop_child(child)
    raise LaunchError(f"the showcase server did not answer on port {port} within {SERVER_WAIT_SECONDS:.0f} s")


def stop_child(child: Optional[subprocess.Popen]) -> None:
    """Stop the server we started (never a reused one).

    Parameters:
        child: The process, or None.
    """
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=CHILD_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _open_screensaver(url: str, runner: Runner, platform: str, child: Optional[subprocess.Popen]) -> None:
    """Open the kiosk browser and block until it closes (or wait for Ctrl+C).

    Parameters:
        url: Page URL.
        runner: Starts programs.
        platform: sys.platform value.
        child: Our server process, or None when reusing one.
    """
    browser = detect_browser(platform, runner)
    if browser:
        profile = Path.home() / PROFILE_SUBDIR
        profile.mkdir(parents=True, exist_ok=True)
        runner.run(chrome_kiosk_command(browser, url, profile))
    elif runner.which("firefox"):
        runner.run(["firefox", "--kiosk", url])
    else:
        _open_default(url, runner, platform, child, prefer_xdg=True)


def _open_default(url: str, runner: Runner, platform: str, child: Optional[subprocess.Popen],
                  prefer_xdg: bool = False) -> None:
    """Open the default browser, then wait for Ctrl+C.

    Parameters:
        url: Page URL.
        runner: Starts programs.
        platform: sys.platform value.
        child: Our server process, or None when reusing one.
        prefer_xdg: On Linux, use xdg-open before the webbrowser module (the
            screensaver's last-resort fallback).
    """
    if prefer_xdg and platform.startswith("linux") and runner.which("xdg-open"):
        runner.run(["xdg-open", url])
    elif not runner.open_url(url):
        print(f"showcase: no browser was found; open {url} yourself.")
    print("Press Ctrl+C in this window to stop the server.")
    _wait_until_interrupted(child, runner)


def _wait_until_interrupted(child: Optional[subprocess.Popen], runner: Runner) -> None:
    """Block until Ctrl+C or until our server exits."""
    try:
        if child is not None:
            child.wait()
        else:
            while True:
                runner.sleep(1.0)
    except KeyboardInterrupt:
        print()


def _exit_on_sigterm():
    """Turn SIGTERM into a normal exit so the cleanup (dock, server child) still runs.

    Returns:
        The previous handler (for restore_sigterm), or None when it could not be changed.
    """
    def handler(signum, frame):  # noqa: ARG001
        raise SystemExit(128 + signum)

    try:
        return signal.signal(signal.SIGTERM, handler)
    except ValueError:
        # Not the main thread (a test harness): the default handling stays.
        return None


def _restore_sigterm(previous) -> None:
    """Put back the SIGTERM handler that _exit_on_sigterm replaced."""
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def run_view(settings: Settings, view: str, port: int, runner: Optional[Runner] = None,
             platform: Optional[str] = None,
             probe: Callable[[int], Optional[dict]] = probe_existing,
             in_use: Callable[[int], bool] = port_in_use) -> int:
    """Run one view from start to finish.

    Parameters:
        settings: Resolved settings.
        view: screensaver or slideshow.
        port: TCP port.
        runner: Outside effects (default: the real ones).
        platform: sys.platform value (default: the real one).
        probe: Reads /_showcase_info.json (tests stub it).
        in_use: Tells whether anything listens on the port (tests stub it).
    Returns:
        Process exit code: 0 after a normal session, 1 when it could not start.
    """
    runner = runner or Runner()
    platform = platform or sys.platform
    previous_handler = _exit_on_sigterm()
    url = f"http://{HOST}:{port}/{view}.html"
    dock = DockToggle(runner, platform if view == "screensaver" else "none")
    helper = None
    child = None
    try:
        child = ensure_server(settings, view, port, runner, probe, in_use)
        print(f"VICAR Lab {view}: {url}")
        print(f"Content folder ({settings.content_dir_source}): {settings.content_dir}")
        if view == "screensaver":
            dock.apply()
            helper = start_fullscreen_helper(runner, platform)
            _open_screensaver(url, runner, platform, child)
        else:
            _open_default(url, runner, platform, child)
        return 0
    except LaunchError as exc:
        print(f"showcase: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if helper is not None:
            helper.set()
        dock.restore()
        stop_child(child)
        _restore_sigterm(previous_handler)
