# Coda compiler implementation specification (draft 0.1)

This document specifies an implementation plan for `codac`, the Coda-to-C
compiler. It is intended to be actionable by an implementation agent. The source
language is defined by [LANGUAGE.md](LANGUAGE.md); this document defines compiler
architecture, lowering, diagnostics, and tests.

## 1. Scope and non-goals

`codac` compiles a root `.cod` file and its `#import` closure into generated C
headers and implementation files. z88dk (or another configured C compiler)
compiles the generated C.

The first implementation must support every Coda 0.1 feature in `LANGUAGE.md`:

- `#import` modules;
- structs, implementations, methods, `init`, and `deinit`;
- `.` and `->` method calls;
- single inheritance and virtual methods;
- type-only templates with one or more type parameters;
- the closed operator-overload set;
- non-virtual extension methods on foreign tagged C structs.

It must not add semantics that Coda deliberately excludes, particularly automatic
destruction, implicit allocation, interfaces, exceptions, overload resolution, or
implicit conversions.

The compiler must generate conservative C. C89-compatible output is preferred;
target headers may of course require target-specific extensions.

## 2. Recommended implementation shape

Implement the first compiler in Python 3 using only the standard library. This
makes the parser and test suite easy to iterate on and avoids an initial external
toolchain dependency. Keep components independent of Python-specific details so a
future Rust, Go, or C implementation can follow the same model.

Suggested layout:

```text
codac/
  __main__.py             command-line entry point
  diagnostics.py          spans, source maps, stable diagnostics
  lexer.py                lossless C/Coda lexer
  parser.py               structural Coda and C parser
  ast.py                  token spans and Coda AST nodes
  modules.py              import graph and module loading
  typesys.py              semantic model and type resolution
  specialize.py           template-instantiation collection
  lower.py                Coda-object-model lowering and body dispatch
  expr.py                 Coda-expression lowering, scope analysis, cleanup injection
  emit.py                 deterministic .h/.c emission
  names.py                C identifier mangling and include guards
tests/
  fixtures/
  test_lexer.py
  test_parser.py
  test_semantics.py
  test_emit.py
  test_runtime.py
```

Use `unittest` for the mandatory test suite. Optional `pytest` support may be
added, but no test should require it.

## 3. Command-line interface

The initial CLI is intentionally small:

```text
codac ROOT.cod --out-dir DIR [-I DIR]... [--emit-deps FILE] [--keep-going]
```

Required behavior:

- `ROOT.cod` is the entry module.
- `--out-dir` receives one `.h` and one `.c` for every imported Coda module.
- `-I` adds directories searched by `#import` after the importing module's
  directory.
- `--emit-deps FILE` writes make-style dependencies.
- diagnostics use `path:line:column: error: message`.
- a failed compilation returns a non-zero status and writes no partial final
  output. Emit to a temporary directory, then atomically replace completed files.

Example:

```text
codac game.cod --out-dir build/coda -I lib
zcc +zx -Ibuild/coda build/coda/game.c build/coda/point.c -o game
```

## 4. Compilation pipeline

```text
read source
  -> lossless lexing
  -> import scan and module graph
  -> structural parse
  -> semantic collection and type resolution
  -> template-instantiation discovery
  -> object-model layout and virtual-slot calculation
  -> expression lowering and scope-guard injection
  -> deterministic C header/source emission
```

The lowering stage (`codac/expr.py`) handles both Coda-construct lowering
(method calls, operator calls) and scope-based cleanup injection. After
parsing a method body, the pipeline is:

```text
parse tokens into preliminary Stmt list
  -> build variable-info map (var name -> type for deinit types)
  -> lower each Stmt (method/operator calls, diagnostics)
  -> analyze scope (collect variables needing cleanup)
  -> inject cleanup (deinit calls before returns and at block exits)
  -> emit each Stmt to C text
```

Compilation is whole-program over the root module's `#import` closure. This is
important: it allows Coda to discover all template instantiations and resolve
cyclic module imports before emitting output.

## 5. Lossless lexer

Do not use regular expressions to parse Coda. Implement a character-based,
lossless lexer.

Each token records:

```text
kind, spelling, source path, byte offset, line, column, leading trivia
```

