from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path


CHAS_ROOT = Path(__file__).resolve().parents[1] / "chas"
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

    def test_closure_mutates_enclosing_scope(self) -> None:
        source = (CHAS_ROOT / "examples" / "closures.chs").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
