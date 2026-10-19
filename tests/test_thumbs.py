"""Module: tests.test_thumbs
Purpose: Test thumbnail caching and generation with a stub ffmpeg and (if installed) the real one.
Inputs: Temporary videos.
Outputs: Test results.
"""

import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import FFMPEG, TempDirTestCase, make_video
from vicar_showcase import thumbs
from vicar_showcase.content import ContentReport, Slide, scan_content


class StubFfmpeg:
    """A runner that records calls and writes a fake JPEG as the last argument."""

    def __init__(self, delay=0.0, code=0, write=True):
        self.calls = []
        self.delay, self.code, self.write = delay, code, write
        self.lock = threading.Lock()

    def __call__(self, command, **kwargs):
        with self.lock:
            self.calls.append(command)
        time.sleep(self.delay)
        if self.write:
            Path(command[-1]).write_bytes(b"\xff\xd8jpeg")
        return subprocess.CompletedProcess(command, self.code)


class ThumbTests(TempDirTestCase):
    """thumbs module."""

    def setUp(self):
        super().setUp()
        thumbs._failed_keys.clear()
        self.video = self.tmp / "1_clip.mp4"
        self.video.write_bytes(b"not really a video")
        self.cache = self.tmp / "cache"

    def test_cache_path_changes_with_size_and_mtime(self):
        a = thumbs.thumb_cache_path(self.cache, self.video)
        self.assertEqual(a.parent, self.cache)
        self.assertEqual(len(a.stem), 40)
        self.video.write_bytes(b"changed content here")
        self.assertNotEqual(a, thumbs.thumb_cache_path(self.cache, self.video))

    def test_cache_path_missing_video(self):
        with self.assertRaises(OSError):
            thumbs.thumb_cache_path(self.cache, self.tmp / "gone.mp4")

    def test_cached_thumb_does_not_generate(self):
        self.assertIsNone(thumbs.cached_thumb(self.cache, self.video))
        self.assertFalse(self.cache.exists())
        self.assertIsNone(thumbs.cached_thumb(self.cache, self.tmp / "gone.mp4"))

    def test_ensure_generates_with_expected_command(self):
        stub = StubFfmpeg()
        out = thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg")
        self.assertEqual(out, thumbs.thumb_cache_path(self.cache, self.video))
        self.assertTrue(out.is_file())
        cmd = stub.calls[0]
        for token in ("-ss", "0.5", "-vframes", "1", "scale=480:-2", "-q:v", "4", str(self.video)):
            self.assertIn(token, cmd)
        self.assertEqual(thumbs.cached_thumb(self.cache, self.video), out)
        self.assertEqual([p.name for p in self.cache.iterdir()], [out.name])

    def test_second_call_uses_cache(self):
        stub = StubFfmpeg()
        thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg")
        thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg")
        self.assertEqual(len(stub.calls), 1)

    def test_concurrent_requests_run_ffmpeg_once(self):
        stub = StubFfmpeg(delay=0.3)
        results = []
        def go():
            results.append(thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg"))
        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(len(set(results)), 1)
        self.assertIsNotNone(results[0])

    def test_ffmpeg_failure_returns_none_and_leaves_no_partial(self):
        stub = StubFfmpeg(code=1, write=True)
        self.assertIsNone(thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg"))
        self.assertEqual(list(self.cache.iterdir()), [])
        thumbs.ensure_thumb(self.cache, self.video, runner=stub, ffmpeg="ffmpeg")
        self.assertEqual(len(stub.calls), 1, "a failure is not retried on every request")

    def test_ffmpeg_timeout_returns_none(self):
        def hang(command, **kw):
            raise subprocess.TimeoutExpired(command, kw.get("timeout", 1))
        self.assertIsNone(thumbs.ensure_thumb(self.cache, self.video, runner=hang, ffmpeg="ffmpeg", timeout=1))

    def test_ffmpeg_not_executable(self):
        def boom(command, **kw):
            raise FileNotFoundError("ffmpeg")
        self.assertIsNone(thumbs.ensure_thumb(self.cache, self.video, runner=boom, ffmpeg="ffmpeg"))

    def test_ffmpeg_missing_returns_none(self):
        with mock.patch.object(thumbs.shutil, "which", return_value=None):
            self.assertIsNone(thumbs.ensure_thumb(self.cache, self.video, runner=StubFfmpeg()))
            report = ContentReport(self.tmp, self.tmp / "captions.csv", True,
                                   [Slide("1_clip.mp4", "video", 1, "clip", "", "", "", 18)])
            counts = thumbs.make_all_thumbs(report, self.cache)
        self.assertEqual(counts["ffmpeg_missing"], 1)
        self.assertEqual(counts["failed"], 1)

    def test_empty_output_is_a_failure(self):
        class Empty(StubFfmpeg):
            def __call__(self, command, **kw):
                Path(command[-1]).write_bytes(b"")
                return subprocess.CompletedProcess(command, 0)
        self.assertIsNone(thumbs.ensure_thumb(self.cache, self.video, runner=Empty(), ffmpeg="ffmpeg"))

    def test_make_all_counts(self):
        (self.tmp / "2_pic.png").write_bytes(b"png")
        (self.tmp / "3_other.mov").write_bytes(b"mov")
        report = scan_content(self.tmp)
        stub = StubFfmpeg()
        with mock.patch.object(thumbs.shutil, "which", return_value="/usr/bin/ffmpeg"):
            counts = thumbs.make_all_thumbs(report, self.cache, runner=stub)
            self.assertEqual((counts["videos"], counts["made"], counts["cached"], counts["failed"]), (2, 2, 0, 0))
            counts = thumbs.make_all_thumbs(report, self.cache, runner=stub)
        self.assertEqual((counts["made"], counts["cached"]), (0, 2))

    def test_default_dir(self):
        self.assertEqual(thumbs.default_thumbs_dir(Path("/r")), Path("/r/.cache/thumbs"))

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_real_ffmpeg(self):
        video = self.tmp / "real.mp4"
        self.assertTrue(make_video(video))
        out = thumbs.ensure_thumb(self.cache, video)
        self.assertIsNotNone(out)
        self.assertEqual(out.read_bytes()[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
