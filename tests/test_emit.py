import os
import tempfile
import unittest

from codac.emit import Emitter
from codac.lower import Lowerer
from codac.parser import Parser
from codac.typesys import SemanticAnalyzer


class TestEmit(unittest.TestCase):
    def emit_source(self, source: str, module_name: str = "test") -> tuple[str, str]:
        parser = Parser(f"{module_name}.cod", source)
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
        parser = Parser(f"{module_name}.cod", source)
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
        self.assertIn("coda_Point_move_impl", c)
        self.assertIn("Point_move", c)

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
        self.assertIn("CODA_MY_MODULE_COD", h)

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
                String s("hello");
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
                String s("hello");
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
                String s("hello");
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
                String a("first");
                String b("second");
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
                    String inner("inner");
                }
                String outer("outer");
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


if __name__ == "__main__":
    unittest.main()