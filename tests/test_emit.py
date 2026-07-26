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
        implementation Point {
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
        implementation Point {
            void move(int dx, int dy) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("coda_Point_move_impl", c)
        self.assertIn("Point_move", c)

    def test_init_generated(self):
        source = """
        struct Point { int x; int y; };
        implementation Point {
            init(int x, int y) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertIn("Point_init", h)

    def test_virtual_dispatcher(self):
        source = """
        struct Entity { int id; };
        implementation Entity {
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
        implementation Point {
            void move(int dx, int dy) { }
        }
        """
        h, c = self.emit_source(source)
        self.assertNotIn("implementation", h)
        self.assertNotIn("implementation", c)
        self.assertNotIn("#import", h)
        self.assertNotIn("#import", c)

    def test_include_guard(self):
        h, c = self.emit_source("", "my_module")
        self.assertIn("CODA_MY_MODULE_COD", h)

    def test_deterministic_output(self):
        source = """
        struct Point { int x; int y; };
        implementation Point {
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
