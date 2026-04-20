# Chas

Chas is a small statically typed language I wrote to teach myself how
compilers actually work. It has its own lexer, parser, AST, type checker,
and tree walking interpreter, all hand written in Python with no external
libraries.

The whole pipeline is short enough to read in one sitting, which was kind
of the point. If you've ever looked at a "build your own language" book
and wondered what a finished project from one looks like, this is that.

Version 0.1.0. File extension `.chs`. Python 3.10 or newer.

## A quick look

```
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

No semicolons, braces for blocks, and you can leave off type annotations
when the compiler can figure them out. `let x = 5` infers `int`, but you
can still write `let x: int = 5` if you want to be explicit.

## Running it

There's nothing to install. Just run the CLI:

```
python chas.py run    examples/hello.chs
python chas.py tokens examples/fibonacci.chs   # dump the token stream
python chas.py ast    examples/fibonacci.chs   # dump the AST
python chas.py check  examples/fibonacci.chs   # type check only, no run
```

### Examples included

`examples/hello.chs` covers literals and the built in functions.
`examples/fibonacci.chs` does recursion, a `while` loop, and `for-in` over
a range. `examples/closures.chs` shows a nested function mutating a
variable from its enclosing scope.

## The language

The real reference is `SPEC.md`, which has the full BNF grammar. Short
version:

Primitive types are `int`, `float`, `string`, `bool`, and `void`. You
declare variables with `let` and functions with `fn`:

```
let x = 5              // inferred as int
let y: float = 3.14    // annotated
let name = "chas"
let ok: bool = true

fn add(a: int, b: int) -> int {
    return a + b
}
```

Control flow is the usual `if / else if / else`, `while`, and `for i in
0..10`. The `..` operator makes a range. The standard library is tiny:
`print`, `len`, `range`, and `type`.

Closures work the way you'd expect. A nested `fn` captures variables from
the scope it was defined in, and can mutate them:

```
fn counter() {
    let n = 0
    fn tick() {
        n = n + 1
        print(n)
    }
    tick()   // 1
    tick()   // 2
    tick()   // 3
}
```

### Errors

Every error has a file, line, column, category, and a human readable
message. You never see a Python traceback, even when the program blows
up at runtime:

```
RuntimeError at program.chs:7:12: integer division by zero
TypeError    at program.chs:3:5:  'if' condition must be bool, got int
NameError    at program.chs:9:16: undeclared name 'notdefined'
```

## How it's put together

The pipeline is the classic five stages:

```
source  ->  lexer  ->  parser  ->  semantic  ->  evaluator  ->  output
            tokens     AST        typed AST     values
```

Each stage lives in its own file under `src/`, and each one is usable on
its own if you want to poke at it from Python:

```python
from lexer import tokenize
from parser import parse
from semantic import analyze
from evaluator import run

program = parse(tokenize(open("foo.chs").read(), "foo.chs"))
analyze(program)
run(program)
```

### Layout

```
chas/
├── chas.py              CLI
├── README.md
├── SPEC.md              grammar and semantics
├── src/
│   ├── errors.py        ChasError + SourceLocation
│   ├── lexer.py         tokenizer
│   ├── ast_nodes.py     AST dataclasses and visitor
│   ├── parser.py        recursive descent parser
│   ├── semantic.py      type checker and scope resolver
│   └── evaluator.py     tree walking interpreter
└── examples/
    ├── hello.chs
    ├── fibonacci.chs
    └── closures.chs
```

### A few notes on the design

The AST nodes are just dataclasses, which made them free to construct,
compare, and pretty print while I was debugging. I used a classic visitor
pattern where each analyzer method is named `visit_<NodeName>`, but the
analyzer and evaluator honestly just use `isinstance` chains because that
turned out to be easier to read.

The parser is precedence climbing, one method per precedence level, and
maps almost one to one onto the BNF in `SPEC.md`. I started with a
Pratt parser and switched when I realized precedence climbing was simpler
for the operator set Chas has.

Types are represented as a small frozen dataclass, not strings, so the
type checker does identity comparisons instead of string equality. There
is one slightly unusual thing in there: functions are hoisted at the top
of each scope in a first pass, so mutual recursion at the top level works
without forward declarations.

## What's missing

There's plenty I haven't built yet:

1. First class `fn` types in annotations, so you could actually pass a
   function as a parameter with a proper type. Right now closures work at
   runtime but the type system can't talk about function types in
   signatures.
2. Arrays and indexing.
3. User defined records or structs.
4. A bytecode compiler and VM. The current implementation is a tree
   walker, which is slow but easy to follow.
5. A real test suite under `tests/`. Right now I tested each stage by
   hand while building it.

## License

MIT. See the repository root.
