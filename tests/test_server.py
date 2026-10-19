"""Module: tests.test_server
Purpose: Test the HTTP server: routes, manifest, media with Range, thumbnails, traversal
    and refusal cases, against a real server on an ephemeral port.
Inputs: A temporary content folder and repository copy.
Outputs: Test results.
"""

import json
import unittest
from unittest import mock

from tests.helpers import FFMPEG, LiveServerTestCase, make_png, make_video, request
from vicar_showcase import server as srv
from vicar_showcase.server import parse_range, safe_name


class ParseRangeTests(unittest.TestCase):
    """parse_range."""

    def test_cases(self):
        cases = {
            "bytes=0-9": ("partial", 0, 9), "bytes=10-": ("partial", 10, 99), "bytes=-10": ("partial", 90, 99),
            "bytes=90-500": ("partial", 90, 99), "bytes=-500": ("partial", 0, 99), "bytes=99-99": ("partial", 99, 99),
        }
        for header, expected in cases.items():
            self.assertEqual(parse_range(header, 100), expected, header)

    def test_whole_for_bad_or_multiple(self):
        for header in (None, "", "bytes=0-1,5-6", "bytes=abc", "bytes=-", "items=0-5", "bytes=5-2", "bytes=1-2-3", "0-5"):
            self.assertEqual(parse_range(header, 100)[0], "whole", repr(header))

    def test_unsatisfiable(self):
        for header in ("bytes=100-", "bytes=200-300", "bytes=-0"):
            self.assertEqual(parse_range(header, 100)[0], "unsatisfiable", header)
        self.assertEqual(parse_range("bytes=0-1", 0)[0], "unsatisfiable")
        self.assertEqual(parse_range("bytes=-5", 0)[0], "unsatisfiable")

    def test_safe_name(self):
        self.assertEqual(safe_name("a%20b.png"), "a b.png")
        for bad in ("", "..", "a/b", "a%2fb", "a%5cb", "a%00b", ".hidden", "%2ehidden", "_x.png", "%2e%2e%2fsettings.json"):
            self.assertIsNone(safe_name(bad), bad)


