"""Module: tests.test_cli
Purpose: Test the command line in-process and as subprocesses (check, copy-content, errors,
    exit codes, the Python version guard and the two .command wrappers).
Inputs: Temporary repositories and content folders.
Outputs: Test results.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import REPO, FFMPEG, TempDirTestCase, make_content, make_png, make_repo, make_video
from vicar_showcase import cli


def run_main(argv, repo):
    """Run cli.main in-process; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv, repo_dir=repo)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def sha(path) -> str:
    """Independent SHA-256."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CliInProcessTests(TempDirTestCase):
    """cli.main with a temporary repository."""

    def setUp(self):
        super().setUp()
        self.content = self.tmp / "content"; self.content.mkdir()
        make_content(self.content)
        (self.content / "x.heic").write_bytes(b"h")
        self.repo = make_repo(self.tmp, self.content)

    def test_check_text(self):
        code, out, err = run_main(["check"], self.repo)
        self.assertEqual((code, err), (0, ""))
        for text in (f"Content folder: {self.content}", "Chosen by: settings.json", "Videos: 0", "Images: 3",
                     "Slides: 3", "alpha  [image, caption]", "x.heic: file type .heic is not shown", "Warnings:", "Not shown:"):
            self.assertIn(text, out)

    def test_check_text_reports_missing_caption(self):
        (self.content / "04_nocap.png").write_bytes(make_png())
        self.assertIn("nocap  [image, NO CAPTION]", run_main(["check"], self.repo)[1])

    def test_check_json(self):
        code, out, _ = run_main(["check", "--json"], self.repo)
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["repo_dir"], str(self.repo))
        self.assertEqual(data["content_dir_source"], "settings.json")
        self.assertEqual(data["thumbs_dir"], str(self.repo / ".cache" / "thumbs"))
        self.assertIn(".mp4", data["supported_extensions"])
        self.assertEqual(len(data["slides"]), 3)
        self.assertTrue(all("thumb_path" in s for s in data["slides"]))
        self.assertEqual(data["skipped"][0]["file"], "x.heic")

    def test_check_json_thumb_path_for_cached_video(self):
        from vicar_showcase import thumbs
        video = self.content / "04_clip.mp4"; video.write_bytes(b"v")
        cache = thumbs.thumb_cache_path(self.repo / ".cache" / "thumbs", video)
        cache.parent.mkdir(parents=True); cache.write_bytes(b"\xff\xd8")
        slide = next(s for s in json.loads(run_main(["check", "--json"], self.repo)[1])["slides"] if s["kind"] == "video")
        self.assertEqual(slide["thumb_path"], str(cache))

    def test_check_content_override_and_source(self):
        other = self.tmp / "other"; other.mkdir(); (other / "1_a.png").write_bytes(make_png())
        code, out, _ = run_main(["check", "--content", str(other)], self.repo)
        self.assertIn("Chosen by: --content flag", out)
        self.assertIn("Slides: 1", out)

    def test_check_missing_folder_exit_2_json_still_printed(self):
        code, out, _ = run_main(["check", "--json", "--content", str(self.tmp / "nowhere")], self.repo)
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["exists"])
        code, out, _ = run_main(["check", "--content", str(self.tmp / "nowhere")], self.repo)
        self.assertEqual(code, 2)
        self.assertIn("Problem: content folder not found", out)

    def test_settings_error_exit_1_no_traceback(self):
        (self.repo / "settings.json").write_text("{bad")
        code, out, err = run_main(["check"], self.repo)
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("showcase: error: "))
        self.assertNotIn("Traceback", err)

    def test_usage_errors_exit_1(self):
        for argv in ([], ["bogus"], ["serve"], ["serve", "--view", "x"], ["screensaver", "--port", "0"],
                     ["slideshow", "--port", "abc"], ["screensaver", "--port", "70000"], ["copy-content"]):
            code, _, err = run_main(argv, self.repo)
            self.assertEqual(code, 1, argv)
            self.assertIn("showcase: error:", err)

    def test_help_exits_0(self):
        code, out, _ = run_main(["--help"], self.repo)
        self.assertEqual(code, 0)
        for name in ("screensaver", "slideshow", "serve", "check", "copy-content", "make-thumbs"):
            self.assertIn(name, out)

    def test_default_ports(self):
        self.assertEqual((cli.VIEW_PORTS["screensaver"], cli.VIEW_PORTS["slideshow"]), (8766, 8765))
        args = cli.build_parser().parse_args(["screensaver"])
        self.assertEqual(args.port, 8766)
        self.assertEqual(cli.build_parser().parse_args(["slideshow"]).port, 8765)

    def test_screensaver_with_missing_folder_exit_2(self):
        with mock.patch.object(cli, "run_view") as view:
            code, _, err = run_main(["screensaver", "--content", str(self.tmp / "nowhere")], self.repo)
        self.assertEqual(code, 2)
        self.assertIn("content folder not found", err)
        view.assert_not_called()

    def test_slideshow_calls_run_view(self):
        with mock.patch.object(cli, "run_view", return_value=0) as view:
            code, _, _ = run_main(["slideshow", "--port", "9999"], self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(view.call_args[0][1:], ("slideshow", 9999))

    def test_serve_port_in_use_is_error_not_traceback(self):
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); sock.listen(1)
            port = sock.getsockname()[1]
            code, _, err = run_main(["serve", "--view", "slideshow", "--port", str(port)], self.repo)
        self.assertEqual(code, 1)
        self.assertIn("cannot serve on port", err)

    def test_make_thumbs(self):
        (self.content / "04_clip.mp4").write_bytes(b"v")
        with mock.patch.object(cli, "make_all_thumbs", return_value={"videos": 1, "cached": 0, "made": 1, "failed": 0,
                                                                     "ffmpeg_missing": 0}) as mk:
            code, out, _ = run_main(["make-thumbs"], self.repo)
        self.assertEqual(code, 0)
        self.assertIn("made: 1", out)
        self.assertEqual(mk.call_args[0][1], self.repo / ".cache" / "thumbs")

    def test_make_thumbs_without_ffmpeg_is_friendly(self):
        with mock.patch.object(cli, "make_all_thumbs", return_value={"videos": 2, "cached": 0, "made": 0, "failed": 2,
                                                                     "ffmpeg_missing": 1}):
            code, out, _ = run_main(["make-thumbs"], self.repo)
        self.assertEqual(code, 0)
        self.assertIn("ffmpeg is not installed", out)

    def test_make_thumbs_missing_folder(self):
        self.assertEqual(run_main(["make-thumbs", "--content", str(self.tmp / "no")], self.repo)[0], 2)

    def test_copy_dry_run_and_real_default_destination(self):
        src = self.tmp / "src"; src.mkdir(); make_content(src)
        dest = self.tmp / "dest"; dest.mkdir()
        argv = ["copy-content", "--from", str(src), "--content", str(dest)]
        code, out, _ = run_main(argv + ["--dry-run"], self.repo)
        self.assertEqual(code, 0)
        self.assertIn("Dry run: nothing was written.", out)
        self.assertEqual(os.listdir(dest), [])
        code, out, _ = run_main(argv, self.repo)
        self.assertEqual(code, 0)
        self.assertIn("Step 1 complete", out); self.assertIn("Step 2 complete", out); self.assertIn("Step 3 complete", out)
        self.assertIn("Slides shown: source 3, destination 3", out)
        for name in ("01_alpha.png", "02-beta.png", "10_gamma.png", "captions.csv"):
            self.assertEqual(sha(dest / name), sha(src / name))
        self.assertTrue((dest / "_showcase_copy_log.csv").is_file())

    def test_copy_conflict_exit_3_and_replace(self):
        src = self.tmp / "src"; src.mkdir(); make_content(src)
        dest = self.tmp / "dest"; dest.mkdir(); (dest / "01_alpha.png").write_bytes(b"old")
        argv = ["copy-content", "--from", str(src), "--to", str(dest)]
        code, _, err = run_main(argv, self.repo)
        self.assertEqual(code, 3)
        self.assertIn("01_alpha.png", err)
        self.assertEqual(os.listdir(dest), ["01_alpha.png"])
        code, out, _ = run_main(argv + ["--replace"], self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(sha(dest / "01_alpha.png"), sha(src / "01_alpha.png"))
        self.assertEqual(len(list((dest / "_superseded").glob("*/01_alpha.png"))), 1)

    def test_copy_destination_missing_exit_2(self):
        src = self.tmp / "src"; src.mkdir(); make_content(src)
        code, _, err = run_main(["copy-content", "--from", str(src), "--to", str(self.tmp / "nope")], self.repo)
        self.assertEqual(code, 2)
        self.assertFalse((self.tmp / "nope").exists())

    def test_copy_failure_exit_4(self):
        src = self.tmp / "src"; src.mkdir(); make_content(src)
        dest = self.tmp / "dest"; dest.mkdir()
        from vicar_showcase import copy_content as cc
        real = cc.sha256_file
        with mock.patch.object(cc, "sha256_file", side_effect=lambda p: "0" * 64 if str(p).endswith(".partial") else real(p)):
            code, _, err = run_main(["copy-content", "--from", str(src), "--to", str(dest)], self.repo)
        self.assertEqual(code, 4)
        self.assertIn("verification failed", err)

    def test_copy_explicit_captions(self):
        src = self.tmp / "src"; src.mkdir(); make_content(src)
        (src / "captions.csv").unlink()
        cap = self.tmp / "legacy.csv"; cap.write_text("title,caption\nalpha,Legacy\n")
        dest = self.tmp / "dest"; dest.mkdir()
        code, _, _ = run_main(["copy-content", "--from", str(src), "--to", str(dest), "--captions", str(cap)], self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(sha(dest / "captions.csv"), sha(cap))


class CliSubprocessTests(TempDirTestCase):
    """The real script as a subprocess, in a temporary repository copy."""

    def setUp(self):
        super().setUp()
        self.content = self.tmp / "content"; self.content.mkdir()
        make_content(self.content)
        self.repo = make_repo(self.tmp, self.content)

    def sh(self, *args, env=None, cwd=None):
        base = {k: v for k, v in os.environ.items() if k != "VICAR_SHOWCASE_CONTENT"}
        base.update(env or {})
        return subprocess.run([sys.executable, str(self.repo / "showcase.py"), *args], capture_output=True, text=True,
                              cwd=cwd or self.tmp, env=base, timeout=60)

    def test_check_json_end_to_end_with_real_video(self):
        if FFMPEG:
            self.assertTrue(make_video(self.content / "04_clip.mp4"))
        result = self.sh("check", "--json", "--content", str(self.content))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["content_dir_source"], "--content flag")
        self.assertEqual(len(data["slides"]), 4 if FFMPEG else 3)

    def test_env_override(self):
        other = self.tmp / "envdir"; other.mkdir(); (other / "1_a.png").write_bytes(make_png())
        data = json.loads(self.sh("check", "--json", env={"VICAR_SHOWCASE_CONTENT": str(other)}).stdout)
        self.assertEqual((data["content_dir_source"], len(data["slides"])), ("VICAR_SHOWCASE_CONTENT", 1))

    def test_copy_content_end_to_end(self):
        dest = self.tmp / "dest"; dest.mkdir()
        result = self.sh("copy-content", "--from", str(self.content), "--to", str(dest))
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in os.listdir(self.content):
            self.assertEqual(sha(dest / name), sha(self.content / name))
        self.assertIn("Slides shown: source 3, destination 3", result.stdout)
        again = self.sh("copy-content", "--from", str(self.content), "--to", str(dest))
        self.assertIn("already there: 01_alpha.png", again.stdout)

    def test_expected_failures_have_no_traceback(self):
        (self.repo / "settings.json").write_text("[]")
        for args, code in ((("check",), 1), (("serve",), 1), (("bogus",), 1)):
            result = self.sh(*args)
            self.assertEqual(result.returncode, code, args)
            self.assertNotIn("Traceback", result.stderr)
        (self.repo / "settings.json").unlink()
        result = self.sh("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("repository is incomplete", result.stderr)

    def test_missing_content_exit_2(self):
        result = self.sh("check", "--content", str(self.tmp / "gone"))
        self.assertEqual(result.returncode, 2)

    def test_python_version_guard(self):
        code = ("import sys, runpy; sys.version_info = (3, 9, 18, 'final', 0); "
                f"runpy.run_path({str(self.repo / 'showcase.py')!r}, run_name='__main__')")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.10 or newer is required, this is Python 3.9", result.stderr)

    def test_serve_subprocess_answers_and_stops(self):
        from tests.helpers import free_port, request
        import time
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(self.repo / "showcase.py"), "serve", "--view", "screensaver",
                                 "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=self.tmp)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        status = None
        for _ in range(100):
            try:
                status = request(port, "GET", "/_manifest.json")
                break
            except OSError:
                time.sleep(0.05)
        self.assertEqual(status[0], 200)
        self.assertEqual(len(json.loads(status[2])["slides"]), 3)
        self.assertEqual(request(port, "GET", "/")[1]["location"], "/screensaver.html")
        proc.terminate()
        proc.wait(timeout=10)


class WrapperTests(TempDirTestCase):
    """The two .command wrappers, with a stub python3."""

    def stub_bin(self, python_ok=True):
        bindir = self.tmp / "bin"; bindir.mkdir()
        stub = bindir / "python3"
        stub.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit %d; fi\necho "cwd=$(pwd) args=$*"\n' % (0 if python_ok else 1))
        stub.chmod(0o755)
        for tool in ("dirname",):
            shutil.copy(shutil.which(tool), bindir / tool)
        return bindir

    def run_wrapper(self, name, bindir, *args):
        return subprocess.run(["/bin/bash", str(REPO / "slideshow" / name), *args], capture_output=True, text=True,
                              env={"PATH": str(bindir), "HOME": str(self.tmp)}, cwd=self.tmp, stdin=subprocess.DEVNULL)

    def test_wrappers_are_executable_and_set_e(self):
        for name in ("start-screensaver.command", "start-slideshow.command"):
            path = REPO / "slideshow" / name
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, name)
            text = path.read_text()
            self.assertTrue(text.startswith("#!/bin/bash"))
            self.assertIn("\nset -e\n", text)
            self.assertNotIn("http.server", text)

    def test_execs_showcase_from_repo_root_with_args(self):
        bindir = self.stub_bin()
        for name, view in (("start-screensaver.command", "screensaver"), ("start-slideshow.command", "slideshow")):
            result = self.run_wrapper(name, bindir, "--content", "/x y")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"cwd={REPO}", result.stdout)
            self.assertIn(f"args=showcase.py {view} --content /x y", result.stdout)

    def test_old_python_is_refused(self):
        bindir = self.stub_bin(python_ok=False)
        for name in ("start-screensaver.command", "start-slideshow.command"):
            result = self.run_wrapper(name, bindir)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Python 3.10 or newer is required", result.stdout)

    def test_no_python_at_all(self):
        bindir = self.tmp / "bin"; bindir.mkdir()
        shutil.copy(shutil.which("dirname"), bindir / "dirname")
        result = self.run_wrapper("start-screensaver.command", bindir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.10 or newer is required", result.stdout)


if __name__ == "__main__":
    unittest.main()
