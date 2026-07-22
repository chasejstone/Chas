# Chas

[![CI](https://github.com/chasejstone/Chas/actions/workflows/ci.yml/badge.svg)](https://github.com/chasejstone/Chas/actions/workflows/ci.yml)

Chas is a small statically typed language I wrote to teach myself how
compilers actually work. It has a handwritten lexer, recursive-descent parser,
AST, type checker, and two execution backends: the original tree walker and a
stack bytecode virtual machine. Everything is Python standard library code.

The project is still short enough to follow end to end, which is the point.
Version 0.2.0 makes the compiler and runtime inspectable without hiding the
readable reference implementation behind them.

Implementation version **0.2.0**. Language version **0.1**. File extension
`.chs`. Python 3.10 or newer.

## Chas Studio

Chas Studio is a dependency-free local browser workbench for exploring the
whole language pipeline without leaving the source editor.

```console
python chas/chas.py studio
python chas/chas.py studio chas/examples/closures.chs
```

Studio opens a local page with bundled examples, Check and Run actions, and a
choice between the tree walker and bytecode VM. Alongside program output, it
shows diagnostics, tokens, the AST, and compiled bytecode. The interface is
responsive, keyboard friendly, and entirely embedded in the Python
application, so the portable build includes it too.

The server binds only to `127.0.0.1` and uses a random token for every launch.
It loads no CDN code, exposes no host-file API, and limits request size, source
size, inspection data, program output, and executed instructions.

Studio is deliberately focused in this release. It holds one in-memory
document and does not yet have a project browser, LSP, breakpoints, or a visual
instruction-step debugger. The VM exposes the stepping primitives for that
next layer.

## A quick look

```text
fn fib(n: int) -> int {
    if n < 2 {
        return n
    }
    return fib(n - 1) + fib(n - 2)
}

for i in 0..10 {
    print(fib(i))
}
```

There are no semicolons, blocks use braces, and type annotations can be left
off when the type checker can infer them. `let x = 5` infers `int`, while
`let x: int = 5` keeps the type explicit.

## Running it

Clone the repository and run the CLI. There are no packages to install.

```console
git clone https://github.com/chasejstone/Chas.git
cd Chas
python chas/chas.py run chas/examples/hello.chs
```

That example prints:

```text
Hello, Chas!
Year: 2026
string
int
float
bool
```

The CLI exposes every compiler stage and both execution engines:

```console
python chas/chas.py run chas/examples/hello.chs
python chas/chas.py run chas/examples/hello.chs --engine vm
python chas/chas.py bytecode chas/examples/fibonacci.chs
python chas/chas.py tokens chas/examples/fibonacci.chs
python chas/chas.py ast chas/examples/fibonacci.chs
python chas/chas.py check chas/examples/fibonacci.chs
python chas/chas.py --version
```

`run` uses the tree walker by default. `--engine vm` runs the same checked AST
through the compiler and bytecode VM. `bytecode` prints a source-mapped
disassembly, including nested function code objects.

The repository includes three sample programs:

- [`hello.chs`](chas/examples/hello.chs) covers literals and built-in functions.
- [`fibonacci.chs`](chas/examples/fibonacci.chs) uses recursion, a `while` loop,
  and `for-in` over a range.
- [`closures.chs`](chas/examples/closures.chs) shows nested functions reading and
  mutating variables from an enclosing scope.

## Bytecode VM

The 0.2.0 VM is a real second backend, not a separate dialect. It supports the
current language, including closures, mutual recursion, short-circuiting, and
fresh loop scopes. Its explicit frame stack can run deep Chas recursion without
consuming the Python call stack.

```console
python chas/chas.py run program.chs --engine vm
python chas/chas.py bytecode program.chs
```

Each instruction retains its Chas source location. The VM can execute one
instruction at a time and expose stack and scope snapshots for tooling. An
instruction budget stops runaway programs with a normal source-located Chas
diagnostic.

The VM deliberately uses chained name environments so its closure and scoping
behavior stays easy to compare with the reference evaluator. Version 0.2.0
makes no performance claim for it. See the
[`bytecode guide`](docs/BYTECODE.md) for the instruction model and current
limits.

## Single-file build

The release build is a Python zip application, so it stays portable without
turning Chas into a platform-specific executable.

```console
python scripts/build_zipapp.py
python dist/chas-0.2.0.pyz --version
python dist/chas-0.2.0.pyz studio
python dist/chas-0.2.0.pyz run program.chs --engine vm
```

## The language

The full grammar and semantics are in the
[`Chas language specification`](chas/SPEC.md). The short version:

- Primitive types are `int`, `float`, `string`, `bool`, and `void`.
- Variables use `let`, with optional type annotations.
- Functions use `fn` and have typed parameters and return values.
- Control flow includes `if`, `else if`, `else`, `while`, and `for-in`.
- The `..` operator creates a half-open integer range.
- Built-ins are `print`, `len`, `range`, and `type`.

```text
let x = 5
let y: float = 3.14
let name = "chas"

fn add(a: int, b: int) -> int {
    return a + b
}
```

Closures capture variables by reference. A nested function can update a
variable from the scope where it was defined:

```text
fn counter() {
    let n = 0
    fn tick() {
        n = n + 1
        print(n)
    }
    tick()
    tick()
    tick()
}
```

## Errors

Every Chas error has a file, line, column, category, and readable message. The
CLI and Studio catch language errors instead of exposing Python tracebacks.

```text
RuntimeError at program.chs:7:12: integer division by zero
TypeError at program.chs:3:5: 'if' condition must be bool, got int
NameError at program.chs:9:16: undeclared name 'notdefined'
```

## How it works

The checked AST feeds two deliberately compatible execution paths:

```text
source -> lexer -> parser -> semantic analysis -> checked AST
                                                    |-- tree evaluator -> output
                                                    `-- bytecode compiler -> stack VM -> output
```

The tree walker is the smallest statement of Chas semantics, so it remains the
default and acts as the reference in VM parity tests. The VM uses an operand
stack, explicit call frames, and source-mapped instructions. Closures in both
backends capture chained lexical environments by reference.

The implementation stays split by stage:

```text
.
|-- chas/
|   |-- chas.py                 command-line entry point
|   |-- SPEC.md                 grammar and language semantics
|   |-- examples/               sample .chs programs
|   `-- src/
|       |-- errors.py           diagnostics and source locations
|       |-- lexer.py            tokenizer
|       |-- ast_nodes.py        AST dataclasses and pretty-printer
|       |-- parser.py           recursive-descent parser
|       |-- semantic.py         type checker and scope resolver
|       |-- evaluator.py        reference tree-walking evaluator
|       |-- bytecode.py         compiler, VM, and disassembler
|       |-- runtime_format.py   cross-version numeric output formatting
|       |-- studio_service.py   bounded compiler and execution services
|       |-- studio.py           local server and embedded browser UI
|       `-- version.py          implementation version
|-- docs/
|   `-- BYTECODE.md             bytecode design and instruction model
|-- scripts/
|   `-- build_zipapp.py         single-file release builder
`-- tests/                      language, VM, CLI, and Studio coverage
```

## Current limits

Chas does not yet have:

1. Function types in source annotations
2. Arrays and indexing
3. User-defined records or structs
4. Modules or imports
5. A stable serialized bytecode format, optimizer, JIT, or native backend

Studio is a single-document compiler workbench rather than a general editor.
The VM resolves names through chained dictionaries and is designed for clarity
and inspection, not benchmark wins.

## Tests

Run the lexer, parser, type checker, both execution engines, Studio services,
CLI, examples, and portable-build coverage from the repository root:

```console
python -m unittest discover -s tests -v
python scripts/build_zipapp.py
```

CI runs on Windows and Linux with Python 3.10 and 3.14. See
[`CHANGELOG.md`](CHANGELOG.md) for release notes.

## License

Chas is available under the [MIT License](LICENSE).
