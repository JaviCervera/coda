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
        parser = Parser("test.cod", cod_source)
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
        implementation Point {
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
        implementation Point {
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
        implementation Point {
            deinit(void) { }
        }
        """
        self._compile(source)

    def test_inheritance_compiles(self):
        source = """
        struct Entity { int id; };
        implementation Entity {
            void tick(void) { }
        }
        struct Sprite { struct Entity base; int x; };
        implementation Sprite : Entity {
            void update(void) { }
        }
        """
        self._compile(source)

    def test_virtual_compiles(self):
        source = """
        struct Entity { int id; };
        implementation Entity {
            virtual void update(void) {
                self->id = 1;
            }
        }
        struct Sprite { struct Entity base; int x; };
        implementation Sprite : Entity {
            virtual void update(void) {
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
        """)


if __name__ == "__main__":
    unittest.main()
