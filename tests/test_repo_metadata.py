"""
Checks on the repository metadata read by Home Assistant, hassfest and HACS.

These tests read files, not Python objects, so they run without an instance of
Home Assistant and catch packaging mistakes before the CI validators do.
"""

import json
from pathlib import Path
import re

from packaging.requirements import Requirement
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


def test_serialx_requirement_accepts_the_versions_of_both_cores(
    manifest: dict[str, object],
) -> None:
    """
    Home Assistant 2026.9 constrains serialx to 1.10.0 and 2026.10 to 1.11.0.

    A pin on either fails on the other core: hassfest runs against the newest one,
    and the instance of the owner runs the older one.
    """
    requirements = manifest["requirements"]
    assert isinstance(requirements, list)
    (serialx,) = (
        requirement
        for requirement in map(Requirement, requirements)
        if requirement.name == "serialx"
    )
    assert "1.10.0" in serialx.specifier
    assert "1.11.0" in serialx.specifier
    assert "2.0.0" not in serialx.specifier


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


PLACEHOLDER = re.compile(r"{(\w+)}")


def _translation_strings(language: str) -> dict[str, str]:
    """Return every string of one translation file, keyed by its dotted path."""
    tree = json.loads(
        (INTEGRATION_DIR / "translations" / f"{language}.json").read_text()
    )

    def flatten(node: object, prefix: str) -> dict[str, str]:
        if isinstance(node, dict):
            flat: dict[str, str] = {}
            for key, value in node.items():
                flat |= flatten(value, f"{prefix}.{key}")
            return flat
        return {prefix: str(node)}

    return flatten(tree, "")


def test_translation_placeholders_match_between_languages() -> None:
    """A placeholder missing from fr.json shows up as raw braces or as nothing."""
    english = _translation_strings("en")
    french = _translation_strings("fr")
    for key, text in english.items():
        expected = set(PLACEHOLDER.findall(text))
        assert set(PLACEHOLDER.findall(french.get(key, ""))) == expected, key


@pytest.mark.parametrize("language", ["en", "fr"])
def test_translation_strings_have_no_surrounding_whitespace(language: str) -> None:
    """Hassfest refuses a translation string that starts or ends with whitespace."""
    for key, text in _translation_strings(language).items():
        assert text == text.strip(), key


@pytest.mark.parametrize("language", ["en", "fr"])
def test_config_flow_steps_use_the_placeholders_the_flow_provides(
    language: str,
) -> None:
    """The user step names the listen, the modules step what was heard."""
    strings = _translation_strings(language)
    user = strings[".config.step.user.description"]
    modules = strings[".config.step.modules.description"]
    assert set(PLACEHOLDER.findall(user)) == {"seconds"}
    assert set(PLACEHOLDER.findall(modules)) == {"protocol", "modules"}


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_abort_reason_the_config_flow_can_raise_is_translated(
    language: str,
) -> None:
    """Home Assistant raises ``already_in_progress`` itself when two flows meet."""
    prefix = ".config.abort."
    reasons = {
        key.removeprefix(prefix)
        for key in _translation_strings(language)
        if key.startswith(prefix)
    }
    assert {
        "already_configured",
        "already_in_progress",
        "reconfigure_successful",
    } <= reasons


def test_no_strings_json_in_custom_integration() -> None:
    """Home Assistant forbids strings.json for custom integrations."""
    assert not (INTEGRATION_DIR / "strings.json").exists()
