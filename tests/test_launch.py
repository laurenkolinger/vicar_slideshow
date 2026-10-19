"""Module: tests.test_launch
Purpose: Test browser detection, the server child, the dock toggle, the fullscreen helper
    and the view sessions, with every browser, gsettings and wmctrl call stubbed.
Inputs: A fake runner; one test starts the real server as a child process.
Outputs: Test results.
"""

import http.server
import io
import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import (LaunchGuardMixin, LiveServerTestCase, StrictRunner, TempDirTestCase, free_port,
                           make_content, make_repo)
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


class FakeRunner(StrictRunner):
    """Records every outside effect and answers from tables (never the real Runner)."""

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

    def run_foreground(self, args):
        self.calls.append(list(args))
        if self.run_raises:
            raise self.run_raises
        return subprocess.CompletedProcess(args, 0)

    def run(self, args, timeout=None):
        self.calls.append(list(args))
        if args[0] not in ("gsettings", "wmctrl"):
            raise AssertionError(f"a browser or other program went through run(): {args}")
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


class DetectTests(LaunchGuardMixin, unittest.TestCase):
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

    def test_windows_edge_is_found_when_chrome_is_not(self):
        env = {"ProgramFiles(x86)": "C:\\Program Files (x86)"}
        want = os.path.join("C:\\Program Files (x86)", launch.WINDOWS_CHROME_PARTS[-1])
        self.assertIn("msedge.exe", want)
        self.assertEqual(launch.detect_browser("win32", FakeRunner(files=[want]), env), want)

    def test_macos_user_applications_folder(self):
        with mock.patch.dict(os.environ, {"HOME": "/Users/someone"}):
            want = "/Users/someone/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            self.assertEqual(launch.detect_browser("darwin", FakeRunner(files=[want])), want)
            self.assertIsNone(launch.detect_browser("darwin", FakeRunner(files=["/Users/other/Applications/x"])))

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


class ProbeTests(LaunchGuardMixin, LiveServerTestCase):
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


class EnsureServerTests(LaunchGuardMixin, TempDirTestCase):
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

    def test_start_failure_hint_carries_the_content_folder_when_it_was_chosen_by_flag(self):
        flagged = settings_for(self.tmp / "repo", self.content, "--content flag")
        for settings, wants in ((flagged, True), (self.settings, False)):
            runner = FakeRunner(child=FakeChild(exit_code=1))
            with self.assertRaises(LaunchError) as ctx:
                launch.ensure_server(settings, "screensaver", 8766, runner, lambda p: None, lambda p: False)
            self.assertEqual(f"--content {self.content}" in str(ctx.exception), wants, str(ctx.exception))

    def test_start_failure_hint_quotes_a_folder_with_spaces(self):
        spaced = self.tmp / "my slides"; spaced.mkdir()
        runner = FakeRunner(child=FakeChild(exit_code=1))
        with self.assertRaises(LaunchError) as ctx:
            launch.ensure_server(settings_for(self.tmp / "repo", spaced, "--content flag"), "slideshow", 8765,
                                 runner, lambda p: None, lambda p: False)
        self.assertIn(f"--content '{spaced}'", str(ctx.exception))

    def test_start_failure_hint_names_the_view_and_port(self):
        runner = FakeRunner(child=FakeChild(exit_code=1))
        with self.assertRaises(LaunchError) as ctx:
            launch.ensure_server(self.settings, "screensaver", 8766, runner, lambda p: None, lambda p: False)
        self.assertIn("python3 showcase.py serve --view screensaver --port 8766", str(ctx.exception))

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


class DockAndHelperTests(LaunchGuardMixin, unittest.TestCase):
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