Trivia includes whitespace and comments so unchanged C can be re-emitted with
useful source correspondence.

The lexer must recognize at least:

- identifiers and C keywords;
- integer, character, floating, and string literals;
- all C punctuators, preferring longest match (`->`, `+=`, `<<=`, `...`, etc.);
- line and block comments;
- escaped newlines;
- preprocessor logical lines, including backslash continuations.

Preprocessor directives are represented as opaque directive tokens except for a
strictly recognized import form:

```text
#import "relative-or-search-path.cod"
```

`#import` inside a macro continuation or a `#if`/`#ifdef`/`#ifndef` branch is a
diagnostic in 0.1. Coda constructs inside macro replacement lists are also a
diagnostic. Ordinary preprocessor text is preserved without evaluation.

The lexer must never interpret Coda words in strings, comments, character
literals, or opaque preprocessor text.

## 6. Parsing strategy

### 6.1 General principle

Parsing must be real, but it does not need to type-check every C extension. Parse
standard C declarations, statements, and expressions sufficiently to find types
and rewrite Coda expressions. Preserve the source-token spelling of C constructs
that do not need rewriting.

The parser must support standard C declarators, including pointers, arrays,
function pointers, typedef names, `struct`/`union`/`enum`, qualifiers, and
function definitions. Maintain a typedef-name scope stack, because C grammar is
ambiguous without it.

z88dk-specific declarations or pragmas that do not contain Coda syntax may be
stored as opaque token regions and emitted unchanged. If a region contains a Coda
method call or overloaded operator but cannot be structurally parsed, emit a
diagnostic asking the user to rewrite that particular expression in ordinary C.

### 6.2 Top-level additions

At top level, recognize these Coda forms:

```text
#import "path.cod"

template <Identifier (, Identifier)*>
    struct-definition [: BaseName]

template <Identifier (, Identifier)*>
    impl Name <TypeArg (, TypeArg)*> impl-body

impl [struct] Name impl-body

struct Identifier [: BaseName] { field* }
```

A struct with `: BaseName` declares single inheritance. Coda injects
`struct BaseName base;` as the first field during semantic registration.

`struct-definition` uses normal C struct syntax. For a Coda-owned struct, retain
the field declarations as a token-backed AST so generated layout can inject a
vtable pointer when required.

An `impl` body contains only method definitions. Its grammar is:

```text
impl-body  := '{' method* '}'
method               := ['virtual'] method-head compound-statement
method-head          := type identifier '(' parameter-list ')'
                      | 'init' '(' parameter-list ')'
                      | 'deinit' '(' 'void' ')'
                      | type 'operator' operator-token '(' parameter-list ')'
```

Use balanced-token parsing for compound statements after building an expression
AST for each expression inside them. A method may contain ordinary C statements,
including declarations, loops, switch statements, labels, and nested blocks.

### 6.3 Expressions

Implement a Pratt parser, or equivalent precedence parser, for C expressions.
It must preserve standard C precedence and associativity while adding:

```text
postfix: receiver . identifier ( arguments )
postfix: receiver -> identifier ( arguments )
```

These are parsed as ordinary member access first. Semantic resolution turns them
into method calls only when the receiver type has the named Coda method. Plain
field access stays plain C.

The expression parser must cover operator precedence through comma expressions,
casts, `sizeof`, conditional expressions, calls, indexing, member access, unary
operators, and assignment expressions. It should emit an AST only for expressions
that contain Coda syntax; token-preserved emission is sufficient for all others.

## 7. Semantic model

Create explicit semantic objects for the following:

```text
Module
StructType                  tag, fields, owner module, foreign/Coda-owned state
TemplateType                parameter names, struct AST, implementation AST
TemplateInstance            template + concrete type arguments
Implementation
Method                      name, result type, parameter types, flags, body
VirtualSlot                 declaring type, signature, slot number
```

Types must include at least:

```text
Builtin, NamedStruct, TemplateInstance, Pointer, Array, Function, Union, Enum
```

Use canonical type equality, not textual equality. Preserve original spelling for
emission and diagnostics where possible.

### 7.1 Ownership and foreign types

A Coda-owned struct is defined in a `.cod` module. A struct seen only through a
C `#include` is foreign.

