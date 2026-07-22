#!/usr/bin/env python3
"""Build Chas as a self-contained Python zip application."""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import zipapp
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAS_ROOT = REPO_ROOT / "chas"
VERSION_PATTERN = re.compile(r'^VERSION = "([^"]+)"$', re.MULTILINE)


def read_version() -> str:
    source = (CHAS_ROOT / "src" / "version.py").read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(source)
    if match is None:
        raise RuntimeError("could not find VERSION in chas/src/version.py")
    return match.group(1)


def build(output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="chas-build-") as temp_dir:
        stage = Path(temp_dir)
        shutil.copy2(CHAS_ROOT / "chas.py", stage / "__main__.py")
        shutil.copy2(REPO_ROOT / "LICENSE", stage / "LICENSE")
        shutil.copytree(
            CHAS_ROOT / "src",
            stage / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        zipapp.create_archive(
            stage,
            target=output,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="output path (default: dist/chas-<version>.pyz)",
    )
    args = parser.parse_args()

    version = read_version()
    output = args.output or REPO_ROOT / "dist" / f"chas-{version}.pyz"
    built = build(output)
    print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
