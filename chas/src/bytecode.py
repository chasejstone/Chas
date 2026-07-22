"""Bytecode compiler and virtual machine for Chas.

The tree-walking evaluator is intentionally small and readable.  This module
provides a second execution backend without changing that reference
implementation: an AST is lowered to compact stack instructions and executed
by an explicit-frame virtual machine.

Runtime environments remain chained dictionaries.  That is a deliberate
choice rather than a shortcut: it gives the VM the language's existing lexical
scope and by-reference closure behavior exactly, including fresh environments
for loop iterations.  A later optimizer can replace name operations with
local/upvalue slots without changing the bytecode-facing API.

Public entry points:

    compile_program(program)  -> BytecodeProgram
    disassemble(program)      -> str
    run(program)              -> None

Only the Python standard library is used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Iterable, List, Optional

import ast_nodes as ast
from errors import RuntimeError_, SourceLocation
from runtime_format import format_integer


# ---------------------------------------------------------------------------
# Bytecode representation
# ---------------------------------------------------------------------------


class Opcode(Enum):
    """Operations understood by :class:`VirtualMachine`."""

    # Values and names.
    CONSTANT = auto()
    LOAD_NAME = auto()
    DEFINE_NAME = auto()
    STORE_NAME = auto()
    POP = auto()
    DUP = auto()

    # Lexical environments and functions.
    ENTER_SCOPE = auto()
    EXIT_SCOPE = auto()
    MAKE_CLOSURE = auto()
    CALL = auto()
    RETURN = auto()
    RETURN_VOID = auto()

    # Unary and binary expressions.
    NEGATE = auto()
    NOT = auto()
    ADD = auto()
    SUBTRACT = auto()
    MULTIPLY = auto()
    DIVIDE = auto()
    MODULO = auto()
    EQUAL = auto()
    NOT_EQUAL = auto()
    LESS = auto()
    LESS_EQUAL = auto()
    GREATER = auto()
    GREATER_EQUAL = auto()
    MAKE_RANGE = auto()

    # Control flow and iteration.
    JUMP = auto()
    JUMP_IF_FALSE = auto()
    JUMP_IF_TRUE = auto()
    GET_ITER = auto()
    ITER_NEXT = auto()
    HALT = auto()


@dataclass
class Instruction:
    """One operation, its optional operand, and its source position."""

    opcode: Opcode
    operand: object = None
    location: SourceLocation = field(
        default_factory=lambda: SourceLocation("<?>", 0, 0)
    )


@dataclass
class CodeObject:
    """Instructions and constants for the program or one Chas function."""

    name: str
    params: tuple[str, ...]
    instructions: List[Instruction]
    constants: List[object]
    location: SourceLocation


@dataclass
class BytecodeProgram:
    """The compiled top-level code object."""

    code: CodeObject


def _invalid_bytecode(message: str, location: SourceLocation) -> None:
    """Raise the public, source-mapped error used for malformed bytecode."""

    raise RuntimeError_(f"invalid bytecode: {message}", location)


def _integer_operand(
    operand: object,
    description: str,
    location: SourceLocation,
) -> int:
    # bool is an int subclass, but it is never a valid bytecode operand.
    if type(operand) is not int or operand < 0:
        _invalid_bytecode(
            f"{description} must be a non-negative integer, got {operand!r}",
            location,
        )
    return operand


def _constant_at(
    code: CodeObject,
    operand: object,
    location: SourceLocation,
) -> tuple[int, object]:
    index = _integer_operand(operand, "constant index", location)
    try:
        count = len(code.constants)
    except TypeError:
        _invalid_bytecode("code object has an invalid constant pool", location)
    if index >= count:
        _invalid_bytecode(
            f"constant index {index} is out of range for {count} constant(s)",
            location,
        )
    try:
        return index, code.constants[index]
    except (IndexError, TypeError):
        _invalid_bytecode(f"cannot read constant index {index}", location)


def _name_operand(operand: object, location: SourceLocation) -> str:
    if not isinstance(operand, str) or not operand:
        _invalid_bytecode(
            f"name operand must be a non-empty string, got {operand!r}",
            location,
        )
    return operand


def _jump_target(
    code: CodeObject,
    operand: object,
    location: SourceLocation,
) -> int:
    target = _integer_operand(operand, "jump target", location)
    try:
        count = len(code.instructions)
    except TypeError:
        _invalid_bytecode("code object has an invalid instruction stream", location)
    if target >= count:
        _invalid_bytecode(
            f"jump target {target} is outside the {count}-instruction code object",
            location,
        )
    return target


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


_BINARY_OPCODES = {
    "+": Opcode.ADD,
    "-": Opcode.SUBTRACT,
    "*": Opcode.MULTIPLY,
    "/": Opcode.DIVIDE,
    "%": Opcode.MODULO,
    "==": Opcode.EQUAL,
    "!=": Opcode.NOT_EQUAL,
    "<": Opcode.LESS,
    "<=": Opcode.LESS_EQUAL,
    ">": Opcode.GREATER,
    ">=": Opcode.GREATER_EQUAL,
}


class _CodeBuilder:
    def __init__(
        self,
        name: str,
        params: Iterable[str],
        location: SourceLocation,
    ) -> None:
        self.name = name
        self.params = tuple(params)
        self.location = location
        self.instructions: List[Instruction] = []
        self.constants: List[object] = []

    def emit(
        self,
        opcode: Opcode,
        operand: object = None,
        location: Optional[SourceLocation] = None,
    ) -> int:
        index = len(self.instructions)
        self.instructions.append(
            Instruction(opcode, operand, location or self.location)
        )
        return index

    def constant(self, value: object) -> int:
        # Do not deduplicate with equality: in Python, True == 1 == 1.0.
        # Keeping constants append-only preserves Chas's runtime type names.
        self.constants.append(value)
        return len(self.constants) - 1

    def patch(self, instruction: int, target: Optional[int] = None) -> None:
        self.instructions[instruction].operand = (
            len(self.instructions) if target is None else target
        )

    def finish(self) -> CodeObject:
        return CodeObject(
            name=self.name,
            params=self.params,
            instructions=self.instructions,
            constants=self.constants,
            location=self.location,
        )


class BytecodeCompiler:
    """Lower a parsed Chas program to stack bytecode.

    The compiler expects the normal semantic analysis pass to have succeeded.
    It still reports an unsupported AST node as a Chas RuntimeError so callers
    using the stages independently do not receive an implementation traceback.
    """

    def compile(self, program: ast.Program) -> BytecodeProgram:
        builder = _CodeBuilder("<main>", (), program.location)

        # Match the evaluator's top-level-only function hoisting.  Each closure
        # captures the same mutable global environment, so mutual recursion is
        # available before any ordinary top-level statement executes.
        for stmt in program.statements:
            if isinstance(stmt, ast.FnDecl):
                self._function_declaration(stmt, builder)

        for stmt in program.statements:
            if not isinstance(stmt, ast.FnDecl):
                self._statement(stmt, builder)

        builder.emit(Opcode.HALT, location=program.location)
        return BytecodeProgram(builder.finish())

    def _compile_function(self, node: ast.FnDecl) -> CodeObject:
        builder = _CodeBuilder(
            node.name,
            (param.name for param in node.params),
            node.location,
        )

        # The evaluator creates a parameter environment and then a child body
        # environment.  Valid programs cannot redeclare a parameter in the body
        # (the analyzer rejects it), but mirroring the shape keeps closure and
        # debugger state faithful to the reference backend.
        builder.emit(Opcode.ENTER_SCOPE, location=node.body.location)
        for stmt in node.body.statements:
            self._statement(stmt, builder)
        builder.emit(Opcode.EXIT_SCOPE, location=node.body.location)
        builder.emit(Opcode.RETURN_VOID, location=node.location)
        return builder.finish()

    def _function_declaration(
        self, node: ast.FnDecl, builder: _CodeBuilder
    ) -> None:
        code_index = builder.constant(self._compile_function(node))
        # MAKE_CLOSURE runs before DEFINE_NAME.  The closure holds the current
        # environment by reference, so the subsequent definition is visible to
        # the function itself and nested self-recursion works.
        builder.emit(Opcode.MAKE_CLOSURE, code_index, node.location)
        builder.emit(Opcode.DEFINE_NAME, node.name, node.location)

    def _statement(self, node: ast.Stmt, builder: _CodeBuilder) -> None:
        if isinstance(node, ast.LetDecl):
            self._expression(node.value, builder)
            builder.emit(Opcode.DEFINE_NAME, node.name, node.location)
            return

        if isinstance(node, ast.FnDecl):
            self._function_declaration(node, builder)
            return

        if isinstance(node, ast.Return):
            if node.value is None:
                builder.emit(Opcode.RETURN_VOID, location=node.location)
            else:
                self._expression(node.value, builder)
                builder.emit(Opcode.RETURN, location=node.location)
            return

        if isinstance(node, ast.If):
            self._if_statement(node, builder)
            return

        if isinstance(node, ast.While):
            loop_start = len(builder.instructions)
            self._expression(node.condition, builder)
            exit_jump = builder.emit(
                Opcode.JUMP_IF_FALSE, location=node.condition.location
            )
            self._block(node.body, builder)
            builder.emit(Opcode.JUMP, loop_start, node.location)
            builder.patch(exit_jump)
            return

        if isinstance(node, ast.For):
            # The iterable is evaluated once.  ITER_NEXT keeps the iterator on
            # the stack and pushes one value, or removes it and exits the loop.
            self._expression(node.iterable, builder)
            builder.emit(Opcode.GET_ITER, location=node.iterable.location)
            loop_start = len(builder.instructions)
            exit_jump = builder.emit(Opcode.ITER_NEXT, location=node.location)

            # A for body uses one fresh environment per iteration, containing
            # both the loop variable and declarations directly in the body.
            # Compiling the Block through _block would add an incorrect scope.
            builder.emit(Opcode.ENTER_SCOPE, location=node.body.location)
            builder.emit(Opcode.DEFINE_NAME, node.var_name, node.location)
            for stmt in node.body.statements:
                self._statement(stmt, builder)
            builder.emit(Opcode.EXIT_SCOPE, location=node.body.location)
            builder.emit(Opcode.JUMP, loop_start, node.location)
            builder.patch(exit_jump)
            return

        if isinstance(node, ast.Assign):
            self._expression(node.value, builder)
            builder.emit(Opcode.STORE_NAME, node.name, node.location)
            return

        if isinstance(node, ast.ExprStmt):
            self._expression(node.expression, builder)
            builder.emit(Opcode.POP, location=node.location)
            return

        if isinstance(node, ast.Block):
            self._block(node, builder)
            return

        self._unsupported(node, "statement")

    def _block(self, node: ast.Block, builder: _CodeBuilder) -> None:
        builder.emit(Opcode.ENTER_SCOPE, location=node.location)
        for stmt in node.statements:
            self._statement(stmt, builder)
        builder.emit(Opcode.EXIT_SCOPE, location=node.location)

    def _if_statement(self, node: ast.If, builder: _CodeBuilder) -> None:
        self._expression(node.condition, builder)
        else_jump = builder.emit(
            Opcode.JUMP_IF_FALSE, location=node.condition.location
        )
        self._block(node.then_branch, builder)

        if node.else_branch is None:
            builder.patch(else_jump)
            return

        end_jump = builder.emit(Opcode.JUMP, location=node.location)
        builder.patch(else_jump)
        if isinstance(node.else_branch, ast.If):
            # An `else if` does not introduce a scope around its condition.
            self._if_statement(node.else_branch, builder)
        else:
            self._block(node.else_branch, builder)
        builder.patch(end_jump)

    def _expression(self, node: ast.Expr, builder: _CodeBuilder) -> None:
        if isinstance(
            node,
            (ast.IntLiteral, ast.FloatLiteral, ast.StringLiteral, ast.BoolLiteral),
        ):
            index = builder.constant(node.value)
            builder.emit(Opcode.CONSTANT, index, node.location)
            return

        if isinstance(node, ast.Identifier):
            builder.emit(Opcode.LOAD_NAME, node.name, node.location)
            return

        if isinstance(node, ast.UnaryOp):
            self._expression(node.operand, builder)
            opcode = Opcode.NOT if node.op == "!" else Opcode.NEGATE
            builder.emit(opcode, location=node.location)
            return

        if isinstance(node, ast.BinaryOp):
            if node.op in ("&&", "||"):
                self._short_circuit(node, builder)
                return
            self._expression(node.left, builder)
            self._expression(node.right, builder)
            opcode = _BINARY_OPCODES.get(node.op)
            if opcode is None:
                self._unsupported(node, f"binary operator {node.op!r}")
            builder.emit(opcode, location=node.location)
            return

        if isinstance(node, ast.RangeExpr):
            self._expression(node.start, builder)
            self._expression(node.end, builder)
            builder.emit(Opcode.MAKE_RANGE, location=node.location)
            return

        if isinstance(node, ast.Call):
            # Preserve the evaluator's left-to-right order: callee first, then
            # each argument in source order.
            self._expression(node.callee, builder)
            for arg in node.args:
                self._expression(arg, builder)
            builder.emit(Opcode.CALL, len(node.args), node.location)
            return

        self._unsupported(node, "expression")

    def _short_circuit(
        self, node: ast.BinaryOp, builder: _CodeBuilder
    ) -> None:
        self._expression(node.left, builder)
        builder.emit(Opcode.DUP, location=node.location)
        jump_opcode = (
            Opcode.JUMP_IF_FALSE if node.op == "&&" else Opcode.JUMP_IF_TRUE
        )
        end_jump = builder.emit(jump_opcode, location=node.location)
        # A taken jump consumed the duplicate and leaves the original result.
        # A fallthrough discards that original before computing the RHS.
        builder.emit(Opcode.POP, location=node.location)
        self._expression(node.right, builder)
        builder.patch(end_jump)

    @staticmethod
    def _unsupported(node: ast.Node, kind: str) -> None:
        raise RuntimeError_(
            f"cannot compile {kind} {type(node).__name__}", node.location
        )


def compile_program(program: ast.Program) -> BytecodeProgram:
    """Compile a parsed (normally already type-checked) Chas program."""

    return BytecodeCompiler().compile(program)


# ---------------------------------------------------------------------------
# Runtime values
# ---------------------------------------------------------------------------


@dataclass
class _Range:
    start: int
    end: int

    def __iter__(self):
        return iter(range(self.start, self.end))


class _Environment:
    __slots__ = ("values", "parent")

    def __init__(self, parent: Optional["_Environment"] = None):
        self.values: Dict[str, object] = {}
        self.parent = parent

    def define(self, name: str, value: object) -> None:
        self.values[name] = value

    def get(self, name: str, location: SourceLocation) -> object:
        env: Optional[_Environment] = self
        while env is not None:
            if name in env.values:
                return env.values[name]
            env = env.parent
        raise RuntimeError_(f"undefined name {name!r}", location)

    def assign(
        self, name: str, value: object, location: SourceLocation
    ) -> None:
        env: Optional[_Environment] = self
        while env is not None:
            if name in env.values:
                env.values[name] = value
                return
            env = env.parent
        raise RuntimeError_(f"undefined name {name!r}", location)


@dataclass
class _Function:
    code: CodeObject
    closure: _Environment


@dataclass
class _Builtin:
    name: str
    arity: int
    function: Callable[[List[object], SourceLocation], object]


def _type_name(value: object) -> str:
    # bool is an int subclass in Python, so its check must come first.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, _Range):
        return "range"
    if isinstance(value, (_Function, _Builtin)):
        return "fn"
    return "unknown"


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "void"
    if isinstance(value, int):
        return format_integer(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        if value == int(value):
            return f"{value:.1f}"
        return repr(value)
    if isinstance(value, _Range):
        return (
            f"{format_integer(value.start)}..{format_integer(value.end)}"
        )
    if isinstance(value, _Function):
        return f"<fn {value.code.name}>"
    if isinstance(value, _Builtin):
        return f"<builtin {value.name}>"
    return str(value)


def _truncating_quotient(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return quotient if (a >= 0) == (b >= 0) else -quotient


def _builtin_print(args: List[object], location: SourceLocation) -> object:
    print(_format(args[0]))
    return None


def _builtin_len(args: List[object], location: SourceLocation) -> object:
    value = args[0]
    if not isinstance(value, str):
        raise RuntimeError_(
            f"len() requires string, got {_type_name(value)}", location
        )
    return len(value)


def _builtin_range(args: List[object], location: SourceLocation) -> object:
    start, end = args
    if type(start) is not int or type(end) is not int:
        _invalid_bytecode("range() requires two int values", location)
    return _Range(start, end)


def _builtin_type(args: List[object], location: SourceLocation) -> object:
    return _type_name(args[0])


_BUILTINS: Dict[str, _Builtin] = {
    "print": _Builtin("print", 1, _builtin_print),
    "len": _Builtin("len", 1, _builtin_len),
    "range": _Builtin("range", 2, _builtin_range),
    "type": _Builtin("type", 1, _builtin_type),
}


# ---------------------------------------------------------------------------
# Virtual machine
# ---------------------------------------------------------------------------


@dataclass
class _Frame:
    code: CodeObject
    ip: int
    environment: _Environment
    stack_base: int


class VirtualMachine:
    """Execute Chas bytecode with an explicit operand and call stack.

    Besides the all-at-once :meth:`run` API, the VM exposes one-instruction
    stepping and immutable snapshots.  Those small inspection primitives are
    enough for a debugger or teaching UI without coupling the runtime to one.
    """

    def __init__(
        self,
        program: ast.Program | BytecodeProgram | None = None,
        *,
        output: Callable[[str], object] = print,
    ) -> None:
        self._output = output
        self._globals = _Environment()
        self._stack: List[object] = []
        self._frames: List[_Frame] = []
        self._program: Optional[BytecodeProgram] = None
        self._reset_globals()
        if program is not None:
            self.load(program)

    @property
    def halted(self) -> bool:
        """Whether there is no instruction left to execute."""

        return not self._frames

    @property
    def current_instruction(self) -> Optional[Instruction]:
        """The next instruction that :meth:`step` will execute."""

        if not self._frames:
            return None
        frame = self._frames[-1]
        if frame.ip >= len(frame.code.instructions):
            return None
        instruction = frame.code.instructions[frame.ip]
        if not isinstance(instruction, Instruction):
            _invalid_bytecode(
                f"instruction {frame.ip} is not an Instruction",
                frame.code.location,
            )
        return instruction

    @property
    def current_location(self) -> Optional[SourceLocation]:
        """Source location of :attr:`current_instruction`, if any."""

        instruction = self.current_instruction
        return None if instruction is None else instruction.location

    @property
    def source_location(self) -> Optional[SourceLocation]:
        """Debugger-friendly alias for :attr:`current_location`."""

        return self.current_location

    @property
    def stack_snapshot(self) -> tuple[object, ...]:
        """A stable, read-only snapshot of the operand stack."""

        return tuple(self._stack)

    @property
    def scope_snapshot(self) -> tuple[dict[str, object], ...]:
        """Copied lexical scopes, ordered from innermost to global."""

        if not self._frames:
            return ()
        scopes: List[dict[str, object]] = []
        environment: Optional[_Environment] = self._frames[-1].environment
        while environment is not None:
            scopes.append(dict(environment.values))
            environment = environment.parent
        return tuple(scopes)

    def load(self, program: ast.Program | BytecodeProgram) -> None:
        """Reset the VM and prepare ``program`` for stepping or running."""

        compiled = (
            program
            if isinstance(program, BytecodeProgram)
            else compile_program(program)
        )
        self._program = compiled
        self._stack = []
        self._frames = []
        self._reset_globals()
        self._frames.append(_Frame(compiled.code, 0, self._globals, 0))

    def step(self) -> bool:
        """Execute one instruction and return ``True`` while execution remains."""

        if not self._frames:
            return False

        frame = self._frames[-1]
        if frame.ip >= len(frame.code.instructions):
            # Compiler-produced function bodies always end in RETURN_VOID,
            # and main ends in HALT.  Treat malformed bytecode as a clean
            # implicit void return instead of leaking IndexError.
            self._return(None)
            return not self.halted

        instruction = frame.code.instructions[frame.ip]
        if not isinstance(instruction, Instruction):
            self._invalid(
                f"instruction {frame.ip} is not an Instruction",
                frame.code.location,
            )
        frame.ip += 1
        self._dispatch(instruction, frame)
        return not self.halted

    def run(self, instruction_limit: Optional[int] = 100_000) -> None:
        """Run until HALT, rejecting programs that exhaust the step budget."""

        if instruction_limit is not None and (
            type(instruction_limit) is not int or instruction_limit < 0
        ):
            raise ValueError("instruction_limit must be a non-negative int or None")
        if self._program is None:
            raise RuntimeError_("no bytecode program loaded")

        executed = 0
        while not self.halted:
            if (
                instruction_limit is not None
                and executed >= instruction_limit
            ):
                location = self.current_location or self._program.code.location
                raise RuntimeError_(
                    f"instruction limit of {instruction_limit} exceeded",
                    location,
                )
            self.step()
            executed += 1

    def execute(
        self,
        program: ast.Program | BytecodeProgram,
        instruction_limit: Optional[int] = 100_000,
    ) -> None:
        """Compatibility convenience combining :meth:`load` and :meth:`run`."""

        self.load(program)
        self.run(instruction_limit=instruction_limit)

    def _reset_globals(self) -> None:
        self._globals = _Environment()
        self._globals.define("print", _Builtin("print", 1, self._print))
        for name, builtin in _BUILTINS.items():
            if name != "print":
                self._globals.define(name, builtin)

    def _print(self, args: List[object], location: SourceLocation) -> object:
        self._output(_format(args[0]))
        return None

    def _dispatch(self, instruction: Instruction, frame: _Frame) -> None:
        op = instruction.opcode
        arg = instruction.operand
        location = instruction.location

        if not isinstance(op, Opcode):
            self._invalid(f"unknown opcode {op!r}", location)
        if op in _NO_OPERAND_OPS and arg is not None:
            self._invalid(
                f"{op.name} does not accept an operand, got {arg!r}",
                location,
            )

        if op is Opcode.CONSTANT:
            _, constant = _constant_at(frame.code, arg, location)
            self._stack.append(constant)
        elif op is Opcode.LOAD_NAME:
            self._stack.append(
                frame.environment.get(_name_operand(arg, location), location)
            )
        elif op is Opcode.DEFINE_NAME:
            frame.environment.define(
                _name_operand(arg, location), self._pop(location)
            )
        elif op is Opcode.STORE_NAME:
            frame.environment.assign(
                _name_operand(arg, location), self._pop(location), location
            )
        elif op is Opcode.POP:
            self._pop(location)
        elif op is Opcode.DUP:
            if not self._stack:
                self._invalid("cannot duplicate an empty stack", location)
            self._stack.append(self._stack[-1])
        elif op is Opcode.ENTER_SCOPE:
            frame.environment = _Environment(parent=frame.environment)
        elif op is Opcode.EXIT_SCOPE:
            if frame.environment.parent is None:
                self._invalid("cannot exit the global scope", location)
            frame.environment = frame.environment.parent
        elif op is Opcode.MAKE_CLOSURE:
            _, code = _constant_at(frame.code, arg, location)
            if not isinstance(code, CodeObject):
                self._invalid("closure constant is not code", location)
            self._stack.append(_Function(code, frame.environment))
        elif op is Opcode.CALL:
            self._call(
                _integer_operand(arg, "argument count", location), location
            )
        elif op is Opcode.RETURN:
            self._return(self._pop(location))
        elif op is Opcode.RETURN_VOID:
            self._return(None)
        elif op is Opcode.NEGATE:
            value = self._pop(location)
            if type(value) not in (int, float):
                self._invalid("NEGATE requires a numeric value", location)
            self._stack.append(-value)
        elif op is Opcode.NOT:
            value = self._pop(location)
            if type(value) is not bool:
                self._invalid("NOT requires a bool value", location)
            self._stack.append(not value)
        elif op in _BINARY_RUNTIME_OPS:
            self._binary(op, location)
        elif op is Opcode.MAKE_RANGE:
            end = self._pop(location)
            start = self._pop(location)
            if type(start) is not int or type(end) is not int:
                self._invalid("MAKE_RANGE requires two int values", location)
            self._stack.append(_Range(start, end))
        elif op is Opcode.JUMP:
            frame.ip = _jump_target(frame.code, arg, location)
        elif op is Opcode.JUMP_IF_FALSE:
            target = _jump_target(frame.code, arg, location)
            condition = self._pop(location)
            if type(condition) is not bool:
                self._invalid("JUMP_IF_FALSE requires a bool value", location)
            if not condition:
                frame.ip = target
        elif op is Opcode.JUMP_IF_TRUE:
            target = _jump_target(frame.code, arg, location)
            condition = self._pop(location)
            if type(condition) is not bool:
                self._invalid("JUMP_IF_TRUE requires a bool value", location)
            if condition:
                frame.ip = target
        elif op is Opcode.GET_ITER:
            value = self._pop(location)
            if not isinstance(value, _Range):
                raise RuntimeError_("for-in iterable must be a range", location)
            self._stack.append(iter(value))
        elif op is Opcode.ITER_NEXT:
            target = _jump_target(frame.code, arg, location)
            if not self._stack:
                self._invalid("ITER_NEXT has no iterator", location)
            iterator = self._stack[-1]
            try:
                self._stack.append(next(iterator))
            except StopIteration:
                self._stack.pop()
                frame.ip = target
            except TypeError:
                self._invalid("ITER_NEXT operand is not an iterator", location)
        elif op is Opcode.HALT:
            self._frames.clear()
        else:  # pragma: no cover - exhaustive guard for corrupted bytecode
            self._invalid(f"unknown opcode {op!r}", location)

    def _call(self, argument_count: int, location: SourceLocation) -> None:
        callee_index = len(self._stack) - argument_count - 1
        if callee_index < 0:
            self._invalid("CALL does not have enough operands", location)

        callee = self._stack[callee_index]
        arguments = self._stack[callee_index + 1 :]
        del self._stack[callee_index:]

        if isinstance(callee, _Builtin):
            if len(arguments) != callee.arity:
                self._invalid(
                    f"builtin {callee.name!r} expects {callee.arity} "
                    f"argument(s), got {len(arguments)}",
                    location,
                )
            self._stack.append(callee.function(arguments, location))
            return

        if not isinstance(callee, _Function):
            raise RuntimeError_("attempt to call a non-function value", location)

        if len(arguments) != len(callee.code.params):
            self._invalid(
                f"function {callee.code.name!r} expects "
                f"{len(callee.code.params)} argument(s), got {len(arguments)}",
                location,
            )

        environment = _Environment(parent=callee.closure)
        for name, value in zip(callee.code.params, arguments):
            environment.define(name, value)
        self._frames.append(
            _Frame(callee.code, 0, environment, len(self._stack))
        )

    def _return(self, value: object) -> None:
        frame = self._frames.pop()
        del self._stack[frame.stack_base:]
        if self._frames:
            self._stack.append(value)

    def _binary(self, opcode: Opcode, location: SourceLocation) -> None:
        right = self._pop(location)
        left = self._pop(location)

        if opcode is Opcode.ADD:
            if not (
                type(left) is type(right)
                and type(left) in (int, float, str)
            ):
                self._invalid("ADD received incompatible values", location)
            result = left + right
        elif opcode is Opcode.SUBTRACT:
            self._require_matching_numbers(left, right, "SUBTRACT", location)
            result = left - right
        elif opcode is Opcode.MULTIPLY:
            self._require_matching_numbers(left, right, "MULTIPLY", location)
            result = left * right
        elif opcode is Opcode.DIVIDE:
            self._require_matching_numbers(left, right, "DIVIDE", location)
            if type(left) is int:
                if right == 0:
                    raise RuntimeError_("integer division by zero", location)
                result = _truncating_quotient(left, right)
            else:
                if right == 0:
                    raise RuntimeError_("division by zero", location)
                result = left / right
        elif opcode is Opcode.MODULO:
            self._require_matching_numbers(left, right, "MODULO", location)
            if right == 0:
                raise RuntimeError_("modulo by zero", location)
            if type(left) is int:
                result = left - _truncating_quotient(left, right) * right
            else:
                result = left % right
        elif opcode is Opcode.EQUAL:
            self._require_matching_primitives(left, right, "EQUAL", location)
            result = left == right
        elif opcode is Opcode.NOT_EQUAL:
            self._require_matching_primitives(
                left, right, "NOT_EQUAL", location
            )
            result = left != right
        elif opcode is Opcode.LESS:
            self._require_matching_numbers(left, right, "LESS", location)
            result = left < right
        elif opcode is Opcode.LESS_EQUAL:
            self._require_matching_numbers(left, right, "LESS_EQUAL", location)
            result = left <= right
        elif opcode is Opcode.GREATER:
            self._require_matching_numbers(left, right, "GREATER", location)
            result = left > right
        elif opcode is Opcode.GREATER_EQUAL:
            self._require_matching_numbers(
                left, right, "GREATER_EQUAL", location
            )
            result = left >= right
        else:  # pragma: no cover - guarded by caller
            self._invalid(f"invalid binary opcode {opcode.name}", location)
            return
        self._stack.append(result)

    def _require_matching_numbers(
        self,
        left: object,
        right: object,
        operation: str,
        location: SourceLocation,
    ) -> None:
        if type(left) is not type(right) or type(left) not in (int, float):
            self._invalid(
                f"{operation} requires matching numeric values", location
            )

    def _require_matching_primitives(
        self,
        left: object,
        right: object,
        operation: str,
        location: SourceLocation,
    ) -> None:
        if type(left) is not type(right) or type(left) not in (
            int,
            float,
            str,
            bool,
        ):
            self._invalid(
                f"{operation} requires matching primitive values", location
            )

    def _pop(self, location: SourceLocation) -> object:
        if not self._stack:
            self._invalid("operand stack underflow", location)
        return self._stack.pop()

    @staticmethod
    def _invalid(message: str, location: SourceLocation) -> None:
        _invalid_bytecode(message, location)


_BINARY_RUNTIME_OPS = {
    Opcode.ADD,
    Opcode.SUBTRACT,
    Opcode.MULTIPLY,
    Opcode.DIVIDE,
    Opcode.MODULO,
    Opcode.EQUAL,
    Opcode.NOT_EQUAL,
    Opcode.LESS,
    Opcode.LESS_EQUAL,
    Opcode.GREATER,
    Opcode.GREATER_EQUAL,
}


_NO_OPERAND_OPS = {
    Opcode.POP,
    Opcode.DUP,
    Opcode.ENTER_SCOPE,
    Opcode.EXIT_SCOPE,
    Opcode.RETURN,
    Opcode.RETURN_VOID,
    Opcode.NEGATE,
    Opcode.NOT,
    Opcode.ADD,
    Opcode.SUBTRACT,
    Opcode.MULTIPLY,
    Opcode.DIVIDE,
    Opcode.MODULO,
    Opcode.EQUAL,
    Opcode.NOT_EQUAL,
    Opcode.LESS,
    Opcode.LESS_EQUAL,
    Opcode.GREATER,
    Opcode.GREATER_EQUAL,
    Opcode.MAKE_RANGE,
    Opcode.GET_ITER,
    Opcode.HALT,
}


BytecodeVM = VirtualMachine


def run(
    program: ast.Program | BytecodeProgram,
    *,
    output: Callable[[str], object] = print,
    instruction_limit: Optional[int] = 100_000,
) -> None:
    """Compile, if needed, and execute a program with a fresh VM."""

    VirtualMachine(program, output=output).run(
        instruction_limit=instruction_limit
    )


# ---------------------------------------------------------------------------
# Disassembly
# ---------------------------------------------------------------------------


def disassemble(program: ast.Program | BytecodeProgram) -> str:
    """Return a deterministic, recursive, source-mapped bytecode listing."""

    compiled = (
        program if isinstance(program, BytecodeProgram) else compile_program(program)
    )
    lines: List[str] = []
    _disassemble_code(compiled.code, lines, "")
    return "\n".join(lines)


def _disassemble_code(code: CodeObject, lines: List[str], indent: str) -> None:
    params = ", ".join(code.params)
    signature = f"({params})" if code.name != "<main>" else ""
    lines.append(f"{indent}== {code.name}{signature} ==")

    nested: List[CodeObject] = []
    for offset, instruction in enumerate(code.instructions):
        if not isinstance(instruction, Instruction):
            _invalid_bytecode(
                f"instruction {offset} is not an Instruction", code.location
            )
        operand = _display_operand(code, instruction)
        location = instruction.location
        line = (
            f"{indent}{offset:04d} {instruction.opcode.name:<18}"
            f"{operand:<24} ; {location}"
        )
        lines.append(line.rstrip())
        if instruction.opcode is Opcode.MAKE_CLOSURE:
            _, constant = _constant_at(
                code, instruction.operand, instruction.location
            )
            if isinstance(constant, CodeObject):
                nested.append(constant)

    for child in nested:
        lines.append("")
        _disassemble_code(child, lines, indent + "  ")


def _display_operand(code: CodeObject, instruction: Instruction) -> str:
    opcode = instruction.opcode
    operand = instruction.operand
    location = instruction.location
    if not isinstance(opcode, Opcode):
        _invalid_bytecode(f"unknown opcode {opcode!r}", location)
    if opcode in _NO_OPERAND_OPS:
        if operand is not None:
            _invalid_bytecode(
                f"{opcode.name} does not accept an operand, got {operand!r}",
                location,
            )
        return ""
    if opcode is Opcode.CONSTANT:
        index, value = _constant_at(code, operand, location)
        return f"{index} ({value!r})"
    if opcode is Opcode.MAKE_CLOSURE:
        index, value = _constant_at(code, operand, location)
        if not isinstance(value, CodeObject):
            _invalid_bytecode("closure constant is not code", location)
        name = value.name if isinstance(value, CodeObject) else repr(value)
        return f"{index} (<fn {name}>)"
    if opcode in (Opcode.LOAD_NAME, Opcode.DEFINE_NAME, Opcode.STORE_NAME):
        return _name_operand(operand, location)
    if opcode is Opcode.CALL:
        return str(_integer_operand(operand, "argument count", location))
    if opcode in (
        Opcode.JUMP,
        Opcode.JUMP_IF_FALSE,
        Opcode.JUMP_IF_TRUE,
        Opcode.ITER_NEXT,
    ):
        return str(_jump_target(code, operand, location))
    _invalid_bytecode(f"missing operand rules for {opcode.name}", location)
