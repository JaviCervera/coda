# Coda compiler agent guide

Before changing the compiler, read `LANGUAGE.md` and `IMPLEMENTATION.md`. They
are the source of truth for Coda 0.1 syntax, semantics, lowering, diagnostics,
and tests.

## Scope

- Implement only features defined in `LANGUAGE.md`.
- Do not add C++-style features by inference: exceptions, RAII, implicit
  allocation, implicit destruction, implicit conversions, overload resolution,
  or interfaces are out of scope unless the language specification is updated
  first.
- Do not change Coda syntax or semantics without updating both specification files
  and adding tests.

## Compatibility

- Preserve ordinary C and target-specific C extensions unless a Coda construct
  requires transformation.
- Generated code must contain no Coda-only syntax.
- Prefer conservative C89-compatible generated C.
- Keep z88dk and constrained/legacy C toolchains in mind: avoid mandatory runtime
  allocation, RTTI, exceptions, or large hidden runtimes.

## Compiler design

- Never parse Coda with regular expressions alone; use the lossless lexer and
  structural parser.
- Preserve source locations and emit useful diagnostics in
  `path:line:column` form.
- Keep generated C deterministic.
- Do not silently reinterpret ambiguous C as Coda. Emit a diagnostic when a Coda
  construct cannot be resolved safely.
- Maintain the explicit object-lifetime model: storage is supplied by the
  programmer; `init` and `deinit` are never invoked implicitly.

## Testing

- Add or update tests for every behavior change.
- Include success, generated-C golden, diagnostic, and runtime tests where
  applicable.
- Run the complete test suite before declaring work complete.
- When available, compile representative generated C with a strict host compiler
  and z88dk.
- Preserve existing tests unless the language specification intentionally changes.

## Generated files

- Do not edit generated C or headers by hand.
- Treat generated names, vtable layouts, and ABI details as implementation
  contracts once covered by tests.
- Keep output suitable for inspection and debugging; use `#line` directives for
  user-originated code where practical.

## Documentation

- Update `LANGUAGE.md` for user-visible syntax or semantic changes.
- Update `IMPLEMENTATION.md` for compiler architecture, lowering, ABI,
  diagnostic, or test-plan changes.
- Record any intentional compatibility limitation explicitly rather than leaving
  it implicit.
