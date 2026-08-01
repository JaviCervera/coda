import os
import tempfile
import unittest

from codac.emit import Emitter
from codac.lower import Lowerer
from codac.modules import ModuleLoader
from codac.parser import Parser
from codac.specialize import Specializer
from codac.typesys import SemanticAnalyzer


class TestEmit(unittest.TestCase):
    def emit_source(self, source: str, module_name: str = "test") -> tuple[str, str]:
        parser = Parser(f"{module_name}.co", source)
        module = parser.parse()

        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)

        lowerer = Lowerer(analyzer)
        lowerer.lower(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = Emitter(tmpdir)
            emitter.emit(module, lowerer, analyzer)
            h_path = os.path.join(tmpdir, f"{module_name}.h")
            c_path = os.path.join(tmpdir, f"{module_name}.c")
            h_content = ""
            c_content = ""
            if os.path.exists(h_path):
                with open(h_path) as f:
                    h_content = f.read()
            if os.path.exists(c_path):
                with open(c_path) as f:
                    c_content = f.read()
            return h_content, c_content

    def _emit_with_diagnostics(self, source: str, module_name: str = "test"):
        parser = Parser(f"{module_name}.co", source)
        module = parser.parse()

        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)

        lowerer = Lowerer(analyzer)
        lowerer.lower(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = Emitter(tmpdir)
            emitter.emit(module, lowerer, analyzer)
            h_path = os.path.join(tmpdir, f"{module_name}.h")
            c_path = os.path.join(tmpdir, f"{module_name}.c")
            h_content = ""
            c_content = ""
            if os.path.exists(h_path):
                with open(h_path) as f:
                    h_content = f.read()
            if os.path.exists(c_path):
                with open(c_path) as f:
                    c_content = f.read()
            return h_content, c_content, analyzer.diagnostics

    def test_empty_module(self):
        h, c = self.emit_source("")
        self.assertIn("ifndef", h)
        self.assertIn("#define", h)

    def test_include_in_header(self):
        h, c = self.emit_source('#include <stdio.h>\n')
        self.assertIn("#include <stdio.h>", h)

    def test_struct_declaration(self):
        h, c = self.emit_source("struct Point { int x; int y; };")
        self.assertIn("struct Point", h)
        self.assertIn("int x", h)
        self.assertIn("int y", h)
        self.assertIn("typedef struct Point Point;", h)

    def test_method_declaration(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) {
                self->x += dx;
                self->y += dy;
            }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("Point_move", h)
        self.assertIn("struct Point *self", h)

    def test_method_implementation(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("void Point_move(struct Point *self, int dx, int dy)", c)
        self.assertNotIn("coda_Point_move_impl", c)

    def test_init_generated(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("Point_init", h)

    def test_virtual_dispatcher(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("Entity_update", h)
        self.assertIn("__coda_vptr", h)
        self.assertNotIn("virtual", h)

    def test_const_virtual_vtable_slot(self):
        source = """
        struct Shape { int dummy; };
        impl Shape {
            virtual double area(void) const { return 0.0; }
        }
        struct Rectangle : Shape { double w; };
        impl Rectangle {
            override double area(void) const { return 1.0; }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("double (*area)(const struct Shape *);", h)
        self.assertIn("double (*area)(const struct Rectangle *);", h)
        self.assertIn("double Shape_area(const struct Shape *self)", h)
        self.assertIn("double Rectangle_area(const struct Rectangle *self)", h)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_no_coda_syntax_in_output(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
        }
        """
        h, c = self.emit_source(source)
        import re
        self.assertFalse(re.search(r'\bimpl\b', h), f"'impl' found in header:\n{h}")
        self.assertFalse(re.search(r'\bimpl\b', c), f"'impl' found in source:\n{c}")
        self.assertNotIn("#import", h)
        self.assertNotIn("#import", c)

    def test_include_guard(self):
        h, c = self.emit_source("", "my_module")
        self.assertIn("CODA_MY_MODULE_CO", h)

    def test_auto_deinit_zero_init(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                String s;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("String s = {0}", c, "zero-init expected for deinit type without init")
        self.assertIn("String_deinit(&s)", c, "deinit should be called at scope exit")
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_auto_deinit_with_init(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                String s.init("hello");
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("String_init(&s, \"hello\")", c)
        self.assertIn("String_deinit(&s)", c)
        self.assertNotIn("= {0}", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_return_transfer_no_deinit(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            struct String make(void) {
                String s.init("hello");
                return s;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertNotIn("String_deinit(&s)", c, "returned variable should NOT be deinit'd")
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_explicit_deinit_rejected(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                String s;
                s.deinit();
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertTrue(any("E070" in d.code for d in diags),
                        "Expected E070 for explicit deinit on auto variable")

    def test_discard_return_value_rejected(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            struct String make(void) {
                String s.init("hello");
                return s;
            }
            void run(void) {
                self->make();
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertTrue(any("E071" in d.code for d in diags),
                        "Expected E071 for discarded return value with deinit")

    def test_no_deinit_for_plain_type(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                int x = 10;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertNotIn("deinit", c.lower())
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_multiple_vars_cleanup(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                String a.init("first");
                String b.init("second");
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("String_deinit(&b)", c)
        self.assertIn("String_deinit(&a)", c)
        b_pos = c.index("String_deinit(&b)")
        a_pos = c.index("String_deinit(&a)")
        self.assertLess(b_pos, a_pos, "deinit should be in reverse declaration order")
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_block_scope_cleanup(self):
        source = """
        struct String { char *data; };
        impl String {
            init(const char *str) { }
            deinit(void) { }
        }
        struct Example { int dummy; };
        impl Example {
            void run(void) {
                {
                    String inner.init("inner");
                }
                String outer.init("outer");
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("String_deinit(&inner)", c)
        self.assertIn("String_deinit(&outer)", c)
        inner_pos = c.index("String_deinit(&inner)")
        outer_pos = c.index("String_deinit(&outer)")
        self.assertLess(inner_pos, outer_pos,
                        "inner block deinit should fire before outer's deinit at function exit")
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_deterministic_output(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
            int get_x(void) { return self->x; }
        }
        """
        h1, c1 = self.emit_source(source, "test1")
        h2, c2 = self.emit_source(source, "test1")
        self.assertEqual(h1, h2)
        self.assertEqual(c1, c2)


    def test_typedef_for_inherited_struct(self):
        h, c = self.emit_source("""
        struct Entity { int id; };
        struct Sprite : Entity { int x; };
        """)
        self.assertIn("typedef struct Entity Entity;", h)
        self.assertIn("typedef struct Sprite Sprite;", h)

    def test_bare_struct_field_decl(self):
        source = """
        struct Point { int x; int y; };
        struct Shape {
            Point origin;
            struct Point pos;
        };
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Point origin;", h)
        self.assertIn("struct Point pos;", h)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_bare_struct_name_in_method_body(self):
        source = """
        struct Point { int x; int y; };
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Point p;
                Point *ptr;
                Point arr[4];
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_pointer_decl_passthrough(self):
        source = """
        struct Point { int x; int y; };
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Point *ptr;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_free_function_init_decl_and_method_call(self):
        source = """
        struct Message { char msg[64]; };
        impl Message {
            init(const char *msg) { }
            void print() { }
        }
        int main() {
            Message msg.init("hello");
            msg.print();
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("int main()", c)
        self.assertIn("struct Message msg;", c)
        self.assertIn("Message_init(&msg, \"hello\")", c)
        self.assertIn("Message_print(&msg)", c)
        self.assertIn("return 0;", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_free_function_plain_c(self):
        source = """
        int add(int a, int b) {
            return a + b;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("int add(int a, int b)", c)
        self.assertIn("return a + b;", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_forward_decl_passthrough(self):
        source = """
        int add(int a, int b);
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("int add(int a, int b);", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_on_local_variable(self):
        source = """
        struct Message { char msg[64]; };
        impl Message {
            init(const char *msg) { }
            void print() { }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Message msg.init("hello");
                msg.print();
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Message_print(&msg)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_array_decl_passthrough(self):
        source = """
        struct Point { int x; int y; };
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Point corners[4];
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)


    def test_multi_module_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shapes_co = os.path.join(tmpdir, "shapes.co")
            main_co = os.path.join(tmpdir, "main.co")
            out_dir = os.path.join(tmpdir, "out")

            with open(shapes_co, "w") as f:
                f.write("""
                    struct Point { int x; int y; };
                    impl Point {
                        void move(int dx, int dy) { }
                    }
                """)
            with open(main_co, "w") as f:
                f.write('#import "shapes.co"\nstruct Holder { Point p; };\n')

            loader = ModuleLoader(include_dirs=[tmpdir])
            all_diags: list = []
            loader.load(main_co, all_diags)
            all_modules = loader.all_modules

            analyzer = SemanticAnalyzer()
            for mod in all_modules:
                analyzer.analyze(mod)

            specializer = Specializer(analyzer)
            for mod in all_modules:
                specializer.collect_templates(mod)

            lowerer = Lowerer(analyzer)
            for mod in all_modules:
                lowerer.lower(mod, finalize=False)
            lowerer.compute_virtual_layouts()

            emitter = Emitter(out_dir)
            for mod in all_modules:
                emitter.emit(mod, lowerer, analyzer)

            shapes_h = os.path.join(out_dir, "shapes.h")
            shapes_c = os.path.join(out_dir, "shapes.c")
            main_h = os.path.join(out_dir, "main.h")
            main_c = os.path.join(out_dir, "main.c")

            self.assertTrue(os.path.exists(shapes_h), "shapes.h should exist")
            self.assertTrue(os.path.exists(shapes_c), "shapes.c should exist")
            self.assertTrue(os.path.exists(main_h), "main.h should exist")
            self.assertTrue(os.path.exists(main_c), "main.c should exist")

            with open(shapes_h) as f:
                shapes_h_content = f.read()
            self.assertIn("CODA_SHAPES_CO", shapes_h_content)
            self.assertIn("struct Point", shapes_h_content)
            self.assertIn("Point_move", shapes_h_content)

            with open(shapes_c) as f:
                shapes_c_content = f.read()
            self.assertIn('#include "shapes.h"', shapes_c_content)
            self.assertIn("Point_move", shapes_c_content)

            with open(main_h) as f:
                main_h_content = f.read()
            self.assertIn('#include "shapes.h"', main_h_content)
            self.assertIn("struct Holder", main_h_content)

            self.assertEqual(len([d for d in all_diags if d.severity == "error"]), 0)

    def test_multi_module_import_diamond(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_co = os.path.join(tmpdir, "lib.co")
            helper_co = os.path.join(tmpdir, "helper.co")
            main_co = os.path.join(tmpdir, "main.co")
            out_dir = os.path.join(tmpdir, "out")

            with open(lib_co, "w") as f:
                f.write("struct Lib { int x; };\n")
            with open(helper_co, "w") as f:
                f.write('#import "lib.co"\nimpl Lib { void help(void) { } }\n')
            with open(main_co, "w") as f:
                f.write('#import "lib.co"\n#import "helper.co"\nstruct Main { Lib l; };\n')

            loader = ModuleLoader(include_dirs=[tmpdir])
            all_diags: list = []
            loader.load(main_co, all_diags)
            all_modules = loader.all_modules

            analyzer = SemanticAnalyzer()
            for mod in all_modules:
                analyzer.analyze(mod)

            specializer = Specializer(analyzer)
            for mod in all_modules:
                specializer.collect_templates(mod)

            lowerer = Lowerer(analyzer)
            for mod in all_modules:
                lowerer.lower(mod, finalize=False)
            lowerer.compute_virtual_layouts()

            emitter = Emitter(out_dir)
            for mod in all_modules:
                emitter.emit(mod, lowerer, analyzer)

            for name in ("lib", "helper", "main"):
                self.assertTrue(os.path.exists(os.path.join(out_dir, f"{name}.h")),
                                f"{name}.h should exist")
                self.assertTrue(os.path.exists(os.path.join(out_dir, f"{name}.c")),
                                f"{name}.c should exist")

            self.assertEqual(len([d for d in all_diags if d.severity == "error"]), 0)


    def test_binary_operator_expr_stmt(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw + rhs.raw;
                return r;
            }
        }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Fix16 a;
                Fix16 b;
                a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Fix16_operator_add(&a, b)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_binary_operator_vardecl(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw + rhs.raw;
                return r;
            }
        }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Fix16 a;
                Fix16 b;
                Fix16 c = a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("struct Fix16 c = Fix16_operator_add(&a, b)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_binary_operator_return(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw + rhs.raw;
                return r;
            }
        }
        struct Example { int dummy; };
        impl Example {
            struct Fix16 test(void) {
                Fix16 a;
                Fix16 b;
                return a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("return Fix16_operator_add(&a, b)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_compound_assign_operator(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 *operator+=(struct Fix16 rhs) {
                self->raw += rhs.raw;
                return self;
            }
        }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Fix16 a;
                Fix16 b;
                a += b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("*Fix16_operator_add_assign(&a, b)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_binary_operator_primitive(self):
        source = """
        struct Example { int dummy; };
        impl Example {
            int test(void) {
                int a = 10;
                int b = 20;
                return a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("a + b", c)
        self.assertNotIn("operator_add", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_binary_operator_not_found(self):
        source = """
        struct Vec2 { int x; int y; };
        impl Vec2 { }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Vec2 a;
                Vec2 b;
                a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertTrue(any("E052" in d.code for d in diags),
                        "Expected E052 for missing operator overload")

    def test_chained_binary_operators(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw + rhs.raw;
                return r;
            }
            struct Fix16 operator*(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw * rhs.raw;
                return r;
            }
        }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Fix16 a;
                Fix16 b;
                Fix16 c;
                a + b * c;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Fix16_operator_add(&a, Fix16_operator_mul(&b, c))", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_no_coda_syntax_in_operator_output(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 r;
                r.raw = self->raw + rhs.raw;
                return r;
            }
        }
        struct Example { int dummy; };
        impl Example {
            void test(void) {
                Fix16 a;
                Fix16 b;
                Fix16 c = a + b;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertNotIn("operator+", c)
        self.assertNotIn("impl", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)


    def test_const_method_declaration(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            int get_x(void) const { return self->x; }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("int Point_get_x(const struct Point *self)", h)
        self.assertIn("int Point_get_x(const struct Point *self)", c)
        self.assertIn("const struct Point *self", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_const_init_declaration(self):
        source = """
        struct Message { char msg[64]; };
        impl Message {
            init(const char *msg) { }
            deinit(void) { }
            void print(void) const { }
        }
        int main() {
            const Message msg.init("hello");
            msg.print();
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("const struct Message msg", c)
        self.assertIn("Message_init((struct Message *)&msg, \"hello\")", c)
        self.assertIn("Message_print(&msg)", c)
        self.assertIn("Message_deinit((struct Message *)&msg)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_const_init_decl_no_deinit(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) { }
            int get_x(void) const { return self->x; }
        }
        int main() {
            const Point p.init(1, 2);
            p.get_x();
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("const struct Point p", c)
        self.assertIn("Point_init((struct Point *)&p, 1, 2)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_const_method_on_nonconst_var(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) { }
            int get_x(void) const { return self->x; }
            void set_x(int x) { self->x = x; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Point p.init(1, 2);
                p.get_x();
                p.set_x(3);
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Point_get_x(&p)", c)
        self.assertIn("Point_set_x(&p, 3)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_const_var_nonconst_method_cast(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) { }
            int get_x(void) { return self->x; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                const Point p.init(1, 2);
                p.get_x();
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Point_get_x((struct Point *)&p)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_inside_if_body(self):
        source = """
        struct Counter { int n; };
        impl Counter {
            init(int n) { self->n = n; }
            void bump(void) { self->n++; }
            int get(void) { return self->n; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Counter c.init(0);
                if (c.get() == 0) {
                    c.bump();
                } else {
                    c.bump();
                }
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("if (Counter_get(&c) == 0)", c)
        self.assertIn("Counter_bump(&c)", c)
        self.assertIn("else {", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_inside_while_body_and_condition(self):
        source = """
        struct Counter { int n; };
        impl Counter {
            init(int n) { self->n = n; }
            void bump(void) { self->n++; }
            int get(void) { return self->n; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Counter c.init(0);
                while (c.get() < 3) {
                    c.bump();
                }
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("while (Counter_get(&c) < 3)", c)
        self.assertIn("Counter_bump(&c)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_inside_for_body(self):
        source = """
        struct Counter { int n; };
        impl Counter {
            init(int n) { self->n = n; }
            void bump(void) { self->n++; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Counter c.init(0);
                int i;
                for (i = 0; i < 3; i++) {
                    c.bump();
                }
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("for (i = 0;i < 3; i++)", c)
        self.assertIn("Counter_bump(&c)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_operator_inside_control_flow(self):
        source = """
        struct Fix16 { int raw; };
        impl Fix16 {
            int *operator+=(int rhs) {
                self->raw += rhs;
                return &self->raw;
            }
        }
        struct Helper { int dummy; };
        impl Helper {
            int run(void) {
                Fix16 a;
                Fix16 b;
                int i;
                int total = 0;
                for (i = 0; i < 2; i++) {
                    a += i;
                }
                if (a.raw > b.raw) total = 1;
                return total;
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Fix16_operator_add_assign(&a, i)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_do_while_lowering(self):
        source = """
        struct Counter { int n; };
        impl Counter {
            init(int n) { self->n = n; }
            void bump(void) { self->n++; }
            int get(void) { return self->n; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Counter c.init(0);
                do {
                    c.bump();
                } while (c.get() < 2);
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("do {", c)
        self.assertIn("Counter_bump(&c)", c)
        self.assertIn("} while (Counter_get(&c) < 2);", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_nested_control_flow_method_calls(self):
        source = """
        struct Counter { int n; };
        impl Counter {
            init(int n) { self->n = n; }
            void bump(void) { self->n++; }
            int get(void) { return self->n; }
        }
        struct Helper { int dummy; };
        impl Helper {
            void run(void) {
                Counter c.init(0);
                int i;
                int j;
                for (i = 0; i < 3; i++) {
                    if (i > 0) {
                        while (c.get() < i) {
                            c.bump();
                        }
                    }
                }
            }
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("Counter_get(&c)", c)
        self.assertIn("Counter_bump(&c)", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_on_coda_param(self):
        source = """
        struct A { int x; };
        impl A {
            init(int x) { self->x = x; }
            int get(void) { return self->x; }
            void bump(void) { self->x++; }
        }
        struct B { int y; };
        impl B {
            init(int y) { self->y = y; }
            int use(struct A *a) {
                if (a->get() == 0) {
                    a->bump();
                }
                return a->get();
            }
        }
        int main(void) {
            B b.init(0);
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("if (A_get(a) == 0)", c)
        self.assertIn("A_bump(a)", c)
        self.assertIn("return A_get(a);", c)
        self.assertNotIn("a->get()", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_method_call_on_foreign_param(self):
        source = """
        struct R { int x; int w; };
        impl struct R {
            int left(void) { return self->x; }
            int right(void) { return self->x + self->w - 1; }
            int contains(int p) { return p >= self->left() && p <= self->right(); }
        }
        struct Ball { int pos; };
        impl Ball {
            init(int pos) { self->pos = pos; }
            int bounce(struct R *field) {
                if (field->contains(self->pos)) {
                    return 0;
                }
                return field->left();
            }
        }
        int main(void) {
            Ball b.init(3);
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("if (R_contains(field, self->pos))", c)
        self.assertIn("return R_left(field);", c)
        self.assertNotIn("field->contains", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_operator_on_struct_params(self):
        source = """
        struct Vec2 { int x; int y; };
        impl Vec2 {
            struct Vec2 *operator+=(struct Vec2 rhs) {
                self->x += rhs.x;
                self->y += rhs.y;
                return self;
            }
        }
        struct Mover { int dummy; };
        impl Mover {
            init(int dummy) { self->dummy = dummy; }
            void step(struct Vec2 *a, struct Vec2 *b) {
                *a += *b;
            }
        }
        int main(void) {
            Mover m.init(0);
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("(void)*Vec2_operator_add_assign(&*a, *b);", c)
        self.assertEqual(len([d for d in diags if d.severity == "error"]), 0)

    def test_deinit_call_on_param_no_e070(self):
        source = """
        struct R { int n; };
        impl R {
            init(int n) { self->n = n; }
            deinit(void) { self->n = 0; }
        }
        struct B { int y; };
        impl B {
            init(int y) { self->y = y; }
            void cleanup(struct R *r) {
                r->deinit();
            }
        }
        int main(void) {
            B b.init(1);
            return 0;
        }
        """
        h, c, diags = self._emit_with_diagnostics(source)
        self.assertIn("R_deinit(r)", c)
        errors = [d for d in diags if d.severity == "error"]
        self.assertEqual([d.code for d in errors], [])


if __name__ == "__main__":
    unittest.main()