#include "shapes.h"
#include <stdio.h>

int main(void) {
    /* Point with init, move */
    Point p;
    Point_init(&p, 3, 4);
    Point_move(&p, 1, -1);
    printf("Point: (%d, %d)\n", p.x, p.y);

    /* Virtual dispatch through base pointer */
    Rectangle r;
    Rectangle_init(&r, 5.0, 3.0);
    Shape *s = (Shape *)&r;
    printf("Rectangle area: %g\n", Shape_area(s));

    /* Operator overloading */
    Vec2 a, b;
    Vec2_init(&a, 10, 20);
    Vec2_init(&b, 1, 2);
    Vec2 c = Vec2_operator_add(&a, b);
    printf("Vec2: (%d, %d)\n", c.x, c.y);

    return 0;
}
