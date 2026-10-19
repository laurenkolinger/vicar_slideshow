"""Module: tests.test_repo_layout
Purpose: Check the repository holds what the tool and VICARIUS rely on: settings.json,
    module.yaml serve_cmd entries and ports, the pinned file set, and the house prose rules.
Inputs: Files in the repository.
Outputs: Test results.
"""

import json
import re
import unittest

from tests.helpers import REPO
from vicar_showcase.settings import SCREENSAVER_PORT, SLIDESHOW_PORT, VIEW_PORTS, SettingsError, load_settings

OWNED_SOURCES = (
    sorted((REPO / "vicar_showcase").glob("*.py")) + sorted((REPO / "tests").glob("*.py")) + [REPO / "showcase.py"]
    + sorted((REPO / "slideshow").glob("start-*.command"))
)
BANNED_CHARS = (chr(0x2014), chr(0x2013))
LAND_WORD = re.compile(r"\bland(s|ed|ing)?\b", re.IGNORECASE)
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


class LayoutTests(unittest.TestCase):
    """Repository layout."""

    def test_pinned_files_exist(self):
        for rel in ("showcase.py", "vicar_showcase/__init__.py", "vicar_showcase/settings.py", "vicar_showcase/content.py",
                    "vicar_showcase/copy_content.py", "vicar_showcase/thumbs.py", "vicar_showcase/server.py",
                    "vicar_showcase/launch.py", "vicar_showcase/cli.py", "slideshow/start-screensaver.command",
                    "slideshow/start-slideshow.command"):
            self.assertTrue((REPO / rel).is_file(), rel)

    def test_port_constants(self):
        self.assertEqual((SCREENSAVER_PORT, SLIDESHOW_PORT), (8766, 8765))
        self.assertEqual(VIEW_PORTS, {"screensaver": 8766, "slideshow": 8765})

    def test_settings_json_loads(self):
        if not (REPO / "settings.json").is_file():
            self.skipTest("settings.json is written by the docs and config builder and is not there yet")
        settings = load_settings(REPO, environ={})
        self.assertEqual(settings.content_dir_source, "settings.json")
        data = json.loads((REPO / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("content_dir", data)
        with self.assertRaises(SettingsError):
            load_settings(REPO / "slideshow", environ={})

    def test_module_yaml_serve_cmd_matches_pinned_argv(self):
        module_yaml = REPO / "module.yaml"
        text = module_yaml.read_text(encoding="utf-8") if module_yaml.is_file() else ""
        if "serve_cmd" not in text:
            self.skipTest("module.yaml serve_cmd entries are written by the docs and config builder and are not there yet")
        blocks = re.split(r"(?m)^\s*-\s*key:\s*", text)[1:]
        found = {}
        for block in blocks:
            view = re.match(r"[\"']?(\w+)", block).group(1)
            cmd = re.search(r"serve_cmd:\s*(\[[^\]]*\])", block)
            port = re.search(r"(?m)^\s*port:\s*(\d+)", block)
            if cmd:
                found[view] = (json.loads(cmd.group(1)), int(port.group(1)) if port else None)
        self.assertEqual(set(found), {"screensaver", "slideshow"})
        for view, (cmd, port) in found.items():
            self.assertEqual(cmd, ["showcase.py", "serve", "--view", view, "--port", str(VIEW_PORTS[view])], view)
            if port is not None:
                self.assertEqual(port, VIEW_PORTS[view], view)

    def test_no_dashes_land_word_or_emoji_in_owned_files(self):
        for path in OWNED_SOURCES:
            text = path.read_text(encoding="utf-8")
            for char in BANNED_CHARS:
                self.assertNotIn(char, text, f"{path.name} has a dash character")
            if path.name != "test_repo_layout.py":
                self.assertIsNone(LAND_WORD.search(text), f"{path.name} uses the banned word")
            self.assertIsNone(EMOJI.search(text), f"{path.name} has an emoji")

    def test_every_source_file_opens_with_a_module_docstring(self):
        for path in OWNED_SOURCES:
            if path.suffix == ".py" and path.name != "__init__.py" or path.name == "__init__.py":
                lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#!")]
                self.assertTrue(lines[0].startswith('"""Module:'), path.name)
                self.assertIn("Purpose:", "\n".join(lines[:6]), path.name)


if __name__ == "__main__":
    unittest.main()
