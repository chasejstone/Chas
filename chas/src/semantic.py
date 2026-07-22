"""
semantic.py

The type checker and scope resolver. It walks the AST once and enforces
all the static rules from sections 3 and 4 of SPEC.md:

  * every name is declared before it's used,
  * operators get operands they can actually handle,
  * calls match the function's declared signature,
  * any non void function returns on every path,
  * variables, parameters, and loop variables respect their scope.

If everything checks out, `analyze` just returns. Otherwise it raises a
TypeError_ or NameError_ with the source location where things went
wrong, so the evaluator can assume the program is well typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import ast_nodes as ast
from errors import NameError_, TypeError_


# ---------------------------------------------------------------------------
# Type representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Type:
    """A primitive type. Functions use FnType below."""

    name: str

    def __str__(self) -> str:
        return self.name


INT = Type("int")
FLOAT = Type("float")
STRING = Type("string")
BOOL = Type("bool")
VOID = Type("void")
RANGE = Type("range")
ANY = Type("any")  # internal only, used for built-ins


@dataclass(frozen=True)
class FnType(Type):
    name: str = "fn"
    params: tuple = ()
    ret: Type = VOID

    def __str__(self) -> str:
        ps = ", ".join(str(p) for p in self.params)
        return f"fn({ps}) -> {self.ret}"


_PRIMITIVES = {
    "int": INT,
    "float": FLOAT,
    "string": STRING,
    "bool": BOOL,
    "void": VOID,
}


def _resolve_type_node(node: ast.TypeNode) -> Type:
    t = _PRIMITIVES.get(node.name)
    if t is None:
        raise TypeError_(f"unknown type {node.name!r}", node.location)
    return t


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass
class Scope:
    parent: Optional["Scope"] = None
    symbols: Dict[str, Type] = field(default_factory=dict)

    def declare(self, name: str, type_: Type, location) -> None:
        if name in self.symbols:
            raise NameError_(
                f"{name!r} is already declared in this scope", location
            )
        self.symbols[name] = type_

    def lookup(self, name: str) -> Optional[Type]:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent is not None:
            return self.parent.lookup(name)
        return None


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


BUILTINS: Dict[str, FnType] = {
    "print": FnType(params=(ANY,), ret=VOID),
    "len": FnType(params=(STRING,), ret=INT),
    "range": FnType(params=(INT, INT), ret=RANGE),
    "type": FnType(params=(ANY,), ret=STRING),
}


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class SemanticAnalyzer:
    """Walks a Program and type checks it. Raises on the first problem."""

    def __init__(self) -> None:
        self._scope = Scope()
        for name, t in BUILTINS.items():
            self._scope.symbols[name] = t
        # Stack of return types for the enclosing fn declarations.
        self._return_stack: List[Type] = []

    # ---- public entry ----

    def analyze(self, program: ast.Program) -> None:
        # Hoist top level fns first so they can call each other (and
        # themselves) regardless of declaration order.
        for stmt in program.statements:
            if isinstance(stmt, ast.FnDecl):
                self._declare_fn(stmt)
        # Then actually check each statement.
        for stmt in program.statements:
            self._check_stmt(stmt, hoisted=isinstance(stmt, ast.FnDecl))

    # ---- scope ----

    def _push_scope(self) -> None:
        self._scope = Scope(parent=self._scope)

    def _pop_scope(self) -> None:
        assert self._scope.parent is not None
        self._scope = self._scope.parent

    # ---- declarations ----

    def _declare_fn(self, fn: ast.FnDecl) -> None:
        params = tuple(_resolve_type_node(p.type) for p in fn.params)
        ret = _resolve_type_node(fn.return_type)
        self._scope.declare(fn.name, FnType(params=params, ret=ret), fn.location)

    # ---- statements ----

    def _check_stmt(self, stmt: ast.Stmt, *, hoisted: bool = False) -> None:
        if isinstance(stmt, ast.LetDecl):
            self._check_let(stmt)
        elif isinstance(stmt, ast.FnDecl):
            if not hoisted:
                self._declare_fn(stmt)
            self._check_fn(stmt)
        elif isinstance(stmt, ast.Return):
            self._check_return(stmt)
        elif isinstance(stmt, ast.If):
            self._check_if(stmt)
        elif isinstance(stmt, ast.While):
            self._check_while(stmt)
        elif isinstance(stmt, ast.For):
            self._check_for(stmt)
        elif isinstance(stmt, ast.Assign):
            self._check_assign(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            self._infer(stmt.expression)
        elif isinstance(stmt, ast.Block):
            self._push_scope()
            try:
                for s in stmt.statements:
                    self._check_stmt(s)
            finally:
                self._pop_scope()
        else:  # pragma: no cover
            raise TypeError_(
                f"unhandled statement node {type(stmt).__name__}",
                stmt.location,
            )

    def _check_let(self, node: ast.LetDecl) -> None:
        value_t = self._infer(node.value)
        if node.declared_type is not None:
            declared = _resolve_type_node(node.declared_type)
            if not _assignable(declared, value_t):
                raise TypeError_(
                    f"cannot assign value of type {value_t} to variable "
                    f"of type {declared}",
                    node.location,
                )
            self._scope.declare(node.name, declared, node.location)
        else:
            if value_t is VOID:
                raise TypeError_(
                    f"cannot infer type for {node.name!r}: "
                    "initializer has type void",
                    node.location,
                )
            self._scope.declare(node.name, value_t, node.location)

    def _check_fn(self, node: ast.FnDecl) -> None:
        ret = _resolve_type_node(node.return_type)
        self._push_scope()
        try:
            for p in node.params:
                t = _resolve_type_node(p.type)
                self._scope.declare(p.name, t, p.location)
            self._return_stack.append(ret)
            try:
                for s in node.body.statements:
                    self._check_stmt(s)
                if ret is not VOID and not _definitely_returns(node.body):
                    raise TypeError_(
                        f"function {node.name!r} must return a value "
                        f"of type {ret} on every path",
                        node.location,
                    )
            finally:
                self._return_stack.pop()
        finally:
            self._pop_scope()

    def _check_return(self, node: ast.Return) -> None:
        if not self._return_stack:
            raise TypeError_(
                "'return' outside of any function", node.location
            )
        expected = self._return_stack[-1]
        if node.value is None:
            if expected is not VOID:
                raise TypeError_(
                    f"empty return in function expecting {expected}",
                    node.location,
                )
            return
        actual = self._infer(node.value)
        if expected is VOID:
            raise TypeError_(
                "cannot return a value from a void function",
                node.location,
            )
        if not _assignable(expected, actual):
            raise TypeError_(
                f"return type mismatch: expected {expected}, got {actual}",
                node.location,
            )

    def _check_if(self, node: ast.If) -> None:
        ct = self._infer(node.condition)
        if ct is not BOOL:
            raise TypeError_(
                f"'if' condition must be bool, got {ct}",
                node.condition.location,
            )
        self._check_stmt(node.then_branch)
        if node.else_branch is not None:
            self._check_stmt(node.else_branch)

    def _check_while(self, node: ast.While) -> None:
        ct = self._infer(node.condition)
        if ct is not BOOL:
            raise TypeError_(
                f"'while' condition must be bool, got {ct}",
                node.condition.location,
            )
        self._check_stmt(node.body)

    def _check_for(self, node: ast.For) -> None:
        it = self._infer(node.iterable)
        if it is not RANGE:
            raise TypeError_(
                f"'for-in' requires a range (a..b), got {it}",
                node.iterable.location,
            )
        self._push_scope()
        try:
            self._scope.declare(node.var_name, INT, node.location)
            for s in node.body.statements:
                self._check_stmt(s)
        finally:
            self._pop_scope()

    def _check_assign(self, node: ast.Assign) -> None:
        existing = self._scope.lookup(node.name)
        if existing is None:
            raise NameError_(
                f"assignment to undeclared variable {node.name!r}",
                node.location,
            )
        value_t = self._infer(node.value)
        if not _assignable(existing, value_t):
            raise TypeError_(
                f"cannot assign {value_t} to variable of type {existing}",
                node.location,
            )

    # ---- expressions ----

    def _infer(self, expr: ast.Expr) -> Type:
        if isinstance(expr, ast.IntLiteral):
            return INT
        if isinstance(expr, ast.FloatLiteral):
            return FLOAT
        if isinstance(expr, ast.StringLiteral):
            return STRING
        if isinstance(expr, ast.BoolLiteral):
            return BOOL
        if isinstance(expr, ast.Identifier):
            t = self._scope.lookup(expr.name)
            if t is None:
                raise NameError_(
                    f"undeclared name {expr.name!r}", expr.location
                )
            return t
        if isinstance(expr, ast.UnaryOp):
            return self._infer_unary(expr)
        if isinstance(expr, ast.BinaryOp):
            return self._infer_binary(expr)
        if isinstance(expr, ast.RangeExpr):
            a = self._infer(expr.start)
            b = self._infer(expr.end)
            if a is not INT or b is not INT:
                raise TypeError_(
                    f"range bounds must be int, got {a}..{b}",
                    expr.location,
                )
            return RANGE
        if isinstance(expr, ast.Call):
            return self._infer_call(expr)
        raise TypeError_(
            f"unhandled expression {type(expr).__name__}", expr.location
        )

    def _infer_unary(self, node: ast.UnaryOp) -> Type:
        t = self._infer(node.operand)
        if node.op == "!":
            if t is not BOOL:
                raise TypeError_(
                    f"'!' requires bool, got {t}", node.location
                )
            return BOOL
        if node.op == "-":
            if t not in (INT, FLOAT):
                raise TypeError_(
                    f"unary '-' requires numeric, got {t}", node.location
                )
            return t
        raise TypeError_(
            f"unknown unary operator {node.op!r}", node.location
        )

    def _infer_binary(self, node: ast.BinaryOp) -> Type:
        lt = self._infer(node.left)
        rt = self._infer(node.right)
        op = node.op
        if op in ("+", "-", "*", "/", "%"):
            if op == "+" and lt is STRING and rt is STRING:
                return STRING
            if lt in (INT, FLOAT) and lt is rt:
                return lt
            raise TypeError_(
                f"operator {op!r} is not defined for {lt} and {rt}",
                node.location,
            )
        if op in ("==", "!="):
            if lt is not rt or lt not in (INT, FLOAT, STRING, BOOL):
                raise TypeError_(
                    f"operator {op!r} requires matching primitive types, "
                    f"got {lt} and {rt}",
                    node.location,
                )
            return BOOL
        if op in ("<", "<=", ">", ">="):
            if lt in (INT, FLOAT) and lt is rt:
                return BOOL
            raise TypeError_(
                f"operator {op!r} requires numeric operands, got {lt} "
                f"and {rt}",
                node.location,
            )
        if op in ("&&", "||"):
            if lt is BOOL and rt is BOOL:
                return BOOL
            raise TypeError_(
                f"operator {op!r} requires bool operands, got {lt} and {rt}",
                node.location,
            )
        raise TypeError_(
            f"unknown binary operator {op!r}", node.location
        )

    def _infer_call(self, node: ast.Call) -> Type:
        callee_t = self._infer(node.callee)
        if not isinstance(callee_t, FnType):
            raise TypeError_(
                f"value of type {callee_t} is not callable", node.location
            )
        if len(node.args) != len(callee_t.params):
            raise TypeError_(
                f"call expects {len(callee_t.params)} argument(s), "
                f"got {len(node.args)}",
                node.location,
            )
        for i, (arg, expected) in enumerate(zip(node.args, callee_t.params)):
            actual = self._infer(arg)
            if expected is ANY:
                continue
            if not _assignable(expected, actual):
                raise TypeError_(
                    f"argument {i + 1}: expected {expected}, got {actual}",
                    arg.location,
                )
        return callee_t.ret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assignable(target: Type, source: Type) -> bool:
    return target is source


def _definitely_returns(block: ast.Block) -> bool:
    """True if every path through this block ends in a Return."""

    for stmt in block.statements:
        if isinstance(stmt, ast.Return):
            return True
        if isinstance(stmt, ast.If) and stmt.else_branch is not None:
            then_ret = _definitely_returns(stmt.then_branch)
            else_ret = (
                _definitely_returns(stmt.else_branch)
                if isinstance(stmt.else_branch, ast.Block)
                else (
                    isinstance(stmt.else_branch, ast.If)
                    and _if_definitely_returns(stmt.else_branch)
                )
            )
            if then_ret and else_ret:
                return True
        if isinstance(stmt, ast.Block) and _definitely_returns(stmt):
            return True
    return False


def _if_definitely_returns(if_node: ast.If) -> bool:
    if if_node.else_branch is None:
        return False
    then_ret = _definitely_returns(if_node.then_branch)
    else_ret = (
        _definitely_returns(if_node.else_branch)
        if isinstance(if_node.else_branch, ast.Block)
        else _if_definitely_returns(if_node.else_branch)
    )
    return then_ret and else_ret


def analyze(program: ast.Program) -> None:
    """Shortcut for running the type checker on a program."""
    SemanticAnalyzer().analyze(program)
