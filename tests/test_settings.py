"""Module: tests.test_settings
Purpose: Test settings loading: precedence, validation, hostile and malformed files.
Inputs: Temporary repositories.
Outputs: Test results.
"""

import json
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from vicar_showcase.settings import (
    ENV_CONTENT_DIR, KNOWN_KEYS, SettingsError, load_settings, resolve_content_path,
)


class SettingsTests(TempDirTestCase):
    """Settings precedence and validation."""

    def write(self, name: str, content) -> None:
        """Write a settings file (dict becomes JSON, str is written as is)."""
        text = content if isinstance(content, str) else json.dumps(content)
        (self.tmp / name).write_text(text, encoding="utf-8")

    def test_shared_settings(self):
        self.write("settings.json", {"content_dir": "/data/slides"})
        s = load_settings(self.tmp, environ={})
        self.assertEqual(s.content_dir, Path("/data/slides"))
        self.assertEqual(s.content_dir_source, "settings.json")
        self.assertEqual(s.captions_file, "captions.csv")
        self.assertEqual(s.repo_dir, self.tmp)

    def test_precedence_order(self):
        self.write("settings.json", {"content_dir": "/a"})
        self.write("settings.local.json", {"content_dir": "/b"})
        self.assertEqual(load_settings(self.tmp, environ={}).content_dir_source, "settings.local.json")
        self.assertEqual(load_settings(self.tmp, environ={}).content_dir, Path("/b"))
        env = {ENV_CONTENT_DIR: "/c"}
        s = load_settings(self.tmp, environ=env)
        self.assertEqual((s.content_dir, s.content_dir_source), (Path("/c"), "VICAR_SHOWCASE_CONTENT"))
        s = load_settings(self.tmp, content_override="/d", environ=env)
        self.assertEqual((s.content_dir, s.content_dir_source), (Path("/d"), "--content flag"))

    def test_empty_env_is_ignored(self):
        self.write("settings.json", {"content_dir": "/a"})
        self.assertEqual(load_settings(self.tmp, environ={ENV_CONTENT_DIR: "  "}).content_dir_source, "settings.json")

    def test_tilde_and_relative_paths(self):
        self.write("settings.json", {"content_dir": "~/slides"})
        s = load_settings(self.tmp, environ={})
        self.assertEqual(s.content_dir, Path("~/slides").expanduser())
        self.assertEqual(resolve_content_path("media/../content", self.tmp), self.tmp / "content")

    def test_content_dir_not_checked_for_existence(self):
        self.write("settings.json", {"content_dir": "/definitely/not/here"})
        self.assertEqual(load_settings(self.tmp, environ={}).content_dir, Path("/definitely/not/here"))

    def test_missing_settings_file(self):
        with self.assertRaisesRegex(SettingsError, "repository is incomplete"):
            load_settings(self.tmp, environ={})

    def test_malformed_json_names_file_line_column(self):
        self.write("settings.json", '{\n  "content_dir": \n}')
        with self.assertRaises(SettingsError) as ctx:
            load_settings(self.tmp, environ={})
        message = str(ctx.exception)
        self.assertIn("settings.json", message)
        self.assertRegex(message, r"line \d+, column \d+")

    def test_malformed_local_names_local_file(self):
        self.write("settings.json", {"content_dir": "/a"})
        self.write("settings.local.json", "{nope")
        with self.assertRaisesRegex(SettingsError, "settings.local.json"):
            load_settings(self.tmp, environ={})

    def test_empty_file(self):
        self.write("settings.json", "")
        with self.assertRaisesRegex(SettingsError, "not valid JSON"):
            load_settings(self.tmp, environ={})

    def test_not_an_object(self):
        for text in ("[]", '"x"', "3", "null"):
            self.write("settings.json", text)
            with self.assertRaisesRegex(SettingsError, "JSON object"):
                load_settings(self.tmp, environ={})

    def test_unknown_key_lists_known_keys(self):
        self.write("settings.json", {"content_dir": "/a", "conten_dir": "/b"})
        with self.assertRaises(SettingsError) as ctx:
            load_settings(self.tmp, environ={})
        message = str(ctx.exception)
        self.assertIn("settings.json", message)
        self.assertIn("conten_dir", message)
        for key in KNOWN_KEYS:
            self.assertIn(key, message)

    def test_unknown_key_in_local_file(self):
        self.write("settings.json", {"content_dir": "/a"})
        self.write("settings.local.json", {"port": 1})
        with self.assertRaisesRegex(SettingsError, "settings.local.json.*port"):
            load_settings(self.tmp, environ={})

    def test_wrong_types_for_content_dir(self):
        for bad in (5, ["/a"], True, None, {"a": 1}, "", "   "):
            self.write("settings.json", {"content_dir": bad})
            with self.assertRaisesRegex(SettingsError, "content_dir"):
                load_settings(self.tmp, environ={})

    def test_empty_override_is_an_error(self):
        self.write("settings.json", {"content_dir": "/a"})
        with self.assertRaises(SettingsError):
            load_settings(self.tmp, content_override="", environ={})

    def test_no_content_dir_anywhere(self):
        self.write("settings.json", {"captions_file": "x.csv"})
        with self.assertRaisesRegex(SettingsError, "no content_dir"):
            load_settings(self.tmp, environ={})

    def test_captions_file_valid(self):
        self.write("settings.json", {"content_dir": "/a", "captions_file": "My Captions.CSV"})
        self.assertEqual(load_settings(self.tmp, environ={}).captions_file, "My Captions.CSV")

    def test_captions_file_invalid(self):
        for bad in ("captions.txt", "sub/captions.csv", "..\\c.csv", ".hidden.csv", ".csv", 4, "", "../c.csv"):
            self.write("settings.json", {"content_dir": "/a", "captions_file": bad})
            with self.assertRaisesRegex(SettingsError, "captions_file"):
                load_settings(self.tmp, environ={})

    def test_bom_in_settings_file(self):
        (self.tmp / "settings.json").write_bytes(b"\xef\xbb\xbf" + b'{"content_dir": "/a"}')
        self.assertEqual(load_settings(self.tmp, environ={}).content_dir, Path("/a"))

    def test_binary_garbage(self):
        (self.tmp / "settings.json").write_bytes(b"\xff\xfe\x00\x81garbage")
        with self.assertRaises(SettingsError):
            load_settings(self.tmp, environ={})


if __name__ == "__main__":
    unittest.main()
