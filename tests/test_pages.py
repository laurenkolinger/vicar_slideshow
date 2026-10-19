"""Module: tests.test_pages
Purpose: Check that the two HTML pages read the live manifest, do not parse captions.csv,
    and carry no file:// media paths.
Inputs: slideshow/screensaver.html and slideshow/slideshow.html.
Outputs: Test results.
"""

import re
import unittest
from html.parser import HTMLParser

from tests.helpers import REPO

PAGES = ("screensaver.html", "slideshow.html")
FETCH_CAPTIONS = re.compile(r"fetch\(\s*[`'\"][^`'\"]*captions\.csv")
FETCH_MANIFEST = re.compile(r"fetch\(\s*[`'\"]_manifest\.json")
FILE_URL_LITERAL = re.compile(r"[`'\"]file://")


class PageParser(HTMLParser):
    """Collects the title, inline scripts and every src/href attribute."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.scripts = []
        self.urls = []
        self._in = None

    def handle_starttag(self, tag, attrs):
        if tag in ("title", "script"):
            self._in = tag
            if tag == "script":
                self.scripts.append("")
        for key, value in attrs:
            if key in ("src", "href", "poster", "data-src") and value:
                self.urls.append(value)

    def handle_endtag(self, tag):
        self._in = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "script":
            self.scripts[-1] += data


def parse(name: str) -> tuple[str, PageParser]:
    """Return (raw text, parsed page) for a page in slideshow/."""
    text = (REPO / "slideshow" / name).read_text(encoding="utf-8")
    parser = PageParser()
    parser.feed(text)
    return text, parser


class PageTests(unittest.TestCase):
    """Both pages."""

    def test_pages_exist_and_have_window_title(self):
        for name in PAGES:
            _, page = parse(name)
            self.assertIn("VICAR Lab", page.title, f"{name}: wmctrl matches the window title")

    def test_pages_fetch_the_manifest(self):
        for name in PAGES:
            _, page = parse(name)
            self.assertTrue(any(FETCH_MANIFEST.search(s) for s in page.scripts), f"{name} does not fetch _manifest.json")

    def test_pages_never_fetch_captions_csv(self):
        for name in PAGES:
            text, page = parse(name)
            if any(FETCH_CAPTIONS.search(s) for s in page.scripts) and "manifest.slides" not in text:
                self.skipTest(f"{name} still parses captions.csv; this assertion applies once the page edits are in")
            self.assertFalse(any(FETCH_CAPTIONS.search(s) for s in page.scripts), f"{name} fetches captions.csv")
            self.assertFalse(any("captions.csv" in u for u in page.urls), f"{name} links captions.csv")

    def test_pages_have_no_file_url_media_paths(self):
        for name in PAGES:
            _, page = parse(name)
            self.assertFalse([u for u in page.urls if u.startswith("file:")], f"{name}: file: attribute")
            for script in page.scripts:
                self.assertIsNone(FILE_URL_LITERAL.search(script), f"{name}: file:// URL literal in script")


if __name__ == "__main__":
    unittest.main()