class RunViewTests(LaunchGuardMixin, TempDirTestCase):
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

    def test_browser_that_cannot_start_is_reported_and_everything_is_cleaned_up(self):
        """The real runner raises OSError for a program it cannot start; the session must say so and exit 1."""
        missing = str(self.tmp / "no-such-dir" / "Google Chrome")
        probes = iter([None, {"app": "vicar-showcase"}])

        class RealBrowserRunner(FakeRunner):
            """Fake everything except the browser start, which uses the real runner method."""
            run_foreground = Runner.run_foreground

        runner = RealBrowserRunner(files=[missing])
        err = io.StringIO()
        with mock.patch.object(launch, "MAC_CHROME_PATHS", (missing,)), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = launch.run_view(self.settings, "screensaver", 8766, runner, "darwin",
                                   lambda p: next(probes, {"app": "vicar-showcase"}), lambda p: False)
        self.assertEqual(code, 1)
        self.assertIn("could not be started", err.getvalue())
        self.assertIn(missing, err.getvalue())
        self.assertTrue(runner.child.terminated)

    def test_dock_restored_when_browser_fails(self):
        runner = FakeRunner(["google-chrome", "gsettings"])
        runner.run_raises = OSError("chrome crashed")
        self.assertEqual(self.run_view("screensaver", runner), 1)
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])

    def test_ctrl_c_during_the_kiosk_browser_exits_quietly_after_cleanup(self):
        probes = iter([None, {"app": "vicar-showcase"}])
        runner = FakeRunner(["google-chrome", "gsettings"])
        runner.run_raises = KeyboardInterrupt()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.run_view("screensaver", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertTrue(runner.child.terminated)
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])

    def default_signals(self):
        """Run the test with SIGHUP and SIGTERM at their defaults, whatever started the test run."""
        saved = {n: signal.getsignal(n) for n in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)}
        self.addCleanup(lambda: [signal.signal(n, h) for n, h in saved.items()])
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP exists on POSIX only")
    def test_a_second_hangup_during_cleanup_does_not_skip_stopping_the_server(self):
        """Closing a terminal can send SIGHUP twice in quick succession."""
        self.default_signals()
        probes = iter([None, {"app": "vicar-showcase"}])
        runner = FakeRunner(["google-chrome", "gsettings"])
        original = signal.getsignal(signal.SIGHUP)
        original_int = signal.getsignal(signal.SIGINT)
        fake_run = runner.run

        def hang_up_once(args):
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(0.05)  # let the handler run in this (main) thread

        def second_hang_up_during_restore(args, timeout=None):
            result = fake_run(args, timeout)
            if args[:2] == ["gsettings", "set"] and args[-1] == "true":
                os.kill(os.getpid(), signal.SIGHUP)
                os.kill(os.getpid(), signal.SIGTERM)
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.05)
            return result

        runner.run_foreground = hang_up_once
        runner.run = second_hang_up_during_restore
        with self.assertRaises(SystemExit) as ctx:
            self.run_view("screensaver", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertEqual(ctx.exception.code, 128 + signal.SIGHUP)
        self.assertTrue(runner.child.terminated, "the server child is stopped even after more signals")
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])
        self.assertEqual(signal.getsignal(signal.SIGHUP), original, "the old handler is put back")
        self.assertEqual(signal.getsignal(signal.SIGINT), original_int)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP exists on POSIX only")
    def test_an_inherited_ignore_is_kept(self):
        """Under nohup SIGHUP is ignored on purpose; the launcher must not replace that."""
        self.default_signals()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        previous = launch._exit_on_termination()
        try:
            self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            self.assertNotIn(signal.getsignal(signal.SIGTERM), (signal.SIG_DFL, signal.SIG_IGN),
                             "SIGTERM, which was not ignored, still gets the handler")
        finally:
            launch._restore_termination(previous)
        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP exists on POSIX only")
    def test_closing_the_terminal_stops_the_server_and_restores_the_dock(self):
        self.default_signals()
        probes = iter([None, {"app": "vicar-showcase"}])
        runner = FakeRunner(["google-chrome", "gsettings"])
        original = signal.getsignal(signal.SIGHUP)
        seen = {}

        def hang_up(args):
            handler = signal.getsignal(signal.SIGHUP)
            seen["installed"] = handler not in (signal.SIG_DFL, signal.SIG_IGN, original)
            handler(signal.SIGHUP, None)  # what the signal does when the terminal closes

        runner.run_foreground = hang_up
        with self.assertRaises(SystemExit) as ctx:
            self.run_view("screensaver", runner, probe=lambda p: next(probes, {"app": "vicar-showcase"}))
        self.assertTrue(seen["installed"], "SIGHUP has no handler during a session")
        self.assertEqual(ctx.exception.code, 128 + signal.SIGHUP)
        self.assertTrue(runner.child.terminated)
        sets = [c for c in runner.named("gsettings") if c[1] == "set"]
        self.assertEqual([c[-1] for c in sets], ["false", "true"])
        self.assertEqual(signal.getsignal(signal.SIGHUP), original, "the old handler is put back")

    def test_reused_server_message_does_not_claim_ctrl_c_stops_it(self):
        out = io.StringIO()
        runner = FakeRunner()
        # Reused server: no child. Ctrl+C is simulated by making the wait raise.
        runner.sleep = mock.Mock(side_effect=KeyboardInterrupt())
        with contextlib.redirect_stdout(out):
            launch._open_default("http://x/", runner, "linux", None)
        self.assertIn("belongs to another window", out.getvalue())
        self.assertIn("only closes this launcher", out.getvalue())
        self.assertNotIn("stop the server", out.getvalue())

    def test_own_server_message_says_ctrl_c_stops_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            launch._open_default("http://x/", FakeRunner(), "linux", FakeChild(wait_raises=KeyboardInterrupt()))
        self.assertIn("Press Ctrl+C in this window to stop the server.", out.getvalue())

    def test_reused_server_is_never_stopped(self):
        runner = FakeRunner(["google-chrome"])
        self.assertEqual(self.run_view("screensaver", runner), 0)
        self.assertEqual(runner.popens, [])
        self.assertFalse(runner.child.terminated)

    def test_firefox_fallback_is_its_own_instance_with_its_own_profile(self):
        runner = FakeRunner(["firefox"])
        self.assertEqual(self.run_view("screensaver", runner), 0)
        profile = self.tmp / "home" / ".cache" / "vicar-screensaver-firefox-profile"
        self.assertEqual(runner.named("firefox")[0],
                         ["firefox", "--new-instance", "--profile", str(profile), "--kiosk",
                          "http://127.0.0.1:8766/screensaver.html"])
        self.assertTrue(profile.is_dir(), "the profile folder is created on demand")

    def test_browser_that_exits_with_an_error_is_reported_and_cleaned_up(self):
        for programs, expected in ((["google-chrome"], "google-chrome"), (["firefox"], "firefox"),
                                   (["xdg-open"], "xdg-open")):
            with self.subTest(expected=expected):
                runner = FakeRunner(programs, child=FakeChild(wait_raises=KeyboardInterrupt()))
                runner.run_foreground = lambda args, runner=runner: (
                    runner.calls.append(list(args)) or subprocess.CompletedProcess(args, 3))
                probes = iter([None, {"app": "vicar-showcase"}])
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = launch.run_view(self.settings, "screensaver", 8766, runner, "linux",
                                           lambda p: next(probes, {"app": "vicar-showcase"}), lambda p: False)
                self.assertEqual(code, 1)
                self.assertIn(f"{expected} exited with an error (code 3)", err.getvalue())
                self.assertTrue(runner.child.terminated)

    def test_xdg_open_success_keeps_serving_and_says_so(self):
        out = io.StringIO()
        runner = FakeRunner(["xdg-open"], child=FakeChild(wait_raises=KeyboardInterrupt()))
        with contextlib.redirect_stdout(out):
            launch._open_default("http://x/", runner, "linux", runner.child, prefer_xdg=True)
        self.assertIn("returns at once, so the server keeps running", out.getvalue())
        self.assertIn("Press Ctrl+C", out.getvalue())

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


