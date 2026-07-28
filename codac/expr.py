from __future__ import annotations

from typing import Optional

from codac.ast import Expr, Stmt, Token
from codac.diagnostics import Diagnostic
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


def _parse_coda_declaration(c: Cursor, type_names: frozenset[str]) -> Stmt | None:
    saved = c.pos
    type_tokens: list[Token] = []

    if c.peek_spelling() in ("struct", "union"):
        type_tokens.append(c.advance())

    if not c.peek() or c.peek().kind != "identifier":
        c.pos = saved
        return None
    type_token = c.advance()
    type_name = type_token.spelling
    type_tokens.append(type_token)

    if not c.peek() or c.peek().kind != "identifier":
        c.pos = saved
        return None
    var_token = c.advance()
    var_name = var_token.spelling

    has_init = False
    init_tokens: list[Token] = []

    next_spelling = c.peek_spelling()

    if next_spelling == ";":
        c.advance()
        return Stmt(
            kind="vardecl", tokens=[],
            var_name=var_name, var_type=type_name,
            has_init_call=False,
            var_init_arg_tokens=[],
        )

    if next_spelling == ".":
        c.advance()
        if c.peek() and c.peek().kind == "identifier" and c.peek().spelling == "init":
            c.advance()
            if c.peek_spelling() == "(":
                has_init = True
                depth = 0
                while not c.done:
                    t = c.advance()
                    init_tokens.append(t)
                    if t.spelling == "(":
                        depth += 1
                    elif t.spelling == ")":
                        depth -= 1
                        if depth == 0:
                            break
                if c.peek_spelling() == ";":
                    c.advance()
                return Stmt(
                    kind="vardecl", tokens=[],
                    var_name=var_name, var_type=type_name,
                    has_init_call=True,
                    var_init_arg_tokens=init_tokens,
                )
        c.pos = saved
        return None

    c.pos = saved
    return None


def _parse_stmts(tokens: list[Token], type_names: frozenset[str] = frozenset()) -> list[Stmt]:
    c = Cursor(tokens)
    stmts: list[Stmt] = []
    c.match("{")
    while not c.done and c.peek_spelling() != "}":
        stmt = _parse_stmt(c, type_names)
        if stmt:
            stmts.append(stmt)
        else:
            c.advance()
    return stmts


def _parse_stmt(c: Cursor, type_names: frozenset[str] = frozenset()) -> Stmt | None:
    if c.match("return"):
        return _finish_return(c)

    if c.match("{"):
        return _finish_block(c, type_names)

    if c.peek_spelling() in ("if", "while", "for", "do", "switch", "case", "break", "continue", "goto", "else"):
        return _parse_raw_until_semicolon_or_brace(c)

    if c.peek_spelling() in DECL_KEYWORDS:
        return _parse_raw_until_semicolon(c)

    t = c.peek()
    if t and t.kind == "identifier":
        saved = c.pos
        if t.spelling in type_names:
            c.advance()
            next_tok = c.peek()

            if next_tok and next_tok.kind == "identifier":
                c.pos = saved
                result = _parse_coda_declaration(c, type_names)
                if result is not None:
                    return result
                c.pos = saved
                return _parse_raw_until_semicolon(c)

            if next_tok and next_tok.spelling in ("*", "[", "("):
                c.pos = saved
                return _parse_raw_until_semicolon(c)

            c.pos = saved

        elif t.spelling == "struct":
            c.advance()
            if c.peek() and c.peek().kind == "identifier" and c.peek().spelling in type_names:
                c.advance()
                with_struct_type_name = c.peek()
                c.pos = saved
                if with_struct_type_name and with_struct_type_name.kind == "identifier":
                    result = _parse_coda_declaration(c, type_names)
                    if result is not None:
                        return result
                    c.pos = saved
                    return _parse_raw_until_semicolon(c)
                if with_struct_type_name and with_struct_type_name.spelling in ("*", "[", "("):
                    c.pos = saved
                    return _parse_raw_until_semicolon(c)
            c.pos = saved

    return _parse_expr_stmt(c, type_names)


def _finish_return(c: Cursor) -> Stmt:
    tokens: list[Token] = []
    while not c.done and c.peek_spelling() != ";":
        tokens.append(c.advance())
    if c.peek_spelling() == ";":
        tokens.append(c.advance())
    expr = _parse_expr_raw(tokens[:-1]) if len(tokens) > 1 else None
    return Stmt(kind="return_stmt", tokens=tokens, children=[], expr=expr)


