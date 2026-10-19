"""Module: vicar_showcase.settings
Purpose: Decide which content folder the showcase reads. Precedence, highest
    first: the --content flag, the VICAR_SHOWCASE_CONTENT environment variable,
    settings.local.json (one machine, not committed), settings.json (the fork's
    setting, committed).
Inputs: The repository folder, an optional --content value, the environment.
Outputs: A frozen Settings record, or a SettingsError that says what to fix.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

SETTINGS_FILE = "settings.json"
LOCAL_SETTINGS_FILE = "settings.local.json"
ENV_CONTENT_DIR = "VICAR_SHOWCASE_CONTENT"
KNOWN_KEYS = {"content_dir", "captions_file"}
DEFAULT_CAPTIONS_FILE = "captions.csv"

SOURCE_FLAG = "--content flag"
SOURCE_ENV = ENV_CONTENT_DIR
SOURCE_LOCAL = LOCAL_SETTINGS_FILE
SOURCE_SHARED = SETTINGS_FILE

# The two views and the port each one uses (one place, used by cli and launch).
VIEWS = ("screensaver", "slideshow")
SCREENSAVER_PORT = 8766
SLIDESHOW_PORT = 8765
VIEW_PORTS = {"screensaver": SCREENSAVER_PORT, "slideshow": SLIDESHOW_PORT}


class SettingsError(ValueError):
    """Raised when the settings cannot be read or hold a bad value."""


@dataclass(frozen=True)
class Settings:
    """The resolved settings.

    Attributes:
        repo_dir: The repository folder.
        content_dir: The folder holding the slides and the captions file.
        content_dir_source: Which of the four sources chose content_dir.
        captions_file: Bare name of the captions spreadsheet in content_dir.
    """

    repo_dir: Path
    content_dir: Path
    content_dir_source: str
    captions_file: str


def _read_json_object(path: Path) -> dict[str, Any]:
    """Read a settings file that must hold one JSON object.

    Parameters:
        path: The file to read.
    Returns:
        The decoded object.
    Raises:
        SettingsError: The file cannot be read, is not JSON, or is not an object.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object like {{\"content_dir\": \"...\"}}")
    return data


def _check_keys(path: Path, data: Mapping[str, Any]) -> None:
    """Reject keys this tool does not know, so a typo never falls back silently.

    Parameters:
        path: The file the keys came from (named in the error).
        data: The decoded settings object.
    Raises:
        SettingsError: A key is not in KNOWN_KEYS.
    """
    for key in data:
        if key not in KNOWN_KEYS:
            known = ", ".join(sorted(KNOWN_KEYS))
            raise SettingsError(f"{path} has an unknown key \"{key}\"; known keys are: {known}")


def _check_content_value(source: str, value: Any) -> str:
    """Check that a content_dir value is a non-empty string.

    Parameters:
        source: Where the value came from (named in the error).
        value: The value to check.
    Returns:
        The value, unchanged.
    Raises:
        SettingsError: The value is not a non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"content_dir from {source} must be a non-empty text path, got {value!r}")
    return value


def check_captions_file(value: Any, source: str = SETTINGS_FILE) -> str:
    """Check that captions_file is a bare, visible .csv file name.

    Parameters:
        value: The value to check.
        source: Where the value came from (named in the error).
    Returns:
        The value, unchanged.
    Raises:
        SettingsError: The value is not a plain .csv file name.
    """
    ok = (
        isinstance(value, str)
        and value.lower().endswith(".csv")
        and len(value) > len(".csv")
        and not value.startswith(".")
        and not any(ch in value for ch in "/\\\0")
    )
    if not ok:
        raise SettingsError(
            f"captions_file from {source} must be a bare file name ending in .csv "
            f"(no folders, not hidden), got {value!r}"
        )
    return value


def resolve_content_path(raw: str, repo_dir: Path) -> Path:
    """Turn a configured path into an absolute one.

    Parameters:
        raw: The configured text; ~ means the home folder.
        repo_dir: Relative paths resolve against this folder.
    Returns:
        The absolute, normalised path (symlinks are not followed).
    Raises:
        SettingsError: The home folder cannot be determined for a ~ path.
    """
    try:
        path = Path(raw.strip()).expanduser()
    except RuntimeError as exc:
        raise SettingsError(f"cannot expand ~ in {raw!r}: {exc}") from exc
    if not path.is_absolute():
        path = repo_dir / path
    return Path(os.path.normpath(str(path)))


def _load_layers(repo_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read settings.json (required) and settings.local.json (optional).

    Parameters:
        repo_dir: The repository folder.
    Returns:
        (source name, validated object) pairs, lowest precedence first.
    Raises:
        SettingsError: A file is missing (shared one), unreadable or invalid.
    """
    shared = repo_dir / SETTINGS_FILE
    if not shared.is_file():
        raise SettingsError(
            f"{shared} is missing; the repository is incomplete. Restore it from git."
        )
    layers = [(SOURCE_SHARED, _read_json_object(shared))]
    local = repo_dir / LOCAL_SETTINGS_FILE
    if local.is_file():
        layers.append((SOURCE_LOCAL, _read_json_object(local)))
    for name, data in layers:
        _check_keys(repo_dir / name, data)
    return layers


def load_settings(
    repo_dir: Path,
    content_override: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Settings:
    """Resolve the settings from the four sources.

    Parameters:
        repo_dir: The repository folder holding settings.json.
        content_override: The --content flag value, or None.
        environ: The environment to read (default os.environ).
    Returns:
        The resolved Settings.
    Raises:
        SettingsError: A file is missing, malformed or holds a bad value, or no
            source names a content folder.
    Example:
        load_settings(Path("."), content_override="~/slides").content_dir_source
        -> "--content flag"
    """
    repo_dir = Path(repo_dir)
    env = os.environ if environ is None else environ
    layers = _load_layers(repo_dir)
    content_raw: Optional[str] = None
    source = ""
    captions_file = DEFAULT_CAPTIONS_FILE
    for name, data in layers:
        if "content_dir" in data:
            content_raw, source = _check_content_value(name, data["content_dir"]), name
        if "captions_file" in data:
            captions_file = check_captions_file(data["captions_file"], name)
    if env.get(ENV_CONTENT_DIR, "").strip():
        content_raw, source = env[ENV_CONTENT_DIR], SOURCE_ENV
    if content_override is not None:
        content_raw, source = _check_content_value(SOURCE_FLAG, content_override), SOURCE_FLAG
    if content_raw is None:
        raise SettingsError(f"no content_dir is set; add it to {repo_dir / SETTINGS_FILE}")
    return Settings(
        repo_dir=repo_dir,
        content_dir=resolve_content_path(content_raw, repo_dir),
        content_dir_source=source,
        captions_file=captions_file,
    )
