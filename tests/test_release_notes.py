"""
Tests for ``scripts/release_notes.py``, the check ``release.yml`` runs on every tag.

The script decides whether a tag may become a GitHub Release: it refuses a tag
that disagrees with ``manifest.json``, a commit that ``main`` does not contain,
or a version that the changelog does not describe, and otherwise prints the
changelog section that becomes the release notes.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
import yaml

from scripts import release_notes

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
        tag: str,
        *,
        manifest_version: str,
        changelog: str = CHANGELOG,
        extra: Sequence[str] = (),
    ) -> tuple[int, str, str]:
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"domain": "biomatx", "version": manifest_version}),
            encoding="utf-8",
        )
        changelog_file = tmp_path / "CHANGELOG.md"
        changelog_file.write_text(changelog, encoding="utf-8")
        status = release_notes.main(
            [
                tag,
                "--manifest",
                str(manifest),
                "--changelog",
                str(changelog_file),
                *extra,
            ]
        )
        out, err = capsys.readouterr()
        return status, out, err

    return _release


GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` with a fixed identity; the arguments are test constants."""
    assert GIT is not None
    subprocess.run(  # noqa: S603  # list of fixed strings, no shell
        [
            GIT,
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return a repository whose ``main`` holds two commits."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "--quiet", "--initial-branch", "main")
    for text in ("one", "two"):
        (path / "file").write_text(f"{text}\n", encoding="utf-8")
        _git(path, "add", "file")
        _git(path, "commit", "--quiet", "--message", text)
    return path


def _commit_on_side_branch(repo: Path) -> None:
    """Leave HEAD on a commit that ``main`` does not contain."""
    _git(repo, "switch", "--quiet", "--create", "side")
    (repo / "file").write_text("side\n", encoding="utf-8")
    _git(repo, "commit", "--quiet", "--all", "--message", "side")


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


def test_head_of_main_is_on_main(repo: Path) -> None:
    """The usual case: the tag sits on the last commit of main."""
    release_notes.check_on_main(repo, "main")


def test_older_commit_of_main_is_on_main(repo: Path) -> None:
    """Main moved on since the tag was pushed: the commit is still an ancestor."""
    _git(repo, "switch", "--quiet", "--detach", "HEAD~1")
    release_notes.check_on_main(repo, "main")


def test_commit_of_an_unmerged_branch_is_not_on_main(repo: Path) -> None:
    """A tag on a branch that never reached main would publish unreviewed code."""
    _commit_on_side_branch(repo)
    with pytest.raises(release_notes.ReleaseError, match="not on main"):
        release_notes.check_on_main(repo, "main")


def test_unknown_main_ref_is_an_error_not_a_pass(repo: Path) -> None:
    """A workflow that fetched no ``origin/main`` must fail, not release."""
    with pytest.raises(release_notes.ReleaseError, match="cannot compare"):
        release_notes.check_on_main(repo, "origin/main")


def test_missing_git_is_an_error_not_a_pass(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runner without git cannot prove that the tag is on main."""
    monkeypatch.setattr(release_notes.shutil, "which", lambda _name: None)
    with pytest.raises(release_notes.ReleaseError, match="git is needed"):
        release_notes.check_on_main(repo, "main")


def test_tag_on_main_passes_the_main_check(release: Release, repo: Path) -> None:
    """The check is part of the command the workflow runs, after the version."""
    status, out, err = release(
        "v1.0.0-rc.1",
        manifest_version="1.0.0-rc.1",
        extra=("--main-ref", "main", "--repo", str(repo)),
    )
    assert status == 0
    assert err == ""
    assert out.startswith("Scope frozen.")


def test_tag_off_main_is_refused_before_anything_is_printed(
    release: Release, repo: Path
) -> None:
    """Nothing is published for a commit that main does not contain."""
    _commit_on_side_branch(repo)
    status, out, err = release(
        "v1.0.0-rc.1",
        manifest_version="1.0.0-rc.1",
        extra=("--main-ref", "main", "--repo", str(repo)),
    )
    assert status == 1
    assert out == ""
    assert "not on main" in err


def test_release_workflow_runs_only_on_version_tags() -> None:
    """A release is published from ``v*`` tags with a write scope limited to its job."""
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(text)
    assert workflow[True] == {"push": {"tags": ["v*"]}}  # YAML 1.1 reads ``on`` as True
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write"}


def test_release_workflow_passes_the_tag_through_the_environment() -> None:
    """A tag name is user input: no expression may be expanded inside a script."""
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    steps = yaml.safe_load(text)["jobs"]["release"]["steps"]
    scripts = [step["run"] for step in steps if "run" in step]
    assert scripts
    assert not [script for script in scripts if "${{" in script]


def test_release_workflow_checks_that_the_tag_is_on_main() -> None:
    """The checkout has the history to compare with, and the script is told to."""
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    steps = yaml.safe_load(text)["jobs"]["release"]["steps"]
    (checkout,) = (
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0
    (check,) = (step for step in steps if "release_notes.py" in step.get("run", ""))
    assert "--main-ref origin/main" in check["run"]
