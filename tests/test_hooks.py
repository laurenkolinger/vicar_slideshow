"""Module: tests.test_hooks
Purpose: Test the two git hooks in scratch git repositories: every block of the pre-commit
    hook (including a non-ASCII 3 MB video name) and one allowed commit, and the places the
    pre-push hook will and will not take its logger from.
Inputs: The hooks in .githooks/; scratch repositories under a temporary folder.
Outputs: Test results.
"""

import os
import shutil
import subprocess
import unittest

from tests.helpers import REPO, TempDirTestCase, make_png

HOOKS = REPO / ".githooks"
BASH = shutil.which("bash")
GIT = shutil.which("git")
MEGABYTE = 1024 * 1024
BIG_FILE_MB = 96  # over the 95 MB cap; written sparse so it costs no disk


@unittest.skipUnless(BASH and GIT, "bash and git are needed to run the hooks")
class PreCommitTests(TempDirTestCase):
    """The pre-commit hook, run by a real `git commit` in a scratch repository."""

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            [GIT, "-c", "user.name=Tester", "-c", "user.email=tester@example.org", "-c", f"core.hooksPath={HOOKS}",
             *args], cwd=self.work, capture_output=True, text=True, env=env, timeout=120)

    def write(self, rel, size):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(size)
        return path

    def commit(self, rel, size):
        """Stage a file of `size` bytes and commit it; return the commit result."""
        self.write(rel, size)
        self.assertEqual(self.git("add", "-f", "--", rel).returncode, 0)
        return self.git("commit", "-q", "-m", "test")

    def assert_blocked(self, rel, size, label):
        result = self.commit(rel, size)
        self.assertEqual(result.returncode, 1, f"{rel}: {result.stdout}{result.stderr}")
        self.assertIn(label, result.stderr)
        self.assertIn(rel, result.stderr)

    def test_hook_has_no_bash4_only_syntax_and_reads_nul_separated_names(self):
        text = (HOOKS / "pre-commit").read_text(encoding="utf-8")
        self.assertNotIn(",,", text, "lower-casing with ${f,,} needs bash 4; macOS ships bash 3.2")
        self.assertIn("-z", text)
        self.assertIn("read -r -d ''", text)
        self.assertIn("core.quotePath=false", text)

    def test_small_example_image_is_allowed(self):
        (self.work / "example_content").mkdir()
        (self.work / "example_content" / "01_x.png").write_bytes(make_png())
        self.git("add", "example_content")
        result = self.git("commit", "-q", "-m", "ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-list", "--count", "HEAD").stdout.strip(), "1")

    def test_blocks_media_folder_thumbs_and_cache(self):
        for rel in ("slideshow/slideshow_media/a.png", "slideshow/_thumbs/a.jpg", ".cache/thumbs/a.jpg",
                    "deep/slideshow_media/a.png"):
            self.assert_blocked(rel, 10, "[content or cache dir]")
            self.git("reset", "-q")

    def test_blocks_machine_settings(self):
        self.assert_blocked("settings.local.json", 10, "[machine settings]")

    def test_blocks_file_over_95_mb(self):
        self.assert_blocked("big.bin", BIG_FILE_MB * MEGABYTE, "[over 95 MB]")

    def test_blocks_image_or_video_over_2_mb(self):
        self.assert_blocked("clip.mp4", 3 * MEGABYTE, "[image or video over 2 MB]")

    def test_blocks_upper_case_extension(self):
        self.assert_blocked("CLIP.MOV", 3 * MEGABYTE, "[image or video over 2 MB]")

    def test_blocks_non_ascii_video_name(self):
        for rel in ("café récif 日本.mp4", "naïve.MP4"):
            self.assert_blocked(rel, 3 * MEGABYTE, "[image or video over 2 MB]")
            self.git("reset", "-q")

    def test_allows_non_ascii_small_file(self):
        result = self.commit("café.png", 1000)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(BASH and GIT, "bash and git are needed to run the hooks")
class PrePushTests(TempDirTestCase):
    """The pre-push hook finds its logger only in a VICARIUS checkout."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "checkout"
        self.repo = self.root / "vicarius" / "modules" / "m" / "github_repo"
        self.repo.mkdir(parents=True)
        subprocess.run([GIT, "init", "-q"], cwd=self.repo, check=True)
        self.marker = self.tmp / "marker"
        lib = self.root / ".githooks" / "lib"
        lib.mkdir(parents=True)
        (lib / "modules-push-log.sh").write_text(f'echo sourced > "{self.marker}"\n')
        self.hook = HOOKS / "pre-push"

    def run_hook(self, **env):
        base = {k: v for k, v in os.environ.items() if k != "VICARIUS_ROOT"}
        base.update(env)
        return subprocess.run([BASH, str(self.hook)], cwd=self.repo, capture_output=True, text=True, env=base,
                              timeout=60)

    def test_parent_folder_without_the_checkout_signature_is_not_trusted(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists(), "code from a stranger's parent folder ran")
        self.assertIn("push not logged", result.stderr)

    def test_parent_folder_with_the_checkout_signature_is_used(self):
        (self.root / "vicarius_ui_os").mkdir()
        (self.root / "vicarius_ui_os" / "app.py").write_text("")
        result = self.run_hook()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.marker.exists())

    def test_vicarius_root_is_used_when_set(self):
        result = self.run_hook(VICARIUS_ROOT=str(self.root))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.marker.exists())

    def test_vicarius_root_without_the_logger_falls_back_to_the_signature_rule(self):
        result = self.run_hook(VICARIUS_ROOT=str(self.tmp / "elsewhere"))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