class RouteTests(LiveServerTestCase):
    """Routes and refusals."""

    def get(self, path, method="GET", headers=None):
        return request(self.port, method, path, headers)

    def test_root_redirects_to_view_page(self):
        status, headers, _ = self.get("/")
        self.assertEqual((status, headers["location"]), (302, "/slideshow.html"))

    def test_pages_served_from_slideshow_dir(self):
        (self.repo / "slideshow" / "slideshow.html").write_text("<title>VICAR Lab</title>")
        (self.repo / "slideshow" / "vicar-icon.png").write_bytes(make_png())
        status, headers, body = self.get("/slideshow.html")
        self.assertEqual((status, body), (200, b"<title>VICAR Lab</title>"))
        self.assertIn("text/html", headers["content-type"])
        status, headers, _ = self.get("/vicar-icon.png")
        self.assertEqual((status, headers["content-type"]), (200, "image/png"))

    def test_missing_page_is_404(self):
        (self.repo / "slideshow" / "screensaver.html").unlink(missing_ok=True)
        self.assertEqual(self.get("/screensaver.html")[0], 404)

    def test_manifest(self):
        status, headers, body = self.get("/_manifest.json")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["content_name"], "content")
        self.assertIsNone(data["error"])
        self.assertEqual([s["file"] for s in data["slides"]], ["01_alpha.png", "02-beta.png", "10_gamma.png"])
        self.assertEqual(data["slides"][0]["caption"], "First caption")
        self.assertEqual(set(data), {"schema", "content_dir", "content_name", "error", "slides", "warnings"})

    def test_manifest_is_live(self):
        (self.content / "04_delta.png").write_bytes(make_png())
        self.assertEqual(len(json.loads(self.get("/_manifest.json")[2])["slides"]), 4)

    def test_manifest_when_folder_vanishes(self):
        import shutil
        shutil.rmtree(self.content)
        data = json.loads(self.get("/_manifest.json")[2])
        self.assertIn("not found", data["error"])
        self.assertEqual(data["slides"], [])

    def test_info(self):
        data = json.loads(self.get("/_showcase_info.json")[2])
        self.assertEqual(data["app"], "vicar-showcase")
        self.assertEqual(data["view"], "slideshow")
        self.assertEqual(data["content_dir"], str(self.content))
        self.assertIsInstance(data["pid"], int)
        self.assertEqual(self.get("/_showcase_info.json")[1]["cache-control"], "no-store")

    def test_media_full_and_head(self):
        status, headers, body = self.get("/media/01_alpha.png")
        self.assertEqual((status, headers["content-type"], headers["accept-ranges"]), (200, "image/png", "bytes"))
        self.assertEqual(body, (self.content / "01_alpha.png").read_bytes())
        status, headers, body = self.get("/media/01_alpha.png", "HEAD")
        self.assertEqual((status, body, int(headers["content-length"])), (200, b"", len(make_png())))

    def test_media_name_with_special_characters(self):
        name = "5_a b&c's.png"
        (self.content / name).write_bytes(make_png())
        self.assertEqual(self.get("/media/5_a%20b%26c%27s.png")[0], 200)

    def test_range_requests(self):
        data = (self.content / "01_alpha.png").read_bytes()
        size = len(data)
        status, headers, body = self.get("/media/01_alpha.png", headers={"Range": "bytes=2-5"})
        self.assertEqual((status, body, headers["content-range"]), (206, data[2:6], f"bytes 2-5/{size}"))
        self.assertEqual(headers["content-length"], "4")
        status, _, body = self.get("/media/01_alpha.png", headers={"Range": "bytes=-3"})
        self.assertEqual((status, body), (206, data[-3:]))
        status, _, body = self.get("/media/01_alpha.png", headers={"Range": f"bytes={size - 2}-"})
        self.assertEqual((status, body), (206, data[-2:]))
        status, headers, body = self.get("/media/01_alpha.png", headers={"Range": f"bytes={size}-"})
        self.assertEqual((status, body, headers["content-range"]), (416, b"", f"bytes */{size}"))
        status, _, body = self.get("/media/01_alpha.png", headers={"Range": "bytes=0-1,4-5"})
        self.assertEqual((status, body), (200, data))
        status, _, body = self.get("/media/01_alpha.png", headers={"Range": "bytes=junk"})
        self.assertEqual((status, body), (200, data))
        status, headers, _ = self.get("/media/01_alpha.png", "HEAD", {"Range": "bytes=0-3"})
        self.assertEqual((status, headers["content-length"]), (206, "4"))

    def test_zero_byte_media_with_range(self):
        (self.content / "7_empty.png").write_bytes(b"")
        self.assertEqual(self.get("/media/7_empty.png")[0], 200)
        self.assertEqual(self.get("/media/7_empty.png", headers={"Range": "bytes=0-"})[0], 416)

    def test_content_types(self):
        for name, ctype in (("a.mov", "video/quicktime"), ("a.m4v", "video/x-m4v"), ("a.webm", "video/webm"),
                            ("a.avif", "image/avif"), ("a.webp", "image/webp"), ("a.mp4", "video/mp4"),
                            ("a.jpeg", "image/jpeg"), ("a.gif", "image/gif")):
            (self.content / name).write_bytes(b"x")
            self.assertEqual(self.get(f"/media/{name}")[1]["content-type"], ctype, name)

    def test_traversal_and_hidden_names(self):
        paths = ["/media/../settings.json", "/media/%2e%2e%2fsettings.json", "/media/%2e%2e/settings.json",
                 "/media/.hidden", "/media/_hidden.png", "/media/..%5csettings.json", "/media/%2e%2e%5csettings.json",
                 "/slideshow/../settings.json", "/settings.json", "/media/", "/media", "/_thumbs/../settings.json",
                 "/_thumbs/%2e%2e%2fsettings.json", "/media/a%00.png", "/media/01_alpha.png%00", "/media/..",
                 "/../settings.json", "/%2e%2e/settings.json", "/media//01_alpha.png", "/media/%252e%252e%252fx",
                 "/slideshow.html/../settings.json", "/README.md", "/module.yaml", "/vicar_showcase/cli.py", "/.git/config"]
        for path in paths:
            self.assertEqual(self.get(path)[0], 404, path)
            self.assertEqual(self.get(path, "HEAD")[0], 404, path)

    def test_symlink_escape_not_served(self):
        import os
        os.symlink(self.tmp / "secret.txt", self.content / "9_leak.png")
        os.symlink(self.tmp / "secret.txt", self.content / "9_leak2.txt")
        self.assertEqual(self.get("/media/9_leak.png")[0], 404)
        self.assertEqual(self.get("/media/9_leak2.txt")[0], 404)

    def test_unsupported_extension_not_served(self):
        (self.content / "doc.csv").write_text("a")
        self.assertEqual(self.get("/media/captions.csv")[0], 404)
        self.assertEqual(self.get("/media/doc.csv")[0], 404)

    def test_unknown_route_has_no_listing(self):
        for path in ("/", "/media/", "/_thumbs/", "/foo"):
            if path == "/":
                continue
            status, _, body = self.get(path)
            self.assertEqual(status, 404)
            self.assertNotIn(b"01_alpha", body)

    def test_other_methods_refused(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            status, headers, _ = self.get("/_manifest.json", method)
            self.assertEqual(status, 405, method)
            self.assertEqual(headers["allow"], "GET, HEAD")

    def test_query_string_ignored(self):
        self.assertEqual(self.get("/_manifest.json?t=123")[0], 200)
        self.assertEqual(self.get("/media/01_alpha.png?x=1")[0], 200)

    def test_bound_to_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_connection_reset_is_quiet(self):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.sendall(b"GET /media/01_alpha.png HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.close()
        self.assertEqual(self.get("/_showcase_info.json")[0], 200)

    def test_thumb_unknown_is_404(self):
        self.assertEqual(self.get("/_thumbs/nothing.jpg")[0], 404)
        self.assertEqual(self.get("/_thumbs/01_alpha.jpg")[0], 404, "images have no thumbnail route")
        self.assertEqual(self.get("/_thumbs/nothing.png")[0], 404)


class ThumbRouteTests(LiveServerTestCase):
    """Thumbnail route."""

    def test_stub_thumbnail_generated_and_served(self):
        (self.content / "04_clip.mp4").write_bytes(b"fake")
        def fake_ensure(thumbs_dir, video, **kw):
            out = thumbs_dir / "x.jpg"
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\xff\xd8thumb")
            return out
        with mock.patch.object(srv, "ensure_thumb", side_effect=fake_ensure) as ensure:
            status, headers, body = request(self.port, "GET", "/_thumbs/04_clip.jpg")
        self.assertEqual((status, headers["content-type"], body), (200, "image/jpeg", b"\xff\xd8thumb"))
        self.assertEqual(ensure.call_count, 1)

    def test_thumbnail_failure_is_404(self):
        (self.content / "04_clip.mp4").write_bytes(b"fake")
        with mock.patch.object(srv, "ensure_thumb", return_value=None):
            self.assertEqual(request(self.port, "GET", "/_thumbs/04_clip.jpg")[0], 404)

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_real_video_thumbnail_and_range(self):
        video = self.content / "05_real.mp4"
        self.assertTrue(make_video(video))
        status, headers, body = request(self.port, "GET", "/_thumbs/05_real.jpg")
        self.assertEqual((status, body[:2]), (200, b"\xff\xd8"))
        status, headers, body = request(self.port, "GET", "/media/05_real.mp4", {"Range": "bytes=0-99"})
        self.assertEqual((status, len(body), headers["content-type"]), (206, 100, "video/mp4"))
        self.assertEqual(body, video.read_bytes()[:100])


if __name__ == "__main__":
    unittest.main()
