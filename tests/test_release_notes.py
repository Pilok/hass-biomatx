"""
Tests for ``scripts/release_notes.py``, the check ``release.yml`` runs on every tag.

The script decides whether a tag may become a GitHub Release: it refuses a tag
that disagrees with ``manifest.json`` or that the changelog does not describe,
and otherwise prints the changelog section that becomes the release notes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from scripts import release_notes

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

CHANGELOG = """\
# Changelog

Intro paragraph that belongs to no release.

## [Unreleased]

### Added

- Work in progress.

## [1.0.0-rc.1] - 2026-10-07

Scope frozen.

### Fixed

- A correction.

## [1.0.0-beta.2] - 2026-09-17

### Added

- An older entry.

[Unreleased]: https://example.test/compare/v1.0.0-rc.1...HEAD
[1.0.0-rc.1]: https://example.test/compare/v1.0.0-beta.2...v1.0.0-rc.1
[1.0.0-beta.2]: https://example.test/compare/v1.0.0-beta.1...v1.0.0-beta.2
"""

type Release = Callable[..., tuple[int, str, str]]


@pytest.fixture
def release(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Release:
    """Return a runner: write a manifest and a changelog, run the script on a tag."""

    def _release(
        tag: str, *, manifest_version: str, changelog: str = CHANGELOG
    ) -> tuple[int, str, str]:
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"domain": "biomatx", "version": manifest_version}),
            encoding="utf-8",
        )
        changelog_file = tmp_path / "CHANGELOG.md"
        changelog_file.write_text(changelog, encoding="utf-8")
        status = release_notes.main(
            [tag, "--manifest", str(manifest), "--changelog", str(changelog_file)]
        )
        out, err = capsys.readouterr()
        return status, out, err

    return _release


def test_matching_tag_prints_the_changelog_section(release: Release) -> None:
    """A tag equal to the manifest version yields its section, heading excluded."""
    status, out, err = release("v1.0.0-rc.1", manifest_version="1.0.0-rc.1")
    assert status == 0
    assert err == ""
    assert out == "Scope frozen.\n\n### Fixed\n\n- A correction.\n"


def test_stable_tag_is_released_like_a_pre_release(release: Release) -> None:
    """The pre-release part is optional: ``v1.0.0`` against ``1.0.0`` passes."""
    changelog = "## [1.0.0] - 2026-10-14\n\nFirst stable release.\n"
    status, out, _ = release("v1.0.0", manifest_version="1.0.0", changelog=changelog)
    assert status == 0
    assert out == "First stable release.\n"


def test_tag_different_from_the_manifest_is_refused(release: Release) -> None:
    """Nothing is printed, and the message names both versions."""
    status, out, err = release("v1.0.0-rc.1", manifest_version="1.0.0-beta.2")
    assert status == 1
    assert out == ""
    assert "1.0.0-rc.1" in err
    assert "1.0.0-beta.2" in err


def test_version_without_changelog_section_is_refused(release: Release) -> None:
    """The match is exact: a section for 1.0.0-rc.1 is not one for 1.0.0."""
    status, out, err = release("v1.0.0", manifest_version="1.0.0")
    assert status == 1
    assert out == ""
    assert "no section for 1.0.0" in err


def test_empty_changelog_section_is_refused(release: Release) -> None:
    """A heading with nothing under it would publish a blank release."""
    changelog = "## [1.0.0] - 2026-10-14\n\n## [1.0.0-rc.1] - 2026-10-07\n\nText.\n"
    status, out, err = release("v1.0.0", manifest_version="1.0.0", changelog=changelog)
    assert status == 1
    assert out == ""
    assert "empty" in err


@pytest.mark.parametrize(
    "tag",
    ["1.0.0-rc.1", "V1.0.0-rc.1", "v1.0", "v1.0.0+build", "v1.0.0-", "vUnreleased"],
)
def test_malformed_tag_is_refused(release: Release, tag: str) -> None:
    """Only ``v`` and a Semantic Version with an optional pre-release are released."""
    status, out, err = release(tag, manifest_version=tag.removeprefix("v"))
    assert status == 1
    assert out == ""
    assert "is not" in err


def test_section_ends_at_the_next_release_heading() -> None:
    """The body of a release stops where the previous release starts."""
    notes = release_notes.changelog_section(CHANGELOG, "1.0.0-rc.1")
    assert "A correction." in notes
    assert "An older entry." not in notes


def test_last_section_does_not_swallow_the_link_references() -> None:
    """Keep a Changelog ends the file with link references: they are not notes."""
    notes = release_notes.changelog_section(CHANGELOG, "1.0.0-beta.2")
    assert notes == "### Added\n\n- An older entry.\n"


def test_unreleased_section_is_not_a_release() -> None:
    """``[Unreleased]`` names no version, so no tag can ask for it."""
    with pytest.raises(release_notes.ReleaseError):
        release_notes.version_of_tag("vUnreleased")


def test_current_manifest_version_has_a_changelog_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bumping the manifest without dating a changelog section fails here first."""
    manifest = REPO_ROOT / "custom_components" / "biomatx" / "manifest.json"
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    assert release_notes.main([f"v{version}"]) == 0
    assert capsys.readouterr().out.strip()


def test_release_workflow_runs_only_on_version_tags() -> None:
    """A release is published from ``v*`` tags with a write scope limited to its job."""
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(text)
    assert workflow[True] == {"push": {"tags": ["v*"]}}  # YAML 1.1 reads ``on`` as True
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write"}
