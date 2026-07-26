import unittest

from codac.parser import Parser


class TestParser(unittest.TestCase):
    def parse(self, source: str):
        parser = Parser("test.cod", source)
        return parser.parse()

    def test_empty_module(self):
        module = self.parse("")
        self.assertEqual(len(module.top_level), 0)

    def test_include(self):
        module = self.parse('#include <stdio.h>\n')
        self.assertGreaterEqual(len(module.top_level), 1)
        self.assertEqual(module.top_level[0].kind, "include")

    def test_import(self):
        module = self.parse('#import "point.cod"\n')
        self.assertEqual(len(module.imports), 1)
        self.assertEqual(module.imports[0].path, "point.cod")

    def test_simple_struct(self):
        module = self.parse("struct Point { int x; int y; };")
        self.assertGreaterEqual(len(module.top_level), 1)
        decl = module.top_level[0]
        self.assertEqual(decl.kind, "struct")
        self.assertIsNotNone(decl.body)
        sd = decl.body
        self.assertEqual(sd.name_token.spelling, "Point")

    def test_empty_struct(self):
        module = self.parse("struct Empty {};")
        self.assertEqual(len(module.top_level), 1)
        decl = module.top_level[0]
        self.assertEqual(decl.kind, "struct")

    def test_implementation(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) {
                self->x += dx;
                self->y += dy;
            }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        self.assertGreaterEqual(len(impls), 1)

    def test_implementation_with_init(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) {
                self->x = x;
                self->y = y;
            }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        self.assertGreaterEqual(len(impls), 1)
        impl = impls[0].body
        self.assertIsNotNone(impl)
        self.assertEqual(len(impl.methods), 1)
        self.assertTrue(impl.methods[0].is_init)

    def test_implementation_with_deinit(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            deinit(void) { }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        self.assertGreaterEqual(len(impls), 1)
        impl = impls[0].body
        self.assertTrue(impl.methods[0].is_deinit)

    def test_virtual_method(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        impl = impls[0].body
        self.assertTrue(impl.methods[0].is_virtual)

    def test_inheritance(self):
        source = """
        struct Sprite : Entity { int x; };
        impl Sprite {
            init(int id, int x, int y) {
                self->base.init(id);
                self->x = x;
            }
        }
        """
        module = self.parse(source)
        structs = [d for d in module.top_level if d.kind == "struct"]
        self.assertGreaterEqual(len(structs), 1)
        sd = structs[0].body
        self.assertIsNotNone(sd.base_name_token)
        self.assertEqual(sd.base_name_token.spelling, "Entity")

    def test_template_struct(self):
        source = """
        template <T>
        struct Array {
            T *data;
            unsigned count;
        };
        """
        module = self.parse(source)
        temps = [d for d in module.top_level if d.kind == "template"]
        self.assertGreaterEqual(len(temps), 1)
        td = temps[0].body
        self.assertEqual(len(td.params), 1)
        self.assertEqual(td.params[0].spelling, "T")

    def test_multi_template(self):
        source = """
        template <T, E>
        struct Result {
            bool ok;
            union { T value; E error; } data;
        };
        """
        module = self.parse(source)
        temps = [d for d in module.top_level if d.kind == "template"]
        self.assertGreaterEqual(len(temps), 1)
        td = temps[0].body
        self.assertEqual(len(td.params), 2)
        self.assertEqual(td.params[0].spelling, "T")
        self.assertEqual(td.params[1].spelling, "E")

    def test_operator_method(self):
        source = """
        struct Fix16 { int32_t raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) {
                struct Fix16 result;
                result.raw = self->raw + rhs.raw;
                return result;
            }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        self.assertGreaterEqual(len(impls), 1)
        impl = impls[0].body
        self.assertTrue(impl.methods[0].is_operator)

    def test_foreign_implementation(self):
        source = """
        impl struct LegacyFile {
            int remaining(void) {
                return self->size - self->position;
            }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        self.assertGreaterEqual(len(impls), 1)
        impl = impls[0].body
        self.assertTrue(impl.is_foreign_struct)

    def test_typedef_preserved(self):
        source = "typedef int myint;\n"
        module = self.parse(source)
        self.assertGreaterEqual(len(module.top_level), 1)

    def test_union_preserved(self):
        source = "union Data { int i; float f; };\n"
        module = self.parse(source)
        self.assertGreaterEqual(len(module.top_level), 1)

    def test_enum_preserved(self):
        source = "enum Color { RED, GREEN, BLUE };\n"
        module = self.parse(source)
        self.assertGreaterEqual(len(module.top_level), 1)

    def test_multiple_methods(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
            int dist(void) { return 0; }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        impl = impls[0].body
        self.assertEqual(len(impl.methods), 2)

    def test_method_with_body(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void reset(void) {
                self->x = 0;
                self->y = 0;
            }
        }
        """
        module = self.parse(source)
        impls = [d for d in module.top_level if d.kind == "impl"]
        impl = impls[0].body
        self.assertGreater(len(impl.methods[0].body_tokens), 0)

    def test_forward_struct(self):
        source = "struct Point;"
        module = self.parse(source)
        self.assertGreaterEqual(len(module.top_level), 1)


if __name__ == "__main__":
    unittest.main()
