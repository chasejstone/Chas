"""
parser.py

Takes the tokens from the lexer and builds an AST. The grammar is
basically a straight transcription of the BNF in SPEC.md, one method
per production.

A few notes:

  * It's plain recursive descent, no libraries.
  * Expressions use precedence climbing, with lowest precedence first.
  * One token of lookahead is enough almost everywhere. The one
    exception is telling `ident = expr` (assignment) apart from a bare
    expression starting with an identifier, which needs a two token
    peek. That's handled in `_statement`.
"""

from __future__ import annotations

from typing import List, Optional

from ast_nodes import (
    Assign,
    BinaryOp,
    Block,
    BoolLiteral,
    Call,
    ExprStmt,
    Expr,
    FloatLiteral,
    FnDecl,
    For,
    Identifier,
    If,
    IntLiteral,
    LetDecl,
    Param,
    Program,
    RangeExpr,
    Return,
    Stmt,
    StringLiteral,
    TypeNode,
    UnaryOp,
    While,
)
from errors import ParseError
from lexer import Token, TokenType


# A set of token types that denote a primitive type annotation.
_TYPE_TOKENS = {
    TokenType.TYPE_INT: "int",
    TokenType.TYPE_FLOAT: "float",
    TokenType.TYPE_STRING: "string",
    TokenType.TYPE_BOOL: "bool",
    TokenType.TYPE_VOID: "void",
}


