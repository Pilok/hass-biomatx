"""
Check a release tag and print the CHANGELOG section that becomes its notes.

``release.yml`` runs this on every ``v*`` tag and publishes nothing when it
fails: the tag must be ``v`` plus the ``version`` of ``manifest.json``, the
tagged commit must be on ``main`` (with ``--main-ref``), and ``CHANGELOG.md``
must have a ``## [version]`` section with something in it. Run it by hand
before tagging to see the notes the release will carry::

    .venv/bin/python scripts/release_notes.py v1.0.0 --main-ref origin/main
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "custom_components" / "biomatx" / "manifest.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)$")
"""A release tag: ``v`` and a Semantic Version, pre-release part optional."""
RELEASE_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]")
"""``## [1.0.0-rc.1] - 2026-10-07``: a release section of the changelog."""
ANY_SECTION_HEADING = re.compile(r"^## ")
LINK_REFERENCE = re.compile(r"^\[[^\]]+\]: ")
"""``[1.0.0]: https://...``: the links Keep a Changelog puts after the last section."""


class ReleaseError(Exception):
    """The tag cannot be released as it stands."""


def version_of_tag(tag: str) -> str:
    """Return the version a release tag stands for."""
    match = TAG.match(tag)
    if match is None:
        msg = f"tag {tag!r} is not v<major>.<minor>.<patch>[-<pre-release>]"
        raise ReleaseError(msg)
    return match["version"]


def check_manifest(tag: str, manifest: Path) -> str:
    """Return the version of ``tag`` once ``manifest`` is known to declare it."""
    version = version_of_tag(tag)
    declared = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    if declared != version:
        msg = f"{manifest.name} declares {declared}, but tag {tag} needs {version}"
        raise ReleaseError(msg)
    return version


def check_on_main(repo: Path, main_ref: str) -> None:
    """Refuse the commit checked out in ``repo`` unless ``main_ref`` contains it."""
    git = shutil.which("git")
    if git is None:
        msg = "git is needed to check that the tagged commit is on main"
        raise ReleaseError(msg)
    result = subprocess.run(  # noqa: S603  # list of arguments, no shell, git from PATH
        [git, "-C", str(repo), "merge-base", "--is-ancestor", "HEAD", main_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        msg = f"the tagged commit is not on {main_ref}: tag a commit of main"
    else:
        msg = (
            f"cannot compare the tagged commit with {main_ref}: {result.stderr.strip()}"
        )
    raise ReleaseError(msg)


def changelog_section(changelog: str, version: str) -> str:
    """Return the notes of ``version``: its section without the heading."""
    body: list[str] | None = None
    for line in changelog.splitlines():
        if body is None:
            heading = RELEASE_HEADING.match(line)
            if heading is not None and heading["version"] == version:
                body = []
        elif ANY_SECTION_HEADING.match(line) or LINK_REFERENCE.match(line):
            break
        else:
            body.append(line)
    if body is None:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ReleaseError(msg)
    notes = "\n".join(body).strip()
    if not notes:
        msg = f"the CHANGELOG.md section for {version} is empty"
        raise ReleaseError(msg)
    return f"{notes}\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Check the tag, print its release notes, and return the exit status."""
    parser = argparse.ArgumentParser(
        description="Check a release tag and print its CHANGELOG section."
    )
    parser.add_argument("tag", help="the tag being released, for example v1.0.0-rc.1")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    parser.add_argument(
        "--main-ref",
        help="also require the checked out commit to be contained in this ref, "
        "for example origin/main",
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        version = check_manifest(args.tag, args.manifest)
        if args.main_ref is not None:
            check_on_main(args.repo, args.main_ref)
        notes = changelog_section(args.changelog.read_text(encoding="utf-8"), version)
    except ReleaseError as err:
        sys.stderr.write(f"error: {err}\n")
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
