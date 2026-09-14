"""
The protocol package must stay free of Home Assistant, pyserial and ``biomatx``.

It is the piece that gets fuzzed, benchmarked and reused; a dependency on the
runtime would make it untestable in isolation and would drag the ``biomatx``
package back after its removal.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

PROTOCOL_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "biomatx"
    / "protocol"
)
FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+(?:homeassistant|biomatx|serial|serial_asyncio|serialx)\b",
    re.MULTILINE,
)


def test_protocol_sources_import_no_runtime_package() -> None:
    """No import line names Home Assistant, a serial library or ``biomatx``."""
    sources = sorted(PROTOCOL_DIR.glob("*.py"))
    assert sources, "protocol package not found"
    for source in sources:
        assert FORBIDDEN.search(source.read_text()) is None, source.name


def test_protocol_package_imports_without_the_runtime_installed() -> None:
    """
    Importing the package alone, in a bare interpreter, loads none of them.

    The integration package itself imports Home Assistant, so ``protocol`` is
    put on the path directly: that is how a fuzzing or benchmark script runs it.
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(PROTOCOL_DIR.parent)!r})\n"
        "import protocol\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] in "
        "('homeassistant', 'biomatx', 'serial', 'serial_asyncio', 'serialx')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""
