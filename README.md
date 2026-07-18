# Chas

Chas is a small statically typed language I wrote to teach myself how
compilers actually work. It has its own lexer, parser, AST, type checker,
and tree-walking interpreter, all handwritten in Python with no external
libraries.

The whole pipeline is short enough to read in one sitting, which was kind
of the point. If you have ever looked at a "build your own language" book
and wondered what a finished project from one looks like, this is that.

Version 0.1.0. File extension `.chs`. Python 3.10 or newer.

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

There are no semicolons, blocks use braces, and type annotations can be
left off when the type checker can infer them. `let x = 5` infers `int`,
while `let x: int = 5` keeps the type explicit.

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

The CLI exposes each stage of the interpreter:

```console
python chas/chas.py run chas/examples/hello.chs
python chas/chas.py tokens chas/examples/fibonacci.chs
python chas/chas.py ast chas/examples/fibonacci.chs
python chas/chas.py check chas/examples/fibonacci.chs
python chas/chas.py --version
```

The repository includes three sample programs:

- [`hello.chs`](chas/examples/hello.chs) covers literals and built-in functions.
- [`fibonacci.chs`](chas/examples/fibonacci.chs) uses recursion, a `while` loop,
  and `for-in` over a range.
- [`closures.chs`](chas/examples/closures.chs) shows nested functions reading and
  mutating variables from an enclosing scope.

## Single-file build

The release build is a Python zip application, so it stays portable without
turning the interpreter into a platform-specific executable.

```console
python scripts/build_zipapp.py
python dist/chas-0.1.0.pyz --version
python dist/chas-0.1.0.pyz run program.chs
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

Every Chas error has a file, line, column, category, and readable message.
The CLI catches language errors instead of exposing a Python traceback.

```text
RuntimeError at program.chs:7:12: integer division by zero
TypeError at program.chs:3:5: 'if' condition must be bool, got int
NameError at program.chs:9:16: undeclared name 'notdefined'
```

## How it works

The interpreter keeps its pipeline small:

```text
source -> lexer -> parser -> semantic analysis -> evaluator -> output
          tokens    AST        typed program       values
```

The implementation is split by stage:

```text
.
|-- chas/
|   |-- chas.py              command-line entry point
|   |-- SPEC.md              grammar and semantics
|   |-- examples/            sample .chs programs
|   `-- src/
|       |-- errors.py        diagnostics and source locations
|       |-- lexer.py         tokenizer
|       |-- ast_nodes.py     AST dataclasses and pretty-printer
|       |-- parser.py        recursive-descent parser
|       |-- semantic.py      type checker and scope resolver
|       `-- evaluator.py     tree-walking interpreter
|-- scripts/
|   `-- build_zipapp.py      single-file release builder
`-- tests/
    `-- test_chas.py
```

AST nodes are dataclasses, which makes them easy to construct, compare, and
print while debugging. The parser uses one method per precedence level and
maps closely to the BNF in the specification. Types use small frozen
dataclasses instead of strings.

Top-level functions are hoisted in a first pass, so mutual recursion works
without forward declarations. Nested functions are declared in source order.

## Current limits

Chas does not yet have:

1. Function types in source annotations
2. Arrays and indexing
3. User-defined records or structs
4. A bytecode compiler or virtual machine

The current tree walker favors readability over execution speed.

## Tests

Run the lexer, parser, type checker, evaluator, CLI, and example coverage from
the repository root:

```console
python -m unittest discover -s tests -v
```

## License

Chas is available under the [MIT License](LICENSE).
