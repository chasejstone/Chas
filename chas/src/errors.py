"""
errors.py

All the error types Chas can throw. Every stage of the compiler (lexer,
parser, type checker, interpreter) raises something from here, and each
error carries a SourceLocation so the CLI can print the file, line, and
column instead of a Python traceback.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceLocation:
    """A spot in a source file. Lines and columns both start at 1."""

    file: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


class ChasError(Exception):
    """Base class for anything the user should see as a Chas error."""

    category: str = "Error"

    def __init__(self, message: str, location: SourceLocation | None = None):
        super().__init__(message)
        self.message = message
        self.location = location

    def render(self) -> str:
        loc = str(self.location) if self.location else "<unknown>"
        return f"{self.category} at {loc}: {self.message}"


class LexerError(ChasError):
    category = "LexerError"


class ParseError(ChasError):
    category = "ParseError"


class TypeError_(ChasError):
    """Type checker error. Trailing underscore keeps it from clashing
    with the built in TypeError."""

    category = "TypeError"


class NameError_(ChasError):
    """Something is undeclared, or redeclared in the same scope."""

    category = "NameError"


class RuntimeError_(ChasError):
    """Anything the interpreter blows up on at runtime."""

    category = "RuntimeError"
