from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAS_ROOT = REPO_ROOT / "chas"
CHAS_CLI = CHAS_ROOT / "chas.py"
sys.path.insert(0, str(CHAS_ROOT / "src"))

from errors import TypeError_  # noqa: E402
from evaluator import run  # noqa: E402
from lexer import tokenize  # noqa: E402
from parser import parse  # noqa: E402
from semantic import analyze  # noqa: E402


def compile_source(source: str):
    program = parse(tokenize(source, "test.chs"))
    analyze(program)
    return program


class ChasTests(unittest.TestCase):
    def test_examples_typecheck(self) -> None:
        for path in sorted((CHAS_ROOT / "examples").glob("*.chs")):
            with self.subTest(path=path.name):
                compile_source(path.read_text(encoding="utf-8"))

    def test_operator_precedence(self) -> None:
        program = compile_source("print(1 + 2 * 3)")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            run(program)

        self.assertEqual(output.getvalue().strip(), "7")

    def test_negative_remainder_matches_truncating_division(self) -> None:
        program = compile_source("print(-5 / 2)\nprint(-5 % 2)")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            run(program)

        self.assertEqual(output.getvalue().splitlines(), ["-2", "-1"])

    def test_closure_mutates_enclosing_scope(self) -> None:
        source = (CHAS_ROOT / "examples" / "closures.chs").read_text(
            encoding="utf-8"
        )
        program = compile_source(source)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            run(program)

        self.assertEqual(
            output.getvalue().splitlines(),
            ["1", "2", "3", "Hello, Alice!", "Hello, Bob!", "Hi, Alice!", "Hi, Bob!"],
        )

    def test_if_condition_must_be_boolean(self) -> None:
        program = parse(tokenize("if 1 { print(1) }", "test.chs"))

        with self.assertRaises(TypeError_):
            analyze(program)


class ChasCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHAS_CLI), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

    def test_version(self) -> None:
        result = self.run_cli("--version")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "Chas 0.1.0")
        self.assertEqual(result.stderr, "")

    def test_check_example(self) -> None:
        example = CHAS_ROOT / "examples" / "fibonacci.chs"
        result = self.run_cli("check", str(example))

        self.assertEqual(result.returncode, 0)
        self.assertIn("type-checks cleanly", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_run_example(self) -> None:
        example = CHAS_ROOT / "examples" / "hello.chs"
        result = self.run_cli("run", str(example))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.splitlines(),
            ["Hello, Chas!", "Year: 2026", "string", "int", "float", "bool"],
        )
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
