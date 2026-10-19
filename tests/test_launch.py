"""Module: tests.test_launch
Purpose: Test browser detection, the server child, the dock toggle, the fullscreen helper
    and the view sessions, with every browser, gsettings and wmctrl call stubbed.
Inputs: A fake runner; one test starts the real server as a child process.
Outputs: Test results.
"""

import http.server
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import LiveServerTestCase, TempDirTestCase, free_port, make_content, make_repo
from vicar_showcase import launch
from vicar_showcase.launch import LaunchError, Runner
from vicar_showcase.settings import Settings


class FakeChild:
    """A stand-in for subprocess.Popen."""

    def __init__(self, exit_code=None, wait_raises=None):
        self.returncode = exit_code
        self.terminated = False
        self.killed = False
        self.wait_raises = wait_raises
        self.pid = 4242

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.wait_raises and timeout is None:
            raise self.wait_raises  # the foreground wait that Ctrl+C interrupts
        return self.returncode


class FakeRunner(Runner):
    """Records every outside effect and answers from tables."""

    def __init__(self, programs=(), files=(), child=None, gsettings=None, windows=""):
        self.programs = set(programs)
        self.files = set(files)
        self.child = child or FakeChild()
        self.calls = []
        self.popens = []
        self.opened = []
        self.gsettings = gsettings or {}
        self.windows = windows
        self.run_raises = None

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.programs else None

    def exists(self, path):
        return path in self.files

    def run(self, args, timeout=None):
        self.calls.append(list(args))
        if self.run_raises and args[0] not in ("gsettings", "wmctrl"):
            raise self.run_raises
        if args[0] == "gsettings":
            sub = args[1]
            if sub == "writable":
                return subprocess.CompletedProcess(args, 0 if self.gsettings.get("writable", True) else 1, "true\n", "")
            if sub == "get":
                return subprocess.CompletedProcess(args, 0, self.gsettings.get("value", "true") + "\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "wmctrl" and args[1] == "-l":
            return subprocess.CompletedProcess(args, 0, self.windows, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def popen(self, args, **kwargs):
        self.popens.append((list(args), kwargs))
        return self.child

    def open_url(self, url):
        self.opened.append(url)
        return True

    def sleep(self, seconds):
        pass

    def named(self, program):
        return [c for c in self.calls if c[0] == program]


def settings_for(repo: Path, content: Path, source="settings.json") -> Settings:
    """Build a Settings record."""
    return Settings(repo, content, source, "captions.csv")


class DetectTests(unittest.TestCase):
    """detect_browser and command builders."""

    def test_linux_order(self):
        self.assertEqual(launch.detect_browser("linux", FakeRunner(["chromium", "google-chrome"])), "google-chrome")
        self.assertEqual(launch.detect_browser("linux", FakeRunner(["chromium-browser"])), "chromium-browser")
        self.assertIsNone(launch.detect_browser("linux", FakeRunner(["firefox"])))

    def test_macos(self):
        path = launch.MAC_CHROME_PATHS[1]
        self.assertEqual(launch.detect_browser("darwin", FakeRunner(files=[path])), path)
        self.assertIsNone(launch.detect_browser("darwin", FakeRunner()))

    def test_windows(self):
        env = {"ProgramFiles": "C:\\Program Files"}
        want = os.path.join("C:\\Program Files", launch.WINDOWS_CHROME_PARTS[0])
        self.assertEqual(launch.detect_browser("win32", FakeRunner(files=[want]), env), want)
        self.assertIsNone(launch.detect_browser("win32", FakeRunner(), {}))

    def test_unknown_platform(self):
        self.assertIsNone(launch.detect_browser("plan9", FakeRunner(["google-chrome"])))

    def test_kiosk_command_keeps_every_flag(self):
        cmd = launch.chrome_kiosk_command("google-chrome", "http://x/", Path("/p"))
        for flag in ("--user-data-dir=/p", "--app=http://x/", "--kiosk", "--noerrdialogs", "--disable-infobars",
                     "--disable-session-crashed-bubble", "--disable-features=TranslateUI", "--no-first-run",
                     "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                     "--disable-pinch", "--overscroll-history-navigation=0"):
            self.assertIn(flag, cmd)

    def test_profile_dir_constant(self):
        self.assertEqual(launch.PROFILE_SUBDIR, Path(".cache") / "vicar-screensaver-profile")

    def test_server_command_exact_argv(self):
        s = settings_for(Path("/r"), Path("/c"))
        self.assertEqual(launch.server_command(s, "screensaver", 8766),
                         [sys.executable, "/r/showcase.py", "serve", "--view", "screensaver", "--port", "8766"])

    def test_server_command_adds_content_for_overrides(self):
        for source in ("--content flag", "VICAR_SHOWCASE_CONTENT"):
            argv = launch.server_command(settings_for(Path("/r"), Path("/c"), source), "slideshow", 8765)
            self.assertEqual(argv[-2:], ["--content", "/c"])
        for source in ("settings.json", "settings.local.json"):
            self.assertNotIn("--content", launch.server_command(settings_for(Path("/r"), Path("/c"), source), "slideshow", 1))


class ProbeTests(LiveServerTestCase):
    """probe_existing and port_in_use."""

    def test_probe_live_server(self):
        info = launch.probe_existing(self.port)
        self.assertEqual(info["app"], "vicar-showcase")
        self.assertTrue(launch.port_in_use(self.port))

    def test_probe_closed_port(self):
        port = free_port()
        self.assertIsNone(launch.probe_existing(port))
        self.assertFalse(launch.port_in_use(port))

    def test_probe_other_program_not_json(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"<html>nope</html>")
            def log_message(self, *a): pass
        other = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=other.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(lambda: (other.shutdown(), other.server_close()))
        self.assertIsNone(launch.probe_existing(other.server_address[1]))
        self.assertTrue(launch.port_in_use(other.server_address[1]))

    def test_probe_times_out_on_silent_listener(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(1)
        self.addCleanup(listener.close)
        start = time.time()
        self.assertIsNone(launch.probe_existing(listener.getsockname()[1], timeout=0.3))
        self.assertLess(time.time() - start, 3)


class EnsureServerTests(TempDirTestCase):
    """ensure_server."""

    def setUp(self):
        super().setUp()
        self.content = self.tmp / "content"; self.content.mkdir()
        self.settings = settings_for(self.tmp / "repo", self.content)

    def test_reuse_same_folder(self):
        info = {"app": "vicar-showcase", "content_dir": str(self.content)}
        runner = FakeRunner()
        self.assertIsNone(launch.ensure_server(self.settings, "screensaver", 1, runner, lambda p: info, lambda p: True))
        self.assertEqual(runner.popens, [])

    def test_reuse_same_folder_through_symlink(self):
        link = self.tmp / "link"; os.symlink(self.content, link)
        info = {"app": "vicar-showcase", "content_dir": str(link)}
        self.assertIsNone(launch.ensure_server(self.settings, "screensaver", 1, FakeRunner(), lambda p: info, lambda p: True))

    def test_other_folder_is_an_error(self):
        info = {"app": "vicar-showcase", "content_dir": "/elsewhere"}
        with self.assertRaisesRegex(LaunchError, r"port 8766 already serves another content folder \(/elsewhere\)"):
            launch.ensure_server(self.settings, "screensaver", 8766, FakeRunner(), lambda p: info, lambda p: True)

    def test_other_program_is_an_error(self):
        with self.assertRaisesRegex(LaunchError, "port 8766 is used by another program"):
            launch.ensure_server(self.settings, "screensaver", 8766, FakeRunner(), lambda p: None, lambda p: True)

    def test_other_app_json_is_not_reused(self):
        with self.assertRaisesRegex(LaunchError, "another program"):
            launch.ensure_server(self.settings, "screensaver", 8766, FakeRunner(), lambda p: {"app": "x"}, lambda p: True)

    def test_starts_child_and_waits(self):
        answers = iter([None, None, {"app": "vicar-showcase"}])
        runner = FakeRunner()
        child = launch.ensure_server(self.settings, "slideshow", 8765, runner, lambda p: next(answers, {"app": "vicar-showcase"}),
                                     lambda p: False)
        self.assertIs(child, runner.child)
        argv, kwargs = runner.popens[0]
        self.assertEqual(argv[1:], [str(self.settings.repo_dir / "showcase.py"), "serve", "--view", "slideshow", "--port", "8765"])
        self.assertEqual(kwargs["cwd"], str(self.settings.repo_dir))

    def test_child_exits_early(self):
        runner = FakeRunner(child=FakeChild(exit_code=1))
        with self.assertRaisesRegex(LaunchError, "stopped at start"):
            launch.ensure_server(self.settings, "slideshow", 8765, runner, lambda p: None, lambda p: False)

    def test_child_never_answers_is_stopped(self):
        runner = FakeRunner()
        with self.assertRaisesRegex(LaunchError, "did not answer"):
            launch.ensure_server(self.settings, "slideshow", 8765, runner, lambda p: None, lambda p: False)
        self.assertTrue(runner.child.terminated)

    def test_stop_child_kills_when_terminate_hangs(self):
        child = FakeChild(wait_raises=subprocess.TimeoutExpired("x", 1))
        child.terminate = lambda: None
        child.wait = mock.Mock(side_effect=[subprocess.TimeoutExpired("x", 1), 0])
        launch.stop_child(child)
        self.assertTrue(child.killed)
        launch.stop_child(None)


class DockAndHelperTests(unittest.TestCase):
    """Dock toggle and fullscreen helper."""

    def test_dock_toggled_and_restored(self):
        runner = FakeRunner(["gsettings"], gsettings={"value": "true"})
        dock = launch.DockToggle(runner, "linux")
        dock.apply()
        dock.restore()
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual(sets[0][-1], "false")
        self.assertEqual(sets[1][-1], "true")
        self.assertEqual(sets[0][2:4], [launch.DOCK_SCHEMA, "dock-fixed"])

    def test_dock_not_writable_untouched(self):
        runner = FakeRunner(["gsettings"], gsettings={"writable": False})
        dock = launch.DockToggle(runner, "linux")
        dock.apply(); dock.restore()
        self.assertEqual([c for c in runner.named("gsettings") if c[1] == "set"], [])

    def test_dock_inactive_without_gsettings_or_off_linux(self):
        for platform, programs in (("linux", []), ("darwin", ["gsettings"]), ("win32", ["gsettings"])):
            runner = FakeRunner(programs)
            dock = launch.DockToggle(runner, platform)
            dock.apply(); dock.restore()
            self.assertEqual(runner.calls, [])

    def test_fullscreen_helper_finds_window(self):
        runner = FakeRunner(["wmctrl"], windows="0x01 0 host VICAR Lab \u00b7 Screensaver\n")
        stop = launch.start_fullscreen_helper(runner, "linux")
        deadline = time.time() + 3
        while time.time() < deadline and len(runner.named("wmctrl")) < 3:
            time.sleep(0.02)
        stop.set()
        calls = runner.named("wmctrl")
        self.assertIn(["wmctrl", "-r", "VICAR Lab", "-b", "add,fullscreen"], calls)
        self.assertIn(["wmctrl", "-r", "VICAR Lab", "-b", "add,above"], calls)

    def test_fullscreen_helper_absent(self):
        self.assertIsNone(launch.start_fullscreen_helper(FakeRunner(), "linux"))
        self.assertIsNone(launch.start_fullscreen_helper(FakeRunner(["wmctrl"]), "darwin"))

    def test_fullscreen_helper_stops(self):
        runner = FakeRunner(["wmctrl"], windows="")
        stop = launch.start_fullscreen_helper(runner, "linux")
        stop.set()
        time.sleep(0.1)
        n = len(runner.calls)
        time.sleep(0.1)
        self.assertEqual(len(runner.calls), n)


class RunViewTests(TempDirTestCase):
    """run_view sessions."""

    def setUp(self):
        super().setUp()
        self.content = self.tmp / "content"; self.content.mkdir()
        self.settings = settings_for(self.tmp / "repo", self.content)
        self.ready = lambda p: {"app": "vicar-showcase", "content_dir": str(self.content)}
        self.home = mock.patch.object(launch.Path, "home", return_value=self.tmp / "home")
        self.home.start()
        self.addCleanup(self.home.stop)

    def run_view(self, view, runner, platform="linux", probe=None, in_use=None):
        with mock.patch("builtins.print"):
            return launch.run_view(self.settings, view, 8766, runner, platform,
                                   probe or self.ready, in_use or (lambda p: False))

    def test_screensaver_chrome_kiosk_blocks_then_cleans_up(self):
        probes = iter([None, {"app": "vicar-showcase"}])
        runner = FakeRunner(["google-chrome", "gsettings", "wmctrl"], gsettings={"value": "true"})
        code = self.run_view("screensaver", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertEqual(code, 0)
        chrome = runner.named("google-chrome")[0]
        self.assertIn("--app=http://127.0.0.1:8766/screensaver.html", chrome)
        self.assertIn(f"--user-data-dir={self.tmp / 'home' / '.cache' / 'vicar-screensaver-profile'}", chrome)
        self.assertTrue((self.tmp / "home" / ".cache" / "vicar-screensaver-profile").is_dir())
        self.assertTrue(runner.child.terminated)
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])

    def test_dock_restored_when_browser_fails(self):
        runner = FakeRunner(["google-chrome", "gsettings"])
        runner.run_raises = OSError("chrome crashed")
        with self.assertRaises(OSError):
            self.run_view("screensaver", runner)
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])

    def test_reused_server_is_never_stopped(self):
        runner = FakeRunner(["google-chrome"])
        self.assertEqual(self.run_view("screensaver", runner), 0)
        self.assertEqual(runner.popens, [])
        self.assertFalse(runner.child.terminated)

    def test_firefox_fallback(self):
        runner = FakeRunner(["firefox"])
        self.run_view("screensaver", runner)
        self.assertEqual(runner.named("firefox")[0][:2], ["firefox", "--kiosk"])

    def test_xdg_open_fallback_waits(self):
        runner = FakeRunner(["xdg-open"], child=FakeChild(wait_raises=KeyboardInterrupt()))
        probes = iter([None, {"app": "vicar-showcase"}])
        code = self.run_view("screensaver", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertEqual(code, 0)
        self.assertEqual(runner.named("xdg-open")[0][1], "http://127.0.0.1:8766/screensaver.html")
        self.assertTrue(runner.child.terminated)

    def test_slideshow_uses_default_browser_and_ctrl_c(self):
        probes = iter([None, {"app": "vicar-showcase"}])
        runner = FakeRunner(["google-chrome", "gsettings"], child=FakeChild(wait_raises=KeyboardInterrupt()))
        code = self.run_view("slideshow", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertEqual(code, 0)
        self.assertEqual(runner.opened, ["http://127.0.0.1:8766/slideshow.html"])
        self.assertEqual(runner.named("gsettings"), [], "the dock is only touched for the screensaver")
        self.assertEqual(runner.named("google-chrome"), [])
        self.assertTrue(runner.child.terminated)

    def test_port_conflict_returns_1_and_touches_nothing(self):
        runner = FakeRunner(["google-chrome", "gsettings"])
        code = self.run_view("screensaver", runner, probe=lambda p: None, in_use=lambda p: True)
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])

    def test_macos_uses_app_path(self):
        runner = FakeRunner(files=[launch.MAC_CHROME_PATHS[0]])
        self.run_view("screensaver", runner, platform="darwin")
        self.assertEqual(runner.calls[0][0], launch.MAC_CHROME_PATHS[0])
        self.assertEqual(runner.named("gsettings"), [])


class RealChildTests(TempDirTestCase):
    """Start the real server as a child process through ensure_server."""

    def test_child_argv_and_stop(self):
        content = self.tmp / "content"; content.mkdir()
        make_content(content)
        repo = make_repo(self.tmp, content)
        port = free_port()
        settings = settings_for(repo, content)
        child = launch.ensure_server(settings, "slideshow", port, Runner())
        self.addCleanup(launch.stop_child, child)
        self.assertIsNotNone(child)
        info = launch.probe_existing(port)
        self.assertEqual((info["pid"], info["view"]), (child.pid, "slideshow"))
        if sys.platform.startswith("linux"):
            argv = Path(f"/proc/{child.pid}/cmdline").read_bytes().split(b"\0")[:-1]
            self.assertEqual([a.decode() for a in argv],
                             [sys.executable, str(repo / "showcase.py"), "serve", "--view", "slideshow", "--port", str(port)])
        again = launch.ensure_server(settings, "slideshow", port, Runner())
        self.assertIsNone(again, "a second launcher reuses the running server")
        launch.stop_child(child)
        self.assertIsNone(launch.probe_existing(port))


if __name__ == "__main__":
    unittest.main()
