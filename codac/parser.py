from __future__ import annotations

import dataclasses
from typing import Optional

from codac.ast import (
    Expr, FieldDecl, Import, Implementation, Method, Module, SourceFile, Stmt,
    StructDecl, TemplateDecl, Token, TopLevelDecl,
)
from codac.diagnostics import Diagnostic, Span
from codac.lexer import Lexer


@dataclasses.dataclass
class TokenStream:
    tokens: list[Token]
    pos: int = 0

    @property
    def current(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def peek(self, offset: int = 0) -> Token | None:
        idx = self.pos + offset
        return self.tokens[idx] if 0 <= idx < len(self.tokens) else None

    def skip(self, *kinds: str) -> Token | None:
        t = self.current
        if t and t.kind in kinds:
            return self.advance()
        return None

    def expect(self, kind: str, msg: str) -> Token:
        t = self.skip(kind)
        if t is None:
            raise ParseError(msg, self.current.span if self.current else None)
        return t

    def skip_semicolon(self):
        self.skip(";")

    def is_eof(self) -> bool:
        return self.pos >= len(self.tokens)


class ParseError(Exception):
    def __init__(self, msg: str, span: Span | None = None):
        self.msg = msg
        self.span = span


class Parser:
    def __init__(self, path: str, source: str):
        lexer = Lexer(path, source)
        sf = lexer.lex()
        self.path = path
        self.source = source
        self.tokens = TokenStream(sf.tokens)
        self.diagnostics: list[Diagnostic] = lexer.diagnostics
        self.typedef_names: set[str] = set()
        self.errors: list[str] = []

    def parse(self) -> Module:
        decls: list[TopLevelDecl] = []
        imports: list[Import] = []
        while self.tokens.current:
            if self.tokens.current.kind == "directive":
                decl = self._parse_directive()
                if decl is not None:
                    decls.append(decl)
                    if decl.kind == "import" and decl.body:
                        imports.append(decl.body)
                continue
            decl = self._parse_top_level()
            if decl is not None:
                decls.append(decl)
        return Module(path=self.path, top_level=decls, imports=imports)

    def _parse_directive(self) -> TopLevelDecl | None:
        token = self.tokens.advance()
        text = token.spelling

        if text.startswith("#include"):
            return TopLevelDecl(kind="include", token=token, is_foreign=True)

        if text.startswith("#import"):
            rest = text[len("#import"):].strip().strip('"')
            if not rest.endswith(".co"):
                self.diagnostics.append(Diagnostic(
                    code="E001", message="malformed import: must be #import \"path.co\"", span=token.span,
                ))
                return None
            import_path = rest
            im = Import(token=token, path=import_path)
            return TopLevelDecl(kind="import", token=token, body=im)

        return TopLevelDecl(kind="preserved", token=token, preserved_tokens=[token])

    def _parse_top_level(self) -> TopLevelDecl | None:
        if self.tokens.current is None:
            return None

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "template":
            return self._parse_template()

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "impl":
            return self._parse_implementation()

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "struct":
            return self._maybe_parse_struct()

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "union":
            return self._parse_preserved_until_semicolon_or_brace()

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "enum":
            return self._parse_preserved_until_semicolon_or_brace()

        if self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "typedef":
            return self._parse_preserved_until_semicolon_or_brace()

        result = self._try_parse_function_def()
        if result is not None:
            return result

        return self._parse_preserved_until_semicolon_or_brace()

    def _parse_preserved_until_semicolon_or_brace(self) -> TopLevelDecl:
        tokens: list[Token] = []
        depth = 0
        while self.tokens.current:
            if self.tokens.current.spelling == "{" and depth == 0:
                depth += 1
                tokens.append(self.tokens.advance())
            elif self.tokens.current.spelling == "}":
                if depth == 0:
                    break
                depth -= 1
                tokens.append(self.tokens.advance())
            elif self.tokens.current.spelling == ";" and depth == 0:
                tokens.append(self.tokens.advance())
                break
            else:
                if self.tokens.current.spelling == "{":
                    depth += 1
                tokens.append(self.tokens.advance())
        return TopLevelDecl(kind="preserved", token=tokens[0] if tokens else Token("preserved", "", Span(self.path, 0, 1, 1)), preserved_tokens=tokens)

    def _try_parse_function_def(self) -> TopLevelDecl | None:
        """Detect and parse a C function definition at the top level.

        Looks for the pattern: <type> <name> ( <params> ) { <body> }
        Returns a TopLevelDecl with kind='function', head_tokens (signature
        up to and including the closing paren) and body_tokens (the { ... }
        block), or None if the current position is not a function definition.
        """
        saved = self.tokens.pos
        head_tokens: list[Token] = []

        while self.tokens.current:
            t = self.tokens.current

            if t.kind == "identifier":
                peek = self.tokens.peek(1)
                if peek and peek.spelling == "(":
                    head_tokens.append(self.tokens.advance())
                    head_tokens.append(self.tokens.advance())
                    depth = 1
                    while self.tokens.current and depth > 0:
                        t2 = self.tokens.advance()
                        head_tokens.append(t2)
                        if t2.spelling == "(":
                            depth += 1
                        elif t2.spelling == ")":
                            depth -= 1
                    if depth != 0:
                        self.tokens.pos = saved
                        return None
                    if self.tokens.current and self.tokens.current.spelling == "{":
                        body_tokens = self._parse_balanced_brace_block()
                        return TopLevelDecl(
                            kind="function",
                            token=head_tokens[0] if head_tokens else Token("function", "", Span(self.path, 0, 1, 1)),
                            head_tokens=head_tokens,
                            body_tokens=body_tokens,
                        )
                    if self.tokens.current and self.tokens.current.spelling == ";":
                        head_tokens.append(self.tokens.advance())
                        return TopLevelDecl(
                            kind="function",
                            token=head_tokens[0] if head_tokens else Token("function", "", Span(self.path, 0, 1, 1)),
                            head_tokens=head_tokens,
                            body_tokens=[],
                        )
                    self.tokens.pos = saved
                    return None
                else:
                    self.tokens.pos = saved
                    return None

            if t.spelling in (";", "="):
                self.tokens.pos = saved
                return None

            head_tokens.append(self.tokens.advance())

        self.tokens.pos = saved
        return None

    def _parse_balanced_brace_block(self) -> list[Token]:
        tokens: list[Token] = []
        if self.tokens.current and self.tokens.current.spelling == "{":
            tokens.append(self.tokens.advance())
            depth = 1
            while self.tokens.current and depth > 0:
                t = self.tokens.advance()
                tokens.append(t)
                if t.spelling == "{":
                    depth += 1
                elif t.spelling == "}":
                    depth -= 1
        return tokens

    def _maybe_parse_struct(self) -> TopLevelDecl:
        struct_token = self.tokens.advance()
        name_token = self.tokens.current
        if name_token is None or name_token.kind not in ("identifier", "keyword"):
            self.tokens.pos -= 1
            return TopLevelDecl(kind="preserved", token=struct_token, preserved_tokens=[struct_token])

        self.tokens.advance()
        base_name_token: Token | None = None
        if self.tokens.current and self.tokens.current.spelling == ":":
            self.tokens.advance()
            bt = self.tokens.current
            if bt and bt.kind in ("identifier", "keyword"):
                base_name_token = bt
                self.tokens.advance()
        fields: list[FieldDecl] = []

        if self.tokens.current and self.tokens.current.spelling == "{":
            self.tokens.advance()
            depth = 1
            field_tokens: list[Token] = []
            while self.tokens.current:
                if self.tokens.current.spelling == "{":
                    depth += 1
                    field_tokens.append(self.tokens.advance())
                elif self.tokens.current.spelling == "}":
                    depth -= 1
                    if depth == 0:
                        break
                    field_tokens.append(self.tokens.advance())
                elif self.tokens.current.spelling == ";" and depth == 1:
                    field_tokens.append(self.tokens.advance())
                    if field_tokens:
                        fields.append(FieldDecl(tokens=list(field_tokens)))
                        field_tokens = []
                else:
                    field_tokens.append(self.tokens.advance())
            if field_tokens:
                fields.append(FieldDecl(tokens=field_tokens))
            self.tokens.advance()

        self._skip_to_semicolon_if_present()

        if self._is_coda_owned_struct(name_token.spelling):
            return TopLevelDecl(
                kind="struct", token=struct_token,
                body=StructDecl(name_token=name_token, fields=fields, base_name_token=base_name_token),
            )
        return TopLevelDecl(
            kind="struct", token=struct_token,
            body=StructDecl(name_token=name_token, fields=fields, base_name_token=base_name_token),
            is_foreign=False,
        )

    def _is_coda_owned_struct(self, name: str) -> bool:
        i = self.tokens.pos
        while i < len(self.tokens.tokens):
            t = self.tokens.tokens[i]
            if t.kind == "directive" and t.spelling.startswith("#import"):
                i += 1
                continue
            if t.kind == "keyword" and t.spelling == "impl":
                peek = i + 1
                if peek < len(self.tokens.tokens):
                    n = self.tokens.tokens[peek]
                    if n.spelling == name or (n.spelling == "struct" and peek + 1 < len(self.tokens.tokens) and self.tokens.tokens[peek + 1].spelling == name):
                        return True
            if t.kind == "keyword" and t.spelling in ("struct", "union", "enum", ";", "#"):
                break
            if t.spelling == "{":
                break
            i += 1
        return False

    def _parse_template(self) -> TopLevelDecl:
        self.tokens.advance()
        self.tokens.expect("<", "expected '<' after template")
        params: list[Token] = []
        while self.tokens.current:
            t = self.tokens.advance()
            if t.spelling == ">":
                break
            if t.spelling == ",":
                continue
            if t.kind == "identifier":
                params.append(t)
        else:
            self.errors.append("unterminated template parameter list")

        if self.tokens.current and self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "struct":
            struct_decl = self._parse_struct_decl()
            if struct_decl:
                struct_decl.template_params = params
            return TopLevelDecl(kind="template", token=params[0] if params else Token("template", "", Span(self.path, 0, 1, 1)), body=TemplateDecl(params=params, body=struct_decl))

        if self.tokens.current and self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "impl":
            impl = self._parse_implementation_body()
            if impl:
                impl.template_args = [[p] for p in params]
            return TopLevelDecl(kind="template", token=params[0] if params else Token("template", "", Span(self.path, 0, 1, 1)), body=TemplateDecl(params=params, body=impl))

        self.errors.append("template must be followed by struct or impl")
        return TopLevelDecl(kind="preserved", token=params[0] if params else Token("template", "", Span(self.path, 0, 1, 1)), preserved_tokens=[])

    def _parse_struct_decl(self) -> StructDecl | None:
        if not (self.tokens.current and self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "struct"):
            return None
        self.tokens.advance()
        name_token = self.tokens.current
        if name_token is None or name_token.kind not in ("identifier", "keyword"):
            return None
        self.tokens.advance()
        base_name_token: Token | None = None
        if self.tokens.current and self.tokens.current.spelling == ":":
            self.tokens.advance()
            bt = self.tokens.current
            if bt and bt.kind in ("identifier", "keyword"):
                base_name_token = bt
                self.tokens.advance()
        fields: list[FieldDecl] = []
        if self.tokens.current and self.tokens.current.spelling == "{":
            self.tokens.advance()
            depth = 1
            field_tokens: list[Token] = []
            while self.tokens.current:
                if self.tokens.current.spelling == "{":
                    depth += 1
                    field_tokens.append(self.tokens.advance())
                elif self.tokens.current.spelling == "}":
                    depth -= 1
                    if depth == 0:
                        break
                    field_tokens.append(self.tokens.advance())
                elif self.tokens.current.spelling == ";" and depth == 1:
                    field_tokens.append(self.tokens.advance())
                    if field_tokens:
                        fields.append(FieldDecl(tokens=list(field_tokens)))
                        field_tokens = []
                else:
                    field_tokens.append(self.tokens.advance())
            if field_tokens:
                fields.append(FieldDecl(tokens=field_tokens))
            self.tokens.advance()
        self._skip_to_semicolon_if_present()
        return StructDecl(name_token=name_token, fields=fields, base_name_token=base_name_token)

    def _parse_implementation(self) -> TopLevelDecl | None:
        token = self.tokens.advance()
        impl = self._parse_implementation_body(token)
        if impl is None:
            return None
        return TopLevelDecl(kind="impl", token=token, body=impl)

    def _parse_implementation_body(self, token: Token | None = None) -> Implementation | None:
        is_foreign = False
        name_tokens: list[Token] = []

        if self.tokens.current and self.tokens.current.spelling == "struct":
            is_foreign = True
            self.tokens.advance()

        while self.tokens.current:
            t = self.tokens.current
            if t.spelling in ("{", "<"):
                break
            name_tokens.append(self.tokens.advance())

        template_args: list[list[Token]] = []
        if self.tokens.current and self.tokens.current.spelling == "<":
            self.tokens.advance()
            current_arg: list[Token] = []
            depth = 1
            while self.tokens.current:
                t = self.tokens.advance()
                if t.spelling == "<":
                    depth += 1
                    current_arg.append(t)
                elif t.spelling == ">":
                    depth -= 1
                    if depth == 0:
                        if current_arg:
                            template_args.append(current_arg)
                        break
                    current_arg.append(t)
                elif t.spelling == "," and depth == 1:
                    if current_arg:
                        template_args.append(current_arg)
                        current_arg = []
                else:
                    current_arg.append(t)
            else:
                self.errors.append("unterminated template argument list")

        if not (self.tokens.current and self.tokens.current.spelling == "{"):
            if token:
                self.diagnostics.append(Diagnostic(
                    code="E010", message="malformed implementation: expected '{'", span=token.span,
                ))
            return None

        methods = self._parse_method_list()
        if token is None:
            token = name_tokens[0] if name_tokens else Token("impl", "", Span(self.path, 0, 1, 1))
        return Implementation(
            struct_token=token, is_foreign_struct=is_foreign,
            name_tokens=name_tokens,
            template_args=template_args, methods=methods,
        )

    def _parse_method_list(self) -> list[Method]:
        methods: list[Method] = []
        self.tokens.advance()
        depth = 1
        while self.tokens.current:
            if self.tokens.current.spelling == "}":
                depth -= 1
                if depth == 0:
                    self.tokens.advance()
                    break
                self.tokens.advance()
                continue
            if self.tokens.current.spelling == "{":
                depth += 1
                self.tokens.advance()
                continue
            method = self._parse_one_method()
            if method:
                methods.append(method)
        return methods

    def _parse_one_method(self) -> Method | None:
        is_virtual = False
        is_override = False
        if self.tokens.current and self.tokens.current.kind == "keyword":
            if self.tokens.current.spelling == "virtual":
                is_virtual = True
                self.tokens.advance()
            elif self.tokens.current.spelling == "override":
                is_override = True
                self.tokens.advance()

        is_init = False
        is_deinit = False
        is_operator = False
        is_const = False
        operator_token: str | None = None
        name_token: Token | None = None

        all_head_tokens: list[Token] = []
        while self.tokens.current:
            t = self.tokens.current
            if t.spelling == "(":
                break
            if t.spelling == ";":
                return None
            all_head_tokens.append(self.tokens.advance())

        if not all_head_tokens:
            return None

        i = len(all_head_tokens) - 1
        if i < 0:
            return None

        last = all_head_tokens[i]
        is_ident_or_keyword = last.kind in ("identifier", "keyword")

        if is_ident_or_keyword and last.spelling == "init":
            is_init = True
            name_token = last
            all_head_tokens.pop()
        elif is_ident_or_keyword and last.spelling == "deinit":
            is_deinit = True
            name_token = last
            all_head_tokens.pop()
        elif (is_ident_or_keyword and last.spelling == "operator"
              and i - 1 >= 0):
            op_token = all_head_tokens[i - 1]
            is_operator = True
            operator_token = op_token.spelling
            name_token = Token(
                kind="identifier",
                spelling=f"operator{operator_token}",
                span=op_token.span,
            )
            all_head_tokens.pop()
            all_head_tokens.pop()
        elif is_ident_or_keyword:
            name_token = last
            all_head_tokens.pop()
        elif (i - 1 >= 0
              and all_head_tokens[i - 1].kind in ("identifier", "keyword")
              and all_head_tokens[i - 1].spelling == "operator"):
            op_token = last
            is_operator = True
            operator_token = op_token.spelling
            name_token = Token(
                kind="identifier",
                spelling=f"operator{operator_token}",
                span=op_token.span,
            )
            all_head_tokens.pop()
            all_head_tokens.pop()
        else:
            return None

        return_type_tokens = all_head_tokens

        params = self._parse_parameter_list()

        if self.tokens.current and self.tokens.current.kind == "keyword" and self.tokens.current.spelling == "const":
            is_const = True
            if is_init or is_deinit:
                self.diagnostics.append(Diagnostic(
                    code="E013",
                    message=f"'{'init' if is_init else 'deinit'}' cannot be declared 'const'",
                    span=self.tokens.current.span,
                ))
            self.tokens.advance()

        body_tokens = self._parse_method_body()
        return Method(
            is_virtual=is_virtual, is_override=is_override,
            is_init=is_init, is_deinit=is_deinit,
            is_operator=is_operator, is_const=is_const,
            operator_token=operator_token,
            name_token=name_token, return_type_tokens=return_type_tokens,
            param_tokens=params, body_tokens=body_tokens,
        )

    def _parse_parameter_list(self) -> list[list[Token]]:
        params: list[list[Token]] = []
        if not (self.tokens.current and self.tokens.current.spelling == "("):
            return params
        self.tokens.advance()
        current_param: list[Token] = []
        depth = 0
        while self.tokens.current:
            t = self.tokens.current
            if t.spelling == "(":
                depth += 1
                current_param.append(self.tokens.advance())
            elif t.spelling == ")":
                if depth == 0:
                    self.tokens.advance()
                    if current_param:
                        params.append(current_param)
                    break
                depth -= 1
                current_param.append(self.tokens.advance())
            elif t.spelling == "," and depth == 0:
                if current_param:
                    params.append(current_param)
                    current_param = []
                self.tokens.advance()
            else:
                current_param.append(self.tokens.advance())
        return params

    def _parse_method_body(self) -> list[Token]:
        body: list[Token] = []
        if not (self.tokens.current and self.tokens.current.spelling == "{"):
            return body
        depth = 0
        while self.tokens.current:
            t = self.tokens.advance()
            body.append(t)
            if t.spelling == "{":
                depth += 1
            elif t.spelling == "}":
                depth -= 1
                if depth == 0:
                    break
        return body

    def _skip_to_semicolon_if_present(self):
        if self.tokens.current and self.tokens.current.spelling == ";":
            self.tokens.advance()
