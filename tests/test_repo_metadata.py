"""
Checks on the repository metadata read by Home Assistant, hassfest and HACS.

These tests read files, not Python objects, so they run without an instance of
Home Assistant and catch packaging mistakes before the CI validators do.
"""

import json
from pathlib import Path
import re

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "biomatx"

# Semantic version with an optional pre-release tag, e.g. 1.0.0 or 1.0.0-beta.1.
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")

# Keys HACS requires in the manifest of a custom integration, plus the keys any
# config-flow integration needs.
REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "version",
    "documentation",
    "issue_tracker",
    "codeowners",
    "config_flow",
    "iot_class",
    "integration_type",
    "requirements",
}


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    """Return the parsed integration manifest."""
    return json.loads((INTEGRATION_DIR / "manifest.json").read_text())


@pytest.fixture(scope="module")
def hacs_json() -> dict[str, object]:
    """Return the parsed HACS repository manifest."""
    return json.loads((REPO_ROOT / "hacs.json").read_text())


def test_manifest_has_required_keys(manifest: dict[str, object]) -> None:
    """HACS and Home Assistant refuse a manifest missing any of these keys."""
    assert manifest.keys() >= REQUIRED_MANIFEST_KEYS
    assert manifest["domain"] == "biomatx"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "hub"
    assert manifest["iot_class"] == "local_push"


def test_manifest_version_is_semver(manifest: dict[str, object]) -> None:
    """HACS matches the manifest version against the GitHub release tag."""
    assert SEMVER.match(str(manifest["version"]))


def test_manifest_keys_are_sorted_like_hassfest_expects(
    manifest: dict[str, object],
) -> None:
    """Hassfest requires domain, then name, then the other keys alphabetically."""
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_manifest_has_no_empty_discovery_keys(manifest: dict[str, object]) -> None:
    """Empty ssdp/zeroconf/homekit blocks are scaffold leftovers hassfest rejects."""
    assert not {"ssdp", "zeroconf", "homekit", "dependencies"} & manifest.keys()


def test_manifest_links_point_to_this_repository(manifest: dict[str, object]) -> None:
    """The in-app documentation and issue links must lead somewhere real."""
    assert manifest["documentation"] == "https://github.com/pilok/hass-biomatx"
    assert manifest["issue_tracker"] == "https://github.com/pilok/hass-biomatx/issues"


def test_hacs_json_is_valid_and_names_biomatx(hacs_json: dict[str, object]) -> None:
    """HACS needs at least a display name and, here, minimum versions."""
    assert hacs_json["name"] == "BioMatX"
    assert re.match(r"^\d{4}\.\d{1,2}\.\d+$", str(hacs_json["homeassistant"]))
    assert re.match(r"^\d+\.\d+\.\d+$", str(hacs_json["hacs"]))
    assert not {"content_in_root", "zip_release"} & hacs_json.keys()


def test_only_one_integration_in_custom_components() -> None:
    """HACS manages a single integration per repository."""
    integrations = [
        path
        for path in (REPO_ROOT / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    ]
    assert integrations == [INTEGRATION_DIR]


def _leaf_keys(node: object, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    return set().union(
        *(_leaf_keys(value, f"{prefix}.{key}") for key, value in node.items())
    )


def test_translation_files_have_identical_key_sets() -> None:
    """A key present in en.json but not in fr.json shows up raw in a French UI."""
    translations = INTEGRATION_DIR / "translations"
    english = json.loads((translations / "en.json").read_text())
    french = json.loads((translations / "fr.json").read_text())
    assert _leaf_keys(english) == _leaf_keys(french)


def test_no_strings_json_in_custom_integration() -> None:
    """Home Assistant forbids strings.json for custom integrations."""
    assert not (INTEGRATION_DIR / "strings.json").exists()
