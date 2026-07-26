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

They generate `Point_init()` and `Point_deinit()` functions. Coda never allocates
storage or invokes either method implicitly:

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

An implementation may also provide ordinary factory functions. Coda may generate
a by-value `Type_make(...)` helper from `init`, but this is convenience syntax,
not allocation or RAII.

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

Mark an instance method `virtual` to introduce or override a virtual dispatch
slot.

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
    virtual void update(void) {
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

- A virtual override repeats `virtual` and must match the base signature exactly.
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

## Explicit non-features in 0.1

```text
Exceptions and stack unwinding
Garbage collection
Implicit allocation, destruction, copy, or ownership transfer
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
