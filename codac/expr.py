from __future__ import annotations

from typing import Optional

from codac.ast import Expr, Stmt, Token
from codac.diagnostics import Diagnostic
from codac.names import mangle_template_name, method_c_name
from codac.typesys import OPERATOR_NAMES, SemanticAnalyzer


DECL_KEYWORDS = frozenset({
    "struct", "union", "enum", "int", "double", "float", "char",
    "void", "unsigned", "signed", "long", "short", "const",
    "volatile", "extern", "static", "typedef",
})

PREC = {
    "=": 1, "+=": 1, "-=": 1, "*=": 1, "/=": 1, "%=": 1,
    "?": 2,
    "||": 3,
    "&&": 4,
    "|": 5,
    "^": 6,
    "&": 7,
    "==": 8, "!=": 8,
    "<": 9, ">": 9, "<=": 9, ">=": 9,
    "<<": 10, ">>": 10,
    "+": 11, "-": 11,
    "*": 12, "/": 12, "%": 12,
}

UNARY_OPS = frozenset({"&", "*", "+", "-", "!", "~", "++", "--"})
COMPOUND_ASSIGN_OPS = frozenset({"operator+=", "operator-=", "operator*=", "operator/="})


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

        if s == "?":
            c.advance()
            then_expr = _parse_expr(c, 0)
            c.match(":")
            else_expr = _parse_expr(c, prec - 1)
            left = Expr(kind="ternary", token=Token("punctuator", "?", None),
                        children=[left, then_expr, else_expr])
            continue

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
        operand = _parse_expr(c, 13)
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
    var_is_const = False

    if c.peek_spelling() == "const":
        var_is_const = True
        type_tokens.append(c.advance())

    if c.peek_spelling() in ("struct", "union"):
        type_tokens.append(c.advance())

    if not c.peek() or c.peek().kind != "identifier":
        c.pos = saved
        return None
    type_token = c.advance()
    type_name = type_token.spelling
    type_tokens.append(type_token)

    if type_name not in type_names:
        c.pos = saved
        return None

    if c.peek_spelling() == "<":
        arg_groups = _consume_template_args(c)
        if arg_groups is None:
            c.pos = saved
            return None
        type_name = mangle_template_name(type_name, arg_groups)
        type_tokens = [Token(kind="identifier", spelling=type_name, span=type_token.span)]

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
            var_is_const=var_is_const,
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
                    var_is_const=var_is_const,
                )
        c.pos = saved
        return None

    if next_spelling == "=":
        c.advance()
        init_expr = _parse_expr(c, 0)
        c.match(";")
        return Stmt(
            kind="vardecl", tokens=[],
            var_name=var_name, var_type=type_name,
            has_init_call=False,
            var_init_arg_tokens=[],
            var_init_expr=init_expr,
            var_is_const=var_is_const,
        )

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

    s = c.peek_spelling()
    if s == "if":
        return _parse_if(c, type_names)
    if s == "while":
        return _parse_while(c, type_names)
    if s == "for":
        return _parse_for(c, type_names)
    if s == "do":
        return _parse_do(c, type_names)
    if s in ("switch", "case", "break", "continue", "goto", "else"):
        return _parse_raw_until_semicolon_or_brace(c)

    if c.peek_spelling() in DECL_KEYWORDS:
        saved = c.pos
        result = _parse_coda_declaration(c, type_names)
        if result is not None:
            return result
        c.pos = saved
        return _parse_raw_until_semicolon(c)

    t = c.peek()
    if t and t.kind == "identifier":
        saved = c.pos
        if t.spelling in type_names:
            c.advance()
            next_tok = c.peek()

            if next_tok and (next_tok.kind == "identifier" or next_tok.spelling == "<"):
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

        else:
            next_idx = saved + 1
            next_tok = c.tokens[next_idx] if next_idx < len(c.tokens) else None
            if next_tok and (next_tok.kind == "identifier" or next_tok.spelling == "*"):
                c.pos = saved
                return _parse_raw_until_semicolon(c)

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


def _parse_control_header(c: Cursor) -> tuple[list[Token], list[Token]]:
    open_tok = c.peek()
    if open_tok is None or open_tok.spelling != "(":
        return [], []
    c.advance()
    header: list[Token] = [open_tok]
    inner: list[Token] = []
    depth = 1
    while not c.done and depth > 0:
        t = c.advance()
        header.append(t)
        if t.spelling == "(":
            depth += 1
        elif t.spelling == ")":
            depth -= 1
        if depth > 0:
            inner.append(t)
    return header, inner


