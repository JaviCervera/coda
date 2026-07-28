import os
import subprocess
import tempfile
import unittest

from codac.emit import Emitter
from codac.lower import Lowerer
from codac.parser import Parser
from codac.typesys import SemanticAnalyzer


class TestRuntime(unittest.TestCase):
    def _compile_and_run(self, cod_source: str, c_helpers: str = "", test_main: str = "") -> str:
        parser = Parser("test.co", cod_source)
        module = parser.parse()

        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)

        lowerer = Lowerer(analyzer)
        lowerer.lower(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = Emitter(tmpdir)
            emitter.emit(module, lowerer, analyzer)

            with open(os.path.join(tmpdir, "test.h")) as f:
                header = f.read()
            with open(os.path.join(tmpdir, "test.c")) as f:
                source = f.read()

            wrapper = f"""
            {c_helpers}
            int main(void) {{
                {test_main}
                return 0;
            }}
            """

            test_c_path = os.path.join(tmpdir, "test_runner.c")
            with open(test_c_path, "w") as f:
                f.write('#include "test.h"\n')
                f.write(source)
                f.write(wrapper)

            try:
                result = subprocess.run(
                    ["cc", "-std=c89", "-Wall", "-Wextra", "-Werror", "-o",
                     os.path.join(tmpdir, "test_runner"), test_c_path],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    self.fail(f"Compilation failed:\n{result.stderr}\n{result.stdout}")

                run_result = subprocess.run(
                    [os.path.join(tmpdir, "test_runner")],
                    capture_output=True, text=True, timeout=10,
                )
                return run_result.stdout
            except FileNotFoundError:
                self.skipTest("C compiler not found")

    def _compile(self, cod_source: str) -> str:
        """Just compile-check without running."""
        return self._compile_and_run(cod_source, "", "/* no-op */")

    def test_method_call_compiles(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) {
                self->x += dx;
                self->y += dy;
            }
        }
        """
        self._compile(source)

    def test_init_compiles(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) {
                self->x = x;
                self->y = y;
            }
        }
        """
        self._compile(source)

    def test_deinit_compiles(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            deinit(void) { }
        }
        """
        self._compile(source)

    def test_deinit_empty_parens_compiles(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            deinit() { }
        }
        """
        self._compile(source)

    def test_inheritance_compiles(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            void tick(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            void update(void) { }
        }
        """
        self._compile(source)

    def test_auto_deinit_compiles(self):
        source = """
        struct String { const char *data; };
        impl String {
            init(const char *str) { self->data = str; }
            deinit(void) { }
        }
        struct Test { int dummy; };
        impl Test {
            void run(void) {
                String s.init("hello");
            }
        }
        """
        self._compile(source)

    def test_auto_deinit_counter(self):
        source = """
        extern void inc_count(void);
        extern void dec_count(void);
        struct String { const char *data; };
        impl String {
            init(const char *str) { inc_count(); self->data = str; }
            deinit(void) { dec_count(); }
        }
        struct Test { int dummy; };
        impl Test {
            void run(void) {
                String s.init("hello");
            }
        }
        """
        c_helpers = """
        static int cleanup_counter = 0;
        void inc_count(void) { cleanup_counter++; }
        void dec_count(void) { cleanup_counter++; }
        int get_count(void) { return cleanup_counter; }
        """
        test_main = """
        struct Test test;
        Test_run(&test);
        if (get_count() != 0) return 1;
        """
        self._compile_and_run(source, c_helpers, test_main)

    def test_virtual_compiles(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) {
                self->id = 1;
            }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void update(void) {
                self->x = 2;
            }
        }
        """
        self._compile_and_run(source, "", R"""
        struct Entity e;
        e.id = 0;
        struct Sprite s;
        s.base.id = 0;
        s.x = 0;
        (void)e;
        (void)s;
        """)


    def test_bare_struct_compiles(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(void) { self->x = 0; self->y = 0; }
        }
        struct Shape {
            Point origin;
        };
        impl Shape {
            void reset(void) {
                self->origin.x = 0;
                self->origin.y = 0;
            }
        }
        """
        self._compile(source)


if __name__ == "__main__":
    unittest.main()
