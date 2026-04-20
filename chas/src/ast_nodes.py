"""
ast_nodes.py

Every node type the parser can build, plus a simple Visitor base class
that the type checker and evaluator use. Nodes are plain dataclasses,
which makes them cheap to construct and easy to print when debugging.

The hierarchy:

    Node
      Expr
        IntLiteral, FloatLiteral, StringLiteral, BoolLiteral
        Identifier
        UnaryOp, BinaryOp, RangeExpr, Call
      Stmt
        LetDecl, FnDecl, Return, If, While, For,
        Assign, ExprStmt, Block
      Program
      TypeNode
      Param
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from errors import SourceLocation


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """Base of every AST node. Every node carries a source location."""

    location: SourceLocation = field(
        default_factory=lambda: SourceLocation("<?>", 0, 0),
        repr=False,
        compare=False,
    )

    def accept(self, visitor: "Visitor"):
        method = getattr(visitor, f"visit_{type(self).__name__}", None)
        if method is None:
            return visitor.generic_visit(self)
        return method(self)

    def pretty(self, indent: int = 0) -> str:
        """Indented text dump of this subtree, handy for debugging."""
        return _pretty(self, indent)

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return self.pretty()


class Expr(Node):
    """Marker: anything that produces a value."""


class Stmt(Node):
    """Marker: anything that's a statement."""


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class TypeNode(Node):
    """A type written in the source, like `int` or `bool`."""

    name: str = "void"


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class IntLiteral(Expr):
    value: int = 0


@dataclass
class FloatLiteral(Expr):
    value: float = 0.0


@dataclass
class StringLiteral(Expr):
    value: str = ""


@dataclass
class BoolLiteral(Expr):
    value: bool = False


@dataclass
class Identifier(Expr):
    name: str = ""


@dataclass
class UnaryOp(Expr):
    op: str = ""
    operand: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class BinaryOp(Expr):
    op: str = ""
    left: Expr = field(default_factory=lambda: IntLiteral())
    right: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class RangeExpr(Expr):
    """`start..end`. Iterates over [start, end) as ints."""

    start: Expr = field(default_factory=lambda: IntLiteral())
    end: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class Call(Expr):
    callee: Expr = field(default_factory=lambda: Identifier())
    args: List[Expr] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class Param(Node):
    name: str = ""
    type: TypeNode = field(default_factory=TypeNode)


@dataclass
class LetDecl(Stmt):
    name: str = ""
    declared_type: Optional[TypeNode] = None
    value: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class FnDecl(Stmt):
    name: str = ""
    params: List[Param] = field(default_factory=list)
    return_type: TypeNode = field(default_factory=lambda: TypeNode(name="void"))
    body: "Block" = field(default_factory=lambda: Block())


@dataclass
class Return(Stmt):
    value: Optional[Expr] = None


@dataclass
class If(Stmt):
    condition: Expr = field(default_factory=lambda: BoolLiteral())
    then_branch: "Block" = field(default_factory=lambda: Block())
    else_branch: Optional[Stmt] = None  # Block or another If


@dataclass
class While(Stmt):
    condition: Expr = field(default_factory=lambda: BoolLiteral())
    body: "Block" = field(default_factory=lambda: Block())


@dataclass
class For(Stmt):
    var_name: str = ""
    iterable: Expr = field(default_factory=lambda: IntLiteral())
    body: "Block" = field(default_factory=lambda: Block())


@dataclass
class Assign(Stmt):
    name: str = ""
    value: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class ExprStmt(Stmt):
    expression: Expr = field(default_factory=lambda: IntLiteral())


@dataclass
class Block(Stmt):
    statements: List[Stmt] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


@dataclass
class Program(Node):
    statements: List[Stmt] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Visitor
# ---------------------------------------------------------------------------


