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

from errors import LexerError, TypeError_  # noqa: E402
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

    def test_evaluator_accepts_an_output_sink(self) -> None:
        program = compile_source('print("captured")')
        lines: list[str] = []

        run(program, output=lines.append)

        self.assertEqual(lines, ["captured"])

    def test_out_of_range_float_literal_is_a_language_error(self) -> None:
        source = "1" * 400 + ".0"

        with self.assertRaisesRegex(LexerError, "float literal is out of range"):
            tokenize(source, "test.chs")

    def test_integer_literal_limit_is_consistent_across_python_versions(self) -> None:
        source = "1" * 4_097

        with self.assertRaisesRegex(LexerError, "4,096 digit limit"):
            tokenize(source, "test.chs")

    def test_non_finite_float_result_formats_without_host_error(self) -> None:
        value = "9" * 200 + ".0"
        program = compile_source(f"print({value} * {value})")
        lines: list[str] = []

        run(program, output=lines.append)

        self.assertEqual(lines, ["inf"])

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

    def test_function_values_cannot_be_compared(self) -> None:
        program = parse(
            tokenize("fn f() {}\nlet g = f\nprint(f == g)", "test.chs")
        )

        with self.assertRaisesRegex(
            TypeError_, "requires matching primitive types"
        ):
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
        self.assertEqual(result.stdout.strip(), "Chas 0.2.0")
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

    def test_run_example_on_bytecode_vm(self) -> None:
        example = CHAS_ROOT / "examples" / "hello.chs"
        result = self.run_cli("run", str(example), "--engine", "vm")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.splitlines(),
            ["Hello, Chas!", "Year: 2026", "string", "int", "float", "bool"],
        )
        self.assertEqual(result.stderr, "")

    def test_bytecode_disassembly(self) -> None:
        example = CHAS_ROOT / "examples" / "fibonacci.chs"
        result = self.run_cli("bytecode", str(example))

        self.assertEqual(result.returncode, 0)
        self.assertIn("fib", result.stdout)
        self.assertIn("CALL", result.stdout)
        self.assertIn("RETURN", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_invalid_run_engine_is_a_usage_error(self) -> None:
        example = CHAS_ROOT / "examples" / "hello.chs"
        result = self.run_cli("run", str(example), "--engine", "jit")

        self.assertEqual(result.returncode, 2)
        self.assertIn("run engine must be 'tree' or 'vm'", result.stderr)


if __name__ == "__main__":
    unittest.main()
