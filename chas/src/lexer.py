"""
lexer.py

First stage of the compiler. Takes a raw source string and chops it up
into a list of Token objects. Each token knows where it came from (file,
line, column), which is what lets every later stage report errors with
a real position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import List

from errors import LexerError, SourceLocation


class TokenType(Enum):
    # Literals
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()

    # Identifiers
    IDENT = auto()

    # Keywords
    LET = auto()
    FN = auto()
    RETURN = auto()
    IF = auto()
    ELSE = auto()
    WHILE = auto()
    FOR = auto()
    IN = auto()
    TYPE_INT = auto()
    TYPE_FLOAT = auto()
    TYPE_STRING = auto()
    TYPE_BOOL = auto()
    TYPE_VOID = auto()

    # Operators
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    EQ = auto()
    EQEQ = auto()
    NEQ = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    ARROW = auto()
    DOTDOT = auto()

    # Delimiters
    LPAREN = auto()
    RPAREN = auto()
    LBRACE = auto()
    RBRACE = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COMMA = auto()
    COLON = auto()

    # Meta
    NEWLINE = auto()  # tracked for error recovery; not semantically used
    EOF = auto()


KEYWORDS: dict[str, TokenType] = {
    "let": TokenType.LET,
    "fn": TokenType.FN,
    "return": TokenType.RETURN,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "while": TokenType.WHILE,
    "for": TokenType.FOR,
    "in": TokenType.IN,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "int": TokenType.TYPE_INT,
    "float": TokenType.TYPE_FLOAT,
    "string": TokenType.TYPE_STRING,
    "bool": TokenType.TYPE_BOOL,
    "void": TokenType.TYPE_VOID,
}


# Python 3.11 added a process-level decimal conversion limit while 3.10 did
# not. Chas uses one explicit source limit so programs behave the same on every
# supported interpreter. Arithmetic results remain arbitrary-precision ints.
MAX_INTEGER_LITERAL_DIGITS = 4_096


@dataclass
class Token:
    type: TokenType
    lexeme: str
    value: object
    location: SourceLocation

    def __repr__(self) -> str:
        v = "" if self.value is None else f" {self.value!r}"
        return f"Token({self.type.name}, {self.lexeme!r}{v}, {self.location})"


class Lexer:
    """Single pass, hand written tokenizer."""

    def __init__(self, source: str, filename: str = "<input>"):
        self._src = source
        self._file = filename
        self._pos = 0
        self._line = 1
        self._col = 1
        self._tokens: List[Token] = []

    # ---- public API ----

    def tokenize(self) -> List[Token]:
        while not self._at_end():
            self._skip_whitespace_and_comments()
            if self._at_end():
                break
            self._scan_token()
        self._tokens.append(
            Token(TokenType.EOF, "", None, self._loc())
        )
        return self._tokens

    # ---- helpers ----

    def _at_end(self) -> bool:
        return self._pos >= len(self._src)

    def _peek(self, offset: int = 0) -> str:
        i = self._pos + offset
        return self._src[i] if i < len(self._src) else "\0"

    def _advance(self) -> str:
        ch = self._src[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._col = 1
        else:
            self._col += 1
        return ch

    def _match(self, expected: str) -> bool:
        if self._peek() == expected:
            self._advance()
            return True
        return False

    def _loc(self) -> SourceLocation:
        return SourceLocation(self._file, self._line, self._col)

    def _add(
        self,
        type_: TokenType,
        lexeme: str,
        value: object,
        loc: SourceLocation,
    ) -> None:
        self._tokens.append(Token(type_, lexeme, value, loc))

    # ---- whitespace & comments ----

    def _skip_whitespace_and_comments(self) -> None:
        while not self._at_end():
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "/" and self._peek(1) == "/":
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            elif ch == "/" and self._peek(1) == "*":
                start = self._loc()
                self._advance()  # /
                self._advance()  # *
                closed = False
                while not self._at_end():
                    if self._peek() == "*" and self._peek(1) == "/":
                        self._advance()
                        self._advance()
                        closed = True
                        break
                    self._advance()
                if not closed:
                    raise LexerError("unterminated /* */ comment", start)
            else:
                return

    # ---- token dispatch ----

    def _scan_token(self) -> None:
        start_loc = self._loc()
        ch = self._advance()

        if ch.isalpha() or ch == "_":
            self._ident(ch, start_loc)
        elif ch.isdigit():
            self._number(ch, start_loc)
        elif ch == '"':
            self._string(start_loc)
        elif ch == "+":
            self._add(TokenType.PLUS, "+", None, start_loc)
        elif ch == "-":
            if self._match(">"):
                self._add(TokenType.ARROW, "->", None, start_loc)
            else:
                self._add(TokenType.MINUS, "-", None, start_loc)
        elif ch == "*":
            self._add(TokenType.STAR, "*", None, start_loc)
        elif ch == "/":
            self._add(TokenType.SLASH, "/", None, start_loc)
        elif ch == "%":
            self._add(TokenType.PERCENT, "%", None, start_loc)
        elif ch == "=":
            if self._match("="):
                self._add(TokenType.EQEQ, "==", None, start_loc)
            else:
                self._add(TokenType.EQ, "=", None, start_loc)
        elif ch == "!":
            if self._match("="):
                self._add(TokenType.NEQ, "!=", None, start_loc)
            else:
                self._add(TokenType.NOT, "!", None, start_loc)
        elif ch == "<":
            if self._match("="):
                self._add(TokenType.LE, "<=", None, start_loc)
            else:
                self._add(TokenType.LT, "<", None, start_loc)
        elif ch == ">":
            if self._match("="):
                self._add(TokenType.GE, ">=", None, start_loc)
            else:
                self._add(TokenType.GT, ">", None, start_loc)
        elif ch == "&":
            if self._match("&"):
                self._add(TokenType.AND, "&&", None, start_loc)
            else:
                raise LexerError("expected '&&'", start_loc)
        elif ch == "|":
            if self._match("|"):
                self._add(TokenType.OR, "||", None, start_loc)
            else:
                raise LexerError("expected '||'", start_loc)
        elif ch == ".":
            if self._match("."):
                self._add(TokenType.DOTDOT, "..", None, start_loc)
            else:
                raise LexerError("unexpected '.'; did you mean '..'?", start_loc)
        elif ch == "(":
            self._add(TokenType.LPAREN, "(", None, start_loc)
        elif ch == ")":
            self._add(TokenType.RPAREN, ")", None, start_loc)
        elif ch == "{":
            self._add(TokenType.LBRACE, "{", None, start_loc)
        elif ch == "}":
            self._add(TokenType.RBRACE, "}", None, start_loc)
        elif ch == "[":
            self._add(TokenType.LBRACKET, "[", None, start_loc)
        elif ch == "]":
            self._add(TokenType.RBRACKET, "]", None, start_loc)
        elif ch == ",":
            self._add(TokenType.COMMA, ",", None, start_loc)
        elif ch == ":":
            self._add(TokenType.COLON, ":", None, start_loc)
        else:
            raise LexerError(f"unexpected character {ch!r}", start_loc)

    # ---- literals & identifiers ----

    def _ident(self, first: str, loc: SourceLocation) -> None:
        chars = [first]
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            chars.append(self._advance())
        text = "".join(chars)
        kw = KEYWORDS.get(text)
        if kw is not None:
            if kw is TokenType.TRUE:
                self._add(TokenType.TRUE, text, True, loc)
            elif kw is TokenType.FALSE:
                self._add(TokenType.FALSE, text, False, loc)
            else:
                self._add(kw, text, None, loc)
        else:
            self._add(TokenType.IDENT, text, text, loc)

    def _number(self, first: str, loc: SourceLocation) -> None:
        chars = [first]
        while not self._at_end() and self._peek().isdigit():
            chars.append(self._advance())
        is_float = False
        # A "." followed by another "." is the range operator, not a float.
        if self._peek() == "." and self._peek(1) != ".":
            if not self._peek(1).isdigit():
                raise LexerError(
                    "expected digit after '.' in float literal", self._loc()
                )
            is_float = True
            chars.append(self._advance())  # .
            while not self._at_end() and self._peek().isdigit():
                chars.append(self._advance())
        text = "".join(chars)
        if is_float:
            value = float(text)
            if not math.isfinite(value):
                raise LexerError("float literal is out of range", loc)
            self._add(TokenType.FLOAT, text, value, loc)
        else:
            if len(text) > MAX_INTEGER_LITERAL_DIGITS:
                raise LexerError(
                    "integer literal exceeds the 4,096 digit limit", loc
                )
            self._add(TokenType.INT, text, int(text), loc)

    def _string(self, loc: SourceLocation) -> None:
        chars: list[str] = []
        while not self._at_end() and self._peek() != '"':
            ch = self._advance()
            if ch == "\\":
                if self._at_end():
                    raise LexerError("unterminated string literal", loc)
                esc = self._advance()
                chars.append(self._decode_escape(esc, loc))
            else:
                chars.append(ch)
        if self._at_end():
            raise LexerError("unterminated string literal", loc)
        self._advance()  # closing "
        text = "".join(chars)
        self._add(TokenType.STRING, f'"{text}"', text, loc)

    def _decode_escape(self, esc: str, loc: SourceLocation) -> str:
        table = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}
        if esc not in table:
            raise LexerError(f"unknown escape '\\{esc}'", loc)
        return table[esc]


def tokenize(source: str, filename: str = "<input>") -> List[Token]:
    """Shortcut for building a Lexer and running it."""
    return Lexer(source, filename).tokenize()