def _parse_control_body(c: Cursor, type_names: frozenset[str]) -> Stmt:
    if c.match("{"):
        return _finish_block(c, type_names)
    stmt = _parse_stmt(c, type_names)
    return stmt if stmt else Stmt(kind="passthrough", tokens=[])


def _parse_if(c: Cursor, type_names: frozenset[str]) -> Stmt:
    if_tok = c.advance()
    header, inner = _parse_control_header(c)
    cond = _parse_expr_raw(inner)
    children = [_parse_control_body(c, type_names)]
    if c.match("else"):
        children.append(_parse_control_body(c, type_names))
    return Stmt(kind="if", tokens=[if_tok] + header, children=children, expr=cond)


def _parse_while(c: Cursor, type_names: frozenset[str]) -> Stmt:
    while_tok = c.advance()
    header, inner = _parse_control_header(c)
    cond = _parse_expr_raw(inner)
    return Stmt(kind="while", tokens=[while_tok] + header,
                children=[_parse_control_body(c, type_names)], expr=cond)


def _parse_for(c: Cursor, type_names: frozenset[str]) -> Stmt:
    for_tok = c.advance()
    header, inner = _parse_control_header(c)
    parts = _split_on_semicolons(inner)
    cond = _parse_expr_raw(parts[1]) if len(parts) > 1 else None
    return Stmt(kind="for", tokens=[for_tok] + header,
                children=[_parse_control_body(c, type_names)], expr=cond)


def _parse_do(c: Cursor, type_names: frozenset[str]) -> Stmt:
    do_tok = c.advance()
    body = _parse_control_body(c, type_names)
    if c.match("while"):
        header, inner = _parse_control_header(c)
        cond = _parse_expr_raw(inner)
        c.match(";")
        return Stmt(kind="do", tokens=[do_tok] + header, children=[body], expr=cond)
    return Stmt(kind="do", tokens=[do_tok], children=[body])


