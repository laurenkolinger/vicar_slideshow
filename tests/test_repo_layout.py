"""Module: tests.test_repo_layout
Purpose: Check the repository holds what the tool and VICARIUS rely on: settings.json,
    module.yaml serve_cmd entries and ports, the pinned file set, and the house prose rules.
Inputs: Files in the repository.
Outputs: Test results.
"""

import ast
import json
import re
import unittest

from tests.helpers import REPO
from vicar_showcase.settings import SCREENSAVER_PORT, SLIDESHOW_PORT, VIEW_PORTS, SettingsError, load_settings

PACKAGE_SOURCES = sorted((REPO / "vicar_showcase").glob("*.py"))
README = REPO / "README.md"
SCREENSAVER_HELP = (
    "Click to open the auto-scrolling full-screen reel from the content folder named in settings.json; "
    "move the mouse, click or press any key to exit, and use Up and Down to dim or brighten it. "
    "The reel runs on its own server (port 8766), so it keeps running when VICARIUS restarts."
)
SLIDESHOW_HELP = (
    "Click to open the manual slideshow and grid from the content folder named in settings.json; "
    "Space toggles the grid, the arrow keys change slides and F toggles full screen. "
    "The slideshow runs on its own server (port 8765), so it keeps running when VICARIUS restarts."
)
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
                    "vicar_showcase/launch.py", "vicar_showcase/cli.py", "vicar_showcase/common.py", "slideshow/start-screensaver.command",
                    "slideshow/start-slideshow.command"):
            self.assertTrue((REPO / rel).is_file(), rel)

    def test_port_constants(self):
        self.assertEqual((SCREENSAVER_PORT, SLIDESHOW_PORT), (8766, 8765))
        self.assertEqual(VIEW_PORTS, {"screensaver": 8766, "slideshow": 8765})

    def test_settings_json_loads(self):
        self.assertTrue((REPO / "settings.json").is_file(), "settings.json is missing")
        settings = load_settings(REPO, environ={})
        self.assertEqual(settings.content_dir_source, "settings.json")
        data = json.loads((REPO / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("content_dir", data)
        with self.assertRaises(SettingsError):
            load_settings(REPO / "slideshow", environ={})

    def test_module_yaml_serve_cmd_matches_pinned_argv(self):
        module_yaml = REPO / "module.yaml"
        self.assertTrue(module_yaml.is_file(), "module.yaml is missing")
        text = module_yaml.read_text(encoding="utf-8")
        self.assertIn("serve_cmd", text, "module.yaml has no serve_cmd entries")
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

    def test_view_help_texts_are_exact(self):
        text = (REPO / "module.yaml").read_text(encoding="utf-8")
        helps = dict(re.findall(r'(?m)^\s*-\s*key:\s*"(\w+)"(?:.*\n)+?\s*help:\s*"([^"\n]*)"', text))
        self.assertEqual(helps, {"screensaver": SCREENSAVER_HELP, "slideshow": SLIDESHOW_HELP})

    def test_readme_follows_the_house_prose_rules(self):
        text = README.read_text(encoding="utf-8")
        self.assertNotIn("UVI", text)
        for char in BANNED_CHARS:
            self.assertNotIn(char, text)
        self.assertIsNone(EMOJI.search(text), "README has an emoji")
        self.assertIsNone(re.search(r"(?i)\bland(s|ed|ing)?\b", text), "README uses the banned word")

    def test_readme_command_table_matches_the_server_behaviour(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("[--port <port>]", next(l for l in text.splitlines() if l.startswith("| `serve ")),
                      "serve's --port is optional")
        self.assertIn("does not name this machine", text)
        self.assertIn("answers 403", text)

    def test_other_text_files_follow_the_prose_rules(self):
        paths = [REPO / "module.yaml", *sorted((REPO / ".githooks").iterdir())]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for char in BANNED_CHARS:
                self.assertNotIn(char, text, path.name)
            self.assertIsNone(re.search(r"(?i)\bland(s|ed|ing)?\b", text), path.name)
            self.assertIsNone(EMOJI.search(text), path.name)

    def test_every_function_has_a_docstring_and_no_function_imports(self):
        for path in PACKAGE_SOURCES + [REPO / "showcase.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertIsNotNone(ast.get_docstring(node) or None, f"{path.name}:{node.lineno} {node.name}")
                    imports = [i for i in ast.walk(node) if isinstance(i, (ast.Import, ast.ImportFrom))]
                    self.assertEqual(imports, [], f"{path.name}: import inside {node.name}")

    def test_shared_constants_are_defined_once(self):
        sources = {p.name: p.read_text(encoding="utf-8") for p in PACKAGE_SOURCES}
        for pattern in (r"(?m)^EXIT_\w+ = ", r"(?m)^BYTES_PER_MB = ", r"(?m)^DEFAULT_CAPTIONS_FILE = ",
                        r"(?m)^\w*HOST\w* = \"127"):
            homes = sorted(name for name, text in sources.items() if re.search(pattern, text))
            self.assertLessEqual(len(homes), 1, f"{pattern} is defined in {homes}")
        for name, text in sources.items():
            if name not in ("common.py", "settings.py"):
                self.assertNotIn('"captions.csv"', text, f"{name} repeats the captions file name")
                self.assertNotIn('"127.0.0.1"', text, f"{name} repeats the host")
            self.assertNotIn("timeout=5)", text, f"{name} has a literal timeout")

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
