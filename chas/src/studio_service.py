"""Pure compiler services used by the local Chas Studio web UI.

Nothing in this module knows about HTTP or a browser.  Keeping that boundary
small makes the compiler pipeline, execution budgets, and output limits easy
to exercise in headless tests.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

import ast_nodes as ast
from errors import ChasError, LexerError, RuntimeError_, SourceLocation
from evaluator import Evaluator, _Environment, _Range
from lexer import Token, TokenType, tokenize
from parser import parse
from semantic import analyze


MAX_SOURCE_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_INSPECTION_BYTES = 512 * 1024
MAX_TOKENS = 8_000
DEFAULT_INSTRUCTION_LIMIT = 100_000
MAX_INSTRUCTION_LIMIT = 1_000_000


class StudioLimitError(ValueError):
    """Input supplied to a Studio service exceeded a public limit."""


class _OutputLimitExceeded(Exception):
    pass


def _new_stages() -> dict[str, str]:
    return {
        "lexer": "pending",
        "parser": "blocked",
        "types": "blocked",
        "bytecode": "blocked",
        "run": "idle",
    }


def _validate_source(source: str) -> None:
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    size = len(source.encode("utf-8"))
    if size > MAX_SOURCE_BYTES:
        raise StudioLimitError(
            f"source is {size:,} bytes; the Studio limit is "
            f"{MAX_SOURCE_BYTES:,} bytes"
        )


def _validate_instruction_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("instruction_limit must be an integer")
    if value < 1 or value > MAX_INSTRUCTION_LIMIT:
        raise StudioLimitError(
            f"instruction_limit must be between 1 and "
            f"{MAX_INSTRUCTION_LIMIT:,}"
        )
    return value


def _diagnostic(error: ChasError) -> dict[str, object]:
    location = error.location or SourceLocation("<studio>", 1, 1)
    return {
        "category": error.category,
        "message": error.message,
        "file": location.file,
        "line": location.line,
        "column": location.column,
        "rendered": error.render(),
    }


def _limit_diagnostic(message: str, filename: str) -> dict[str, object]:
    return {
        "category": "LimitError",
        "message": message,
        "file": filename,
        "line": 1,
        "column": 1,
        "rendered": f"LimitError at {filename}:1:1: {message}",
    }


def _token_record(token: Token) -> dict[str, object]:
    return {
        "kind": token.type.name,
        "lexeme": token.lexeme,
        "value": token.value,
        "line": token.location.line,
        "column": token.location.column,
    }


def _truncate_text(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_INSPECTION_BYTES:
        return text, False
    clipped = raw[:MAX_INSPECTION_BYTES].decode("utf-8", errors="ignore")
    return clipped + "\n\n... inspector output truncated ...", True


def _bytecode_api() -> Optional[tuple[Callable, Callable, Callable]]:
    """Return optional VM functions without making Studio require the VM."""

    try:
        from bytecode import compile_program, disassemble, run
    except (ImportError, AttributeError):
        return None
    return compile_program, disassemble, run


def _pipeline(
    source: str,
    filename: str,
) -> tuple[dict[str, object], Optional[ast.Program], object]:
    _validate_source(source)
    started = time.perf_counter()
    stages = _new_stages()
    result: dict[str, object] = {
        "ok": False,
        "stages": stages,
        "diagnostics": [],
        "tokens": [],
        "tokens_truncated": False,
        "ast": "",
        "ast_truncated": False,
        "bytecode": "",
        "bytecode_truncated": False,
        "bytecode_available": False,
        "duration_ms": 0.0,
    }

    try:
        tokens = tokenize(source, filename)
        for token in tokens:
            if isinstance(token.value, float) and not math.isfinite(token.value):
                raise LexerError(
                    "float literal is outside the supported range",
                    token.location,
                )
        stages["lexer"] = "passed"
    except ChasError as error:
        stages["lexer"] = "failed"
        result["diagnostics"] = [_diagnostic(error)]
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result, None, None

    visible_tokens = [token for token in tokens if token.type is not TokenType.EOF]
    result["tokens"] = [
        _token_record(token) for token in visible_tokens[:MAX_TOKENS]
    ]
    result["tokens_truncated"] = len(visible_tokens) > MAX_TOKENS

    stages["parser"] = "pending"
    try:
        program = parse(tokens)
        ast_text, ast_truncated = _truncate_text(program.pretty())
        stages["parser"] = "passed"
    except ChasError as error:
        stages["parser"] = "failed"
        result["diagnostics"] = [_diagnostic(error)]
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result, None, None
    except RecursionError:
        stages["parser"] = "failed"
        result["diagnostics"] = [
            _limit_diagnostic("source nesting is too deep", filename)
        ]
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result, None, None

    result["ast"] = ast_text
    result["ast_truncated"] = ast_truncated

    stages["types"] = "pending"
    try:
        analyze(program)
        stages["types"] = "passed"
    except ChasError as error:
        stages["types"] = "failed"
        result["diagnostics"] = [_diagnostic(error)]
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result, program, None
    except RecursionError:
        stages["types"] = "failed"
        result["diagnostics"] = [
            _limit_diagnostic("source nesting is too deep", filename)
        ]
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result, program, None

    compiled = None
    api = _bytecode_api()
    if api is None:
        stages["bytecode"] = "unavailable"
    else:
        result["bytecode_available"] = True
        stages["bytecode"] = "pending"
        compile_program, disassemble, _ = api
        try:
            compiled = compile_program(program)
            bytecode_text, bytecode_truncated = _truncate_text(disassemble(compiled))
            result["bytecode"] = bytecode_text
            result["bytecode_truncated"] = bytecode_truncated
            stages["bytecode"] = "passed"
        except ChasError as error:
            stages["bytecode"] = "failed"
            result["diagnostics"] = [_diagnostic(error)]
            result["duration_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
            return result, program, None
        except RecursionError:
            stages["bytecode"] = "failed"
            result["diagnostics"] = [
                _limit_diagnostic("source nesting is too deep", filename)
            ]
            result["duration_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
            return result, program, None

    result["ok"] = True
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result, program, compiled


def analyze_source(
    source: str,
    *,
    filename: str = "<studio>",
) -> dict[str, object]:
    """Lex, parse, type-check, and optionally compile one source string."""

    result, _, _ = _pipeline(source, filename)
    return result


class _OutputCollector:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._size = 0
        self._parts: list[str] = []

    def write(self, value: str) -> None:
        text = f"{value}\n"
        encoded = text.encode("utf-8")
        if self._size + len(encoded) > self._limit:
            raise _OutputLimitExceeded
        self._parts.append(text)
        self._size += len(encoded)

    @property
    def text(self) -> str:
        return "".join(self._parts)


class _BudgetEvaluator(Evaluator):
    """Reference evaluator with a Studio-only cooperative step budget."""

    def __init__(self, output: Callable[[str], None], instruction_limit: int):
        super().__init__(output=output)
        self._studio_instruction_limit = instruction_limit
        self._studio_instructions = 0

    def _tick(self, location: SourceLocation) -> None:
        self._studio_instructions += 1
        if self._studio_instructions > self._studio_instruction_limit:
            raise RuntimeError_(
                f"instruction limit of {self._studio_instruction_limit} exceeded",
                location,
            )

    def _exec(self, stmt, env) -> None:
        self._tick(stmt.location)
        # The reference evaluator executes an empty for-body without another
        # AST visit. Count each iteration here so a huge empty range cannot
        # bypass Studio's cooperative budget.
        if isinstance(stmt, ast.For):
            iterable = self._eval(stmt.iterable, env)
            if not isinstance(iterable, _Range):
                raise RuntimeError_(
                    "for-in iterable must be a range", stmt.location
                )
            for value in iterable:
                self._tick(stmt.location)
                inner = _Environment(parent=env)
                inner.define(stmt.var_name, value)
                for child in stmt.body.statements:
                    self._exec(child, inner)
            return
        super()._exec(stmt, env)

    def _eval(self, expr, env):
        self._tick(expr.location)
        return super()._eval(expr, env)


def run_source(
    source: str,
    *,
    engine: str = "tree",
    filename: str = "<studio>",
    instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
    output_limit: int = MAX_OUTPUT_BYTES,
) -> dict[str, object]:
    """Analyze and execute source with bounded instructions and output."""

    if engine not in ("tree", "vm"):
        raise ValueError("engine must be 'tree' or 'vm'")
    instruction_limit = _validate_instruction_limit(instruction_limit)
    if isinstance(output_limit, bool) or not isinstance(output_limit, int):
        raise TypeError("output_limit must be an integer")
    if output_limit < 1 or output_limit > MAX_OUTPUT_BYTES:
        raise StudioLimitError(
            f"output_limit must be between 1 and {MAX_OUTPUT_BYTES:,}"
        )

    result, program, compiled = _pipeline(source, filename)
    result["engine"] = engine
    result["output"] = ""
    if not result["ok"] or program is None:
        return result

    collector = _OutputCollector(output_limit)
    result["stages"]["run"] = "pending"
    started = time.perf_counter()
    try:
        if engine == "tree":
            _BudgetEvaluator(collector.write, instruction_limit).run(program)
        else:
            api = _bytecode_api()
            if api is None:
                result["ok"] = False
                result["stages"]["run"] = "failed"
                result["diagnostics"] = [
                    _limit_diagnostic("the bytecode VM is unavailable", filename)
                ]
                return result
            _, _, run_vm = api
            run_vm(
                compiled if compiled is not None else program,
                output=collector.write,
                instruction_limit=instruction_limit,
            )
        result["stages"]["run"] = "passed"
    except _OutputLimitExceeded:
        result["ok"] = False
        result["stages"]["run"] = "failed"
        result["diagnostics"] = [
            _limit_diagnostic(
                f"output limit of {output_limit:,} bytes exceeded", filename
            )
        ]
    except ChasError as error:
        result["ok"] = False
        result["stages"]["run"] = "failed"
        result["diagnostics"] = [_diagnostic(error)]
    except RecursionError:
        result["ok"] = False
        result["stages"]["run"] = "failed"
        result["diagnostics"] = [
            _limit_diagnostic("maximum call depth exceeded", filename)
        ]
    except (ArithmeticError, OverflowError):
        result["ok"] = False
        result["stages"]["run"] = "failed"
        result["diagnostics"] = [
            _limit_diagnostic("numeric result is outside the supported range", filename)
        ]

    result["output"] = collector.text
    result["run_duration_ms"] = round(
        (time.perf_counter() - started) * 1000, 2
    )
    return result