class TempFolderCleanupTests(unittest.TestCase):
    """The shared temporary folder never outlives its test, even when the test fails."""

    def test_folder_is_removed_after_pass_fail_and_error(self):
        made = []

        class Probe(TempDirTestCase):
            def test_pass(self):
                made.append(self.tmp); (self.tmp / "content").mkdir()
            def test_fail(self):
                made.append(self.tmp); (self.tmp / "content").mkdir(); self.fail("on purpose")
            def test_error(self):
                made.append(self.tmp); (self.tmp / "content").mkdir(); raise RuntimeError("on purpose")

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Probe).run(result)
        self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (3, 1, 1))
        self.assertEqual(len(made), 3)
        self.assertEqual([p for p in made if p.exists()], [])


class BrowserGuardTests(LaunchGuardMixin, TempDirTestCase):
    """The guard itself: an unstubbed browser start fails the test and starts nothing."""

    class BrowserFoundRunner(Runner):
        """The REAL runner except that a Chrome and Firefox are reported as installed, so the
        launcher goes all the way to starting one. Only safe under the guard."""

        def which(self, name):
            return None if name in ("gsettings", "wmctrl") else f"/usr/bin/{name}"

        def sleep(self, seconds):
            pass

    def setUp(self):
        super().setUp()
        self.content = self.tmp / "content"; self.content.mkdir()
        self.settings = settings_for(self.tmp / "repo", self.content)
        home = mock.patch.object(launch.Path, "home", return_value=self.tmp / "home")
        home.start()
        self.addCleanup(home.stop)

    def test_kiosk_browser_start_through_the_real_runner_is_blocked(self):
        info = {"app": "vicar-showcase", "content_dir": str(self.content)}
        with mock.patch("builtins.print"), self.assertRaises(AssertionError) as ctx:
            launch.run_view(self.settings, "screensaver", 8766, self.BrowserFoundRunner(), "linux",
                            lambda p: info, lambda p: False)
        self.assertIn("subprocess.run", str(ctx.exception))
        self.assertIn("google-chrome", str(ctx.exception))
        self.assertEqual(self.guard.started, [], "no process was started")
        self.assertEqual(len(self.guard.blocked), 1)

    def test_firefox_start_is_blocked(self):
        runner = self.BrowserFoundRunner()
        runner.which = lambda name: "/usr/bin/firefox" if name == "firefox" else None
        info = {"app": "vicar-showcase", "content_dir": str(self.content)}
        with mock.patch("builtins.print"), self.assertRaises(AssertionError):
            launch.run_view(self.settings, "screensaver", 8766, runner, "linux", lambda p: info, lambda p: False)
        self.assertEqual(self.guard.started, [])

    def test_default_browser_opener_is_blocked(self):
        info = {"app": "vicar-showcase", "content_dir": str(self.content)}
        with mock.patch("builtins.print"), self.assertRaises(AssertionError) as ctx:
            launch.run_view(self.settings, "slideshow", 8766, self.BrowserFoundRunner(), "linux",
                            lambda p: info, lambda p: False)
        self.assertIn("webbrowser.open", str(ctx.exception))
        self.assertEqual(self.guard.started, [])

    def test_a_program_that_is_not_allowed_never_starts(self):
        for call in (lambda: subprocess.Popen(["/bin/true"]), lambda: subprocess.run(["xdg-open", "http://x/"]),
                     lambda: os.system("true")):
            with self.assertRaises(AssertionError):
                call()
        self.assertEqual(self.guard.started, [])

    def test_the_strict_runner_fails_every_method_that_is_not_overridden(self):
        strict = StrictRunner()
        for call in (lambda: strict.which("x"), lambda: strict.exists("x"), lambda: strict.run(["x"]),
                     lambda: strict.run_foreground(["x"]), lambda: strict.popen(["x"]),
                     lambda: strict.open_url("x"), lambda: strict.sleep(1)):
            with self.assertRaises(AssertionError):
                call()
        self.assertNotIn(Runner, FakeRunner.__mro__, "the fake must never inherit the real runner")