def _finish_block(c: Cursor, type_names: frozenset[str] = frozenset()) -> Stmt:
    depth = 1
    tokens: list[Token] = []
    while not c.done and depth > 0:
        t = c.advance()
        tokens.append(t)
        if t.spelling == "{":
            depth += 1
        elif t.spelling == "}":
            depth -= 1
    inner = _parse_stmts(tokens[:0], type_names) if len(tokens) <= 1 else _parse_stmts(tokens[:-1], type_names)
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


def _parse_expr_stmt(c: Cursor, type_names: frozenset[str] = frozenset()) -> Stmt:
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


def _build_var_info(stmts: list[Stmt], analyzer: SemanticAnalyzer) -> dict[str, str]:
    var_info: dict[str, str] = {}
    def walk(stmts: list[Stmt]):
        for s in stmts:
            if s.kind == "vardecl" and s.var_name and s.var_type:
                var_info[s.var_name] = s.var_type
            for c in s.children:
                walk([c])
    walk(stmts)
    return var_info


def _lower_expr(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                var_info: dict[str, str] | None = None) -> Expr:
    expr.children = [_lower_expr(c, current_struct, analyzer, var_info) for c in expr.children]

    if expr.kind == "call" and len(expr.children) >= 1:
        callee = expr.children[0]
        if callee.kind in ("member", "arrow"):
            method_name = callee.value or ""
            receiver = callee.children[0] if callee.children else None
            if receiver:
                if method_name == "deinit" and callee.kind == "member":
                    if var_info:
                        recv_name = receiver.value if receiver.value else ""
                        if recv_name in var_info and analyzer.has_deinit(var_info[recv_name]):
                            analyzer.diagnostics.append(Diagnostic(
                                code="E070",
                                message=f"explicit deinit() on automatic variable of type '{var_info[recv_name]}' which has scope cleanup",
                                span=expr.token.span if expr.token and expr.token.span else None,
                            ))
                            return expr

                impl_info, struct_name = _resolve_method(method_name, receiver, current_struct, analyzer, var_info)
                if impl_info and struct_name and method_name in impl_info.methods:
                    sig = impl_info.methods[method_name]
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
                    call = Expr(kind="call", token=name_tok, children=[ident] + new_children)
                    call.value = sig.result_type if method_name != "deinit" else None
                    return call

    return expr


def _resolve_method(method_name: str, receiver: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                    var_info: dict[str, str] | None = None):
    target_type = _resolve_type(receiver, current_struct, analyzer, var_info)
    if target_type:
        impl = analyzer.get_implementation(target_type)
        if impl and method_name in impl.methods:
            return impl, target_type
    return None, None


def _resolve_type(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                  var_info: dict[str, str] | None = None) -> str | None:
    if expr.kind in ("identifier", "ident") and expr.value == "self":
        return current_struct

    if expr.kind in ("identifier", "ident") and var_info and expr.value in var_info:
        return var_info[expr.value]

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


def _type_has_deinit(type_str: str, analyzer: SemanticAnalyzer) -> bool:
    clean = type_str.strip()
    if clean.startswith("struct "):
        clean = clean[7:]
    while clean.endswith("*"):
        clean = clean[:-1].strip()
    if clean.startswith("const "):
        clean = clean[6:].strip()
    return analyzer.has_deinit(clean)


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
    if stmt.kind == "vardecl":
        type_name = stmt.var_type or ""
        var_name = stmt.var_name or ""
        if stmt.has_init_call and stmt.var_init_arg_tokens:
            inner = stmt.var_init_arg_tokens
            if len(inner) >= 2 and inner[0].spelling == "(" and inner[-1].spelling == ")":
                inner = inner[1:-1]
            arg_text = "".join(t.leading_trivia + t.spelling for t in inner).strip()
            return f"struct {type_name} {var_name};\n{type_name}_init(&{var_name}{', ' + arg_text if arg_text else ''});"
        return f"struct {type_name} {var_name} = {{0}};"

    if stmt.kind == "deinit_call":
        var_name = stmt.var_name or ""
        type_name = stmt.var_type or ""
        return f"{type_name}_deinit(&{var_name});"

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


