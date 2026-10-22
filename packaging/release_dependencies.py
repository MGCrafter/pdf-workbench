"""One version-pinned runtime/build inventory for notices and binary provenance."""
from pathlib import Path
import re

TEST_ONLY = frozenset(("reportlab",))


def release_distributions():
    """Every pinned distribution except explicitly removed test-only packages."""
    result = {}
    path = Path(__file__).with_name("requirements-release.txt")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"): continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", stripped)
        if not match: raise ValueError("Release dependencies must have one exact version pin per line")
        name, version = match.groups()
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in TEST_ONLY: continue
        if normalized == "pyinstaller": name = "PyInstaller"
        if normalized in {re.sub(r"[-_.]+", "-", key).lower() for key in result}:
            raise ValueError("Duplicate release dependency pin")
        result[name] = version
    if not {"PyMuPDF", "pyHanko", "pyhanko-certvalidator", "lxml", "aiohttp"} <= result.keys():
        raise ValueError("The 0.9 release runtime inventory is incomplete")
    return result