def _split_on_semicolons(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for t in tokens:
        if t.spelling == "(":
            depth += 1
        elif t.spelling == ")":
            depth -= 1
        if t.spelling == ";" and depth == 0:
            parts.append([])
        else:
            parts[-1].append(t)
    return parts


def _concat_tokens(tokens: list[Token]) -> str:
    result = ""
    for t in tokens:
        result += t.leading_trivia + t.spelling
    return result


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
    paren = 0
    while not c.done:
        t = c.peek()
        if t.spelling == "{":
            tokens.append(c.advance())
            depth += 1
        elif t.spelling == "}":
            if depth == 0:
                break
            tokens.append(c.advance())
            depth -= 1
        elif t.spelling == "(":
            tokens.append(c.advance())
            paren += 1
        elif t.spelling == ")":
            tokens.append(c.advance())
            if paren > 0:
                paren -= 1
        elif t.spelling == ";" and depth == 0 and paren == 0:
            tokens.append(c.advance())
            break
        else:
            tokens.append(c.advance())
        if depth == 0 and tokens and tokens[-1].spelling == "}":
            nxt = c.peek()
            if nxt is None or nxt.spelling not in ("else", "while"):
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


def _consume_template_args(c: Cursor) -> list[str] | None:
    if c.peek_spelling() != "<":
        return None
    c.advance()
    groups: list[str] = []
    current: list[str] = []
    depth = 0
    while not c.done:
        t = c.advance()
        s = t.spelling
        if s == "<":
            depth += 1
            current.append(s)
        elif s == ">>":
            if depth == 0:
                groups.append(" ".join(current).strip())
                return groups
            depth -= 1
            current.append(">")
            if depth == 0:
                groups.append(" ".join(current).strip())
                return groups
        elif s == ">":
            if depth == 0:
                groups.append(" ".join(current).strip())
                return groups
            depth -= 1
            current.append(">")
        elif s == "," and depth == 0:
            groups.append(" ".join(current).strip())
            current = []
        else:
            current.append(s)
    return None


def _build_var_info(stmts: list[Stmt], analyzer: SemanticAnalyzer) -> dict[str, tuple[str, bool]]:
    var_info: dict[str, tuple[str, bool]] = {}
    def walk(stmts: list[Stmt]):
        for s in stmts:
            if s.kind == "vardecl" and s.var_name and s.var_type:
                var_info[s.var_name] = (s.var_type, s.var_is_const)
            for c in s.children:
                walk([c])
    walk(stmts)
    return var_info


def _param_type_map(param_tokens: list[list[Token]], analyzer: SemanticAnalyzer) -> dict[str, str]:
    """Map parameter names to their (bare) struct type name.

    Only parameters whose base type has an implementation are recorded, so
    plain C types are skipped while Coda and foreign struct receivers are
    resolvable during lowering.  Pointer stars and the 'struct '/'union '
    prefix are stripped; the receiver call form (-> vs .) is decided by the
    callee expression, not the parameter type.
    """
    result: dict[str, str] = {}
    for tokens in param_tokens:
        ids = [t for t in tokens if t.kind == "identifier"]
        if not ids:
            continue
        name = ids[-1].spelling
        parts: list[str] = []
        for t in tokens:
            if t is ids[-1]:
                break
            parts.append(t.spelling)
        raw = " ".join(parts).strip()
        if raw.startswith("struct "):
            base = raw[len("struct "):]
        elif raw.startswith("union "):
            base = raw[len("union "):]
        else:
            continue
        base = base.strip()
        while base.endswith("*"):
            base = base[:-1].strip()
        if base and analyzer.get_implementation(base) is not None:
            result[name] = base
    return result


def _split_head_params(head_tokens: list[Token]) -> list[list[Token]]:
    """Split a function signature's head tokens into per-parameter token lists."""
    params: list[list[Token]] = []
    depth = 0
    started = False
    current: list[Token] = []
    for t in head_tokens:
        if t.spelling == "(" and not started:
            started = True
            continue
        if not started:
            continue
        if t.spelling == "(":
            depth += 1
            current.append(t)
        elif t.spelling == ")":
            if depth == 0:
                if current:
                    params.append(current)
                break
            depth -= 1
            current.append(t)
        elif t.spelling == "," and depth == 0:
            if current:
                params.append(current)
                current = []
        else:
            current.append(t)
    return params


def _lower_expr(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                var_info: dict[str, tuple[str, bool]] | None = None,
                param_names: frozenset[str] | None = None) -> Expr:
    expr.children = [_lower_expr(c, current_struct, analyzer, var_info, param_names) for c in expr.children]

    if expr.kind == "call" and len(expr.children) >= 1:
        callee = expr.children[0]
        if callee.kind in ("member", "arrow"):
            method_name = callee.value or ""
            receiver = callee.children[0] if callee.children else None
            if receiver:
                if method_name == "deinit" and callee.kind == "member":
                    if var_info:
                        recv_name = receiver.value if receiver.value else ""
                        if (recv_name in var_info
                                and recv_name not in (param_names or ())
                                and analyzer.has_deinit(var_info[recv_name][0])):
                            analyzer.diagnostics.append(Diagnostic(
                                code="E070",
                                message=f"explicit deinit() on automatic variable of type '{var_info[recv_name][0]}' which has scope cleanup",
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
                        recv_name = receiver.value if receiver.value else ""
                        recv_is_const = var_info and recv_name in var_info and var_info[recv_name][1]
                        if recv_is_const and not sig.is_const:
                            addr = Expr(kind="cast", token=Token("identifier", f"struct {struct_name} *", None), children=[addr])
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

    if expr.kind == "binary":
        op_method_name = f"operator{expr.token.spelling}"
        if op_method_name not in OPERATOR_NAMES:
            return expr
        lhs_type = _resolve_type(expr.children[0], current_struct, analyzer, var_info)
        if lhs_type is None:
            return expr
        impl = analyzer.get_implementation(lhs_type)
        if impl is None:
            return expr
        if op_method_name in impl.methods:
            sig = impl.methods[op_method_name]
            c_name = method_c_name(lhs_type, op_method_name)
            addr = Expr(kind="unary", token=Token("punctuator", "&", None), children=[expr.children[0]])
            ident = Expr(kind="ident", token=Token("identifier", c_name, None), value=c_name)
            call = Expr(kind="call", token=Token("punctuator", "(", None), children=[ident, addr, expr.children[1]])
            call.value = sig.result_type
            if op_method_name in COMPOUND_ASSIGN_OPS:
                call = Expr(kind="unary", token=Token("punctuator", "*", None), children=[call])
            return call
        analyzer.diagnostics.append(Diagnostic(
            code="E052",
            message=f"operator '{expr.token.spelling}' not implemented for type '{lhs_type}'",
            span=expr.token.span if expr.token and expr.token.span else None,
        ))
        return expr

    return expr


def _resolve_method(method_name: str, receiver: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                    var_info: dict[str, tuple[str, bool]] | None = None):
    target_type = _resolve_type(receiver, current_struct, analyzer, var_info)
    if target_type:
        impl = analyzer.get_implementation(target_type)
        if impl and method_name in impl.methods:
            return impl, target_type
    return None, None


def _resolve_type(expr: Expr, current_struct: str, analyzer: SemanticAnalyzer,
                  var_info: dict[str, tuple[str, bool]] | None = None) -> str | None:
    if expr.kind in ("identifier", "ident") and expr.value == "self":
        return current_struct

    if expr.kind in ("identifier", "ident") and var_info and expr.value in var_info:
        return var_info[expr.value][0]

    if expr.kind == "unary" and expr.token and expr.token.spelling == "*":
        inner = expr.children[0] if expr.children else None
        if inner is None:
            return None
        return _resolve_type(inner, current_struct, analyzer, var_info)

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
    if expr.kind == "ternary":
        cond = _emit_expr(expr.children[0]) if expr.children else ""
        then = _emit_expr(expr.children[1]) if len(expr.children) > 1 else ""
        els = _emit_expr(expr.children[2]) if len(expr.children) > 2 else ""
        return f"{cond} ? {then} : {els}"
    if expr.kind == "sizeof":
        return "sizeof"
    if expr.kind == "cast":
        return f"({expr.token.spelling}){_emit_expr(expr.children[0]) if expr.children else ''}"
    return expr.token.spelling


def _emit_stmt(stmt: Stmt) -> str:
    if stmt.kind == "vardecl":
        type_name = stmt.var_type or ""
        var_name = stmt.var_name or ""
        const_prefix = "const " if stmt.var_is_const else ""
        if stmt.has_init_call and stmt.var_init_arg_tokens:
            inner = stmt.var_init_arg_tokens
            if len(inner) >= 2 and inner[0].spelling == "(" and inner[-1].spelling == ")":
                inner = inner[1:-1]
            arg_text = "".join(t.leading_trivia + t.spelling for t in inner).strip()
            init_call = f"{type_name}_init(&{var_name}{', ' + arg_text if arg_text else ''});"
            if stmt.var_is_const:
                init_call = f"{type_name}_init((struct {type_name} *)&{var_name}{', ' + arg_text if arg_text else ''});"
            init_suffix = " = {0}" if stmt.var_is_const else ""
            return f"{const_prefix}struct {type_name} {var_name}{init_suffix};\n{init_call}"
        if stmt.var_init_expr:
            return f"{const_prefix}struct {type_name} {var_name} = {_emit_expr(stmt.var_init_expr)};"
        return f"{const_prefix}struct {type_name} {var_name} = {{0}};"

    if stmt.kind == "deinit_call":
        var_name = stmt.var_name or ""
        type_name = stmt.var_type or ""
        if stmt.var_is_const:
            return f"{type_name}_deinit((struct {type_name} *)&{var_name});"
        return f"{type_name}_deinit(&{var_name});"

    if stmt.kind == "expr_stmt":
        result = _emit_expr(stmt.expr) if stmt.expr else ""
        if stmt.expr and stmt.expr.kind == "unary" and stmt.expr.token.spelling == "*":
            result = f"(void){_emit_expr(stmt.expr)}"
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

    if stmt.kind == "if":
        cond = _emit_expr(stmt.expr) if stmt.expr else ""
        then = _emit_stmt(stmt.children[0]) if stmt.children else "{}"
        result = f"if ({cond}) {then}"
        if len(stmt.children) > 1 and stmt.children[1]:
            result += f" else {_emit_stmt(stmt.children[1])}"
        return result

    if stmt.kind == "while":
        cond = _emit_expr(stmt.expr) if stmt.expr else ""
        body = _emit_stmt(stmt.children[0]) if stmt.children else "{}"
        return f"while ({cond}) {body}"

    if stmt.kind == "for":
        parts = _split_on_semicolons(stmt.tokens[2:-1])
        init = _concat_tokens(parts[0]) if len(parts) > 0 else ""
        incr = _concat_tokens(parts[2]) if len(parts) > 2 else ""
        cond = _emit_expr(stmt.expr) if stmt.expr else ""
        body = _emit_stmt(stmt.children[0]) if stmt.children else "{}"
        return f"for ({init};{cond};{incr}) {body}"

    if stmt.kind == "do":
        body = _emit_stmt(stmt.children[0]) if stmt.children else "{}"
        cond = _emit_expr(stmt.expr) if stmt.expr else ""
        return f"do {body} while ({cond});"

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
    scope_map: dict[int, list[tuple[str, str, bool]]] = {}

    def collect_block_vars(stmts: list[Stmt]) -> list[tuple[str, str, bool]]:
        vars_in_scope: list[tuple[str, str, bool]] = []
        for stmt in stmts:
            if stmt.kind == "vardecl" and stmt.var_name and stmt.var_type:
                if analyzer.has_deinit(stmt.var_type):
                    vars_in_scope.append((stmt.var_name, stmt.var_type, stmt.var_is_const))
            if stmt.kind == "block":
                block_vars = collect_block_vars(stmt.children)
                if block_vars:
                    scope_map[id(stmt)] = block_vars
            if stmt.kind in ("if", "while", "for", "do"):
                collect_block_vars(stmt.children)
        return vars_in_scope

    root_vars = collect_block_vars(stmts)
    return scope_map, root_vars


def _extract_returned_var(expr: Expr | None) -> str | None:
    if expr and expr.kind in ("ident", "identifier"):
        return expr.value or expr.token.spelling
    return None


def _inject_cleanup(stmts: list[Stmt], scope_map: dict, root_vars: list[tuple[str, str, bool]] | None = None) -> list[Stmt]:
    scope_stack: list[list[tuple[str, str, bool]]] = [root_vars] if root_vars else []

    def walk(stmts: list[Stmt]) -> list[Stmt]:
        new_stmts: list[Stmt] = []
        for stmt in stmts:
            if stmt.kind == "block":
                block_id = id(stmt)
                block_vars = scope_map.get(block_id, [])
                scope_stack.append(block_vars)
                stmt.children = walk(stmt.children)
                for var_name, type_name, is_const in reversed(block_vars):
                    stmt.children.append(Stmt(
                        kind="deinit_call", tokens=[],
                        var_name=var_name, var_type=type_name,
                        var_is_const=is_const,
                    ))
                scope_stack.pop()
                new_stmts.append(stmt)

            elif stmt.kind == "return_stmt":
                returned_var = _extract_returned_var(stmt.expr)
                all_vars: list[tuple[str, str, bool]] = []
                for scope in scope_stack:
                    all_vars.extend(scope)
                for var_name, type_name, is_const in reversed(all_vars):
                    if var_name != returned_var:
                        new_stmts.append(Stmt(
                            kind="deinit_call", tokens=[],
                            var_name=var_name, var_type=type_name,
                            var_is_const=is_const,
                        ))
                new_stmts.append(stmt)

            elif stmt.kind in ("if", "while", "for", "do"):
                stmt.children = walk(stmt.children)
                new_stmts.append(stmt)

            else:
                new_stmts.append(stmt)
        return new_stmts

    return walk(stmts)


def _lower_stmt(stmt: Stmt, current_struct: str, analyzer: SemanticAnalyzer,
                var_info: dict[str, tuple[str, bool]] | None = None,
                param_names: frozenset[str] | None = None) -> Stmt:
    if stmt.expr:
        stmt.expr = _lower_expr(stmt.expr, current_struct, analyzer, var_info, param_names)

        if stmt.kind == "expr_stmt" and stmt.expr.kind == "call":
            result_type = stmt.expr.value
            if result_type and _type_has_deinit(result_type, analyzer):
                analyzer.diagnostics.append(Diagnostic(
                    code="E071",
                    message=f"discarded return value: call returns type '{result_type}' which has deinit",
                    span=stmt.expr.token.span if stmt.expr.token and stmt.expr.token.span else None,
                ))

    if stmt.var_init_expr:
        stmt.var_init_expr = _lower_expr(stmt.var_init_expr, current_struct, analyzer, var_info, param_names)

    stmt.children = [_lower_stmt(c, current_struct, analyzer, var_info, param_names) for c in stmt.children]
    return stmt


def lower_method_body(tokens: list[Token], struct_name: str, analyzer: SemanticAnalyzer,
                      param_tokens: list[list[Token]] | None = None) -> str:
    type_names = frozenset(analyzer.structs.keys()) | analyzer.template_names()

    stmts = _parse_stmts(tokens, type_names)
    var_info = _build_var_info(stmts, analyzer)
    param_names: frozenset[str] = frozenset()
    if param_tokens:
        param_types = _param_type_map(param_tokens, analyzer)
        var_info.update({name: (type_name, False) for name, type_name in param_types.items()})
        param_names = frozenset(param_types.keys())
    stmts = [_lower_stmt(s, struct_name, analyzer, var_info, param_names) for s in stmts]

    scope_map, root_vars = _analyze_scope(stmts, analyzer)

    stmts = _inject_cleanup(stmts, scope_map, root_vars)

    returned_var = None
    for s in stmts:
        if s.kind == "return_stmt":
            returned_var = _extract_returned_var(s.expr)
            break

    for var_name, type_name, is_const in reversed(root_vars):
        if var_name != returned_var:
            stmts.append(Stmt(
                kind="deinit_call", tokens=[],
                var_name=var_name, var_type=type_name,
                var_is_const=is_const,
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