- Coda-owned structs may have implementations, inheritance, and virtual methods.
- Foreign tagged structs may have a non-virtual implementation.
- A foreign struct with `virtual` or `: Base` is an error.
- A template struct is Coda-owned after specialization.

### 7.2 Method validation

Validate all of the following before emission:

- exactly one implementation per concrete Coda-owned type;
- no duplicate method names or overloads;
- no method name collides with a field name;
- `init` has no declared result type;
- `deinit` has exactly `(void)` and no result type;
- method parameter names are unique;
- no default arguments;
- an inheritance base exists, is Coda-owned, and is not final (there is no `final`
  keyword in 0.1, so this check is structurally reserved);
- the base field (`struct BaseName base;`) is injected as the first field during
  `_register_struct`; an explicit field named `base` in a struct that declares
  `: BaseName` is rejected (`E020`);
- overridden virtual methods repeat `virtual` and have an exact signature match;
- a non-virtual method may not replace an inherited virtual method;
- no field/method/virtual name conflict;
- no cyclic inheritance.

For inherited ordinary methods, resolve `derived.method()` to the declaring base
method with the appropriate address of the embedded base field.

### 7.3 Method-call validation

For `receiver.method(args)` and `receiver->method(args)`:

- resolve the static receiver type;
- require `.` receivers to be addressable lvalues;
- require `->` receivers to be pointers, or to have a valid `operator->` chain;
- resolve a visible method in the receiver type or its base classes;
- require the exact argument count and types (except normal C conversions for
  primitive C function parameters);
- reject an unresolved name rather than guessing between a field and method.

No Coda object conversion is implicit. Upcasting to a base occurs only when a
method is inherited or a parameter explicitly has a base-pointer type.

## 8. Template specialization

Templates support type parameters only. During semantic analysis, collect every
concrete use such as:

```coda
Array<uint8_t> bytes;
Result<struct Sprite *, ErrorCode> result;
```

Specialization discovery is recursive: specializing `Array<T>` can expose further
template types in fields or method signatures. Store instances in a canonical map
keyed by `(template definition identity, canonical argument types)`, so every
specialization is emitted once.

Substitution occurs in the struct fields, implementation signatures, method
bodies, operators, and inherited type references. Reject:

- wrong number of template arguments;
- use of an unspecialized template as an object type;
- recursive by-value layouts;
- template specializations and non-type arguments, which are not 0.1 features.

Use deterministic C names. One acceptable scheme is:

```text
Array<uint8_t>                  -> coda_Array__u8
Result<struct Sprite *, Error>  -> coda_Result__Sprite_ptr__Error
```

Escape every non-identifier character and append a short stable hash if two
distinct canonical types would otherwise mangle to the same identifier.

## 9. Object-layout lowering

### 9.1 Non-virtual structs

For a Coda-owned struct with no virtual methods in its inheritance chain, emit
the declared fields unchanged.

```coda
struct Point { int x; int y; };
```

```c
struct Point { int x; int y; };
```

### 9.2 Virtual root and derived layouts

If a type introduces a virtual method, it is a virtual root. Insert exactly one
hidden pointer as the first generated field of that root:

```c
const void *__coda_vptr;
```

Use `const void *`, rather than a pointer to one particular vtable type, because
a derived type may extend the base vtable. Dispatch helpers cast it to the vtable
type appropriate to the method's statically known declaring class.

Example generated layout:

```c
struct Entity_vtable {
    void (*update)(struct Entity *);
};

struct Entity {
    const void *__coda_vptr;
    int id;
};

struct Sprite_vtable {
    struct Entity_vtable base;
    /* Sprite-only virtual slots follow, if any. */
};

struct Sprite {
    struct Entity base;
    int x;
};
```

No derived struct receives another vtable pointer.

### 9.3 Virtual functions and thunks

For each virtual method, generate:

1. a public dispatcher named `Type_method`;
2. a concrete implementation named `coda_Type_method_impl`;
3. a base-signature thunk when an override's `self` type is derived.

For example:

```c
static void coda_Sprite_update_as_Entity(struct Entity *base) {
    coda_Sprite_update_impl((struct Sprite *)base);
}

void Entity_update(struct Entity *self) {
    const struct Entity_vtable *vt =
        (const struct Entity_vtable *)self->__coda_vptr;
    vt->update(self);
}
```

