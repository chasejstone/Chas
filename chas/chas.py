#!/usr/bin/env python3
"""
chas.py

Command line entry point. Usage:

    python chas.py run    <file.chs>    run a program
    python chas.py tokens <file.chs>    print the token stream
    python chas.py ast    <file.chs>    print the parsed AST
    python chas.py check  <file.chs>    lex + parse + type check only
    python chas.py bytecode <file.chs>  print compiled bytecode
    python chas.py studio [file.chs]    open Chas Studio
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
from version import VERSION  # noqa: E402


USAGE = f"""\
Chas {VERSION}, a small statically typed language.

Usage:
  python chas.py run      <file.chs> [--engine tree|vm]
  python chas.py tokens   <file.chs>   print the token stream
  python chas.py ast      <file.chs>   print the AST
  python chas.py check    <file.chs>   type check, don't run
  python chas.py bytecode <file.chs>   print compiled bytecode
  python chas.py studio   [file.chs]   open the local browser studio
  python chas.py --help                show this message
  python chas.py --version             show the version
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


def cmd_run(path: str, engine: str = "tree") -> int:
    src = _read_source(path)
    program = parse(tokenize(src, path))
    analyze(program)
    if engine == "vm":
        from bytecode import run as run_bytecode

        # The interactive Studio applies a bounded instruction budget. The
        # ordinary CLI, like the reference tree walker, runs until completion
        # or interruption.
        run_bytecode(program, instruction_limit=None)
    else:
        evaluate(program)
    return 0


def cmd_bytecode(path: str) -> int:
    from bytecode import disassemble

    src = _read_source(path)
    program = parse(tokenize(src, path))
    analyze(program)
    print(disassemble(program))
    return 0


def _run_engine(args: List[str]) -> str:
    """Read the optional run-engine flag without disturbing old calls."""

    if not args:
        return "tree"
    if len(args) == 1 and args[0].startswith("--engine="):
        engine = args[0].split("=", 1)[1]
    elif len(args) == 2 and args[0] == "--engine":
        engine = args[1]
    else:
        raise ValueError("run accepts only --engine tree or --engine vm")
    if engine not in ("tree", "vm"):
        raise ValueError("run engine must be 'tree' or 'vm'")
    return engine


def cmd_studio(args: List[str]) -> int:
    from studio import serve

    path = None
    port = 0
    open_browser = True
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--no-open":
            open_browser = False
        elif arg == "--port":
            i += 1
            if i >= len(args):
                raise ValueError("studio --port requires a number")
            try:
                port = int(args[i])
            except ValueError as exc:
                raise ValueError("studio port must be a number") from exc
            if not 0 <= port <= 65535:
                raise ValueError("studio port must be between 0 and 65535")
        elif arg.startswith("-"):
            raise ValueError(f"unknown studio option {arg!r}")
        elif path is None:
            path = arg
        else:
            raise ValueError("studio accepts at most one source file")
        i += 1

    initial_source = _read_source(path) if path is not None else None
    serve(
        port=port,
        open_browser=open_browser,
        initial_source=initial_source,
    )
    return 0


def main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("-V", "--version", "version"):
        print(f"Chas {VERSION}")
        return 0

    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if len(argv) >= 2 else 1

    cmd = argv[1]
    if cmd not in ("run", "tokens", "ast", "check", "bytecode", "studio"):
        print(f"error: unknown command {cmd!r}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    if cmd == "studio":
        try:
            return cmd_studio(argv[2:])
        except (ChasError, ValueError, OSError) as err:
            if isinstance(err, ChasError):
                print(err.render(), file=sys.stderr)
            else:
                print(f"error: {err}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130

    if len(argv) < 3:
        print(f"error: {cmd} requires a file argument", file=sys.stderr)
        return 2

    path = argv[2]
    try:
        if cmd == "run":
            try:
                engine = _run_engine(argv[3:])
            except ValueError as err:
                print(f"error: {err}", file=sys.stderr)
                return 2
            return cmd_run(path, engine)
        if cmd == "tokens":
            return cmd_tokens(path)
        if cmd == "ast":
            return cmd_ast(path)
        if cmd == "check":
            return cmd_check(path)
        if cmd == "bytecode":
            return cmd_bytecode(path)
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
