#!/usr/bin/env python3
"""
chas.py

Command line entry point. Usage:

    python chas.py run    <file.chs>    run a program
    python chas.py tokens <file.chs>    print the token stream
    python chas.py ast    <file.chs>    print the parsed AST
    python chas.py check  <file.chs>    lex + parse + type check only
    python chas.py --help               print this

Anywhere in the pipeline that raises a ChasError gets caught here and
printed as a one line diagnostic. The user never sees a Python
traceback, even for runtime errors.
"""

from __future__ import annotations

import os
import sys
from typing import List


# Put src/ on sys.path so the imports below work whether you run this as
# `python chas.py` or `python chas/chas.py`.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from errors import ChasError  # noqa: E402
from lexer import tokenize  # noqa: E402
from parser import parse  # noqa: E402
from semantic import analyze  # noqa: E402
from evaluator import run as evaluate  # noqa: E402


VERSION = "0.1.0"

USAGE = f"""\
Chas {VERSION}, a small statically typed language.

Usage:
  python chas.py run    <file.chs>   run a program
  python chas.py tokens <file.chs>   print the token stream
  python chas.py ast    <file.chs>   print the AST
  python chas.py check  <file.chs>   type check, don't run
  python chas.py --help              show this message
  python chas.py --version           show the version
"""


def _read_source(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"error: no such file: {path}", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def cmd_tokens(path: str) -> int:
    src = _read_source(path)
    for tok in tokenize(src, path):
        print(tok)
    return 0


def cmd_ast(path: str) -> int:
    src = _read_source(path)
    program = parse(tokenize(src, path))
    print(program)
    return 0


def cmd_check(path: str) -> int:
    src = _read_source(path)
    program = parse(tokenize(src, path))
    analyze(program)
    print(f"OK: {path} type-checks cleanly.")
    return 0


def cmd_run(path: str) -> int:
    src = _read_source(path)
    program = parse(tokenize(src, path))
    analyze(program)
    evaluate(program)
    return 0


def main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("-V", "--version", "version"):
        print(f"Chas {VERSION}")
        return 0

    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if len(argv) >= 2 else 1

    cmd = argv[1]
    if cmd not in ("run", "tokens", "ast", "check"):
        print(f"error: unknown command {cmd!r}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    if len(argv) < 3:
        print(f"error: {cmd} requires a file argument", file=sys.stderr)
        return 2

    path = argv[2]
    try:
        if cmd == "run":
            return cmd_run(path)
        if cmd == "tokens":
            return cmd_tokens(path)
        if cmd == "ast":
            return cmd_ast(path)
        if cmd == "check":
            return cmd_check(path)
    except ChasError as err:
        print(err.render(), file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Something like `| head` closed the pipe on us. Exit quietly.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
