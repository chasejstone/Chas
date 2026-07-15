"""
evaluator.py

Tree walking interpreter. This runs the AST after the type checker has
said it's okay, so we don't bother re-checking types at runtime and can
focus on the actual execution: environments, closures, control flow,
and the built in I/O.

How things are represented at runtime:

  * Values are normal Python ints, floats, strings, and bools, plus a
    small `_Range` class for `a..b` and `_Function` / `_Builtin` for
    callables.
  * `_Environment` is a chained symbol table. A function closes over
    its defining environment, which is how closures work.
  * `return` is implemented as an exception (`_ReturnSignal`) that
    unwinds the stack until the enclosing function catches it. The
    signal never escapes a function boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import ast_nodes as ast
from errors import RuntimeError_, SourceLocation


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
    """Chained scope: each env has a parent, lookups walk up the chain."""

    __slots__ = ("values", "parent")

    def __init__(self, parent: Optional["_Environment"] = None):
        self.values: Dict[str, object] = {}
        self.parent = parent

    def define(self, name: str, value: object) -> None:
        self.values[name] = value

    def get(self, name: str, loc: SourceLocation) -> object:
        env: Optional[_Environment] = self
        while env is not None:
            if name in env.values:
                return env.values[name]
            env = env.parent
        raise RuntimeError_(f"undefined name {name!r}", loc)

    def assign(self, name: str, value: object, loc: SourceLocation) -> None:
        env: Optional[_Environment] = self
        while env is not None:
            if name in env.values:
                env.values[name] = value
                return
            env = env.parent
        raise RuntimeError_(f"undefined name {name!r}", loc)


@dataclass
class _Function:
    decl: ast.FnDecl
    closure: _Environment

    def __call__(self, interp: "Evaluator", args: List[object], loc: SourceLocation):
        env = _Environment(parent=self.closure)
        for param, value in zip(self.decl.params, args):
            env.define(param.name, value)
        try:
            interp._exec_block(self.decl.body, env)
        except _ReturnSignal as ret:
            return ret.value
        return None


@dataclass
class _Builtin:
    name: str
    func: Callable[["Evaluator", List[object], SourceLocation], object]

    def __call__(self, interp: "Evaluator", args: List[object], loc: SourceLocation):
        return self.func(interp, args, loc)


# ---------------------------------------------------------------------------
# Control-flow signals (internal)
# ---------------------------------------------------------------------------


class _ReturnSignal(Exception):
    def __init__(self, value: object):
        self.value = value


# ---------------------------------------------------------------------------
# Built-in implementations
# ---------------------------------------------------------------------------


def _builtin_print(interp, args, loc):
    print(_format(args[0]))
    return None


def _builtin_len(interp, args, loc):
    s = args[0]
    if not isinstance(s, str):
        raise RuntimeError_(f"len() requires string, got {_type_name(s)}", loc)
    return len(s)


def _builtin_range(interp, args, loc):
    a, b = args
    return _Range(int(a), int(b))


def _builtin_type(interp, args, loc):
    return _type_name(args[0])


BUILTINS: Dict[str, _Builtin] = {
    "print": _Builtin("print", _builtin_print),
    "len": _Builtin("len", _builtin_len),
    "range": _Builtin("range", _builtin_range),
    "type": _Builtin("type", _builtin_type),
}


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------


def _type_name(v: object) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, _Range):
        return "range"
    if isinstance(v, (_Function, _Builtin)):
        return "fn"
    return "unknown"


def _format(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "void"
    if isinstance(v, float):
        # Keep a trailing decimal so floats are visibly distinct from ints.
        if v == int(v):
            return f"{v:.1f}"
        return repr(v)
    if isinstance(v, _Range):
        return f"{v.start}..{v.end}"
    if isinstance(v, _Function):
        return f"<fn {v.decl.name}>"
    if isinstance(v, _Builtin):
        return f"<builtin {v.name}>"
    return str(v)


def _truncating_quotient(a: int, b: int) -> int:
    """Return the integer quotient of a and b, rounded toward zero."""

    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class Evaluator:
    """Runs a type checked AST by walking it node by node."""

    def __init__(self) -> None:
        self._globals = _Environment()
        for name, b in BUILTINS.items():
            self._globals.define(name, b)

    # ---- public entry ----

    def run(self, program: ast.Program) -> None:
        # Hoist top level functions so order of declarations doesn't
        # matter at the program level.
        for stmt in program.statements:
            if isinstance(stmt, ast.FnDecl):
                self._globals.define(
                    stmt.name, _Function(decl=stmt, closure=self._globals)
                )
        for stmt in program.statements:
            if isinstance(stmt, ast.FnDecl):
                continue
            self._exec(stmt, self._globals)

    # ---- statements ----

    def _exec_block(self, block: ast.Block, env: _Environment) -> None:
        inner = _Environment(parent=env)
        for s in block.statements:
            self._exec(s, inner)

    def _exec(self, stmt: ast.Stmt, env: _Environment) -> None:
        if isinstance(stmt, ast.LetDecl):
            env.define(stmt.name, self._eval(stmt.value, env))
            return
        if isinstance(stmt, ast.FnDecl):
            env.define(stmt.name, _Function(decl=stmt, closure=env))
            return
        if isinstance(stmt, ast.Return):
            val = None if stmt.value is None else self._eval(stmt.value, env)
            raise _ReturnSignal(val)
        if isinstance(stmt, ast.If):
            if self._eval(stmt.condition, env):
                self._exec_block(stmt.then_branch, env)
            elif stmt.else_branch is not None:
                if isinstance(stmt.else_branch, ast.Block):
                    self._exec_block(stmt.else_branch, env)
                else:
                    self._exec(stmt.else_branch, env)
            return
        if isinstance(stmt, ast.While):
            while self._eval(stmt.condition, env):
                self._exec_block(stmt.body, env)
            return
        if isinstance(stmt, ast.For):
            it = self._eval(stmt.iterable, env)
            if not isinstance(it, _Range):
                raise RuntimeError_(
                    "for-in iterable must be a range", stmt.location
                )
            for val in it:
                inner = _Environment(parent=env)
                inner.define(stmt.var_name, val)
                for s in stmt.body.statements:
                    self._exec(s, inner)
            return
        if isinstance(stmt, ast.Assign):
            env.assign(stmt.name, self._eval(stmt.value, env), stmt.location)
            return
        if isinstance(stmt, ast.ExprStmt):
            self._eval(stmt.expression, env)
            return
        if isinstance(stmt, ast.Block):
            self._exec_block(stmt, env)
            return
        raise RuntimeError_(
            f"unhandled statement {type(stmt).__name__}", stmt.location
        )

    # ---- expressions ----

    def _eval(self, expr: ast.Expr, env: _Environment) -> object:
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        if isinstance(expr, ast.FloatLiteral):
            return expr.value
        if isinstance(expr, ast.StringLiteral):
            return expr.value
        if isinstance(expr, ast.BoolLiteral):
            return expr.value
        if isinstance(expr, ast.Identifier):
            return env.get(expr.name, expr.location)
        if isinstance(expr, ast.UnaryOp):
            v = self._eval(expr.operand, env)
            if expr.op == "!":
                return not v
            if expr.op == "-":
                return -v
            raise RuntimeError_(
                f"unknown unary operator {expr.op!r}", expr.location
            )
        if isinstance(expr, ast.BinaryOp):
            return self._eval_binary(expr, env)
        if isinstance(expr, ast.RangeExpr):
            a = self._eval(expr.start, env)
            b = self._eval(expr.end, env)
            return _Range(int(a), int(b))
        if isinstance(expr, ast.Call):
            callee = self._eval(expr.callee, env)
            args = [self._eval(a, env) for a in expr.args]
            if not isinstance(callee, (_Function, _Builtin)):
                raise RuntimeError_(
                    "attempt to call a non-function value", expr.location
                )
            return callee(self, args, expr.location)
        raise RuntimeError_(
            f"unhandled expression {type(expr).__name__}", expr.location
        )

    def _eval_binary(self, node: ast.BinaryOp, env: _Environment) -> object:
        op = node.op
        # && and || short circuit.
        if op == "&&":
            left = self._eval(node.left, env)
            if not left:
                return False
            return bool(self._eval(node.right, env))
        if op == "||":
            left = self._eval(node.left, env)
            if left:
                return True
            return bool(self._eval(node.right, env))

        a = self._eval(node.left, env)
        b = self._eval(node.right, env)

        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if isinstance(a, int) and isinstance(b, int):
                if b == 0:
                    raise RuntimeError_(
                        "integer division by zero", node.location
                    )
                return _truncating_quotient(a, b)
            if b == 0:
                raise RuntimeError_("division by zero", node.location)
            return a / b
        if op == "%":
            if b == 0:
                raise RuntimeError_("modulo by zero", node.location)
            if isinstance(a, int) and isinstance(b, int):
                return a - _truncating_quotient(a, b) * b
            return a % b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        raise RuntimeError_(
            f"unknown binary operator {op!r}", node.location
        )


def run(program: ast.Program) -> None:
    """Shortcut for running a program with a fresh interpreter."""
    Evaluator().run(program)
