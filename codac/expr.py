from __future__ import annotations

from typing import Optional

from codac.ast import Expr, Stmt, Token
from codac.names import method_c_name
from codac.typesys import SemanticAnalyzer


DECL_KEYWORDS = frozenset({
    "struct", "union", "enum", "int", "double", "float", "char",
    "void", "unsigned", "signed", "long", "short", "const",
    "volatile", "extern", "static", "typedef",
})

PREC = {
    "=": 1, "+=": 1, "-=": 1, "*=": 1, "/=": 1, "%=": 1,
    "||": 2,
    "&&": 3,
    "|": 4,
    "^": 5,
    "&": 6,
    "==": 7, "!=": 7,
    "<": 8, ">": 8, "<=": 8, ">=": 8,
    "<<": 9, ">>": 9,
    "+": 10, "-": 10,
    "*": 11, "/": 11, "%": 11,
}

UNARY_OPS = frozenset({"&", "*", "+", "-", "!", "~", "++", "--"})


class Cursor:
    __slots__ = ("tokens", "pos")
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    @property
    def done(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if not self.done else None

    def peek_spelling(self) -> str | None:
        t = self.peek()
        return t.spelling if t else None

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def match(self, *spellings: str) -> bool:
        s = self.peek_spelling()
        if s in spellings:
            self.pos += 1
            return True
        return False


def _parse_expr(c: Cursor, min_prec: int) -> Expr | None:
    left = _parse_prefix(c)
    if left is None:
        return None

    while not c.done:
        s = c.peek_spelling()
        if s is None or s in (";", ","):
            break

        if s in ("++", "--"):
            c.advance()
            left = Expr(kind="postfix_op", token=Token("punctuator", s, None), children=[left])
            continue

        if s == ".":
            c.advance()
            name = c.advance()
            left = Expr(kind="member", token=name, children=[left], value=name.spelling)
            continue

        if s == "->":
            c.advance()
            name = c.advance()
            left = Expr(kind="arrow", token=name, children=[left], value=name.spelling)
            continue

        if s == "(":
            c.advance()
            args: list[Expr] = []
            if not c.match(")"):
                while True:
                    arg = _parse_expr(c, 0)
                    if arg:
                        args.append(arg)
                    if not c.match(","):
                        break
                c.match(")")
            left = Expr(kind="call", token=left.token if left else Token("punctuator", "(", None), children=[left] + args)
            continue

        if s == "[":
            c.advance()
            idx = _parse_expr(c, 0)
            left = Expr(kind="index", token=left.token if left else None, children=[left, idx] if idx else [left])
            c.match("]")
            continue

        prec = PREC.get(s, 0)
        if prec == 0 or prec <= min_prec:
            break

        c.advance()
        right = _parse_expr(c, prec)
        left = Expr(kind="binary", token=Token("punctuator", s, None), children=[left, right] if right else [left])

    return left


def _parse_prefix(c: Cursor) -> Expr | None:
    t = c.peek()
    if t is None:
        return None

    if t.spelling == "(":
        c.advance()
        expr = _parse_expr(c, 0)
        c.match(")")
        return Expr(kind="group", token=t, children=[expr] if expr else [])

    if t.spelling in UNARY_OPS:
        c.advance()
        operand = _parse_expr(c, 12)
        return Expr(kind="unary", token=t, children=[operand] if operand else [])

    if t.spelling in ("sizeof",):
        c.advance()
        return Expr(kind="sizeof", token=t)

    if t.kind in ("identifier", "numeric_literal", "string_literal", "char_literal"):
        c.advance()
        lit_kind = t.kind
        if lit_kind == "numeric_literal":
            lit_kind = "int" if t.spelling.isdigit() or (t.spelling.startswith("0x") or t.spelling.startswith("0X")) else "float"
        return Expr(kind=lit_kind, token=t, value=t.spelling)

    return None


def _parse_stmts(tokens: list[Token]) -> list[Stmt]:
    c = Cursor(tokens)
    stmts: list[Stmt] = []
    c.match("{")
    while not c.done and c.peek_spelling() != "}":
        stmt = _parse_stmt(c)
        if stmt:
            stmts.append(stmt)
        else:
            c.advance()
    return stmts


def _parse_stmt(c: Cursor) -> Stmt | None:
    if c.match("return"):
        return _finish_return(c)

    if c.match("{"):
        return _finish_block(c)

    if c.peek_spelling() in ("if", "while", "for", "do", "switch", "case", "break", "continue", "goto", "else"):
        return _parse_raw_until_semicolon_or_brace(c)

    if c.peek_spelling() in DECL_KEYWORDS:
        return _parse_raw_until_semicolon(c)

    return _parse_expr_stmt(c)


def _finish_return(c: Cursor) -> Stmt:
    tokens: list[Token] = []
    while not c.done and c.peek_spelling() != ";":
        tokens.append(c.advance())
    if c.peek_spelling() == ";":
        tokens.append(c.advance())
    expr = _parse_expr_raw(tokens[:-1]) if len(tokens) > 1 else None
    return Stmt(kind="return_stmt", tokens=tokens, children=[], expr=expr)


def _finish_block(c: Cursor) -> Stmt:
    depth = 1
    tokens: list[Token] = []
    while not c.done and depth > 0:
        t = c.advance()
        tokens.append(t)
        if t.spelling == "{":
            depth += 1
        elif t.spelling == "}":
            depth -= 1
    inner = _parse_stmts(tokens[:0]) if len(tokens) <= 1 else _parse_stmts(tokens[:-1])
    return Stmt(kind="block", tokens=tokens, children=inner)


def _parse_raw_until_semicolon(c: Cursor) -> Stmt:
    tokens: list[Token] = []
    while not c.done and c.peek_spelling() != ";":
        tokens.append(c.advance())
    if c.peek_spelling() == ";":
        tokens.append(c.advance())
    return Stmt(kind="passthrough", tokens=tokens)


def _parse_raw_until_semicolon_or_brace(c: Cursor) -> Stmt:
    tokens: list[Token] = []
    depth = 0
    while not c.done:
        t = c.advance()
        tokens.append(t)
        if t.spelling == "{":
            depth += 1
        elif t.spelling == "}":
            depth -= 1
            if depth < 0:
                break
        elif t.spelling == ";" and depth == 0:
            break
    return Stmt(kind="passthrough", tokens=tokens)


def _parse_expr_stmt(c: Cursor) -> Stmt:
    tokens: list[Token] = []
    while not c.done and c.peek_spelling() != ";":
        tokens.append(c.advance())
    if c.peek_spelling() == ";":
        tokens.append(c.advance())
    expr = _parse_expr_raw(tokens[:-1]) if len(tokens) > 1 else None
    return Stmt(kind="expr_stmt", tokens=tokens, children=[], expr=expr)


def _parse_expr_raw(tokens: list[Token]) -> Expr | None:
    if not tokens:
        return None
    c = Cursor(tokens)
    return _parse_expr(c, 0)


def _lower_expr(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer) -> Expr:
    expr.children = [_lower_expr(c, current_struct, analyzer) for c in expr.children]

    if expr.kind == "call" and len(expr.children) >= 1:
        callee = expr.children[0]
        if callee.kind in ("member", "arrow"):
            method_name = callee.value or ""
            receiver = callee.children[0] if callee.children else None
            if receiver:
                impl_info, struct_name = _resolve_method(method_name, receiver, current_struct, analyzer)
                if impl_info and struct_name and method_name in impl_info.methods:
                    c_name = method_c_name(struct_name, method_name)
                    new_children: list[Expr] = []
                    if callee.kind == "member":
                        addr = Expr(kind="unary", token=Token("punctuator", "&", None), children=[receiver])
                        new_children.append(addr)
                    else:
                        new_children.append(receiver)
                    for arg in expr.children[1:]:
                        new_children.append(arg)
                    name_tok = Token("identifier", c_name, None)
                    ident = Expr(kind="ident", token=name_tok, value=c_name)
                    return Expr(kind="call", token=name_tok, children=[ident] + new_children)

    return expr


def _resolve_method(method_name: str, receiver: Expr, current_struct: str, analyzer: SemanticAnalyzer):
    """Given a method name and receiver expression, find the implementation info and struct name."""
    target_type = _resolve_type(receiver, current_struct, analyzer)
    if target_type:
        impl = analyzer.get_implementation(target_type)
        if impl and method_name in impl.methods:
            return impl, target_type
    return None, None


def _resolve_type(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer) -> str | None:
    if expr.kind in ("identifier", "ident") and expr.value == "self":
        return current_struct

    if expr.kind == "arrow":
        inner = expr.children[0] if expr.children else None
        if not inner:
            return None
        inner_type = _resolve_type(inner, current_struct, analyzer)
        if inner_type is None:
            return None
        field_name = expr.value or ""
        st = analyzer.get_struct(inner_type)
        if st is None:
            return None
        for fd in st.fields:
            ids = [t for t in fd.tokens if t.kind == "identifier"]
            if ids and ids[-1].spelling == field_name:
                return _extract_field_type_name(fd.tokens, ids[-1])
        return None

    if expr.kind == "member":
        inner_type = _resolve_type(expr.children[0], current_struct, analyzer) if expr.children else None
        if inner_type is None:
            return None
        field_name = expr.value or ""
        st = analyzer.get_struct(inner_type)
        if st is None:
            return None
        for fd in st.fields:
            ids = [t for t in fd.tokens if t.kind == "identifier"]
            if ids and ids[-1].spelling == field_name:
                return _extract_field_type_name(fd.tokens, ids[-1])
        return None

    return None


def _extract_field_type_name(tokens: list[Token], name_token: Token) -> str | None:
    parts: list[str] = []
    for t in tokens:
        if t is name_token:
            break
        parts.append(t.spelling)
    raw = " ".join(parts).strip()
    if raw.startswith("struct "):
        return raw[len("struct "):]
    if raw.startswith("union "):
        return raw[len("union "):]
    if raw.startswith("enum "):
        raw = raw[len("enum "):]
    return raw if raw else None


def _lower_stmt(stmt: Stmt, current_struct: str, analyzer: SemanticAnalyzer) -> Stmt:
    if stmt.expr:
        stmt.expr = _lower_expr(stmt.expr, current_struct, analyzer)
    stmt.children = [_lower_stmt(c, current_struct, analyzer) for c in stmt.children]
    return stmt


def _emit_expr(expr: Expr | None) -> str:
    if expr is None:
        return ""
    if expr.kind in ("ident", "int", "float", "string_literal", "char_literal"):
        return expr.value or expr.token.spelling
    if expr.kind == "group":
        return f"({_emit_expr(expr.children[0]) if expr.children else ''})"
    if expr.kind == "unary":
        return f"{expr.token.spelling}{_emit_expr(expr.children[0]) if expr.children else ''}"
    if expr.kind == "postfix_op":
        return f"{_emit_expr(expr.children[0]) if expr.children else ''}{expr.token.spelling}"
    if expr.kind == "call":
        callee = _emit_expr(expr.children[0]) if expr.children else ""
        args = ", ".join(_emit_expr(c) for c in expr.children[1:])
        return f"{callee}({args})"
    if expr.kind == "member":
        return f"{_emit_expr(expr.children[0]) if expr.children else ''}.{expr.value or ''}"
    if expr.kind == "arrow":
        return f"{_emit_expr(expr.children[0]) if expr.children else ''}->{expr.value or ''}"
    if expr.kind == "index":
        return f"{_emit_expr(expr.children[0]) if expr.children else ''}[{_emit_expr(expr.children[1]) if len(expr.children) > 1 else ''}]"
    if expr.kind == "binary":
        lhs = _emit_expr(expr.children[0]) if expr.children else ""
        rhs = _emit_expr(expr.children[1]) if len(expr.children) > 1 else ""
        return f"{lhs} {expr.token.spelling} {rhs}"
    if expr.kind == "sizeof":
        return "sizeof"
    return expr.token.spelling


def _emit_stmt(stmt: Stmt) -> str:
    if stmt.kind == "expr_stmt":
        result = _emit_expr(stmt.expr) if stmt.expr else ""
        for t in stmt.tokens:
            if t.spelling == ";":
                result += ";"
                break
        return result

    if stmt.kind == "return_stmt":
        expr_text = _emit_expr(stmt.expr) if stmt.expr else ""
        result = "return" if not expr_text else f"return {expr_text}"
        for t in stmt.tokens:
            if t.spelling == ";":
                result += ";"
                break
        return result

    if stmt.kind == "block":
        result = "{\n"
        inner = "\n".join("    " + l if l.strip() else l for l in (_emit_stmt(c) for c in stmt.children))
        if inner.strip():
            result += inner + "\n"
        result += "}"
        return result

    if stmt.kind == "passthrough":
        result = ""
        for t in stmt.tokens:
            result += t.leading_trivia
            result += t.spelling
        return result

    result = ""
    for t in stmt.tokens:
        result += t.leading_trivia
        result += t.spelling
    return result


def lower_method_body(tokens: list[Token], struct_name: str, analyzer: SemanticAnalyzer) -> str:
    stmts = _parse_stmts(tokens)
    stmts = [_lower_stmt(s, struct_name, analyzer) for s in stmts]
    parts: list[str] = []
    for s in stmts:
        text = _emit_stmt(s)
        if text:
            parts.append(text)
    body = "\n".join(parts)
    if body:
        body = "    " + body.replace("\n", "\n    ") + "\n"
    return body