An `init` for every concrete virtual type assigns the correct static vtable. If a
user-defined derived initializer exists, emit vtable assignment before the body;
if it calls a base initializer, the derived assignment must be restored afterward.
This guarantees the final dynamic type wins.

Virtual dispatch through a base pointer uses the base dispatcher. A derived-only
virtual method uses the derived dispatcher and therefore requires a statically
known derived receiver.

## 10. Expression and operator lowering

All lowering must preserve C evaluation order constraints and evaluate each source
operand once.

### 10.1 Ordinary methods

```coda
p.move(dx, dy);
ptr->move(dx, dy);
```

becomes:

```c
Point_move(&p, dx, dy);
Point_move(ptr, dx, dy);
```

For a base method called through a derived value, lower the receiver to the first
base field (for example, `&sprite.base`). For a virtual method, call the relevant
generated dispatcher.

### 10.2 Operators

An operator method lowers to a uniquely named C function. For example:

```coda
z = x * y + x;
```

becomes conceptually:

```c
z = Fix16_operator_add(
        Fix16_operator_mul(&x, y), x);
```

The exact calling convention is:

- the left operand is passed as a pointer when it is the method receiver;
- the right operand is passed according to the declared parameter type;
- unary `operator*` and `operator->` return pointers;
- a compound assignment method returns a pointer to its receiver, and the
  expression result is the dereferenced returned pointer.

For example:

```coda
x += y;
```

becomes:

```c
*Fix16_operator_add_assign(&x, y);
```

The compiler must reject non-addressable left operands for method calls, unary
operator methods, and compound operator methods.

Supported overload resolution is intentionally simple: the left operand's exact
Coda type chooses the operator; the operator's declared parameter types must
match. Do not implement C++-style candidate search, implicit object conversion,
or commutative reverse lookup.

### 10.3 Pointer-like operators

For a receiver that lacks an ordinary `->member`, look for `operator->`. Its
return type must be a pointer. Reapply member or method resolution to that result.

```coda
sprite->update();
```

for `OwnedPtr<Sprite>` becomes conceptually:

```c
Sprite_update(OwnedPtr_Sprite_operator_arrow(&sprite));
```

Unary `operator*` must return a pointer. `*sprite` has the pointed-to type and is
an lvalue, allowing forms such as `(*sprite).move(1, 0)`.

### 10.4 Auto-deinit and scope cleanup

When a struct has a `deinit` method, Coda may inject automatic cleanup for
stack-local variables declared with init-declaration syntax
(`Type var(args)` or `Type var;`).

**Init-declaration parsing** (`_parse_coda_declaration`):

1. The parser peeks ahead at identifiers in statement position. If the first
   identifier is a known Coda struct name and the second is a new identifier,
   the statement is parsed as a Coda declaration rather than an expression.
2. If followed by `(args)`, the declaration records an init call; the argument
   tokens are preserved for the generated `Type_init(&var, ...)` call.
3. If no init call is present, the variable is zero-initialized.

**Variable-info map** (`_build_var_info`):

Before lowering, walk the parsed statements and collect every variable
declared with a type that has `deinit`. The map `{var_name: type_name}` is
passed to expression lowering so that `.deinit()` calls on auto variables
can be rejected (E070).

**Scope analysis** (`_analyze_scope`):

Walk the statement tree and collect `(var_name, type_name)` pairs for every
declared variable whose type has `deinit`. Pairs are grouped by the owning
block statement (keyed by `id(block)`), including root-level vars.

**Cleanup injection** (`_inject_cleanup`):

After lowering, insert `Type_deinit(&var)` calls:

- Before each `return` statement: deinit every live variable in reverse
  declaration order, except the variable being returned (return-transfer).
- At the end of each block (including the function body): deinit every
  variable declared in that block, in reverse order, unless the block's
  last statement is a return of that variable.

Return-transfer suppresses deinit for the exact variable returned via
`return var;`. Complex return expressions (`return f(...)`) still deinit
all live variables.

**Diagnostic checking:**

- **E070**: A `.deinit()` call whose receiver is a known auto variable name
  (from the variable-info map) is rejected. This prevents double-free.
