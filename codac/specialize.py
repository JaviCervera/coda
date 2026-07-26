from __future__ import annotations

import dataclasses
from typing import Optional

from codac.ast import (
    Expr, FieldDecl, Implementation, Method, Module, SourceFile, StructDecl, Token, TopLevelDecl,
)
from codac.diagnostics import Diagnostic
from codac.names import mangle_template_name
from codac.typesys import SemanticAnalyzer, StructType


@dataclasses.dataclass(frozen=True)
class TemplateKey:
    template_name: str
    arg_types: tuple[str, ...]

    def mangled_name(self) -> str:
        return mangle_template_name(self.template_name, list(self.arg_types))


@dataclasses.dataclass
class SpecializedStruct:
    key: TemplateKey
    struct_decl: StructDecl
    implementation: Implementation | None
    is_coda_owned: bool


class Specializer:
    def __init__(self, analyzer: SemanticAnalyzer):
        self.analyzer = analyzer
        self.specializations: dict[TemplateKey, SpecializedStruct] = {}
        self.templates: dict[str, tuple[list[str], StructDecl | None, Implementation | None]] = {}
        self.diagnostics: list[Diagnostic] = []

    def collect_templates(self, module: Module):
        for decl in module.top_level:
            if decl.kind == "template" and decl.body:
                td = decl.body
                param_names = [p.spelling for p in td.params]
                if isinstance(td.body, StructDecl):
                    self.templates[td.body.name_token.spelling] = (param_names, td.body, None)
                elif isinstance(td.body, Implementation):
                    impl = td.body
                    name = "".join(t.spelling for t in impl.name_tokens)
                    if name in self.templates:
                        existing = self.templates[name]
                        self.templates[name] = (existing[0], existing[1], impl)
                    else:
                        self.templates[name] = (param_names, None, impl)

    def specialize(self, template_name: str, arg_types: list[str]) -> SpecializedStruct | None:
        if template_name not in self.templates:
            self._diag("E040", f"unknown template '{template_name}'")
            return None

        param_names, struct_decl, impl = self.templates[template_name]

        if len(arg_types) != len(param_names):
            self._diag("E041", f"template '{template_name}' expects {len(param_names)} arguments, got {len(arg_types)}")
            return None

        key = TemplateKey(template_name=template_name, arg_types=tuple(arg_types))
        if key in self.specializations:
            return self.specializations[key]

        subst_map = dict(zip(param_names, arg_types))

        specialized_struct = self._substitute_struct(struct_decl, subst_map, template_name, arg_types) if struct_decl else None
        specialized_impl = self._substitute_implementation(impl, subst_map, template_name, arg_types) if impl else None

        result = SpecializedStruct(
            key=key,
            struct_decl=specialized_struct,
            implementation=specialized_impl,
            is_coda_owned=True,
        )
        self.specializations[key] = result
        return result

    def _substitute_struct(self, sd: StructDecl, subst: dict[str, str], template_name: str, arg_types: list[str]) -> StructDecl:
        new_fields: list[FieldDecl] = []
        for field in sd.fields:
            new_tokens: list[Token] = []
            for t in field.tokens:
                if t.spelling in subst:
                    new_tokens.append(Token(
                        kind="identifier",
                        spelling=subst[t.spelling],
                        span=t.span,
                        leading_trivia=t.leading_trivia,
                    ))
                else:
                    new_tokens.append(t)
            new_fields.append(FieldDecl(tokens=new_tokens))

        mangled = mangle_template_name(template_name, arg_types)
        name_token = Token(
            kind="identifier", spelling=mangled,
            span=sd.name_token.span, leading_trivia=sd.name_token.leading_trivia,
        )
        return StructDecl(name_token=name_token, fields=new_fields)

    def _substitute_implementation(self, impl: Implementation, subst: dict[str, str], template_name: str, arg_types: list[str]) -> Implementation:
        mangled = mangle_template_name(template_name, arg_types)
        new_name_tokens = [
            Token(kind="identifier", spelling=mangled, span=t.span, leading_trivia=t.leading_trivia)
            for t in impl.name_tokens
        ]
        new_methods: list[Method] = []
        for m in impl.methods:
            new_body: list[Token] = []
            for t in m.body_tokens:
                if t.spelling in subst:
                    new_body.append(Token(
                        kind="identifier", spelling=subst[t.spelling],
                        span=t.span, leading_trivia=t.leading_trivia,
                    ))
                else:
                    new_body.append(t)
            new_methods.append(Method(
                is_virtual=m.is_virtual, is_init=m.is_init, is_deinit=m.is_deinit,
                is_operator=m.is_operator, operator_token=m.operator_token,
                name_token=m.name_token, return_type_tokens=m.return_type_tokens,
                param_tokens=m.param_tokens, body_tokens=new_body,
            ))
        return Implementation(
            struct_token=impl.struct_token, is_foreign_struct=impl.is_foreign_struct,
            name_tokens=new_name_tokens,
            template_args=impl.template_args, methods=new_methods,
        )

    def _diag(self, code: str, msg: str):
        self.diagnostics.append(Diagnostic(code=code, message=msg))
