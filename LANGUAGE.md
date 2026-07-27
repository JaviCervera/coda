# Coda language extensions (draft 0.1)

Coda is a source-to-source language that adds a deliberately small object model to
the C dialect accepted by its target compiler. Its primary target is constrained
systems, including z88dk targets.

`codac` consumes `.cod` files and emits ordinary C source and headers. The target
C compiler remains responsible for compiling, assembling, and linking the program.

## Design rules

- Ordinary target-supported C, target pragmas, inline assembly, and C headers are
  available in Coda.
- Coda adds only the constructs described in this document.
- Coda does not provide exceptions, garbage collection, implicit heap allocation,
  implicit destruction, or hidden ownership transfer.
- Generated code must use conservative, target-friendly C.
- All Coda-specific keywords are contextual: outside the syntax that introduces a
  Coda construct, they remain valid C identifiers where possible.

## Source files and modules

Coda source files use the `.cod` extension. A Coda module may include C headers
and import other Coda modules:

```coda
#include <stdint.h>
#include "legacy.h"

#import "point.cod"
```

`#include` is passed to generated C. `#import` is consumed by Coda: it imports the
public declarations of a Coda module once and becomes an include of that module's
generated header.

In 0.1, `#import` must occur at top level and outside conditional-preprocessor
branches. Coda resolves cyclic imports. A cycle is valid when C itself can express
the involved declarations, for example through pointers and forward declarations;
cycles containing structs by value remain invalid.

```coda
// a.cod
#import "b.cod"
struct A { struct B *b; };

// b.cod
#import "a.cod"
struct B { struct A *a; };
```

## Structs

A Coda object begins as an ordinary C struct. A struct with no Coda
`impl` remains an ordinary C struct.

```coda
struct Point {
    int x;
    int y;
};
```

Fields are public. Coda 0.1 has no access modifiers, properties, or hidden heap
allocation.

### Type names

Every Coda-owned struct name is automatically a type name, as if declared
with `typedef`. The struct tag name is usable without the `struct` keyword
anywhere in Coda source, including field declarations, pointer declarators,
array declarators, function parameters, and local variable declarations.

```coda
struct Point { int x; int y; };
struct Vec2 { int x; int y; };

struct Rect {
    Point origin;        // 'struct' omitted — valid
    Vec2 size;           // pointer and array declarators also valid
};

impl Rect {
    void scale(double factor) {
        Rect *ptr;       // pointer declarator
        Point corners[4];// array declarator
    }
}
```

The `struct` keyword form (`struct Point origin;`) is also accepted for
compatibility.

Generated C output includes a `typedef` for each Coda-owned struct, making
the bare tag name available in C code that includes the generated header:

```c
/* generated */
struct Point { int x; int y; };
typedef struct Point Point;
```

A declaration that starts with a known Coda struct name but uses a complex
declarator (pointer, array, or function-pointer form) has no scope-cleanup
injection for that variable. The simple forms `Type var;` and
`Type var.init(...)` remain the only ways to obtain automatic cleanup.

## Implementations and instance methods

An `impl` block attaches methods to a struct.

```coda
struct Point {
    int x;
    int y;
};

impl Point {
    void move(int dx, int dy) {
        self->x += dx;
        self->y += dy;
    }

    int distance_squared(void) {
        return self->x * self->x + self->y * self->y;
    }
}
```

Within an instance method, `self` is an implicit `struct Point *` parameter. Coda
generates ordinary C functions, conceptually:

```c
void Point_move(struct Point *self, int dx, int dy);
int Point_distance_squared(struct Point *self);
```

Methods are public and method names may not be overloaded. A Coda object type may
not contain a field and a method with the same name.

### Method-call syntax

Coda recognizes the following forms when the receiver's static type has the named
method:

```coda
struct Point p;
p.move(2, -1);

struct Point *ptr = &p;
ptr->move(1, 0);
```

They lower to normal C calls with the receiver supplied as `self`:

```c
Point_move(&p, 2, -1);
Point_move(ptr, 1, 0);
```

Normal C field access remains unchanged:

```coda
p.x = 10;
ptr->y = 20;
```

The explicit generated C-style call is also valid Coda:

```coda
Point_move(&p, 2, -1);
```

## Construction and destruction

`init` and `deinit` are special method names.

