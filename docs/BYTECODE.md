# Chas Bytecode

Chas 0.2 includes a stack bytecode compiler and virtual machine alongside the
original tree-walking evaluator. The evaluator remains the smallest reference
implementation. The VM exists to make execution inspectable: Chas Studio shows
the compiled instruction listing today, while the stepping API exposes the
operand stack, source location, and lexical scopes for a future visual debugger
or other tooling.

The bytecode is an internal representation, not a stable file format. There is
no bytecode loader for untrusted files, and opcode compatibility between Chas
versions is not promised.

## Pipeline

```text
source -> lexer -> parser -> type checker -> AST
                                             |-- tree evaluator
                                             `-- bytecode compiler -> VM
```

Semantic analysis always runs before compilation. The compiler therefore
expects declarations, calls, and operators to be well typed, while the VM still
turns malformed bytecode state into a source-located Chas runtime error instead
of exposing an implementation traceback.

## Instruction model

Every instruction contains an opcode, an optional operand, and the source
location of the construct that produced it. Code objects have their own
instruction stream and constant pool. A compiled program contains a main code
object plus nested code objects for functions.

The opcodes fall into four groups:

- Values and names: `CONSTANT`, `LOAD_NAME`, `DEFINE_NAME`, `STORE_NAME`,
  `POP`, and `DUP`.
- Scopes and calls: `ENTER_SCOPE`, `EXIT_SCOPE`, `MAKE_CLOSURE`, `CALL`,
  `RETURN`, and `RETURN_VOID`.
- Expressions: arithmetic and comparison operations plus `NEGATE`, `NOT`, and
  `MAKE_RANGE`.
- Control flow: conditional and unconditional jumps, iterator operations, and
  `HALT`.

Use the CLI to inspect the exact listing for a program:

```console
python chas/chas.py bytecode chas/examples/fibonacci.chs
```

The disassembly is deterministic and recursively includes nested function code
objects.

## Environments and closures

The VM deliberately uses chained, mutable name environments rather than local
slots and copied capture arrays. That keeps it faithful to the language:

- closures capture their defining environment by reference;
- assignment walks outward to the nearest existing binding;
- every block opens a lexical scope;
- every `for` iteration receives a fresh environment;
- top-level functions are hoisted, while nested declarations run in source
  order.

This design favors correctness and readability over speed. Chas 0.2 makes no
performance claim for the VM.

## Calls and evaluation order

The VM maintains an explicit call-frame stack, so Chas recursion does not use
the Python call stack. Operands, callees, and arguments evaluate from left to
right. `&&` and `||` compile to jumps so their right operands short circuit.

Integer division truncates toward zero, and integer remainder is calculated
from that quotient. Output formatting matches the tree evaluator, including
lowercase booleans and a visible decimal for whole-valued floats.

## Debugger interface

`VirtualMachine` (also exported as `BytecodeVM`) supplies the small public seam
used by Chas Studio:

- `load(program)` resets the machine;
- `step()` executes one instruction;
- `run(instruction_limit=...)` continues until halt;
- `current_instruction` and `current_location` identify the next operation;
- `stack_snapshot` and `scope_snapshot` return read-only debugger views.

The instruction budget stops runaway programs with a normal `RuntimeError`
carrying the next instruction's source location.

## Testing

The bytecode tests compare VM output with the tree evaluator across arithmetic,
short-circuiting, branches, loops, recursion, mutual recursion, closures,
shadowing, built-ins, and all bundled examples. They also cover disassembly,
deep explicit-frame recursion, stepping, snapshots, output injection, runtime
diagnostics, and instruction-budget exhaustion.
