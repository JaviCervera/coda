# codac — Coda-to-C compiler

Coda 0.1 is a small, explicit object-oriented layer on top of C. It adds
struct methods, single inheritance with virtual dispatch via vtables,
operator overloading, and templates — compiling to C89-compatible C.

Every language feature is described in [`LANGUAGE.md`](LANGUAGE.md).  The
compiler architecture and lowering strategy are documented in
[`IMPLEMENTATION.md`](IMPLEMENTATION.md).

## Features

- **Struct methods** — `impl Point { void move(...) { ... } }`
- **Construction / destruction** — explicit `init` / `deinit`; automatic scope cleanup via `Type var.init(...)` syntax
- **Single inheritance** — `struct Rectangle : Shape { ... };` with
  auto-injected `struct Shape base;` field
- **Virtual dispatch** — `virtual double area(void);` + `override` in derived
- **Operator overloading** — `struct Vec2 operator+(struct Vec2 rhs) { ... }`
- **Templates** — `template<T> struct Array { ... }`
- **Foreign struct extensions** — extra methods on C types via `foreign impl`
- **Imports** — modular compilation across `.co` files
- **Lossless lexer + structural parser** — preserves `#include`, `typedef`,
  `union`, `enum` and trivia for round-tripping

## Usage

```bash
python -m codac <file.co> [--out-dir <dir>]
```

Output: a `.h` / `.c` pair per module.  Compile with any C89+ toolchain:

```bash
cc -std=c89 -Wall -Wextra -Werror -I<out-dir> -o <bin> <out-dir>/<module>.c <your_main.c>
```

## Example — shapes

Save as `shapes.co`:

```coda
struct Point {
    int x;
    int y;
};

impl Point {
    init(int x, int y) {
        self->x = x;
        self->y = y;
    }

    void move(int dx, int dy) {
        self->x += dx;
        self->y += dy;
    }
}

struct Shape {
    struct Point origin;
};

impl Shape {
    init(void) {
        self->origin.init(0, 0);
    }

    virtual double area(void) {
        return 0.0;
    }
}

struct Rectangle : Shape {
    double width;
    double height;
};

impl Rectangle {
    init(double width, double height) {
        self->base.init();
        self->width = width;
        self->height = height;
    }

    override double area(void) {
        return self->width * self->height;
    }
}

struct Vec2 {
    int x;
    int y;
};

impl Vec2 {
    init(int x, int y) {
        self->x = x;
        self->y = y;
    }

    struct Vec2 operator+(struct Vec2 rhs) {
        struct Vec2 result;
        result.x = self->x + rhs.x;
        result.y = self->y + rhs.y;
        return result;
    }
}
```

Compile:

```bash
python -m codac shapes.co --out-dir build/
```

The compiler generates `build/shapes.h` and `build/shapes.c`.  Key parts of
the generated C:

```c
/* ————— build/shapes.h ————— */

struct Point {
    int x;
    int y;
};

struct Shape {
    const void *__coda_vptr;      /* injected vtable pointer */
    struct Point origin;
};

struct Rectangle {
    struct Shape base;
    double width;
    double height;
};

struct Shape_vtable {
    double (*area)(struct Shape *);
};

/* method declarations */
void Point_init(struct Point *self, int x, int y);
void Shape_init(struct Shape *self);
double Shape_area(struct Shape *self);
void Rectangle_init(struct Rectangle *self, double width, double height);
double Rectangle_area(struct Rectangle *self);
struct Vec2 Vec2_operator_add(struct Vec2 *self, struct Vec2 rhs);

/* ————— build/shapes.c (excerpts) ————— */

/* impl forward-declarations, then vtable instances */
const struct Rectangle_vtable coda_Rectangle_vtable = {
    .area = coda_Rectangle_area_impl,
};

const struct Shape_vtable coda_Shape_vtable = {
    .area = coda_Shape_area_impl,
};

/* lowered method call — self->origin.init(0,0) becomes Point_init(&self->origin, 0, 0) */
void coda_Shape_init_impl(struct Shape *self){
    (void)self;
    Point_init(&self->origin, 0, 0);
}

/* virtual dispatcher — reads vptr and dispatches */
double Shape_area(struct Shape *self){
    (void)self;
    const struct Shape_vtable *vt = (const struct Shape_vtable *)self->__coda_vptr;
    return vt->area(self);
}

/* vptr wired in init after base init */
void Rectangle_init(struct Rectangle *self, double width, double height){
    (void)self;
    Shape_init(&self->base);
    self->width = width;
    self->height = height;
    self->base.__coda_vptr = &coda_Rectangle_vtable;
}
```

Write a C driver `shapes_main.c`:

```c
#include "shapes.h"
#include <stdio.h>

int main(void) {
    /* Point with init, move */
    struct Point p;
    Point_init(&p, 3, 4);
    Point_move(&p, 1, -1);
    printf("Point: (%d, %d)\n", p.x, p.y);

    /* Virtual dispatch through base pointer */
    struct Rectangle r;
    Rectangle_init(&r, 5.0, 3.0);
    struct Shape *s = (struct Shape *)&r;
    printf("Rectangle area: %g\n", Shape_area(s));

    /* Operator overloading */
    struct Vec2 a, b;
    Vec2_init(&a, 10, 20);
    Vec2_init(&b, 1, 2);
    struct Vec2 c = Vec2_operator_add(&a, b);
    printf("Vec2: (%d, %d)\n", c.x, c.y);

    return 0;
}
```

Compile and run:

```bash
cc -std=c89 -Wall -Wextra -Werror -Ibuild/ -o shapes_prog build/shapes.c shapes_main.c
./shapes_prog
```

Expected output:

```
Point: (4, 3)
Rectangle area: 15
Vec2: (11, 22)
```

The full example (Coda + driver + Makefile) is in `examples/`.

## Running tests

```bash
python -m pytest tests/ -v
```

Tests cover lexing, parsing, semantic analysis, code emission (golden-file
comparisons), and runtime (compile + run generated C with a host compiler).

## Implementation status

Pipeline phases from [`IMPLEMENTATION.md`](IMPLEMENTATION.md):

| Phase | Status |
|---|---|
| Lossless lexing | ✅ |
| Import scan & module graph | ✅ |
| Structural parse | ✅ |
| Semantic collection & type resolution | ✅ |
| Template-instantiation discovery | ❌ not wired |
| Object-model layout & virtual-slot calculation | ✅ |
| Expression lowering (method calls) | ✅ |
| Operator lowering | ❌ not implemented |
| Deterministic C emission | ✅ |
| Stable error-code diagnostics | ❌ not implemented |
| CLI (`--out-dir`, `-I`, `--emit-deps`) | ✅ |

Acceptance criteria:

| Criterion | Status |
|---|---|
| All tests pass | ⚠️ 59 pass; operator/template/error tests not yet written |
| No Coda syntax in generated C | ✅ |
| Representative fixtures compile with host C89 | ✅ |
| z88dk integration | ❌ not tested |
| Stable diagnostics for every semantic rule | ❌ |
| Deterministic output | ✅ |