- **E071**: An expression statement whose lowered call carries a result type
  that has `deinit` is rejected. The caller must capture the return value.
- **E072**: (reserved for future) A `goto` that crosses a variable
  declaration with `deinit`.

### 10.5 Disallowed overloads

Hard-code the permitted list from `LANGUAGE.md`. Any other `operator` declaration
is a diagnostic. In particular, never overload `=`, `&&`, `||`, casts, comma,
increments, or decrements.

## 11. Header and source emission

For every `foo.cod`, emit `foo.h` and `foo.c`.

`foo.h` contains, in deterministic order:

1. include guard;
2. translated C includes and imported generated headers;
3. forward declarations needed to break pointer-only cycles;
4. generated vtable types;
5. generated struct definitions;
6. generated public method declarations;
7. template specialization declarations owned by the module.

`foo.c` contains:

1. `#include "foo.h"`;
2. preserved C implementation chunks;
3. generated vtable instances and static thunks;
4. generated method and operator function bodies.

Use `#line` directives before re-emitted user method bodies and preserved C
regions. Diagnostics produced by the target C compiler should therefore refer to
the original `.cod` source as often as possible.

Never emit a Coda-only token (`impl`, `virtual`, template angle-bracket
types, `#import`, or `operator` declarations) into generated C.

## 12. Diagnostics

Diagnostics must have stable error codes for tests and tooling. Suggested codes:

```text
E001 malformed import
E002 import not found
E003 conditional import unsupported
E010 malformed impl
E011 unknown impl target
E012 duplicate impl
E013 invalid init/deinit signature
E014 duplicate or colliding method name
E020 invalid inheritance layout
E021 foreign type cannot inherit or be virtual
E022 inheritance cycle
E023 invalid virtual override
E030 unknown method
E031 invalid method receiver
E032 invalid method argument list
E040 malformed template use
E041 template argument count
E042 recursive value layout
E050 unsupported operator
E051 invalid operator signature
E052 invalid operator operand
E060 Coda syntax in unsupported opaque C extension
E070 explicit deinit() on automatic variable that has scope cleanup
E071 discarded return value of type with deinit
E072 goto across variable with deinit
```

Every diagnostic must point to the relevant Coda token and, where useful, include
a secondary note pointing to the conflicting field, base method, or declaration.

## 13. Test plan

Tests are mandatory. Use a fixture directory where each fixture has source files,
expected generated files, and an optional expected diagnostic file.

```text
tests/fixtures/
  methods/
    point.cod
    expected/point.h
    expected/point.c
  errors/duplicate_method/
    input.cod
    expected.txt
```

Golden-file comparisons should normalize line endings and generated-file banners,
but must otherwise compare exactly. Add a `--debug-dump` mode or internal helper
for AST/semantic snapshots when parser failures need focused tests.

### 13.1 Lexer tests

- identifiers `impl`, `virtual`, `template`, `operator`, and `interface`
  in comments, strings, character literals, and ordinary C identifiers;
- every C punctuator, especially longest-match operators;
- escaped strings and escaped physical newlines;
- block comments spanning lines;
- directives with continuations;
- valid and malformed `#import` directives;
- imports in `#if` branches and macro replacement lists produce `E003`.

### 13.2 Parser tests

- plain C translation units round-trip token-preserved;
- C structs, unions, enums, typedefs, function pointers, and nested declarators;
- one and multiple methods;
- `init`, `deinit`, ordinary methods, virtual methods, and all supported operator
  heads;
- template structs and implementations with one and multiple parameters;
- method bodies containing declarations, loops, switch, labels, casts, nested
  blocks, function calls, and normal field access;
- precedence trees for arithmetic, comparison, assignment, conditional, call,
  indexing, `.` and `->`.

### 13.3 Module tests

- import from the importing directory;
- import from `-I` directory;
- duplicate import is included once;
- direct cycle and longer cycle with pointer declarations;
- missing import (`E002`);
- cyclic by-value struct layouts (`E042`);
- deterministic emitted dependency file.

### 13.4 Methods and lifetime tests

- generated declaration and definition of ordinary methods;
- value receiver `p.move()` lowers to address-of receiver;
- pointer receiver `p->move()` lowers to pointer receiver;
- inherited non-virtual method receives address of embedded base;
- valid `init` and `deinit` emission;
- invalid `init`/`deinit` signatures (`E013`);
- duplicate methods, method overloading attempt, and field/method collision
  (`E014`);
