# Chas Language Specification

Chas is a small statically typed language with Python-ish syntax that
runs on a tree walking interpreter. This document is the reference for
the grammar and semantics. If something isn't written here, assume it's
either not supported or subject to change.

Version: **0.1.0**
File extension: **`.chs`**

---

## 1. Lexical Structure

### 1.1 Source encoding

Chas source files are UTF-8 text. Line endings may be `\n` or `\r\n`.

### 1.2 Whitespace and comments

Whitespace (spaces, tabs, newlines) is insignificant except as a token
separator. Chas supports two comment forms:

```
// this is a single-line comment
/* this is a
   multi-line comment */
```

Multi-line comments do not nest.

### 1.3 Keywords (reserved)

```
let       fn        return    if        else
while     for       in        true      false
int       float     string    bool      void
```

### 1.4 Identifiers

```
identifier ::= letter ( letter | digit )*
letter     ::= "a".."z" | "A".."Z" | "_"
digit      ::= "0".."9"
```

Identifiers are case-sensitive and must not be a keyword.

### 1.5 Literals

```
int_literal    ::= digit+
float_literal  ::= digit+ "." digit+
string_literal ::= '"' string_char* '"'
string_char    ::= any-unicode-char-except-quote-or-backslash
                 | "\\" ( '"' | "\\" | "n" | "t" | "r" | "0" )
bool_literal   ::= "true" | "false"
```

### 1.6 Operators and delimiters

```
+  -  *  /  %        // arithmetic
==  !=  <  <=  >  >= // comparison
&&  ||  !            // logical
=                    // assignment
->                   // return type arrow
:                    // type annotation
,                    // separator
(  )  {  }  [  ]     // grouping
..                   // range operator
```

---

## 2. Grammar (BNF)

The following grammar uses standard BNF with these conventions:

- `|`     alternation
- `( )`   grouping
- `*`     zero-or-more
- `+`     one-or-more
- `?`     optional
- `"..."` literal terminal

```
program        ::= statement*

statement      ::= let_decl
                 | fn_decl
                 | return_stmt
                 | if_stmt
                 | while_stmt
                 | for_stmt
                 | expr_stmt
                 | assign_stmt
                 | block

block          ::= "{" statement* "}"

let_decl       ::= "let" identifier ( ":" type )? "=" expression

fn_decl        ::= "fn" identifier "(" param_list? ")" ( "->" type )? block
param_list     ::= param ( "," param )*
param          ::= identifier ":" type

return_stmt    ::= "return" expression?

if_stmt        ::= "if" expression block ( "else" ( if_stmt | block ) )?

while_stmt     ::= "while" expression block

for_stmt       ::= "for" identifier "in" expression block

assign_stmt    ::= identifier "=" expression

expr_stmt      ::= expression

// Expressions, precedence from lowest to highest
expression     ::= logical_or
logical_or     ::= logical_and ( "||" logical_and )*
logical_and    ::= equality    ( "&&" equality )*
equality       ::= comparison  ( ( "==" | "!=" ) comparison )*
comparison     ::= range       ( ( "<" | "<=" | ">" | ">=" ) range )*
range          ::= additive    ( ".." additive )?
additive       ::= multiplicative ( ( "+" | "-" ) multiplicative )*
multiplicative ::= unary       ( ( "*" | "/" | "%" ) unary )*
unary          ::= ( "!" | "-" ) unary
                 | call
call           ::= primary ( "(" arg_list? ")" )*
arg_list       ::= expression ( "," expression )*
primary        ::= int_literal
                 | float_literal
                 | string_literal
                 | bool_literal
                 | identifier
                 | "(" expression ")"

type           ::= "int" | "float" | "string" | "bool" | "void"
```

---

## 3. Type System

Chas is statically typed. It infers types locally when it can.

### 3.1 Primitive types

| Type     | Values                                   |
|----------|------------------------------------------|
| `int`    | 64-bit signed integers                   |
| `float`  | IEEE-754 double precision                |
| `string` | Unicode strings                          |
| `bool`   | `true` or `false`                        |
| `void`   | Function return marker (no value)        |

### 3.2 Inference

`let x = expr` infers the type of `x` from `expr`. Explicit annotations
override inference and are checked for compatibility.

### 3.3 Operator typing

- `+ - * / %` want both operands to be `int` or both `float`, and give
  back the same type. `+` also works on two strings and concatenates
  them. Integer division truncates toward zero, and integer `%` returns
  the matching remainder.
- `== !=` want matching primitive operands and give back `bool`.
- `< <= > >=` want numeric operands and give back `bool`.
- `&& || !` want `bool` operands and give back `bool`.
- `..` wants two `int` operands and gives back a range.

### 3.4 Functions

Parameter and return types have to be written out. If you leave off the
`->` clause, the return type is `void`, and a void function is not
allowed to return a value. A non void function has to return a value on
every path.

### 3.5 Closures

A nested `fn` captures variables from the scope it was defined in, by
reference. At the runtime level a function is a first class value with
type `fn(T1, T2, ...) -> T`.

---

## 4. Scoping

Scoping is lexical. Every `block` opens a new scope. `let` declarations
and function parameters live in the innermost enclosing block. You can
shadow a name in an inner scope, but you can't redeclare it in the same
scope.

Every variable has to be declared with `let` before it's used.

---

## 5. Control Flow

### 5.1 `if`

```
if condition {
    ...
} else if other {
    ...
} else {
    ...
}
```

The condition has to be `bool`. The `else` branch is optional.

### 5.2 `while`

```
while condition {
    ...
}
```

### 5.3 `for-in` over ranges

```
for i in 0..10 {
    print(i)
}
```

The expression after `in` has to be a range (`a..b`). The loop variable
takes values from `a` up to but not including `b`.

---

## 6. Standard Library

These are always in scope, no import needed:

| Signature                        | What it does                        |
|----------------------------------|-------------------------------------|
| `print(value: any) -> void`      | print a value and a newline         |
| `len(s: string) -> int`          | length of a string                  |
| `range(a: int, b: int) -> range` | same as `a..b`                      |
| `type(value: any) -> string`     | type name of a value, as a string   |

`any` is an internal thing the compiler uses for built ins that take
mixed input types. You can't declare a variable of type `any` in user
code.

---

## 7. Errors

Every Chas error carries:

- the file name
- line and column
- a human readable message
- a category: `LexerError`, `ParseError`, `TypeError`, `NameError`, or
  `RuntimeError`

The interpreter never shows a Python traceback to the user, even when
the program blows up at runtime.

---

## 8. Sample Program

```
// fibonacci.chs
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