def _analyze_scope(stmts: list[Stmt], analyzer: SemanticAnalyzer):
    scope_map: dict[int, list[tuple[str, str]]] = {}

    def collect_block_vars(stmts: list[Stmt]) -> list[tuple[str, str]]:
        vars_in_scope: list[tuple[str, str]] = []
        for stmt in stmts:
            if stmt.kind == "vardecl" and stmt.var_name and stmt.var_type:
                if analyzer.has_deinit(stmt.var_type):
                    vars_in_scope.append((stmt.var_name, stmt.var_type))
            if stmt.kind == "block":
                block_vars = collect_block_vars(stmt.children)
                if block_vars:
                    scope_map[id(stmt)] = block_vars
        return vars_in_scope

    root_vars = collect_block_vars(stmts)
    return scope_map, root_vars


def _extract_returned_var(expr: Expr | None) -> str | None:
    if expr and expr.kind in ("ident", "identifier"):
        return expr.value or expr.token.spelling
    return None


def _inject_cleanup(stmts: list[Stmt], scope_map: dict, root_vars: list[tuple[str, str]] | None = None) -> list[Stmt]:
    scope_stack: list[list[tuple[str, str]]] = [root_vars] if root_vars else []

    def walk(stmts: list[Stmt]) -> list[Stmt]:
        new_stmts: list[Stmt] = []
        for stmt in stmts:
            if stmt.kind == "block":
                block_id = id(stmt)
                block_vars = scope_map.get(block_id, [])
                scope_stack.append(block_vars)
                stmt.children = walk(stmt.children)
                for var_name, type_name in reversed(block_vars):
                    stmt.children.append(Stmt(
                        kind="deinit_call", tokens=[],
                        var_name=var_name, var_type=type_name,
                    ))
                scope_stack.pop()
                new_stmts.append(stmt)

            elif stmt.kind == "return_stmt":
                returned_var = _extract_returned_var(stmt.expr)
                all_vars: list[tuple[str, str]] = []
                for scope in scope_stack:
                    all_vars.extend(scope)
                for var_name, type_name in reversed(all_vars):
                    if var_name != returned_var:
                        new_stmts.append(Stmt(
                            kind="deinit_call", tokens=[],
                            var_name=var_name, var_type=type_name,
                        ))
                new_stmts.append(stmt)

            else:
                new_stmts.append(stmt)
        return new_stmts

    return walk(stmts)


def _lower_stmt(stmt: Stmt, current_struct: str, analyzer: SemanticAnalyzer,
                var_info: dict[str, str] | None = None) -> Stmt:
    if stmt.expr:
        stmt.expr = _lower_expr(stmt.expr, current_struct, analyzer, var_info)

        if stmt.kind == "expr_stmt" and stmt.expr.kind == "call":
            result_type = stmt.expr.value
            if result_type and _type_has_deinit(result_type, analyzer):
                analyzer.diagnostics.append(Diagnostic(
                    code="E071",
                    message=f"discarded return value: call returns type '{result_type}' which has deinit",
                    span=stmt.expr.token.span if stmt.expr.token and stmt.expr.token.span else None,
                ))

    stmt.children = [_lower_stmt(c, current_struct, analyzer, var_info) for c in stmt.children]
    return stmt


def lower_method_body(tokens: list[Token], struct_name: str, analyzer: SemanticAnalyzer) -> str:
    type_names = frozenset(analyzer.structs.keys())

    stmts = _parse_stmts(tokens, type_names)
    var_info = _build_var_info(stmts, analyzer)
    stmts = [_lower_stmt(s, struct_name, analyzer, var_info) for s in stmts]

    scope_map, root_vars = _analyze_scope(stmts, analyzer)

    stmts = _inject_cleanup(stmts, scope_map, root_vars)

    returned_var = None
    for s in stmts:
        if s.kind == "return_stmt":
            returned_var = _extract_returned_var(s.expr)
            break

    for var_name, type_name in reversed(root_vars):
        if var_name != returned_var:
            stmts.append(Stmt(
                kind="deinit_call", tokens=[],
                var_name=var_name, var_type=type_name,
            ))

    parts: list[str] = []
    for s in stmts:
        text = _emit_stmt(s)
        if text:
            parts.append(text)
    body = "\n".join(parts)
    if body:
        body = "    " + body.replace("\n", "\n    ") + "\n"
    return body
