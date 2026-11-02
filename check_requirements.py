"""Check every pinned line of a requirements file against PyPI for Python 3.11.

Usage: python check_requirements.py [requirements.txt | -]   ("-" reads from stdin)
With no argument, the built-in DEFAULT_REQUIREMENTS list is checked.
The target Python version is fixed (3.11), independent of the interpreter running this.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

TARGET_PYTHON = "3.11"
# Any 3.11.x patch release counts, so e.g. ">=3.11.4" is still treated as compatible.
TARGET_CANDIDATES = [Version(f"{TARGET_PYTHON}.{patch}") for patch in range(100)]
PYPI = "https://pypi.org/pypi"
HTTP_NOT_FOUND = 404
LOWER_BOUND_OPERATORS = {">=", ">", "==", "~=", "==="}

DEFAULT_REQUIREMENTS = """\
django==4.2
numpy==2.3.0
scipy==1.7.0
scikit-learn==1.4.0
fastapi-agent-router==0.3.1
maxplotlib==3.99.0
six==1.16.0
requests==2.32.0
"""

OK = "ok"
NOT_FOUND = "not found"
INCOMPATIBLE = "incompatible"


@dataclass
class Result:
    """Outcome of checking a single requirement line."""

    status: str
    detail: str
    pin: str = ""
    requires_python: str = ""
    min_python: Version | None = None


def fetch_json(url: str) -> dict | None:
    """Return the parsed JSON body of ``url``, or None on HTTP 404."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == HTTP_NOT_FOUND:
            return None
        raise


def pinned_version(req: Requirement) -> str | None:
    """Return the version of an exact ``==`` pin, or None if not pinned."""
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in {"==", "==="}:
        return specs[0].version
    return None


def supports_target(spec_set: SpecifierSet) -> bool:
    """Return True if any 3.11.x release satisfies ``spec_set``."""
    return any(spec_set.contains(v, prereleases=True) for v in TARGET_CANDIDATES)


def minimum_python(spec_set: SpecifierSet) -> Version | None:
    """Return the lowest Python version implied by ``spec_set``, or None."""
    lows = [
        Version(spec.version.replace(".*", ""))
        for spec in spec_set
        if spec.operator in LOWER_BOUND_OPERATORS
    ]
    return max(lows) if lows else None


def check_release(name: str, version: str) -> Result:
    """Check that ``name==version`` exists on PyPI and supports Python 3.11."""
    pin = f"{name}=={version}"
    release = fetch_json(f"{PYPI}/{name}/{version}/json")
    if release is None:
        exists = fetch_json(f"{PYPI}/{name}/json") is not None
        reason = "version does not exist" if exists else "package does not exist"
        return Result(NOT_FOUND, f"{pin} ({reason})")

    requires_python = (release["info"].get("requires_python") or "").strip()
    if not requires_python:
        return Result(OK, f"{pin} (no requires_python)")

    try:
        spec_set = SpecifierSet(requires_python)
    except InvalidSpecifier:
        return Result(OK, f"{pin} (unparseable requires_python: {requires_python!r})")

    status = OK if supports_target(spec_set) else INCOMPATIBLE
    return Result(
        status,
        f"{pin} ({requires_python})",
        pin,
        requires_python,
        minimum_python(spec_set),
    )


def check_line(line: str) -> Result:
    """Check one requirement line; unpinned lines only get an existence check."""
    try:
        req = Requirement(line)
    except InvalidRequirement:
        return Result(NOT_FOUND, f"{line} (invalid requirement)")

    version = pinned_version(req)
    if version is not None:
        return check_release(req.name, version)
    if fetch_json(f"{PYPI}/{req.name}/json") is None:
        return Result(NOT_FOUND, f"{req.name} (package does not exist)")
    return Result(OK, f"{line} (unpinned, version not checked)")


def read_lines(path: str | None) -> list[str]:
    """Read requirement lines, dropping blanks, comments and pip options."""
    if path is None:
        raw = DEFAULT_REQUIREMENTS
    elif path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    stripped = (line.split("#", 1)[0].strip() for line in raw.splitlines())
    return [line for line in stripped if line and not line.startswith("-")]


def main(path: str | None) -> None:
    """Check every requirement in ``path`` (or the built-in list) and print the summary."""
    try:
        lines = read_lines(path)
    except FileNotFoundError:
        sys.exit(
            f"File not found: {path}\n"
            "Usage: python check_requirements.py <requirements file>  "
            "(or pipe the list in and pass '-')"
        )
    results = [check_line(line) for line in lines]
    for result in results:
        print(f"  [{result.status}] {result.detail}")

    not_found = sum(result.status == NOT_FOUND for result in results)
    incompatible = sum(result.status == INCOMPATIBLE for result in results)
    highest = None
    for result in results:
        if result.min_python is not None and (
            highest is None or result.min_python > highest.min_python
        ):
            highest = result

    print()
    print(f"Dependencies checked: {len(lines)}")
    print(f"Not found on PyPI: {not_found}")
    print(f"Incompatible with Python {TARGET_PYTHON}: {incompatible}")
    if highest is None:
        print("Highest requirement: none")
    else:
        print(f"Highest requirement: {highest.pin} ({highest.requires_python})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