class Parser:
    """Recursive descent parser."""

    def __init__(self, tokens: List[Token]):
        self._tokens = tokens
        self._pos = 0

    # ---- public API ----

    def parse(self) -> Program:
        stmts: list[Stmt] = []
        start_loc = self._peek().location
        while not self._check(TokenType.EOF):
            stmts.append(self._statement())
        return Program(statements=stmts, location=start_loc)

    # ---- token helpers ----

    def _peek(self, offset: int = 0) -> Token:
        i = self._pos + offset
        if i >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[i]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.type is not TokenType.EOF:
            self._pos += 1
        return tok

    def _check(self, *types: TokenType) -> bool:
        return self._peek().type in types

    def _match(self, *types: TokenType) -> Optional[Token]:
        if self._check(*types):
            return self._advance()
        return None

    def _expect(self, type_: TokenType, what: str) -> Token:
        if self._check(type_):
            return self._advance()
        tok = self._peek()
        raise ParseError(
            f"expected {what}, got {tok.lexeme!r}", tok.location
        )

    # ---- statements ----

    def _statement(self) -> Stmt:
        tok = self._peek()
        if tok.type is TokenType.LET:
            return self._let_decl()
        if tok.type is TokenType.FN:
            return self._fn_decl()
        if tok.type is TokenType.RETURN:
            return self._return_stmt()
        if tok.type is TokenType.IF:
            return self._if_stmt()
        if tok.type is TokenType.WHILE:
            return self._while_stmt()
        if tok.type is TokenType.FOR:
            return self._for_stmt()
        if tok.type is TokenType.LBRACE:
            return self._block()
        # assignment vs bare-expression: look ahead for `ident =`
        if (
            tok.type is TokenType.IDENT
            and self._peek(1).type is TokenType.EQ
        ):
            return self._assign_stmt()
        return self._expr_stmt()

    def _let_decl(self) -> LetDecl:
        start = self._expect(TokenType.LET, "'let'")
        name_tok = self._expect(TokenType.IDENT, "identifier after 'let'")
        declared: Optional[TypeNode] = None
        if self._match(TokenType.COLON):
            declared = self._type()
        self._expect(TokenType.EQ, "'=' in let declaration")
        value = self._expression()
        return LetDecl(
            name=name_tok.lexeme,
            declared_type=declared,
            value=value,
            location=start.location,
        )

    def _fn_decl(self) -> FnDecl:
        start = self._expect(TokenType.FN, "'fn'")
        name_tok = self._expect(TokenType.IDENT, "function name")
        self._expect(TokenType.LPAREN, "'(' after function name")
        params: list[Param] = []
        if not self._check(TokenType.RPAREN):
            params.append(self._param())
            while self._match(TokenType.COMMA):
                params.append(self._param())
        self._expect(TokenType.RPAREN, "')' to close parameter list")
        ret: TypeNode
        if self._match(TokenType.ARROW):
            ret = self._type()
        else:
            ret = TypeNode(name="void", location=start.location)
        body = self._block()
        return FnDecl(
            name=name_tok.lexeme,
            params=params,
            return_type=ret,
            body=body,
            location=start.location,
        )

    def _param(self) -> Param:
        name_tok = self._expect(TokenType.IDENT, "parameter name")
        self._expect(TokenType.COLON, "':' after parameter name")
        t = self._type()
        return Param(name=name_tok.lexeme, type=t, location=name_tok.location)

    def _return_stmt(self) -> Return:
        start = self._expect(TokenType.RETURN, "'return'")
        value: Optional[Expr] = None
        # A return has a value unless the next token starts a new statement
        # or ends the enclosing block.
        if not self._check(
            TokenType.RBRACE,
            TokenType.EOF,
            TokenType.LET,
            TokenType.FN,
            TokenType.RETURN,
            TokenType.IF,
            TokenType.WHILE,
            TokenType.FOR,
        ):
            value = self._expression()
        return Return(value=value, location=start.location)

    def _if_stmt(self) -> If:
        start = self._expect(TokenType.IF, "'if'")
        cond = self._expression()
        then_blk = self._block()
        else_branch: Optional[Stmt] = None
        if self._match(TokenType.ELSE):
            if self._check(TokenType.IF):
                else_branch = self._if_stmt()
            else:
                else_branch = self._block()
        return If(
            condition=cond,
            then_branch=then_blk,
            else_branch=else_branch,
            location=start.location,
        )

    def _while_stmt(self) -> While:
        start = self._expect(TokenType.WHILE, "'while'")
        cond = self._expression()
        body = self._block()
        return While(condition=cond, body=body, location=start.location)

    def _for_stmt(self) -> For:
        start = self._expect(TokenType.FOR, "'for'")
        var = self._expect(TokenType.IDENT, "loop variable name")
        self._expect(TokenType.IN, "'in' in for loop")
        it = self._expression()
        body = self._block()
        return For(
            var_name=var.lexeme,
            iterable=it,
            body=body,
            location=start.location,
        )

    def _assign_stmt(self) -> Assign:
        name_tok = self._expect(TokenType.IDENT, "identifier")
        self._expect(TokenType.EQ, "'='")
        value = self._expression()
        return Assign(
            name=name_tok.lexeme, value=value, location=name_tok.location
        )

    def _expr_stmt(self) -> ExprStmt:
        expr = self._expression()
        return ExprStmt(expression=expr, location=expr.location)

    def _block(self) -> Block:
        start = self._expect(TokenType.LBRACE, "'{' to start block")
        stmts: list[Stmt] = []
        while not self._check(TokenType.RBRACE, TokenType.EOF):
            stmts.append(self._statement())
        self._expect(TokenType.RBRACE, "'}' to close block")
        return Block(statements=stmts, location=start.location)

    # ---- types ----

    def _type(self) -> TypeNode:
        tok = self._peek()
        if tok.type in _TYPE_TOKENS:
            self._advance()
            return TypeNode(name=_TYPE_TOKENS[tok.type], location=tok.location)
        raise ParseError(
            f"expected a type, got {tok.lexeme!r}", tok.location
        )

    # ---- expressions (precedence climbing) ----

    def _expression(self) -> Expr:
        return self._logical_or()

    def _logical_or(self) -> Expr:
        left = self._logical_and()
        while self._check(TokenType.OR):
            op = self._advance()
            right = self._logical_and()
            left = BinaryOp(
                op="||", left=left, right=right, location=op.location
            )
        return left

    def _logical_and(self) -> Expr:
        left = self._equality()
        while self._check(TokenType.AND):
            op = self._advance()
            right = self._equality()
            left = BinaryOp(
                op="&&", left=left, right=right, location=op.location
            )
        return left

    def _equality(self) -> Expr:
        left = self._comparison()
        while self._check(TokenType.EQEQ, TokenType.NEQ):
            op = self._advance()
            right = self._comparison()
            left = BinaryOp(
                op=op.lexeme, left=left, right=right, location=op.location
            )
        return left

    def _comparison(self) -> Expr:
        left = self._range()
        while self._check(
            TokenType.LT, TokenType.LE, TokenType.GT, TokenType.GE
        ):
            op = self._advance()
            right = self._range()
            left = BinaryOp(
                op=op.lexeme, left=left, right=right, location=op.location
            )
        return left

    def _range(self) -> Expr:
        left = self._additive()
        if self._check(TokenType.DOTDOT):
            op = self._advance()
            right = self._additive()
            return RangeExpr(start=left, end=right, location=op.location)
        return left

    def _additive(self) -> Expr:
        left = self._multiplicative()
        while self._check(TokenType.PLUS, TokenType.MINUS):
            op = self._advance()
            right = self._multiplicative()
            left = BinaryOp(
                op=op.lexeme, left=left, right=right, location=op.location
            )
        return left

    def _multiplicative(self) -> Expr:
        left = self._unary()
        while self._check(
            TokenType.STAR, TokenType.SLASH, TokenType.PERCENT
        ):
            op = self._advance()
            right = self._unary()
            left = BinaryOp(
                op=op.lexeme, left=left, right=right, location=op.location
            )
        return left

    def _unary(self) -> Expr:
        if self._check(TokenType.NOT, TokenType.MINUS):
            op = self._advance()
            operand = self._unary()
            return UnaryOp(
                op=op.lexeme, operand=operand, location=op.location
            )
        return self._call()

    def _call(self) -> Expr:
        expr = self._primary()
        while self._match(TokenType.LPAREN):
            args: list[Expr] = []
            if not self._check(TokenType.RPAREN):
                args.append(self._expression())
                while self._match(TokenType.COMMA):
                    args.append(self._expression())
            self._expect(TokenType.RPAREN, "')' to close call")
            expr = Call(callee=expr, args=args, location=expr.location)
        return expr

    def _primary(self) -> Expr:
        tok = self._peek()
        if tok.type is TokenType.INT:
            self._advance()
            return IntLiteral(value=tok.value, location=tok.location)
        if tok.type is TokenType.FLOAT:
            self._advance()
            return FloatLiteral(value=tok.value, location=tok.location)
        if tok.type is TokenType.STRING:
            self._advance()
            return StringLiteral(value=tok.value, location=tok.location)
        if tok.type is TokenType.TRUE:
            self._advance()
            return BoolLiteral(value=True, location=tok.location)
        if tok.type is TokenType.FALSE:
            self._advance()
            return BoolLiteral(value=False, location=tok.location)
        if tok.type is TokenType.IDENT:
            self._advance()
            return Identifier(name=tok.lexeme, location=tok.location)
        if tok.type is TokenType.LPAREN:
            self._advance()
            expr = self._expression()
            self._expect(TokenType.RPAREN, "')' to close grouped expression")
            return expr
        raise ParseError(
            f"unexpected token {tok.lexeme!r}", tok.location
        )


def parse(tokens: List[Token]) -> Program:
    """Shortcut for building a Parser and running it."""
    return Parser(tokens).parse()