```coda
impl Point {
    init(int x, int y) {
        self->x = x;
        self->y = y;
    }

    deinit(void) {
        /* release resources owned by this Point */
    }
}
```

They generate `Point_init()` and `Point_deinit()` functions.

### Explicit lifetime model (default)

In the default model, Coda never allocates storage or invokes either method
implicitly:

```coda
struct Point p;
p.init(10, 20);

/* use p */

p.deinit();
```

The same model applies to static, embedded, and dynamically allocated objects.
For dynamically allocated objects, the program chooses and releases storage:

```coda
struct Point *p = my_pool_alloc(sizeof(struct Point));
if (p != 0) {
    p->init(10, 20);
    p->deinit();
    my_pool_free(p);
}
```

### Automatic scope cleanup (init-declaration syntax)

When a struct has a `deinit` method, you may declare a stack-local variable
using init-declaration syntax. Coda automatically calls `deinit` when the
variable goes out of scope.

```coda
struct String { char *data; };
impl String {
    init(const char *str) { self->data = str; }
    deinit(void) { /* release data */ }
}

    impl Example {
        void run(void) {
            String s.init("hello");     // init-declaration: calls String_init(&s, "hello")
            /* use s */
        }                              // auto: String_deinit(&s)
    }
    ```

A variable declared with init-declaration syntax must be of a type that has a
`deinit` method. Calling `deinit` explicitly on such a variable is rejected
(E070) to prevent double-free.

```coda
void run(void) {
    String s.init("hello");
    s.deinit();                    // ERROR (E070)
}
```

If a type has `deinit` but no `init`, the variable is zero-initialized
(`= {0}`) instead of calling init:

```coda
impl String {
    deinit(void) { }
}

void run(void) {
    String s;                      // zero-init: String s = {0}
}                                  // auto: String_deinit(&s)
```

Auto-cleanup applies only to stack-local variables. Static, embedded (field),
and dynamically allocated objects remain explicit.

**Embedded fields:** A container struct's `deinit` must explicitly call
`deinit` on each embedded field that requires cleanup. Coda does not
recursively walk struct fields to inject cleanup — the programmer always
retains control over field lifetime.

```coda
struct Buffer { struct String data; };
impl Buffer {
    deinit(void) {
        self->data.deinit();   // explicit — Coda will not inject this
    }
}
```

### Return-transfer

When returning a local variable of a type with `deinit`, the compiler
suppresses the automatic `deinit` call for that variable, transferring
ownership to the caller:

```coda
struct String make(void) {
    String s.init("hello");
    return s;                      // s is not deinit'd — transfer to caller
}
```

The caller receives a live object and is responsible for calling `deinit`.

### Discarded return values

A call to a function or method that returns a type with `deinit` must capture
the return value. Discarding it is rejected (E071):

```coda
void run(void) {
    make();                        // ERROR (E071): discarded return value
    struct String s = make();      // OK — captured (but not init-declared)
}
```

### Non-deinit types

Variables of types without `deinit` are unaffected — no zero-init, no
automatic cleanup, and no diagnostic on discard.

Default arguments are not supported in 0.1. Use an explicit helper or factory.

## Single inheritance

Inheritance is declared on the struct. Coda injects a `struct BaseName base;`
field as the first field of the derived struct.

```coda
struct Entity {
    int id;
};

impl Entity {
    init(int id) {
        self->id = id;
    }
}

struct Sprite : Entity {
    int x;
    int y;
};

impl Sprite {
    init(int id, int x, int y) {
        self->base.init(id);
        self->x = x;
        self->y = y;
    }
}
```

`struct Sprite : Entity` declares that `Sprite` derives from `Entity`.
Coda inserts `struct Entity base;` as the first field. There is no multiple
inheritance, mixin system, or implicit base-constructor call. The inherited
`base` field name may be used explicitly in Coda code as shown above.

## Virtual methods

Mark an instance method `virtual` to introduce a new virtual dispatch slot.
Mark an instance method `override` to override an inherited virtual slot.

```coda
struct Entity {
    int id;
};

impl Entity {
    virtual void update(void) {
        /* default behavior */
    }
}

struct Sprite : Entity {
    int x;
};

impl Sprite {
    override void update(void) {
        self->x++;
    }
}
```

The first virtual method in a Coda-owned hierarchy causes Coda to add a hidden
vtable pointer to the generated base layout. Derived objects inherit that pointer
through their first base field; they do not contain a second vtable pointer.