class Visitor:
    """Base class for tree walkers. Subclasses define `visit_<NodeName>`
    methods. Anything that isn't handled falls through to `generic_visit`,
    which raises by default so mistakes show up loud."""

    def visit(self, node: Node):
        return node.accept(self)

    def generic_visit(self, node: Node):  # pragma: no cover
        raise NotImplementedError(
            f"{type(self).__name__} has no visit_{type(node).__name__}"
        )


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------


def _pretty(node: Node, indent: int) -> str:
    pad = "  " * indent
    cls = type(node).__name__

    if isinstance(node, Program):
        out = [f"{pad}Program"]
        for s in node.statements:
            out.append(_pretty(s, indent + 1))
        return "\n".join(out)

    if isinstance(node, Block):
        out = [f"{pad}Block"]
        for s in node.statements:
            out.append(_pretty(s, indent + 1))
        return "\n".join(out)

    if isinstance(node, FnDecl):
        params = ", ".join(f"{p.name}: {p.type.name}" for p in node.params)
        header = f"{pad}FnDecl {node.name}({params}) -> {node.return_type.name}"
        return header + "\n" + _pretty(node.body, indent + 1)

    if isinstance(node, LetDecl):
        t = f": {node.declared_type.name}" if node.declared_type else ""
        return (
            f"{pad}LetDecl {node.name}{t}\n"
            + _pretty(node.value, indent + 1)
        )

    if isinstance(node, Return):
        if node.value is None:
            return f"{pad}Return"
        return f"{pad}Return\n" + _pretty(node.value, indent + 1)

    if isinstance(node, If):
        out = [f"{pad}If"]
        out.append(_pretty(node.condition, indent + 1))
        out.append(_pretty(node.then_branch, indent + 1))
        if node.else_branch is not None:
            out.append(f"{pad}Else")
            out.append(_pretty(node.else_branch, indent + 1))
        return "\n".join(out)

    if isinstance(node, While):
        return (
            f"{pad}While\n"
            + _pretty(node.condition, indent + 1)
            + "\n"
            + _pretty(node.body, indent + 1)
        )

    if isinstance(node, For):
        return (
            f"{pad}For {node.var_name} in\n"
            + _pretty(node.iterable, indent + 1)
            + "\n"
            + _pretty(node.body, indent + 1)
        )

    if isinstance(node, Assign):
        return f"{pad}Assign {node.name}\n" + _pretty(node.value, indent + 1)

    if isinstance(node, ExprStmt):
        return f"{pad}ExprStmt\n" + _pretty(node.expression, indent + 1)

    if isinstance(node, BinaryOp):
        return (
            f"{pad}BinaryOp {node.op}\n"
            + _pretty(node.left, indent + 1)
            + "\n"
            + _pretty(node.right, indent + 1)
        )

    if isinstance(node, UnaryOp):
        return f"{pad}UnaryOp {node.op}\n" + _pretty(node.operand, indent + 1)

    if isinstance(node, RangeExpr):
        return (
            f"{pad}RangeExpr\n"
            + _pretty(node.start, indent + 1)
            + "\n"
            + _pretty(node.end, indent + 1)
        )

    if isinstance(node, Call):
        out = [f"{pad}Call"]
        out.append(_pretty(node.callee, indent + 1))
        for a in node.args:
            out.append(_pretty(a, indent + 1))
        return "\n".join(out)

    if isinstance(node, Identifier):
        return f"{pad}Identifier {node.name}"
    if isinstance(node, IntLiteral):
        return f"{pad}IntLiteral {node.value}"
    if isinstance(node, FloatLiteral):
        return f"{pad}FloatLiteral {node.value}"
    if isinstance(node, StringLiteral):
        return f"{pad}StringLiteral {node.value!r}"
    if isinstance(node, BoolLiteral):
        return f"{pad}BoolLiteral {node.value}"
    if isinstance(node, TypeNode):
        return f"{pad}TypeNode {node.name}"

    return f"{pad}{cls}"
