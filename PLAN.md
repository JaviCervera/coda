# Coda compiler implementation plan

## Overview

`codac` is a source-to-source compiler that consumes `.cod` files and emits
ordinary C source and headers. The target C compiler remains responsible for
compiling, assembling, and linking.

- **Language**: Python 3.10+ (stdlib only)
- **Host C testing**: `cc -std=c89 -Wall -Wextra -Werror`
- **z88dk**: added later

## Directory layout

```
codac/
  __init__.py
  __main__.py        CLI entry point
  diagnostics.py     Error codes, Span, SourceMap, formatted messages
  lexer.py           Lossless character-based C/Coda lexer
  parser.py          Structural C/Coda parser
  ast.py             AST node definitions (token-backed)
  modules.py         #import graph, module loading, cycle detection
  typesys.py         Type representations, semantic model, method resolution
  specialize.py      Template instantiation collection and substitution
  lower.py           Expression lowering, method→C call, virtual dispatch, operators
  emit.py            Deterministic .h/.c emission with #line directives
  names.py           C identifier mangling, include guards
tests/
  __init__.py
  fixtures/          Per-feature fixture directories
  test_lexer.py
  test_parser.py
  test_semantics.py
  test_emit.py
  test_runtime.py
```

## Compilation pipeline

```
read source
  → lossless lexing
  → import scan and module graph
  → structural parse
  → semantic collection and type resolution
  → template-instantiation discovery
  → object-model layout and virtual-slot calculation
  → expression lowering
  → deterministic C header/source emission
```

## Phases

### Phase 0: Project scaffolding
Create directory layout, `__main__.py` entry point, test stubs.

### Phase 1: Diagnostics & Lexer
- `diagnostics.py`: Span, SourceMap, Diagnostic with stable error codes (E001–E060)
- `lexer.py`: character-based, lossless, all C punctuators (longest-match), preprocessor lines, `#import` recognition, rejection of conditional import

### Phase 2: AST & Parser
- `ast.py`: all AST node types — Module, StructDecl, Implementation, Method, TemplateDecl, expressions, statements, TokenPreserved
- `parser.py`: typedef scope stack, standard C declarations, Coda top-level forms, Pratt expression parser with method-call syntax, balanced-token method bodies

### Phase 3: Module system
- `modules.py`: ModuleLoader, ModuleGraph, import resolution, cycle detection, dependency emission

### Phase 4: Semantic analysis & type system
- `typesys.py`: Type hierarchy, StructType, Implementation, Method, VirtualSlot, canonical type equality, method/field validation, inheritance checks, ownership (Coda-owned vs foreign)
- `names.py`: deterministic C name mangling, include guards

### Phase 5: Template specialization
- `specialize.py`: collect concrete template uses, recursive instantiation, canonical instance map, type-parameter substitution, validation

### Phase 6: Lowering
- `lower.py`: non-virtual layout, virtual layout (vptr injection, vtable types), method-call lowering, operator lowering, pointer-like `operator->` chaining, compound assignment, init vtable assignment

### Phase 7: C emission
- `emit.py`: per-module .h/.c output, deterministic ordering, #line directives, no Coda tokens in output, atomic temp-dir emission

### Phase 8: Integration & runtime tests
- Golden-file comparisons
- Compile generated C with host C89 compiler
- Run compiled executables for behavior verification

## Key design decisions

1. **Match/case** for lexer dispatch (Python 3.10+)
2. **Typedef scope stack** for correct C parsing (lexer hack)
3. **Token-preserved C regions** — pure C chunks stored verbatim
4. **Whole-program compilation** over #import closure
5. **Canonical type equality** — structural identity, not name strings
6. **Atomic temp-directory emission** — no partial output on failure
7. **Stable error codes** (E001–E060) — every diagnostic has a unique code

## Test strategy

- Unit tests per compiler component (lexer, parser, semantics, emit)
- Golden-file tests comparing expected .h/.c output
- Host C compiler compile-check on generated C
- Runtime behavior tests via compiled executables
