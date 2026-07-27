from __future__ import annotations

from codac.ast import SourceFile, Token
from codac.diagnostics import Diagnostic, SourceMap, Span


KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "int", "long", "register", "return", "short", "signed", "sizeof",
    "static", "struct", "switch", "typedef", "union", "unsigned", "void",
    "volatile", "while",
})

CODA_KEYWORDS = frozenset({
    "impl", "virtual", "template", "operator", "interface", "override",
})

ALL_KEYWORDS = KEYWORDS | CODA_KEYWORDS


PUNCTUATORS = [
    ("...", "..."),
    ("<<=", "<<="),
    (">>=", ">>="),
    ("<<", "<<"),
    (">>", ">>"),
    ("<=", "<="),
    (">=", ">="),
    ("==", "=="),
    ("!=", "!="),
    ("&&", "&&"),
    ("||", "||"),
    ("++", "++"),
    ("--", "--"),
    ("->", "->"),
    ("+=", "+="),
    ("-=", "-="),
    ("*=", "*="),
    ("/=", "/="),
    ("%=", "%="),
    ("&=", "&="),
    ("|=", "|="),
    ("^=", "^="),
    ("##", "##"),
    ("::", "::"),
    ("..", ".."),
    ("#", "#"),
    ("(", "("),
    (")", ")"),
    ("[", "["),
    ("]", "]"),
    ("{", "{"),
    ("}", "}"),
    (".", "."),
    ("&", "&"),
    ("*", "*"),
    ("+", "+"),
    ("-", "-"),
    ("~", "~"),
    ("!", "!"),
    ("/", "/"),
    ("%", "%"),
    ("<", "<"),
    (">", ">"),
    ("=", "="),
    ("?", "?"),
    (":", ":"),
    (";", ";"),
    (",", ","),
    ("|", "|"),
    ("^", "^"),
    ("\\", "\\"),
]

PUNCTUATOR_MAP = {s: k for k, s in PUNCTUATORS}


