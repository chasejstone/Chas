# Changelog

## 0.2.0 - Chas Studio

- Add a dependency-free local browser workbench for editing, checking, and
  running Chas programs.
- Add a stack bytecode compiler, explicit-frame virtual machine, deterministic
  disassembler, instruction stepping, debugger snapshots, and execution
  budgets.
- Keep the tree-walking evaluator as the default reference engine and add
  `--engine vm` as an opt-in execution path.
- Add injectable output sinks to both execution engines.
- Keep arbitrary-precision integer output consistent across Python versions.
- Expand semantic parity, debugger, Studio service, CLI, and security tests.
- Test the project and portable zip application on Windows and Linux.

## 0.1.0

- Add the handwritten lexer, recursive-descent parser, AST, static type checker,
  tree-walking evaluator, closures, CLI, language specification, examples, and
  portable zip application.
