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
import shutil
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

from .common import COMMAND_TIMEOUT_SECONDS, EXIT_OK, EXIT_USAGE, LOOPBACK_HOST
from .server import APP_NAME
from .settings import SOURCE_ENV, SOURCE_FLAG, Settings

PROBE_TIMEOUT_SECONDS = 1.0
SERVER_WAIT_SECONDS = 3.0
SERVER_POLL_SECONDS = 0.1
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
# The same apps installed for one user only; "~" is expanded when the browser is looked for.
MAC_USER_CHROME_PATHS = tuple(path.replace("/Applications/", "~/Applications/", 1) for path in MAC_CHROME_PATHS)
WINDOWS_CHROME_PARTS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Chromium\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)
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

    def run_foreground(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        """Run a program (the browser) until it exits, discarding its output.

        Output goes to the null device, not to memory: a kiosk browser runs for days
        and writes all the time.

        Returns:
            The CompletedProcess.
        Raises:
            OSError: The program could not be started.
        """
        return subprocess.run(list(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False)

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
    url = f"http://{LOOPBACK_HOST}:{port}/_showcase_info.json"
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
        with socket.create_connection((LOOPBACK_HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_browser(platform: str, runner: Runner, environ: Optional[dict] = None) -> Optional[str]:
    """Find a Chrome, Chromium (or, on Windows, Edge) executable.

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
        candidates = [os.path.expanduser(p) for p in MAC_CHROME_PATHS + MAC_USER_CHROME_PATHS]
        return next((p for p in candidates if runner.exists(p)), None)
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
        writable = self.runner.run(["gsettings", "writable", DOCK_SCHEMA, DOCK_KEY], timeout=COMMAND_TIMEOUT_SECONDS)
        if writable is None or writable.returncode != 0:
            return
        current = self.runner.run(["gsettings", "get", DOCK_SCHEMA, DOCK_KEY], timeout=COMMAND_TIMEOUT_SECONDS)
        if current is None or current.returncode != 0 or not current.stdout.strip():
            return
        self.previous = current.stdout.strip()
        self.runner.run(["gsettings", "set", DOCK_SCHEMA, DOCK_KEY, "false"], timeout=COMMAND_TIMEOUT_SECONDS)

    def restore(self) -> None:
        """Put dock-fixed back to its earlier value."""
        if self.active and self.previous:
            self.runner.run(["gsettings", "set", DOCK_SCHEMA, DOCK_KEY, self.previous], timeout=COMMAND_TIMEOUT_SECONDS)
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
        """Poll for the kiosk window, then make it fullscreen and topmost."""
        for _ in range(WINDOW_POLLS):
            if stop.is_set():
                return
            listing = runner.run(["wmctrl", "-l"], timeout=COMMAND_TIMEOUT_SECONDS)
            if listing is not None and WINDOW_TITLE in listing.stdout:
                runner.run(["wmctrl", "-r", WINDOW_TITLE, "-b", "add,fullscreen"], timeout=COMMAND_TIMEOUT_SECONDS)
                runner.run(["wmctrl", "-r", WINDOW_TITLE, "-b", "add,above"], timeout=COMMAND_TIMEOUT_SECONDS)
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
    _wait_for_server(child, view, port, runner, probe)
    return child


def _same_folder(reported: object, wanted: Path) -> bool:
    """Return True when a server's reported folder is the folder we want."""
    if not isinstance(reported, str):
        return False
    return os.path.realpath(reported) == os.path.realpath(wanted)


def _wait_for_server(child: subprocess.Popen, view: str, port: int, runner: Runner,
                     probe: Callable[[int], Optional[dict]]) -> None:
    """Wait until the child's port answers.

    Parameters:
        child: The server process.
        view: screensaver or slideshow (named in the hint).
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
                              f"run `python3 showcase.py serve --view {view} --port {port}` to see why")
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
        child.wait(timeout=COMMAND_TIMEOUT_SECONDS)
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
        _run_browser(runner, chrome_kiosk_command(browser, url, profile))
    elif runner.which("firefox"):
        _run_browser(runner, ["firefox", "--kiosk", url])
    else:
        _open_default(url, runner, platform, child, prefer_xdg=True)


def _run_browser(runner: Runner, command: Sequence[str]) -> None:
    """Run a kiosk browser until it exits.

    Parameters:
        runner: Starts the program.
        command: The browser command line.
    Raises:
        LaunchError: The browser could not be started; the message says why.
    """
    try:
        runner.run_foreground(command)
    except OSError as exc:
        raise LaunchError(f"the browser {command[0]} could not be started ({exc.strerror or exc}); "
                          f"open the page yourself, or install Chrome or Chromium") from exc


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
        _run_browser(runner, ["xdg-open", url])
    elif not runner.open_url(url):
        print(f"showcase: no browser was found; open {url} yourself.")
    if child is None:
        print("This server belongs to another window; Ctrl+C here only closes this launcher.")
    else:
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


def _termination_signals() -> list[int]:
    """Return the signals that end a session: SIGTERM, and SIGHUP where it exists (closing the terminal)."""
    names = ("SIGTERM", "SIGHUP")
    return [getattr(signal, name) for name in names if hasattr(signal, name)]


def _exit_on_termination() -> dict:
    """Turn SIGTERM and SIGHUP into a normal exit so the cleanup (dock, server child) still runs.

    Closing the terminal sends SIGHUP; without this the server child (its own session)
    would keep running and the dock setting would stay changed.

    Returns:
        The previous handlers by signal number (for _restore_termination); empty when
        they could not be changed.
    """
    def handler(signum, frame):  # noqa: ARG001
        """Raise SystemExit so the finally blocks run."""
        raise SystemExit(128 + signum)

    previous = {}
    for number in _termination_signals():
        try:
            previous[number] = signal.signal(number, handler)
        except ValueError:
            # Not the main thread (a test harness): the default handling stays.
            continue
    return previous


def _restore_termination(previous: dict) -> None:
    """Put back the handlers that _exit_on_termination replaced."""
    for number, handler in previous.items():
        signal.signal(number, handler)


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
    previous_handlers = _exit_on_termination()
    url = f"http://{LOOPBACK_HOST}:{port}/{view}.html"
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
        return EXIT_OK
    except LaunchError as exc:
        print(f"showcase: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to end a session; the finally block cleans up.
        print()
        return EXIT_OK
    finally:
        if helper is not None:
            helper.set()
        dock.restore()
        stop_child(child)
        _restore_termination(previous_handlers)