- no implicit allocation or destructor calls are emitted without
  init-declaration syntax.

**Auto-deinit tests:**

- a variable declared with `Type var(args)` on a type with `deinit` emits a
  `Type_init` call followed by `Type_deinit` at scope exit;
- a variable declared with `Type var;` on a type with `deinit` but no `init`
  is zero-initialized (`= {0}`) and deinit'd at scope exit;
- variables without `deinit` are unaffected;
- multiple variables are deinit'd in reverse declaration order;
- nested block variables are deinit'd before outer variables;
- return-transfer: `return var` suppresses deinit for that variable;
- explicit `.deinit()` on an auto variable triggers E070;
- discarded return value of a function returning a deinit type triggers E071;
- generated C for auto-deinit compiles with `-Wall -Wextra -Werror -std=c89`;

### 13.5 Inheritance and virtual-dispatch tests

- derived base field must be first (`E020`);
- unknown base and inheritance cycle diagnostics;
- root virtual layout contains one hidden vptr;
- derived layout contains the base but no second vptr;
- base vtable ordering is stable;
- valid override emits thunk and derived static vtable;
- invalid signature or missing `virtual` on override gives `E023`;
- derived-only virtual slot extends, rather than replaces, base slots;
- base-pointer call dispatches to a derived override at runtime;
- derived initializer installs the derived vtable after any base initializer call.

### 13.6 Template tests

- `Array<uint8_t>` emits a specialized struct and methods;
- the same specialization in multiple modules emits once;
- two different specializations receive different names;
- nested and pointer arguments mangle deterministically;
- `Result<T, E>` verifies multiple parameters;
- wrong number of arguments gives `E041`;
- unspecialized template as value type gives `E040`;
- recursive by-value specializations give `E042`;
- a template method body substitutes all occurrences of its type parameters.

### 13.7 Operator tests

- every permitted operator parses and emits its designated C helper;
- unary versus binary `+`, `-`, and `*` is determined by arity;
- fixed-point expression precedence: `a + b * c`, `(a + b) * c`, comparisons,
  and compound assignment;
- function-call operands appear exactly once in emitted C;
- non-addressable left operands of `+=` are rejected;
- `operator=` and every excluded operator produce `E050`;
- bad return type for `operator->` or unary `operator*` produces `E051`;
- `OwnedPtr<T>->method()` invokes `operator->`, then resolves the target method;
- `(*ptr).method()` invokes unary `operator*` and then method dispatch;
- no implicit `Fix16 + int` conversion is accepted.

### 13.8 Foreign-C tests

- a C header declaring `struct LegacyFile` supports an ordinary extension method;
- the emitted function has `struct LegacyFile *self`;
- virtual method or inheritance on the foreign type gives `E021`;
- a Coda wrapper around a foreign type supports virtual behavior normally.

### 13.9 Generated-C and runtime tests

For representative fixtures, compile generated C with a host C compiler in a
strict mode, such as:

```text
cc -std=c89 -Wall -Wextra -Werror
```

Use small C compatibility headers where C89 lacks `bool` or fixed-width types.
Also provide an optional integration job that compiles a subset with z88dk when
the toolchain is available.

Compile and run host executables for:

- ordinary method mutation;
- base-pointer virtual dispatch;
- derived-only virtual dispatch;
- `Array<uint8_t>` push/at behavior;
- `Result<int, Error>` tag/payload behavior;
- fixed-point operator results;
- smart-pointer `operator->` and `operator*` access;
- explicit `init`/`deinit` counters proving no implicit destructor is inserted.

## 14. Acceptance criteria

The first implementation is complete when:

1. all tests in Section 13 pass;
2. generated C contains no Coda syntax;
3. generated representative fixtures compile with the host C compiler;
4. the z88dk integration subset compiles when z88dk is installed;
5. diagnostics are stable, correctly located, and cover every semantic rule;
6. output is deterministic across two runs on the same source tree.

Do not expand the language while implementing this version. Keep unsupported
constructs as clear diagnostics, then add later features behind updated language
and implementation specifications.
