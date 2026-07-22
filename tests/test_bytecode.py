from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAS_ROOT = REPO_ROOT / "chas"
sys.path.insert(0, str(CHAS_ROOT / "src"))

from bytecode import (  # noqa: E402
    BytecodeVM,
    BytecodeProgram,
    CodeObject,
    Instruction,
    Opcode,
    VirtualMachine,
    compile_program,
    disassemble,
    run as run_bytecode,
)
from errors import RuntimeError_, SourceLocation  # noqa: E402
from evaluator import run as run_tree  # noqa: E402
from lexer import tokenize  # noqa: E402
from parser import parse  # noqa: E402
from semantic import analyze  # noqa: E402


def checked_program(source: str):
    program = parse(tokenize(source, "test.chs"))
    analyze(program)
    return program


def output_from(runner, program) -> list[str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runner(program)
    return output.getvalue().splitlines()


class BytecodeCompilerTests(unittest.TestCase):
    def test_compile_and_disassemble_nested_function(self) -> None:
        program = checked_program(
            """
            fn outer(n: int) -> int {
                fn double(x: int) -> int { return x * 2 }
                return double(n)
            }
            print(outer(4))
            """
        )

        compiled = compile_program(program)
        listing = disassemble(compiled)

        self.assertIsInstance(compiled, BytecodeProgram)
        self.assertEqual(compiled.code.instructions[-1].opcode, Opcode.HALT)
        self.assertIn("== <main> ==", listing)
        self.assertIn("== outer(n) ==", listing)
        self.assertIn("== double(x) ==", listing)
        self.assertIn("MAKE_CLOSURE", listing)
        self.assertIn("test.chs:", listing)

    def test_constant_pool_keeps_python_equal_types_distinct(self) -> None:
        program = checked_program(
            'print(type(true))\nprint(type(1))\nprint(type(1.0))'
        )

        constants = compile_program(program).code.constants

        self.assertEqual(
            [type(value) for value in constants], [bool, int, float]
        )

    def test_short_circuit_compiles_to_jumps(self) -> None:
        program = checked_program("print(false && true || true)")
        opcodes = [
            instruction.opcode
            for instruction in compile_program(program).code.instructions
        ]

        self.assertIn(Opcode.JUMP_IF_FALSE, opcodes)
        self.assertIn(Opcode.JUMP_IF_TRUE, opcodes)


class BytecodeVmTests(unittest.TestCase):
    def assert_matches_tree(self, source: str) -> None:
        program = checked_program(source)
        self.assertEqual(
            output_from(run_bytecode, program), output_from(run_tree, program)
        )

    def test_existing_examples_match_tree_evaluator(self) -> None:
        for path in sorted((CHAS_ROOT / "examples").glob("*.chs")):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assert_matches_tree(source)

    def test_arithmetic_formatting_and_builtins(self) -> None:
        self.assert_matches_tree(
            """
            print(-5 / 2)
            print(-5 % 2)
            print(5 / -2)
            print(5 % -2)
            print(5.0 / 2.0)
            print(5.0 % 2.0)
            print("ch" + "as")
            print(len("hello"))
            print(type(true))
            print(type(1))
            print(type(1.0))
            print(type(0..3))
            """
        )

    def test_top_level_hoisting_supports_mutual_recursion(self) -> None:
        self.assert_matches_tree(
            """
            fn even(n: int) -> bool {
                if n == 0 { return true }
                return odd(n - 1)
            }
            fn odd(n: int) -> bool {
                if n == 0 { return false }
                return even(n - 1)
            }
            print(even(10))
            print(odd(9))
            """
        )

    def test_nested_self_recursion_and_reference_capture(self) -> None:
        self.assert_matches_tree(
            """
            fn outer(n: int) -> int {
                let calls = 0
                fn sum(current: int) -> int {
                    calls = calls + 1
                    if current == 0 { return 0 }
                    return current + sum(current - 1)
                }
                let answer = sum(n)
                print(calls)
                return answer
            }
            print(outer(5))
            """
        )

    def test_shadow_initializer_reads_outer_binding(self) -> None:
        self.assert_matches_tree(
            """
            let value = 10
            {
                let value = value + 5
                print(value)
            }
            print(value)
            """
        )

    def test_loops_use_fresh_scopes_and_evaluate_range_once(self) -> None:
        self.assert_matches_tree(
            """
            let bound_calls = 0
            fn bound() -> int {
                bound_calls = bound_calls + 1
                return 3
            }
            for i in 0..bound() {
                let doubled = i * 2
                print(doubled)
            }
            print(bound_calls)

            let n = 0
            while n < 3 {
                let per_iteration = n + 10
                print(per_iteration)
                n = n + 1
            }
            """
        )

    def test_short_circuit_skips_rhs_side_effects(self) -> None:
        self.assert_matches_tree(
            """
            let calls = 0
            fn yes() -> bool {
                calls = calls + 1
                return true
            }
            print(false && yes())
            print(true || yes())
            print(true && yes())
            print(false || yes())
            print(calls)
            """
        )

    def test_callee_and_arguments_are_evaluated_left_to_right(self) -> None:
        self.assert_matches_tree(
            """
            let order = 0
            fn first() -> int {
                order = order * 10 + 1
                return 4
            }
            fn second() -> int {
                order = order * 10 + 2
                return 5
            }
            fn add(a: int, b: int) -> int { return a + b }
            print(add(first(), second()))
            print(order)
            """
        )

    def test_runtime_error_keeps_operator_location_and_message(self) -> None:
        program = checked_program("print(10 / 0)")

        with self.assertRaises(RuntimeError_) as caught:
            run_bytecode(program)

        self.assertEqual(caught.exception.message, "integer division by zero")
        self.assertEqual(caught.exception.location.file, "test.chs")
        self.assertEqual(caught.exception.location.line, 1)
        self.assertEqual(caught.exception.location.column, 10)

    def test_explicit_call_stack_handles_deep_recursion(self) -> None:
        program = checked_program(
            """
            fn descend(n: int) -> int {
                if n == 0 { return 0 }
                return descend(n - 1)
            }
            print(descend(1500))
            """
        )

        self.assertEqual(output_from(run_bytecode, program), ["0"])

    def test_output_sink_can_be_injected(self) -> None:
        program = checked_program('print("hello")\nprint(42)')
        output: list[str] = []

        run_bytecode(program, output=output.append)

        self.assertEqual(output, ["hello", "42"])

    def test_vm_can_step_and_exposes_debugger_snapshots(self) -> None:
        program = checked_program("let answer = 42\nprint(answer)")
        output: list[str] = []
        vm = VirtualMachine(program, output=output.append)

        self.assertIs(BytecodeVM, VirtualMachine)
        self.assertFalse(vm.halted)
        self.assertEqual(vm.current_instruction.opcode, Opcode.CONSTANT)
        self.assertEqual(vm.current_location.line, 1)

        vm.step()
        self.assertEqual(vm.stack_snapshot, (42,))
        vm.step()
        self.assertEqual(vm.stack_snapshot, ())
        self.assertEqual(vm.scope_snapshot[0]["answer"], 42)

        vm.run()
        self.assertTrue(vm.halted)
        self.assertIsNone(vm.current_instruction)
        self.assertIsNone(vm.source_location)
        self.assertEqual(output, ["42"])

    def test_instruction_budget_stops_infinite_program_cleanly(self) -> None:
        program = checked_program("while true { }")
        vm = VirtualMachine(program)

        with self.assertRaises(RuntimeError_) as caught:
            vm.run(instruction_limit=8)

        self.assertEqual(
            caught.exception.message, "instruction limit of 8 exceeded"
        )
        self.assertEqual(caught.exception.location.file, "test.chs")
        self.assertEqual(caught.exception.location.line, 1)

    def test_none_instruction_budget_allows_more_than_default_limit(self) -> None:
        program = checked_program(
            """
            let count = 0
            while count < 10001 {
                count = count + 1
            }
            print(count)
            """
        )

        with self.assertRaises(RuntimeError_):
            run_bytecode(program, output=lambda line: None)

        output: list[str] = []
        run_bytecode(program, output=output.append, instruction_limit=None)
        self.assertEqual(output, ["10001"])

    def test_malformed_instruction_operands_are_source_mapped(self) -> None:
        location = SourceLocation("broken.chb", 7, 9)
        cases = {
            "constant type": ([Instruction(Opcode.CONSTANT, "zero", location)], []),
            "constant range": ([Instruction(Opcode.CONSTANT, 3, location)], []),
            "name": ([Instruction(Opcode.LOAD_NAME, 42, location)], []),
            "call count": ([Instruction(Opcode.CALL, "one", location)], []),
            "jump target": ([Instruction(Opcode.JUMP, 9, location)], []),
            "unexpected operand": ([Instruction(Opcode.HALT, 1, location)], []),
            "closure code": (
                [Instruction(Opcode.MAKE_CLOSURE, 0, location)],
                ["not code"],
            ),
        }

        for label, (instructions, constants) in cases.items():
            with self.subTest(label=label):
                code = CodeObject(
                    "<broken>", (), instructions, constants, location
                )
                vm = VirtualMachine(BytecodeProgram(code))
                with self.assertRaises(RuntimeError_) as caught:
                    vm.run(instruction_limit=None)
                self.assertTrue(
                    caught.exception.message.startswith("invalid bytecode:")
                )
                self.assertIs(caught.exception.location, location)

    def test_malformed_stack_value_types_do_not_leak_type_error(self) -> None:
        location = SourceLocation("broken.chb", 4, 2)
        code = CodeObject(
            "<broken>",
            (),
            [
                Instruction(Opcode.CONSTANT, 0, location),
                Instruction(Opcode.CONSTANT, 1, location),
                Instruction(Opcode.ADD, location=location),
                Instruction(Opcode.HALT, location=location),
            ],
            ["text", 1],
            location,
        )

        with self.assertRaises(RuntimeError_) as caught:
            VirtualMachine(BytecodeProgram(code)).run(instruction_limit=None)

        self.assertTrue(caught.exception.message.startswith("invalid bytecode:"))
        self.assertIs(caught.exception.location, location)

    def test_disassembler_rejects_bad_constant_index_cleanly(self) -> None:
        location = SourceLocation("broken.chb", 2, 3)
        code = CodeObject(
            "<broken>",
            (),
            [Instruction(Opcode.CONSTANT, -1, location)],
            [],
            location,
        )

        with self.assertRaises(RuntimeError_) as caught:
            disassemble(BytecodeProgram(code))

        self.assertTrue(caught.exception.message.startswith("invalid bytecode:"))
        self.assertIs(caught.exception.location, location)

    def test_non_finite_float_formatting_is_safe(self) -> None:
        location = SourceLocation("generated.chb", 1, 1)
        code = CodeObject(
            "<generated>",
            (),
            [
                Instruction(Opcode.LOAD_NAME, "print", location),
                Instruction(Opcode.CONSTANT, 0, location),
                Instruction(Opcode.CALL, 1, location),
                Instruction(Opcode.POP, location=location),
                Instruction(Opcode.HALT, location=location),
            ],
            [float("inf")],
            location,
        )
        output: list[str] = []

        run_bytecode(
            BytecodeProgram(code), output=output.append, instruction_limit=None
        )

        self.assertEqual(output, ["inf"])

    def test_large_integer_formatting_matches_tree_evaluator(self) -> None:
        power = "1" + "0" * 4_095
        expected = "1" + "0" * 8_190
        program = checked_program(
            f"let huge = {power} * {power}\n"
            "print(huge)\n"
            "print(range(huge, 0))"
        )

        tree = output_from(run_tree, program)
        vm = output_from(run_bytecode, program)

        self.assertEqual(tree, [expected, expected + "..0"])
        self.assertEqual(vm, tree)

    def test_overflowed_float_formatting_matches_tree_evaluator(self) -> None:
        huge = "9" * 200 + ".0"

        self.assert_matches_tree(f"print({huge} * {huge})")


if __name__ == "__main__":
    unittest.main()
