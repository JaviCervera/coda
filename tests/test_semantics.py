import unittest

from codac.parser import Parser
from codac.typesys import SemanticAnalyzer


class TestSemantics(unittest.TestCase):
    def analyze(self, source: str):
        parser = Parser("test.co", source)
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

    def test_deinit_method_empty_parens(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            deinit() { }
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

    def test_has_deinit_returns_true(self):
        source = """
        struct String { char *data; };
        impl String {
            init(void) { }
            deinit(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        self.assertTrue(analyzer.has_deinit("String"))

    def test_has_deinit_returns_false(self):
        source = """
        struct Point { int x; int y; };
        impl Point {
            void move(int dx, int dy) { }
        }
        """
        analyzer, _ = self.analyze(source)
        self.assertFalse(analyzer.has_deinit("Point"))

    def test_has_deinit_inherited(self):
        source = """
        struct Base { int id; };
        impl Base {
            deinit(void) { }
        }
        struct Derived : Base { int x; };
        impl Derived {
            void stuff(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        self.assertTrue(analyzer.has_deinit("Derived"),
                        "Derived inherits deinit from Base")

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


    def test_override_method_valid(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        info = analyzer.get_implementation("Sprite")
        self.assertIn("update", info.methods)
        self.assertTrue(info.methods["update"].is_override)
        self.assertFalse(info.methods["update"].is_virtual)

    def test_virtual_on_override_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            virtual void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E024", codes)

    def test_missing_override_on_override_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E026", codes)

    def test_override_without_base_virtual_errors(self):
        source = """
        struct Entity { int id; };
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E025", codes)

    def test_override_const_mismatch_drop_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) const { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void update(void) { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E027", codes)

    def test_override_const_mismatch_add_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void update(void) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void update(void) const { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E027", codes)

    def test_override_return_type_mismatch_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual int get_value(void) { return 0; }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override double get_value(void) { return 0.0; }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E027", codes)

    def test_override_param_type_mismatch_errors(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual void resize(int w, int h) { }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override void resize(double w, double h) { }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertIn("E027", codes)

    def test_override_signature_match_valid(self):
        source = """
        struct Entity { int id; };
        impl Entity {
            virtual int get_value(void) const { return 0; }
        }
        struct Sprite : Entity { int x; };
        impl Sprite {
            override int get_value(void) const { return 1; }
        }
        """
        analyzer, _ = self.analyze(source)
        codes = [d.code for d in analyzer.diagnostics]
        self.assertNotIn("E027", codes)
        info = analyzer.get_implementation("Sprite")
        self.assertTrue(info.methods["get_value"].is_override)
        self.assertTrue(info.methods["get_value"].is_const)

    def test_template_registered_not_concrete(self):
        source = """
        struct Ring<T> {
            T *data;
            unsigned count;
        };
        """
        analyzer, _ = self.analyze(source)
        self.assertIsNone(analyzer.get_struct("Ring"),
                          "template must not be registered as a concrete struct")
        self.assertIsNotNone(analyzer.get_template("Ring"))
        self.assertIn("Ring", analyzer.template_names())

    def test_template_impl_registered_as_template(self):
        source = """
        struct Ring<T> {
            T *data;
            unsigned count;
        };
        impl Ring {
            void push(T value) { }
        }
        """
        analyzer, _ = self.analyze(source)
        self.assertIsNone(analyzer.get_implementation("Ring"),
                          "template impl must not be a concrete implementation")
        self.assertIn("Ring", analyzer.template_impls)

    def test_template_registered_via_typedef_not(self):
        analyzer, _ = self.analyze("")
        self.assertEqual(analyzer.template_names(), frozenset())


if __name__ == "__main__":
    unittest.main()