class Lexer:
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.source_map = SourceMap(path, source)
        self.pos = 0
        self.tokens: list[Token] = []
        self.diagnostics: list[Diagnostic] = []
        self._in_directive = False

    def lex(self) -> SourceFile:
        while self.pos < len(self.source):
            trivia = self._skip_trivia()
            if self.pos >= len(self.source):
                break

            token = self._lex_token(trivia)
            if token is not None:
                self.tokens.append(token)
            else:
                self.pos += 1

        return SourceFile(path=self.path, source=self.source, tokens=self.tokens)

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.source[idx] if 0 <= idx < len(self.source) else ""

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        return ch

    def _skip_trivia(self) -> str:
        parts = []
        while self.pos < len(self.source):
            ch = self._peek()

            if ch in " \t\n\r":
                parts.append(self._advance())
                continue

            if ch == "/" and self._peek(1) == "/":
                parts.append(self._advance())
                parts.append(self._advance())
                while self.pos < len(self.source) and self._peek() != "\n":
                    parts.append(self._advance())
                continue

            if ch == "/" and self._peek(1) == "*":
                parts.append(self._advance())
                parts.append(self._advance())
                while self.pos < len(self.source):
                    if self._peek() == "*" and self._peek(1) == "/":
                        parts.append(self._advance())
                        parts.append(self._advance())
                        break
                    parts.append(self._advance())
                continue

            if ch == "\\" and self._peek(1) == "\n":
                parts.append(self._advance())
                parts.append(self._advance())
                continue

            break

        return "".join(parts)

    def _lex_token(self, trivia: str) -> Token | None:
        ch = self._peek()
        start_offset = self.pos

        if ch == "'":
            return self._lex_char_literal(trivia, start_offset)

        if ch == '"':
            return self._lex_string_literal(trivia, start_offset)

        if ch == "L" and self._peek(1) == "'":
            return self._lex_char_literal(trivia, start_offset, prefix="L")
        if ch == "L" and self._peek(1) == '"':
            return self._lex_string_literal(trivia, start_offset, prefix="L")

        if ch == "#" and not self._in_directive:
            return self._lex_directive(trivia, start_offset)

        if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
            return self._lex_numeric_literal(trivia, start_offset)

        if ch.isalpha() or ch == "_":
            return self._lex_identifier_or_keyword(trivia, start_offset)

        for punct_str, kind in PUNCTUATORS:
            if self.source[self.pos:self.pos + len(punct_str)] == punct_str:
                self.pos += len(punct_str)
                span = self.source_map.span(start_offset, len(punct_str))
                return Token(kind=kind, spelling=punct_str, span=span, leading_trivia=trivia)

        self._diagnostic(E060, f"unexpected character: {ch!r}", s=self.source_map.span(start_offset))
        return None

    def _lex_char_literal(self, trivia: str, start_offset: int, prefix: str = "") -> Token:
        if prefix:
            self._advance()
        self._advance()
        while self.pos < len(self.source):
            ch = self._advance()
            if ch == "\\":
                if self.pos < len(self.source):
                    self._advance()
            elif ch == "'":
                break
        spelling = self.source[start_offset:self.pos]
        span = self.source_map.span(start_offset, len(spelling))
        return Token(kind="char_literal", spelling=spelling, span=span, leading_trivia=trivia)

    def _lex_string_literal(self, trivia: str, start_offset: int, prefix: str = "") -> Token:
        if prefix:
            self._advance()
        self._advance()
        while self.pos < len(self.source):
            ch = self._advance()
            if ch == "\\":
                if self.pos < len(self.source):
                    self._advance()
            elif ch == '"':
                break
        spelling = self.source[start_offset:self.pos]
        span = self.source_map.span(start_offset, len(spelling))
        return Token(kind="string_literal", spelling=spelling, span=span, leading_trivia=trivia)

    def _lex_numeric_literal(self, trivia: str, start_offset: int) -> Token:
        if self._peek() == "0" and self._peek(1) in "xX":
            self._advance()
            self._advance()
            while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() in ".-+"):
                self._advance()
        else:
            while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() in ".-+"):
                self._advance()
        spelling = self.source[start_offset:self.pos]
        span = self.source_map.span(start_offset, len(spelling))
        return Token(kind="numeric_literal", spelling=spelling, span=span, leading_trivia=trivia)

    def _lex_identifier_or_keyword(self, trivia: str, start_offset: int) -> Token:
        while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        spelling = self.source[start_offset:self.pos]
        kind = "keyword" if spelling in ALL_KEYWORDS else "identifier"
        span = self.source_map.span(start_offset, len(spelling))
        return Token(kind=kind, spelling=spelling, span=span, leading_trivia=trivia)

    def _lex_directive(self, trivia: str, start_offset: int) -> Token:
        self._in_directive = True
        ch = self._advance()
        if ch != "#":
            self._in_directive = False
            span = self.source_map.span(start_offset)
            return Token(kind="#", spelling="#", span=span, leading_trivia=trivia)

        self._skip_identifier()

        while self.pos < len(self.source):
            ch = self._peek()
            if ch == "\\" and self._peek(1) == "\n":
                self._advance()
                self._advance()
            elif ch == "\n":
                break
            elif ch == "/" and self._peek(1) == "*":
                self._advance()
                self._advance()
                while self.pos < len(self.source):
                    if self._peek() == "*" and self._peek(1) == "/":
                        self._advance()
                        self._advance()
                        break
                    self._advance()
            elif ch == "/" and self._peek(1) == "/":
                while self.pos < len(self.source) and self._peek() != "\n":
                    self._advance()
            else:
                self._advance()

        self._in_directive = False
        spelling = self.source[start_offset:self.pos]
        span = self.source_map.span(start_offset, len(spelling))
        return Token(kind="directive", spelling=spelling, span=span, leading_trivia=trivia)

    def _skip_identifier(self) -> str:
        start = self.pos
        while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        return self.source[start:self.pos]

    def _diagnostic(self, code: str, msg: str, s: Span):
        self.diagnostics.append(Diagnostic(code=code, message=msg, span=s))

    def lex_file(self) -> SourceFile:
        return self.lex()
