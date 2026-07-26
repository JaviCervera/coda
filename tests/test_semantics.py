import unittest

from codac.parser import Parser
from codac.typesys import SemanticAnalyzer


class TestSemantics(unittest.TestCase):
    def analyze(self, source: str):
        parser = Parser("test.cod", source)
        module = parser.parse()
        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)
        return analyzer, module

    def test_simple_struct_registered(self):
        analyzer, _ = self.analyze("struct Point { int x; int y; };")
        st = analyzer.get_struct("Point")
        self.assertIsNotNone(st)
        self.assertTrue(st.is_coda_owned)

    def test_implementation_registered(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Point")
        self.assertIsNotNone(info)
        self.assertIn("move", info.methods)

    def test_init_method(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            init(int x, int y) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Point")
        self.assertTrue(info.methods["init"].is_init)

    def test_deinit_method(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            deinit(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Point")
        self.assertTrue(info.methods["deinit"].is_deinit)

    def test_virtual_method_registered(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Entity")
        self.assertTrue(info.methods["update"].is_virtual)

    def test_inheritance_chain(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            void tick(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            void render(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Sprite")
        self.assertIsNotNone(info)
        self.assertEqual(info.base_name, "Entity")

    def test_operator_detected(self):
        source = """
        struct Fix16 { int32_t raw; };
        impl Fix16 {
            struct Fix16 operator+(struct Fix16 rhs) { return *self; }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Fix16")
        self.assertTrue(info.methods["operator+"].is_operator)


if __name__ == "__main__":
    unittest.main()
