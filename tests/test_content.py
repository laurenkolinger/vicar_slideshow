"""Module: tests.test_content
Purpose: Test content scanning, ordering, captions parsing and safe file lookup.
Inputs: Temporary content folders.
Outputs: Test results.
"""

import json
import os
import sys
import unittest
from unittest import mock

from tests.helpers import CAPTIONS_HEADER, TempDirTestCase, make_content, make_png
from vicar_showcase.content import (
    CAPTIONS_MAX_BYTES, MEDIA_EXTENSIONS, NOT_UTF8_REASON, VIDEO_EXTENSIONS, _inside, classify_entry,
    load_captions, parse_media_name, resolve_media_file, scan_content,
)

UNDECODABLE_NAME = b"03_caf\xe9.png"


def make_undecodable(folder, data=None):
    """Create a file whose name is not valid UTF-8; return its name, or None when the platform refuses."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        path = os.path.join(os.fsencode(str(folder)), UNDECODABLE_NAME)
        with open(path, "wb") as handle:
            handle.write(make_png() if data is None else data)
    except OSError:
        return None
    return os.fsdecode(UNDECODABLE_NAME)


class ParseNameTests(unittest.TestCase):
    """parse_media_name."""

    def test_forms(self):
        self.assertEqual(parse_media_name("05_reef.mp4"), ("05", "reef"))
        self.assertEqual(parse_media_name("05-reef.mp4"), ("05", "reef"))
        self.assertEqual(parse_media_name("reef.mp4"), ("", "reef"))
        self.assertEqual(parse_media_name("a.b.png"), ("", "a.b"))
        self.assertEqual(parse_media_name("007_x_y.jpg"), ("007", "x_y"))
        self.assertEqual(parse_media_name("00_zero.jpg"), ("00", "zero"))
        self.assertEqual(parse_media_name("05_.jpg"), ("", "05_"))
        self.assertEqual(parse_media_name("noext"), ("", "noext"))

    def test_extension_sets(self):
        self.assertIn(".mov", VIDEO_EXTENSIONS)
        self.assertIn(".avif", MEDIA_EXTENSIONS)
        self.assertNotIn(".heic", MEDIA_EXTENSIONS)


class ScanTests(TempDirTestCase):
    """scan_content."""

    def setUp(self):
        super().setUp()
        self.c = self.tmp / "c"
        self.c.mkdir()

    def put(self, name, data=None):
        (self.c / name).write_bytes(make_png() if data is None else data)

    def skipped(self, report):
        return {s.file: s.reason for s in report.skipped}

    def test_happy_path(self):
        make_content(self.c)
        r = scan_content(self.c)
        self.assertTrue(r.exists)
        self.assertIsNone(r.error)
        self.assertEqual([s.file for s in r.slides], ["01_alpha.png", "02-beta.png", "10_gamma.png"])
        first = r.slides[0]
        self.assertEqual((first.number, first.title, first.caption, first.credit, first.chapter, first.kind),
                         ("01", "alpha", "First caption", "Credit A", "Intro", "image"))
        self.assertEqual(r.warnings, [])
        self.assertEqual(r.to_json()["content_name"], "c")

    def test_order_numeric_then_unnumbered_alpha(self):
        for n in ("100_z.png", "11_y.png", "9_x.png", "zeta.png", "Alpha.png", "beta.png"):
            self.put(n)
        r = scan_content(self.c)
        self.assertEqual([s.file for s in r.slides],
                         ["9_x.png", "11_y.png", "100_z.png", "Alpha.png", "beta.png", "zeta.png"])

    def test_number_text_is_kept_and_zero_is_a_number(self):
        for n in ("100_c.png", "05_b.png", "00_a.png", "plain.png"):
            self.put(n)
        r = scan_content(self.c)
        self.assertEqual([(s.file, s.number) for s in r.slides],
                         [("00_a.png", "00"), ("05_b.png", "05"), ("100_c.png", "100"), ("plain.png", "")])
        self.assertEqual([s["number"] for s in r.to_json()["slides"]], ["00", "05", "100", ""])

    def test_zero_padded_duplicates_share_a_number(self):
        self.put("05_a.png"); self.put("5_b.png")
        self.assertTrue(any("share the number 5" in w for w in scan_content(self.c).warnings))

    def test_two_slides_share_a_number(self):
        self.put("05_a.png"); self.put("05_b.png")
        r = scan_content(self.c)
        self.assertEqual([s.file for s in r.slides], ["05_a.png", "05_b.png"])
        self.assertTrue(any("share the number 5" in w for w in r.warnings))

    def test_skip_reasons(self):
        self.put("a.heic"); self.put("empty.png", b""); self.put("1_x (Lauren's conflicted copy 2026-01-01).png")
        self.put("notes.csv", b"a"); self.put("noext"); self.put("clip.avi"); self.put("1_ok.png")
        (self.c / "sub").mkdir()
        r = scan_content(self.c)
        s = self.skipped(r)
        self.assertEqual([x.file for x in r.slides], ["1_ok.png"])
        self.assertIn("save it as .jpg or .png", s["a.heic"])
        self.assertIn("syncing", s["empty.png"])
        self.assertIn("Dropbox conflicted copy", s["1_x (Lauren's conflicted copy 2026-01-01).png"])
        self.assertIn("spreadsheet not used", s["notes.csv"])
        self.assertIn("captions come only from captions.csv", s["notes.csv"])
        self.assertIn("no extension", s["noext"])
        self.assertIn(".mp4", s["clip.avi"])
        self.assertIn("subfolder", s["sub"])

    def test_ignored_without_report(self):
        for n in (".hidden.png", "_tmp.mp4", "Thumbs.db", "desktop.ini", "Icon\r", "_showcase_copy_log.csv"):
            self.put(n)
        (self.c / "_superseded").mkdir()
        r = scan_content(self.c)
        self.assertEqual(r.skipped, [])
        self.assertEqual(r.slides, [])

    def test_symlink_inside_and_outside(self):
        outside = self.tmp / "outside.png"
        outside.write_bytes(make_png())
        self.put("1_real.png")
        os.symlink(outside, self.c / "2_escape.png")
        os.symlink(self.c / "1_real.png", self.c / "3_inside.png")
        os.symlink(self.tmp / "gone.png", self.c / "4_broken.png")
        r = scan_content(self.c)
        self.assertEqual([s.file for s in r.slides], ["1_real.png", "3_inside.png"])
        s = self.skipped(r)
        self.assertEqual(s["2_escape.png"], "link points outside the content folder")
        self.assertEqual(s["4_broken.png"], "link is broken")

    def test_symlinked_content_folder_itself_is_fine(self):
        self.put("1_a.png")
        link = self.tmp / "link"
        os.symlink(self.c, link)
        self.assertEqual(len(scan_content(link).slides), 1)

    def test_folder_missing(self):
        r = scan_content(self.tmp / "nope")
        self.assertFalse(r.exists)
        self.assertIn("content folder not found", r.error)
        self.assertEqual(r.slides, [])

    def test_path_is_a_file(self):
        f = self.tmp / "afile"
        f.write_text("x")
        r = scan_content(f)
        self.assertFalse(r.exists)
        self.assertIn("not a folder", r.error)

    @unittest.skipIf(os.geteuid() == 0, "root can read any folder")
    def test_folder_unreadable(self):
        self.put("1_a.png")
        os.chmod(self.c, 0)
        self.addCleanup(os.chmod, self.c, 0o755)
        r = scan_content(self.c)
        self.assertTrue(r.exists)
        self.assertIn("cannot read the content folder", r.error)

    def test_hostile_names(self):
        names = ["1_a b.png", "2_it's \"quoted\".png", "3_<b>html&amp;.png", "4_café テスト.png",
                 "5_%2e%2e.png", "6_..png", "7_tab\tname.png"]
        for n in names:
            self.put(n)
        r = scan_content(self.c)
        self.assertEqual(sorted(s.file for s in r.slides), sorted(names))
        urls = [s["url"] for s in r.to_json()["slides"]]
        self.assertTrue(all("/" not in u[len("media/"):] for u in urls))
        self.assertIn("media/1_a%20b.png", urls)
        self.assertIn("media/5_%252e%252e.png", urls)

    def test_video_thumb_url(self):
        self.put("1_clip.mp4", b"x")
        j = scan_content(self.c).to_json()["slides"][0]
        self.assertEqual(j["kind"], "video")
        self.assertEqual(j["thumb"], "_thumbs/1_clip.mp4.jpg")
        self.assertEqual(j["url"], "media/1_clip.mp4")

    def test_same_stem_videos_get_distinct_thumbnails(self):
        self.put("20_clip.mp4", b"x"); self.put("20_clip.mov", b"y"); self.put("20_clip.webm", b"z")
        thumbs = [s["thumb"] for s in scan_content(self.c).to_json()["slides"]]
        self.assertEqual(len(set(thumbs)), 3)
        self.assertEqual(sorted(thumbs), ["_thumbs/20_clip.mov.jpg", "_thumbs/20_clip.mp4.jpg",
                                          "_thumbs/20_clip.webm.jpg"])

    def test_image_thumb_is_media_url(self):
        self.put("1_pic.png")
        j = scan_content(self.c).to_json()["slides"][0]
        self.assertEqual(j["thumb"], j["url"])

    def test_undecodable_file_name_is_reported_not_shown(self):
        name = make_undecodable(self.c)
        if name is None:
            self.skipTest("this platform refuses file names that are not valid UTF-8")
        self.put("01_ok.png")
        r = scan_content(self.c)
        self.assertEqual([s.file for s in r.slides], ["01_ok.png"])
        self.assertEqual(len(r.skipped), 1)
        self.assertEqual(r.skipped[0].reason, NOT_UTF8_REASON)
        self.assertIn("rename it", r.skipped[0].reason)
        r.skipped[0].file.encode("utf-8")  # the reported name always prints
        json.dumps(r.to_json())
        self.assertIsNone(resolve_media_file(self.c, name))

    def test_captions_link_outside_the_folder_is_ignored(self):
        self.put("1_a.png")
        outside = self.tmp / "outside.csv"
        outside.write_text(CAPTIONS_HEADER + "a,Leaked,,\n", encoding="utf-8")
        os.symlink(outside, self.c / "captions.csv")
        r = scan_content(self.c)
        self.assertEqual(r.slides[0].caption, "")
        self.assertTrue(any("link that points outside the content folder" in w for w in r.warnings))

    def test_captions_link_inside_the_folder_is_followed(self):
        self.put("1_a.png")
        (self.c / "real.txt").write_text(CAPTIONS_HEADER + "a,Fine,,\n", encoding="utf-8")
        os.symlink(self.c / "real.txt", self.c / "captions.csv")
        self.assertEqual(scan_content(self.c).slides[0].caption, "Fine")

    def test_no_captions_file_warns(self):
        self.put("1_a.png")
        r = scan_content(self.c)
        self.assertTrue(any("no captions file" in w for w in r.warnings))

    def test_captions_match_case_insensitive_and_nfc(self):
        self.put("1_Reef.png"); self.put("2_café.png")
        (self.c / "captions.csv").write_text(CAPTIONS_HEADER + "reef,Lower,,\ncafé,Accent,,\n", encoding="utf-8")
        r = scan_content(self.c)
        self.assertEqual([s.caption for s in r.slides], ["Lower", "Accent"])
        self.assertEqual(r.warnings, [])

    def test_exact_match_beats_case_insensitive(self):
        self.put("1_Reef.png")
        (self.c / "captions.csv").write_text(CAPTIONS_HEADER + "reef,lower,,\nReef,exact,,\n", encoding="utf-8")
        self.assertEqual(scan_content(self.c).slides[0].caption, "exact")

    def test_slides_without_rows_and_rows_without_slides(self):
        self.put("1_a.png"); self.put("2_b.png")
        (self.c / "captions.csv").write_text(CAPTIONS_HEADER + "a,x,,\nghost,y,,\n", encoding="utf-8")
        w = scan_content(self.c).warnings
        self.assertEqual(len([x for x in w if "no row in" in x]), 1)
        self.assertTrue(any("2_b.png" in x for x in w))
        self.assertTrue(any("name no slide" in x and "ghost" in x for x in w))

    def test_conflicted_captions_copy_reported(self):
        make_content(self.c)
        self.put("captions (Lauren's conflicted copy 2026-02-02).csv", b"title\nx\n")
        r = scan_content(self.c)
        self.assertTrue(any("conflicted copy of captions.csv" in w for w in r.warnings))
        self.assertIn("captions (Lauren's conflicted copy 2026-02-02).csv", self.skipped(r))

    def test_custom_captions_file_name(self):
        self.put("1_a.png")
        (self.c / "words.csv").write_text(CAPTIONS_HEADER + "a,hello,,\n", encoding="utf-8")
        self.put("captions.csv", b"title\n")
        r = scan_content(self.c, "words.csv")
        self.assertEqual(r.slides[0].caption, "hello")
        self.assertIn("captions come only from words.csv", self.skipped(r)["captions.csv"])


class CaptionsTests(TempDirTestCase):
    """load_captions."""

    def csv_file(self, data: bytes, name="captions.csv"):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_missing_file_is_empty_without_warning(self):
        self.assertEqual(load_captions(self.tmp / "none.csv"), ({}, []))

    def test_utf8_bom(self):
        p = self.csv_file(b"\xef\xbb\xbf" + CAPTIONS_HEADER.encode() + "a,café,x,y\n".encode())
        caps, warns = load_captions(p)
        self.assertEqual(caps["a"]["caption"], "café")
        self.assertEqual(warns, [])

    def test_windows_1252_warns(self):
        p = self.csv_file(CAPTIONS_HEADER.encode() + "a,café ’,x,y\n".encode("cp1252"))
        caps, warns = load_captions(p)
        self.assertEqual(caps["a"]["caption"], "café ’")
        self.assertTrue(any("Windows-1252" in w for w in warns))

    def test_quoted_line_breaks_become_spaces(self):
        p = self.csv_file(b'title,caption,credit,chapter\na,"line one\r\nline two\nthree\ttab",c,d\n')
        self.assertEqual(load_captions(p)[0]["a"]["caption"], "line one line two three tab")

    def test_control_characters_dropped(self):
        p = self.csv_file(b"title,caption\na,x\x01y\x07z\x00w\n")
        self.assertEqual(load_captions(p)[0]["a"]["caption"], "xyzw")

    def test_no_title_column(self):
        caps, warns = load_captions(self.csv_file(b"name,caption\na,b\n"))
        self.assertEqual(caps, {})
        self.assertTrue(any("no title column" in w for w in warns))

    def test_header_case_and_spaces(self):
        caps, _ = load_captions(self.csv_file(b" Title ,CAPTION\na,b\n"))
        self.assertEqual(caps["a"]["caption"], "b")

    def test_duplicate_titles_first_wins(self):
        caps, warns = load_captions(self.csv_file(b"title,caption\na,first\na,second\n"))
        self.assertEqual(caps["a"]["caption"], "first")
        self.assertTrue(any("duplicate titles" in w and "a" in w for w in warns))

    def test_short_rows_and_blank_lines(self):
        caps, _ = load_captions(self.csv_file(b"title,caption,credit\na\n\n,,\nb,x\n"))
        self.assertEqual(caps["a"], {"caption": "", "credit": "", "chapter": ""})
        self.assertEqual(caps["b"]["caption"], "x")
        self.assertEqual(len(caps), 2)

    def test_empty_file(self):
        self.assertEqual(load_captions(self.csv_file(b"")), ({}, []))

    def test_huge_file_ignored(self):
        p = self.csv_file(b"title,caption\n" + b"a,b\n" * (CAPTIONS_MAX_BYTES // 4 + 10))
        caps, warns = load_captions(p)
        self.assertEqual(caps, {})
        self.assertTrue(any("over 5 MB" in w for w in warns))

    def test_huge_cell_does_not_crash(self):
        p = self.csv_file(b"title,caption\na," + b"x" * 400_000 + b"\n")
        caps, warns = load_captions(p)
        self.assertEqual(caps, {})
        self.assertTrue(any("could not be read" in w for w in warns))

    def test_many_rows(self):
        rows = "".join(f"t{i},c{i}\n" for i in range(20_000))
        caps, _ = load_captions(self.csv_file(("title,caption\n" + rows).encode()))
        self.assertEqual(len(caps), 20_000)

    def test_directory_named_like_captions(self):
        d = self.tmp / "captions.csv"
        d.mkdir()
        self.assertEqual(load_captions(d), ({}, []))


class ResolveTests(TempDirTestCase):
    """resolve_media_file."""

    def setUp(self):
        super().setUp()
        self.c = self.tmp / "c"
        self.c.mkdir()
        (self.c / "1_a.png").write_bytes(make_png())
        (self.tmp / "secret.png").write_bytes(make_png())
        os.symlink(self.tmp / "secret.png", self.c / "2_link.png")

    def test_valid(self):
        self.assertEqual(resolve_media_file(self.c, "1_a.png"), self.c / "1_a.png")

    def test_refused(self):
        for bad in ("", ".", "..", "../secret.png", "sub/1_a.png", "sub\\1_a.png", ".1_a.png", "_1_a.png",
                    "1_a.png\0", "missing.png", "1_a.txt", "2_link.png", "1_a", None, 5):
            self.assertIsNone(resolve_media_file(self.c, bad), repr(bad))

    def test_directory_with_media_name(self):
        (self.c / "dir.png").mkdir()
        self.assertIsNone(resolve_media_file(self.c, "dir.png"))

    def test_serves_exactly_what_the_scan_lists(self):
        c = self.c
        (c / "empty.png").write_bytes(b"")
        (c / "3_x (conflicted copy).png").write_bytes(make_png())
        (c / "sub").mkdir()
        (c / "sub" / "4_in.png").write_bytes(make_png())
        os.symlink(c / "sub" / "4_in.png", c / "4_link.png")
        os.symlink(c / "nowhere.png", c / "5_broken.png")
        (c / "captions.csv").write_text("title\n")
        (c / "6_ok.mp4").write_bytes(b"v")
        listed = {s.file for s in scan_content(c).slides}
        for name in sorted(os.listdir(c)):
            self.assertEqual(resolve_media_file(c, name) is not None, name in listed, name)
        self.assertNotIn("empty.png", listed)
        self.assertNotIn("3_x (conflicted copy).png", listed)

    def test_empty_and_conflicted_files_are_refused(self):
        (self.c / "7_empty.png").write_bytes(b"")
        (self.c / "8_a (Lauren's conflicted copy 2026-01-01).png").write_bytes(make_png())
        self.assertIsNone(resolve_media_file(self.c, "7_empty.png"))
        self.assertIsNone(resolve_media_file(self.c, "8_a (Lauren's conflicted copy 2026-01-01).png"))

    def test_classify_entry_reasons(self):
        self.assertEqual(classify_entry(self.c, "1_a.png"), (self.c / "1_a.png", None))
        self.assertEqual(classify_entry(self.c, "2_link.png")[1], "link points outside the content folder")

    def test_paths_on_another_drive_count_as_outside(self):
        with mock.patch("vicar_showcase.content.os.path.commonpath", side_effect=ValueError("different drives")):
            self.assertFalse(_inside("C:\\content", "D:\\elsewhere"))

    def test_uppercase_extension(self):
        (self.c / "3_X.PNG").write_bytes(make_png())
        self.assertIsNotNone(resolve_media_file(self.c, "3_X.PNG"))


if __name__ == "__main__":
    unittest.main()
