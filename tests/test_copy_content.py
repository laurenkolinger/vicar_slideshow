"""Module: tests.test_copy_content
Purpose: Test the copy tool: planning, conflicts, replace, space, verification, logging.
Inputs: Temporary source and destination folders.
Outputs: Test results.
"""

import csv
import errno
import hashlib
import os
import threading
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

from tests.helpers import TempDirTestCase, make_content, make_png
from vicar_showcase import copy_content as cc
from vicar_showcase.copy_content import CopyError, execute_plan, plan_copy


def sha(path) -> str:
    """Independent SHA-256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CopyTests(TempDirTestCase):
    """plan_copy and execute_plan."""

    def setUp(self):
        super().setUp()
        self.src = self.tmp / "src"; self.src.mkdir()
        self.dst = self.tmp / "dst"; self.dst.mkdir()
        make_content(self.src)
        (self.src / "x.heic").write_bytes(b"h")

    def rows(self):
        with open(self.dst / cc.LOG_NAME, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def run_copy(self, **kw):
        plan = plan_copy(self.src, self.dst, **kw)
        return plan, execute_plan(plan, run_by="tester", out=lambda s: None)

    def test_plan_matches_scan_plus_captions(self):
        plan = plan_copy(self.src, self.dst)
        self.assertEqual([i.name for i in plan.items],
                         ["01_alpha.png", "02-beta.png", "10_gamma.png", "captions.csv"])
        self.assertEqual([n for n, _ in plan.skipped], ["x.heic"])
        self.assertEqual(os.listdir(self.dst), [])

    def test_copy_verified_independently(self):
        plan, result = self.run_copy()
        self.assertEqual((result.copied, result.failed, result.skipped), (4, 0, 1))
        for item in plan.items:
            self.assertEqual(sha(self.dst / item.name), sha(self.src / item.name))
        self.assertFalse((self.dst / "x.heic").exists())
        self.assertEqual([n for n in os.listdir(self.dst) if n.endswith(".partial")], [])

    def test_log_rows(self):
        self.run_copy()
        rows = self.rows()
        self.assertEqual(list(rows[0].keys()), list(cc.LOG_COLUMNS))
        copied = [r for r in rows if r["action"] == "copied"]
        self.assertEqual(len(copied), 4)
        self.assertTrue(all(r["run_by"] == "tester" and r["source_dir"] == str(self.src) for r in rows))
        self.assertTrue(all(r["timestamp"].endswith("-04:00") for r in rows))
        row = next(r for r in copied if r["file"] == "01_alpha.png")
        self.assertEqual(row["sha256"], sha(self.src / "01_alpha.png"))
        self.assertEqual(int(row["bytes"]), (self.src / "01_alpha.png").stat().st_size)
        self.assertEqual([r["file"] for r in rows if r["action"] == "skipped"], ["x.heic"])

    def test_second_run_is_already_there(self):
        self.run_copy()
        _, result = self.run_copy()
        self.assertEqual((result.copied, result.present), (0, 4))
        self.assertEqual(len([r for r in self.rows() if r["action"] == "already there"]), 4)

    def test_captions_from_explicit_path_written_under_setting_name(self):
        other = self.tmp / "other.csv"
        other.write_text("title,caption\nalpha,Special\n")
        (self.src / "captions.csv").unlink()
        self.run_copy(captions_path=other, captions_file="captions.csv")
        self.assertEqual(sha(self.dst / "captions.csv"), sha(other))

    def test_missing_captions_warns_but_copies_slides(self):
        (self.src / "captions.csv").unlink()
        plan, result = self.run_copy()
        self.assertEqual(result.copied, 3)
        self.assertTrue(plan.warnings)

    def test_conflict_refuses_everything(self):
        (self.dst / "02-beta.png").write_bytes(b"different")
        (self.dst / "captions.csv").write_text("other")
        plan = plan_copy(self.src, self.dst)
        with self.assertRaises(CopyError) as ctx:
            execute_plan(plan, out=lambda s: None)
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertIn("02-beta.png", str(ctx.exception))
        self.assertIn("captions.csv", str(ctx.exception))
        self.assertEqual(sorted(os.listdir(self.dst)), ["02-beta.png", "captions.csv"])
        self.assertEqual((self.dst / "02-beta.png").read_bytes(), b"different")

    def test_replace_moves_old_to_superseded(self):
        (self.dst / "02-beta.png").write_bytes(b"different")
        plan, result = self.run_copy(replace=True)
        self.assertEqual(result.replaced, 1)
        self.assertEqual(sha(self.dst / "02-beta.png"), sha(self.src / "02-beta.png"))
        moved = list((self.dst / "_superseded").glob("*/02-beta.png"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_bytes(), b"different")
        self.assertRegex(moved[0].parent.name, r"^\d{8}-\d{6}$")
        self.assertEqual(len([r for r in self.rows() if r["action"] == "replaced"]), 1)

    def test_replace_of_a_file_that_vanished_is_logged_as_copied(self):
        (self.dst / "02-beta.png").write_bytes(b"different")
        plan = plan_copy(self.src, self.dst, replace=True)
        (self.dst / "02-beta.png").unlink()  # removed by someone else after planning
        result = execute_plan(plan, run_by="tester", out=lambda s: None)
        self.assertEqual((result.replaced, result.copied, result.failed), (0, 4, 0))
        actions = {r["file"]: r["action"] for r in self.rows()}
        self.assertEqual(actions["02-beta.png"], "copied")
        self.assertEqual(list((self.dst / "_superseded").glob("*/*")), [])
        self.assertEqual(sha(self.dst / "02-beta.png"), sha(self.src / "02-beta.png"))

    def test_failed_rename_without_hard_links_removes_the_empty_placeholder(self):
        plan = plan_copy(self.src, self.dst)
        real_replace = os.replace

        def refuse_final(src, dst):
            if Path(dst).name == "01_alpha.png":
                raise OSError(errno.EIO, "disk error")
            return real_replace(src, dst)

        with mock.patch.object(cc.os, "link", side_effect=OSError(errno.ENOTSUP, "no hard links")), \
                mock.patch.object(cc.os, "replace", side_effect=refuse_final):
            result = execute_plan(plan, run_by="tester", out=lambda s: None)
        self.assertEqual(result.failed, 1)
        self.assertFalse((self.dst / "01_alpha.png").exists(), "no 0-byte placeholder is left behind")
        self.assertEqual([n for n in os.listdir(self.dst) if n.endswith(".partial")], [])
        self.assertEqual(sha(self.dst / "02-beta.png"), sha(self.src / "02-beta.png"))

    def test_a_file_that_replaced_the_placeholder_is_not_removed(self):
        final = self.dst / "01_alpha.png"
        partial = self.dst / "x.partial"
        partial.write_bytes(b"data")

        def someone_else_wins(src, dst):
            Path(dst).unlink()
            Path(dst).write_bytes(b"theirs")
            raise OSError(errno.EIO, "disk error")

        with mock.patch.object(cc.os, "link", side_effect=OSError(errno.ENOTSUP, "no hard links")), \
                mock.patch.object(cc.os, "replace", side_effect=someone_else_wins):
            with self.assertRaises(OSError):
                cc._publish_without_overwrite(partial, final)
        self.assertEqual(final.read_bytes(), b"theirs")

    def test_dest_must_exist_and_is_not_created(self):
        with self.assertRaises(CopyError) as ctx:
            plan_copy(self.src, self.tmp / "nowhere")
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertFalse((self.tmp / "nowhere").exists())

    def test_source_must_exist(self):
        with self.assertRaises(CopyError) as ctx:
            plan_copy(self.tmp / "nowhere", self.dst)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_destination_inside_source_refused(self):
        inside = self.src / "copy_here"; inside.mkdir()
        with self.assertRaises(CopyError) as ctx:
            plan_copy(self.src, inside)
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_source_inside_destination_and_same_folder_refused(self):
        for a, b in ((self.src, self.src), (self.src, self.tmp)):
            with self.assertRaises(CopyError) as ctx:
                plan_copy(a, b)
            self.assertEqual(ctx.exception.exit_code, 3)

    def test_symlinked_destination_into_source_refused(self):
        inside = self.src / "d"; inside.mkdir()
        link = self.tmp / "link"; os.symlink(inside, link)
        with self.assertRaises(CopyError):
            plan_copy(self.src, link)

    def test_insufficient_space(self):
        usage = namedtuple("usage", "total used free")
        with mock.patch.object(cc.shutil, "disk_usage", return_value=usage(10, 10, 5)):
            with self.assertRaisesRegex(CopyError, "not enough free space") as ctx:
                plan_copy(self.src, self.dst)
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertEqual(os.listdir(self.dst), [])

    def test_space_margin_is_five_percent(self):
        total = sum(i.size for i in plan_copy(self.src, self.dst).items)
        usage = namedtuple("usage", "total used free")
        with mock.patch.object(cc.shutil, "disk_usage", return_value=usage(0, 0, int(total * 1.05))):
            plan_copy(self.src, self.dst)
        with mock.patch.object(cc.shutil, "disk_usage", return_value=usage(0, 0, int(total * 1.04))):
            with self.assertRaises(CopyError):
                plan_copy(self.src, self.dst)

    def test_hash_mismatch_fails_and_removes_only_partial(self):
        (self.dst / "keep.txt").write_text("mine")
        real = cc.sha256_file
        with mock.patch.object(cc, "sha256_file", side_effect=lambda p: "0" * 64 if str(p).endswith(".partial") else real(p)):
            plan = plan_copy(self.src, self.dst)
            result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual(result.failed, 4)
        self.assertEqual(result.copied, 0)
        self.assertEqual(sorted(os.listdir(self.dst)), ["_showcase_copy_log.csv", "keep.txt"])
        self.assertEqual(len([r for r in self.rows() if r["action"] == "failed"]), 4)
        self.assertIn("verification failed", result.failures[0])

    def test_failed_replace_keeps_old_file(self):
        (self.dst / "02-beta.png").write_bytes(b"different")
        real = cc.sha256_file
        with mock.patch.object(cc, "sha256_file", side_effect=lambda p: "0" * 64 if str(p).endswith(".partial") else real(p)):
            plan = plan_copy(self.src, self.dst, replace=True)
            execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((self.dst / "02-beta.png").read_bytes(), b"different")
        self.assertFalse((self.dst / "_superseded").exists())

    def test_dry_run_writes_nothing(self):
        plan = plan_copy(self.src, self.dst)
        lines = cc.describe_plan(plan)
        self.assertTrue(any("will copy: 01_alpha.png" in l for l in lines))
        self.assertTrue(any("not copied: x.heic" in l for l in lines))
        self.assertEqual(os.listdir(self.dst), [])

    def test_unreadable_source_file_fails_that_file_only(self):
        real_open = open
        def fake_open(path, *a, **k):
            if str(path).endswith("02-beta.png") and "rb" in a:
                raise PermissionError(13, "Permission denied")
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", side_effect=fake_open):
            result = execute_plan(plan_copy(self.src, self.dst), run_by="t", out=lambda s: None)
        self.assertEqual((result.copied, result.failed), (3, 1))
        self.assertFalse((self.dst / "02-beta.png").exists())

    def appear_after_copy(self, name, content=b"someone else"):
        """Patch the copy step so `name` appears in the destination after planning, before it is placed."""
        real = cc._copy_hashing

        def racing(src, partial):
            digest = real(src, partial)
            if Path(src).name == name:
                (self.dst / name).write_bytes(content)
            return digest

        return mock.patch.object(cc, "_copy_hashing", side_effect=racing)

    def test_file_that_appears_after_planning_is_not_overwritten(self):
        plan = plan_copy(self.src, self.dst)
        with self.appear_after_copy("02-beta.png"):
            result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.conflicts, result.copied, result.failed), (1, 3, 0))
        self.assertEqual((self.dst / "02-beta.png").read_bytes(), b"someone else")
        self.assertEqual(sha(self.dst / "10_gamma.png"), sha(self.src / "10_gamma.png"), "the rest still copied")
        self.assertEqual([n for n in os.listdir(self.dst) if n.endswith(cc.PARTIAL_SUFFIX)], [])
        self.assertEqual([r["file"] for r in self.rows() if r["action"] == "conflict"], ["02-beta.png"])

    def test_case_variant_name_counts_as_a_conflict(self):
        """On a case-insensitive folder os.link fails because A.jpg is a.jpg; simulate that failure."""
        plan = plan_copy(self.src, self.dst)
        real_link = os.link

        def link(src, dst, **kw):
            if Path(dst).name == "10_gamma.png":
                raise FileExistsError(errno.EEXIST, "File exists", str(dst))
            return real_link(src, dst, **kw)

        with mock.patch.object(cc.os, "link", side_effect=link):
            result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.conflicts, result.copied), (1, 3))
        self.assertFalse((self.dst / "10_gamma.png").exists())

    def test_without_hard_links_the_exclusive_create_guard_still_copies(self):
        no_links = OSError(errno.EPERM, "links not supported")
        with mock.patch.object(cc.os, "link", side_effect=no_links):
            _, result = self.run_copy()
        self.assertEqual((result.copied, result.conflicts, result.failed), (4, 0, 0))
        self.assertEqual(sha(self.dst / "01_alpha.png"), sha(self.src / "01_alpha.png"))
        self.assertEqual([n for n in os.listdir(self.dst) if n.endswith(cc.PARTIAL_SUFFIX)], [])

    def test_without_hard_links_a_file_that_appeared_is_still_not_overwritten(self):
        plan = plan_copy(self.src, self.dst)
        no_links = OSError(errno.EPERM, "links not supported")
        with mock.patch.object(cc.os, "link", side_effect=no_links), self.appear_after_copy("01_alpha.png"):
            result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.conflicts, result.copied), (1, 3))
        self.assertEqual((self.dst / "01_alpha.png").read_bytes(), b"someone else")

    def test_present_file_that_vanishes_is_a_failure_not_a_crash(self):
        self.run_copy()
        plan = plan_copy(self.src, self.dst)
        (self.dst / "01_alpha.png").unlink()
        result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.failed, result.present), (1, 3))
        self.assertIn("01_alpha.png", result.failures[0])
        self.assertEqual([r["file"] for r in self.rows() if r["action"] == "failed"], ["01_alpha.png"])

    def test_conflict_file_that_vanishes_before_replace_is_copied(self):
        (self.dst / "02-beta.png").write_bytes(b"different")
        plan = plan_copy(self.src, self.dst, replace=True)
        (self.dst / "02-beta.png").unlink()
        result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.failed, result.conflicts), (0, 0))
        self.assertEqual(sha(self.dst / "02-beta.png"), sha(self.src / "02-beta.png"))

    def test_captions_path_that_is_not_a_file_fails_before_anything_is_copied(self):
        for bad in (self.tmp / "no-such.csv", self.tmp):
            with self.assertRaises(CopyError) as ctx:
                plan_copy(self.src, self.dst, captions_path=bad)
            self.assertEqual(ctx.exception.exit_code, 2)
            self.assertIn(str(bad), str(ctx.exception))
        self.assertEqual(os.listdir(self.dst), [])

    def test_long_file_name_copies(self):
        name = "9_" + "a" * 244 + ".png"
        self.assertEqual(len(name.encode()), 250)
        (self.src / name).write_bytes(make_png())
        _, result = self.run_copy()
        self.assertEqual((result.failed, result.copied), (0, 5))
        self.assertEqual(sha(self.dst / name), sha(self.src / name))

    def test_partial_name_is_short_and_unique(self):
        names = {cc._partial_path(self.dst).name for _ in range(5)}
        self.assertEqual(len(names), 5)
        self.assertTrue(all(len(n) < 40 for n in names))

    def test_failure_to_remove_the_temporary_file_is_logged_and_keeps_the_result(self):
        plan = plan_copy(self.src, self.dst)
        real_unlink = Path.unlink

        def unlink(self_path, *a, **k):
            if self_path.name.startswith(cc.PARTIAL_PREFIX):
                raise PermissionError(errno.EACCES, "Permission denied")
            return real_unlink(self_path, *a, **k)

        with mock.patch.object(Path, "unlink", unlink), self.assertLogs(cc.__name__, "WARNING") as logs:
            result = execute_plan(plan, run_by="t", out=lambda s: None)
        self.assertEqual((result.copied, result.failed), (4, 0))
        self.assertIn("could not remove the temporary file", logs.output[0])
        for leftover in self.dst.glob(cc.PARTIAL_PREFIX + "*"):
            leftover.unlink()

    def test_concurrent_log_appends_keep_one_header(self):
        log = cc.CopyLog(self.dst, self.src, "t")
        threads = [threading.Thread(target=log.write, args=("copied", f"f{i}", 1, "x")) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.rows()), 20)

    def test_unwritable_log_is_an_error(self):
        log = cc.CopyLog(self.tmp / "nowhere", self.src, "t")
        with self.assertRaises(CopyError):
            log.write("copied", "f")

    def test_hostile_names_copy(self):
        name = "9_it's \"q\" <b>&.png"
        (self.src / name).write_bytes(make_png())
        self.run_copy()
        self.assertEqual(sha(self.dst / name), sha(self.src / name))


if __name__ == "__main__":
    unittest.main()