class RealRunnerTests(LaunchGuardMixin, TempDirTestCase):
    """The real Runner's browser method (run only against this Python, never a browser)."""

    allowed_programs = (sys.executable,)

    def test_foreground_discards_output_and_returns_the_result(self):
        result = Runner().run_foreground([sys.executable, "-c", "print('x' * 100000); import sys; sys.exit(3)"])
        self.assertEqual(result.returncode, 3)
        self.assertIsNone(result.stdout)
        self.assertIsNone(result.stderr)
        self.assertTrue(self.guard.started and all("'" + sys.executable + "'" in c for c in self.guard.started),
                        self.guard.started)

    def test_foreground_raises_oserror_for_a_missing_program(self):
        with self.assertRaises(OSError):
            Runner().run_foreground([str(self.tmp / "no-such-browser")])


class RealChildTests(LaunchGuardMixin, TempDirTestCase):
    """Start the real server as a child process through ensure_server."""

    allowed_programs = (sys.executable,)

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
        self.assertTrue(self.guard.started and all("'" + sys.executable + "'" in c for c in self.guard.started),
                        self.guard.started)
        again = launch.ensure_server(settings, "slideshow", port, Runner())
        self.assertIsNone(again, "a second launcher reuses the running server")
        launch.stop_child(child)
        self.assertIsNone(launch.probe_existing(port))


if __name__ == "__main__":
    unittest.main()
