import unittest

from codac.lexer import Lexer


class TestLexer(unittest.TestCase):
    def lex(self, source: str):
        lexer = Lexer("test.cod", source)
        return lexer.lex()

    def test_empty(self):
        sf = self.lex("")
        self.assertEqual(len(sf.tokens), 0)

    def test_identifiers(self):
        sf = self.lex("foo bar _baz")
        self.assertEqual(len(sf.tokens), 3)
        self.assertEqual(sf.tokens[0].spelling, "foo")
        self.assertEqual(sf.tokens[1].spelling, "bar")
        self.assertEqual(sf.tokens[2].spelling, "_baz")

    def test_keywords(self):
        sf = self.lex("struct int return if else while")
        for t in sf.tokens:
            self.assertEqual(t.kind, "keyword")

    def test_coda_keywords(self):
        sf = self.lex("impl virtual template operator interface")
        for t in sf.tokens:
            self.assertEqual(t.kind, "keyword")

    def test_punctuators(self):
        sf = self.lex("-> += == ; { } ( ) [ ]")
        kinds = [t.kind for t in sf.tokens]
        self.assertIn("->", kinds)
        self.assertIn("+=", kinds)
        self.assertIn("==", kinds)
        self.assertIn(";", kinds)
        self.assertIn("{", kinds)
        self.assertIn("}", kinds)

    def test_longest_match(self):
        sf = self.lex("->")
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].kind, "->")

        sf = self.lex("- >")
        self.assertEqual(len(sf.tokens), 2)
        self.assertEqual(sf.tokens[0].kind, "-")
        self.assertEqual(sf.tokens[1].kind, ">")

    def test_numeric_literals(self):
        sf = self.lex("42 0xFF 077 3.14")
        for t in sf.tokens:
            self.assertEqual(t.kind, "numeric_literal")

    def test_string_literal(self):
        sf = self.lex('"hello world"')
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].kind, "string_literal")
        self.assertEqual(sf.tokens[0].spelling, '"hello world"')

    def test_char_literal(self):
        sf = self.lex("'x'")
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].kind, "char_literal")

    def test_comments(self):
        sf = self.lex("// line comment\nint")
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].spelling, "int")
        self.assertIn("// line comment\n", sf.tokens[0].leading_trivia)

        sf = self.lex("/* block */int")
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].spelling, "int")
        self.assertIn("/* block */", sf.tokens[0].leading_trivia)

    def test_escaped_newline(self):
        sf = self.lex("int\\\n x")
        self.assertEqual(len(sf.tokens), 2)

    def test_directive(self):
        sf = self.lex('#include <stdio.h>\n')
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].kind, "directive")

    def test_import_directive(self):
        sf = self.lex('#import "point.cod"\n')
        self.assertEqual(len(sf.tokens), 1)
        self.assertEqual(sf.tokens[0].kind, "directive")
        self.assertIn('"point.cod"', sf.tokens[0].spelling)

    def test_coda_keywords_in_comments(self):
        sf = self.lex("// impl\nint x;")
        self.assertEqual(len(sf.tokens), 3)
        self.assertEqual(sf.tokens[0].spelling, "int")
        self.assertEqual(sf.tokens[1].spelling, "x")
        self.assertEqual(sf.tokens[2].spelling, ";")

    def test_operator_punctuation(self):
        sf = self.lex("operator+ operator- operator* operator/")
        for t in sf.tokens:
            self.assertEqual(t.kind, "keyword" if t.spelling == "operator" else t.kind)

    def test_trivia_preserved(self):
        sf = self.lex("  int  x;")
        self.assertIn("  ", sf.tokens[0].leading_trivia)
        self.assertIn("  ", sf.tokens[1].leading_trivia)

    def test_source_map(self):
        lexer = Lexer("test.cod", "int x;\nint y;")
        sf = lexer.lex()
        self.assertEqual(len(sf.tokens), 6)
        line, col = lexer.source_map.offset_to_line_col(5)
        self.assertEqual(line, 1)
        line, col = lexer.source_map.offset_to_line_col(8)
        self.assertEqual(line, 2)


if __name__ == "__main__":
    unittest.main()