```coda
void tick(struct Entity *entity) {
    entity->update();       // dispatches to Sprite::update for a Sprite
}
```

Rules:

- A method that introduces a new virtual dispatch slot uses `virtual`.
- A method that overrides an inherited virtual slot uses `override` and must
  match the base signature exactly.
- A derived type may add new virtual methods; its generated vtable extends the
  base vtable.
- A virtual method may be declared only on a Coda-owned struct hierarchy.
- Pure virtual methods and abstract types are not part of 0.1.

## Templates

Coda templates are compile-time type specialization. They are intended for typed
containers and value types, not template metaprogramming.

```coda
template <T>
struct Array {
    T *data;
    unsigned count;
    unsigned capacity;
};

template <T>
impl Array<T> {
    init(T *storage, unsigned capacity) {
        self->data = storage;
        self->count = 0;
        self->capacity = capacity;
    }

    bool push(T value) {
        if (self->count == self->capacity) {
            return false;
        }
        self->data[self->count++] = value;
        return true;
    }

    T *at(unsigned index) {
        if (index >= self->count) {
            return 0;
        }
        return &self->data[index];
    }
}
```

Use a specialization as a type:

```coda
Array<uint8_t> bytes;
uint8_t storage[64];

bytes.init(storage, 64);
bytes.push(42);
```

Coda emits a specialized C struct and functions, such as `Array_u8` and
`Array_u8_push`. Every used specialization is compiled independently; there is no
runtime generic type information.

Multiple type parameters are supported for types such as `Result<T, E>`:

```coda
template <T, E>
struct Result {
    bool ok;
    union {
        T value;
        E error;
    } data;
};
```

0.1 does not include template specialization, template deduction, non-type
parameters, template metaprogramming, or generic free functions.

## Operator methods

Coda supports a fixed list of overloadable operators. An operator is declared as
a method inside an implementation and lowers to a generated C function. It does
not introduce custom precedence or implicit conversion rules.

```coda
struct Fix16 {
    int32_t raw;
};

impl Fix16 {
    struct Fix16 operator+(struct Fix16 rhs) {
        struct Fix16 result;
        result.raw = self->raw + rhs.raw;
        return result;
    }

    struct Fix16 operator*(struct Fix16 rhs) {
        struct Fix16 result;
        result.raw = fix16_mul_raw(self->raw, rhs.raw);
        return result;
    }

    bool operator<(struct Fix16 rhs) {
        return self->raw < rhs.raw;
    }

    struct Fix16 *operator+=(struct Fix16 rhs) {
        self->raw += rhs.raw;
        return self;
    }
}
```

```coda
struct Fix16 x = Fix16_from_int(2);
struct Fix16 y = Fix16_from_int(3);
struct Fix16 z = x * y + x;

if (z >= y) {
    z += Fix16_from_int(1);
}
```

The permitted operator methods are:

```text
operator+   operator-   operator*   operator/
operator==  operator!=  operator<   operator<=  operator>  operator>=
operator+=  operator-=  operator*=  operator/=
operator[]
operator->
```

`operator+` and `operator-` are unary with no explicit parameter and binary with
one explicit parameter. `operator*` is unary with no explicit parameter for
pointer-like dereference and binary with one explicit parameter for multiplication.

Pointer-like wrappers use `operator->` and unary `operator*`:

```coda
template <T>
struct OwnedPtr {
    T *ptr;
};

template <T>
impl OwnedPtr<T> {
    T *operator->(void) {
        return self->ptr;
    }

    T *operator*(void) {
        return self->ptr;
    }
}
```

```coda
OwnedPtr<Sprite> sprite;
sprite->update();
(*sprite).move(1, 0);
```

The following are intentionally not overloadable in 0.1:

```text
operator=, ++, --, %, bitwise operators, shifts, !, &&, ||, comma, and casts
```

In particular, assignment and copying are never overloaded. Ownership-sensitive
types must expose explicit operations such as `clone_from`, `move_from`, or
`release`.

There are no implicit conversions between Coda object types. A fixed-point value
and an `int`, for example, require an explicit conversion function:

```coda
x + Fix16_from_int(1);
```

## Foreign C structs

Coda may add non-virtual methods to a tagged struct defined by a C header:

```coda
#include "legacy_file.h"

impl struct LegacyFile {
    int remaining(void) {
        return self->size - self->position;
    }
}
```

