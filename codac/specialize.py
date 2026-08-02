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
        self.specializing: set[TemplateKey] = set()
        # name -> (param names, struct decl, implementation | None)
        self.templates: dict[str, tuple[list[str], StructDecl, Implementation | None]] = {}
        # template name -> Module that declares it (emission target)
        self.template_module: dict[str, Module] = {}
        # (module, struct decl, impl decl) to append for emission
        self.synthetic: list[tuple[Module, TopLevelDecl, TopLevelDecl | None]] = []
        self.diagnostics: list[Diagnostic] = []

    def collect_templates(self, module: Module):
        for name, sd in self.analyzer.templates.items():
            params = [p.spelling for p in sd.template_params]
            if name not in self.templates:
                self.templates[name] = (params, sd, None)
                self.template_module[name] = module
        for name, impl in self.analyzer.template_impls.items():
            if name in self.templates:
                params, sd, _ = self.templates[name]
                self.templates[name] = (params, sd, impl)

    def discover(self, module: Module):
        for decl in module.top_level:
            if decl.kind == "struct" and decl.body:
                if decl.body.template_params:
                    continue
                self._scan_struct(decl.body, module.path)
            elif decl.kind == "impl" and decl.body:
                if "".join(t.spelling for t in decl.body.name_tokens) in self.templates:
                    continue
                self._scan_implementation(decl.body, module.path)
            elif decl.kind == "function":
                self._scan_tokens(decl.head_tokens, module.path)
                if decl.body_tokens:
                    self._scan_tokens(decl.body_tokens, module.path)
            elif decl.kind == "preserved":
                self._scan_tokens(decl.preserved_tokens, module.path)

    def _scan_struct(self, sd: StructDecl, module_path: str):
        for field in sd.fields:
            self._scan_tokens(field.tokens, module_path)

    def _scan_implementation(self, impl: Implementation, module_path: str):
        for m in impl.methods:
            self._scan_tokens(m.return_type_tokens, module_path)
            for p in m.param_tokens:
                self._scan_tokens(p, module_path)
            self._scan_tokens(m.body_tokens, module_path)

    def _scan_tokens(self, tokens: list[Token], module_path: str, cycle_check: bool = False):
        i = 0
        n = len(tokens)
        while i < n:
            t = tokens[i]
            if t.kind == "identifier" and t.spelling in self.templates:
                j = i + 1
                if j < n and tokens[j].spelling == "<":
                    end = self._find_matching_bracket(tokens, j)
                    if end is not None:
                        arg_groups = self._split_args(tokens, j + 1, end)
                        arg_types = [self._resolve_arg(g, module_path) for g in arg_groups]
                        key = TemplateKey(template_name=t.spelling, arg_types=tuple(arg_types))
                        if (
                            cycle_check
                            and key in self.specializing
                            and not self._is_pointer_member(tokens, end)
                        ):
                            self._diag(
                                "E042",
                                f"recursive by-value specialization of '{key.mangled_name()}'",
                            )
                        else:
                            self.specialize(t.spelling, arg_types, module_path)
                        i = end + 1
                        continue
            i += 1

    def _is_pointer_member(self, tokens: list[Token], close_idx: int) -> bool:
        for k in range(close_idx + 1, len(tokens)):
            if tokens[k].spelling == "*":
                return True
            if tokens[k].kind == "identifier":
                return False
        return False

    def _find_matching_bracket(self, tokens: list[Token], open_idx: int) -> int | None:
        depth = 0
        for k in range(open_idx, len(tokens)):
            if tokens[k].spelling == "<":
                depth += 1
            elif tokens[k].spelling == ">":
                depth -= 1
                if depth == 0:
                    return k
        return None

    def _split_args(self, tokens: list[Token], start: int, end: int) -> list[list[Token]]:
        args: list[list[Token]] = []
        current: list[Token] = []
        depth = 0
        for k in range(start, end):
            t = tokens[k]
            if t.spelling == "<":
                depth += 1
                current.append(t)
            elif t.spelling == ">":
                depth -= 1
                current.append(t)
            elif t.spelling == "," and depth == 0:
                args.append(current)
                current = []
            else:
                current.append(t)
        if current:
            args.append(current)
        return args

    def _instantiate(self, tmpl_name: str, arg_tokens: list[list[Token]], module_path: str):
        arg_types: list[str] = []
        for group in arg_tokens:
            arg_types.append(self._resolve_arg(group, module_path))
        self.specialize(tmpl_name, arg_types, module_path)

    def _resolve_arg(self, tokens: list[Token], module_path: str) -> str:
        if (
            len(tokens) >= 3
            and tokens[0].kind == "identifier"
            and tokens[0].spelling in self.templates
            and tokens[1].spelling == "<"
        ):
            pos_closing = self._find_matching_bracket(tokens, 1)
            if pos_closing is not None:
                inner = self._split_args(tokens, 2, pos_closing)
                self._instantiate(tokens[0].spelling, inner, module_path)
                return mangle_template_name(tokens[0].spelling, [self._resolve_arg(g, module_path) for g in inner])
        return " ".join(t.spelling for t in tokens).strip()

    def specialize(self, template_name: str, arg_types: list[str], module_path: str = "") -> SpecializedStruct | None:
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

        if key in self.specializing:
            self._diag("E042", f"recursive by-value specialization of '{key.mangled_name()}'")
            return None

        self.specializing.add(key)

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

        if specialized_struct:
            self.analyzer.register_specialized_struct(specialized_struct, module_path)
        if specialized_impl:
            self.analyzer.register_specialized_impl(specialized_impl, module_path)

        self._discover_nested(specialized_struct, specialized_impl, module_path, key)

        self.specializing.discard(key)

        str_decl = TopLevelDecl(kind="struct", token=specialized_struct.name_token, body=specialized_struct)
        impl_decl = None
        if specialized_impl:
            impl_decl = TopLevelDecl(kind="impl", token=specialized_impl.struct_token, body=specialized_impl)
        target = self.template_module.get(template_name)
        if target is not None:
            self.synthetic.append((target, str_decl, impl_decl))

        return result

    def _discover_nested(self, sd: StructDecl | None, impl: Implementation | None, module_path: str, current: TemplateKey):
        if sd:
            for field in sd.fields:
                self._scan_tokens(field.tokens, module_path, cycle_check=True)
        if impl:
            for m in impl.methods:
                self._scan_tokens(m.return_type_tokens, module_path)
                for p in m.param_tokens:
                    self._scan_tokens(p, module_path)

    def _substitute_tokens(self, tokens: list[Token], subst: dict[str, str]) -> list[Token]:
        new_tokens: list[Token] = []
        for t in tokens:
            if t.kind == "identifier" and t.spelling in subst:
                new_tokens.append(Token(
                    kind="identifier", spelling=subst[t.spelling],
                    span=t.span, leading_trivia=t.leading_trivia,
                ))
            else:
                new_tokens.append(t)
        return new_tokens

    def _substitute_struct(self, sd: StructDecl, subst: dict[str, str], template_name: str, arg_types: list[str]) -> StructDecl:
        new_fields: list[FieldDecl] = []
        for field in sd.fields:
            new_fields.append(FieldDecl(tokens=self._substitute_tokens(field.tokens, subst)))

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
            new_methods.append(Method(
                is_virtual=m.is_virtual, is_override=m.is_override,
                is_init=m.is_init, is_deinit=m.is_deinit,
                is_operator=m.is_operator, operator_token=m.operator_token,
                is_const=m.is_const,
                name_token=m.name_token,
                return_type_tokens=self._substitute_tokens(m.return_type_tokens, subst),
                param_tokens=[self._substitute_tokens(p, subst) for p in m.param_tokens],
                body_tokens=self._substitute_tokens(m.body_tokens, subst),
            ))
        return Implementation(
            struct_token=impl.struct_token, is_foreign_struct=impl.is_foreign_struct,
            name_tokens=new_name_tokens,
            template_args=impl.template_args, methods=new_methods,
        )

    def _diag(self, code: str, msg: str):
        self.diagnostics.append(Diagnostic(code=code, message=msg))