This only emits a helper function and does not alter the foreign layout. Declaring
virtual methods or inheritance for a foreign struct is an error, because Coda
cannot add its required object metadata. Use a Coda-owned wrapper when virtual
behavior is needed.

## Reserved future syntax

`interface` is reserved contextually for a future structural-interface feature.
It has no semantics in 0.1 and an `interface Name { ... }` declaration is rejected
with a diagnostic explaining that interfaces are not implemented yet.

## Ownership and lifetime — known gaps

Coda 0.1 provides automatic scope cleanup for stack-local variables of types
with `deinit`, and return-transfer to move ownership out of a function. The
following gaps are known and are intended to be addressed in future versions.

### 1. Recursive field cleanup

A container's `deinit` must manually call `deinit` on each embedded field
that needs it. Coda does not auto-generate or verify this.

```coda
struct Buffer { struct String data; };
impl Buffer {
    deinit(void) {
        // self->data.deinit();  // forgotten — leak or double-free
    }
}
```

0.1 compiles this without a diagnostic. A future version should either
auto-generate the field deinit calls or require them and reject omissions.

### 2. No copy control

Copying a value whose type has `deinit` produces a bitwise copy, and both
copies receive a `deinit` call at scope exit — a double-free.

```coda
void run(void) {
    String a.init("hello");
    String b = a;   // bitwise copy
}                   // String_deinit(&b), then String_deinit(&a) — double-free
```

0.1 compiles this without a diagnostic. A future version should provide
copy-constructor declarations, deleted copies (`@nopcopy`), or explicit
clone/move operations.

### 3. General move expressions

`return var` transfers ownership correctly, but there is no general-purpose
move expression for non-return contexts.

```coda
void consume(String s);

void run(void) {
    String s.init("hello");
    consume(s);       // double-free: s deinit'd at scope exit,
}                     // and consume deinit's its parameter
```

0.1 compiles this without a diagnostic. A future version should provide
an explicit move expression (`s.move()` or similar) that zeroes the source
and suppresses its deinit.

### 4. No standard ownership types

Templates, operator overloading, and init/deinit make it possible to build
owning-pointer types yourself, but 0.1 ships no standard library with such
helpers.

```coda
template <T>
struct OwnedPtr { T *ptr; };

template <T>
impl OwnedPtr<T> {
    init(T *p) { self->ptr = p; }
    deinit(void) {
        if (self->ptr) {
            self->ptr->deinit();
            free(self->ptr);
        }
    }
    T *operator->(void) { return self->ptr; }
    T *operator*(void) { return self->ptr; }
}
```

Usage integrates with scope cleanup:

```coda
void run(void) {
    OwnedPtr<String> p.init(malloc(sizeof(String)));
    p->init("hello");
}   // auto: OwnedPtr<String>_deinit(&p) → String_deinit + free
```

The remaining limitation is that bitwise copy (gap 2) applies to any such
wrapper, and there is no move expression (gap 3) to transfer ownership out
of one. A future version should ship a standard header with `Owned<T>`,
`Arc<T>`, or similar.

### 5. Array cleanup

An array of a type with `deinit` does not trigger cleanup for its elements.

```coda
void run(void) {
    String arr[2];    // two zero-initialized String values
    arr[0].init("a");
    arr[1].init("b");
}                     // no deinit called — leak
```

0.1 compiles this without a diagnostic. A future version should call
`deinit` on each element when the array goes out of scope, or reject
arrays of deinit types outright until the feature is ready.

### 6. Exception safety and panics

Coda deliberately has no exceptions or stack unwinding. However, any
future unwind mechanism (longjmp / panic / `setjmp`-based errors) must
interact correctly with scope cleanup, or variables with `deinit` will
leak on the unwinding path.

0.1 does not address this. Any future unwind feature must be designed
together with the cleanup infrastructure.

## Explicit non-features in 0.1

```text
Exceptions and stack unwinding
Garbage collection
Implicit allocation or heap ownership transfer
Interfaces and duck typing
Pure virtual methods and abstract types
Multiple inheritance
Access control and properties
Method overloading
Default arguments
Operator overloading outside the fixed list above
Template specialization, deduction, non-type parameters, and generic free functions
```

Errors should normally be represented with explicit return values, including
specialized `Result<T, E>` types where appropriate.
