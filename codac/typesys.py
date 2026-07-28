from __future__ import annotations

import dataclasses
from typing import Optional

from codac.ast import (
    Expr, FieldDecl, Implementation, Method, Module, StructDecl, Token, TopLevelDecl,
)
from codac.diagnostics import Diagnostic, Span
from codac.names import method_c_name


@dataclasses.dataclass(frozen=True)
class StructType:
    name: str
    fields: tuple[FieldDecl, ...]
    is_coda_owned: bool
    has_virtual: bool = False
    module_path: str = ""
    base_name: str | None = None


@dataclasses.dataclass(frozen=True)
class MethodSig:
    name: str
    result_type: str
    param_types: tuple[str, ...]
    is_init: bool
    is_deinit: bool
    is_virtual: bool
    is_override: bool
    is_operator: bool
    operator_token: str | None
    ast: Method | None = None


@dataclasses.dataclass(frozen=True)
class VirtualSlot:
    slot_index: int
    declaring_type: str
    method_name: str
    signature: MethodSig


@dataclasses.dataclass
class ImplementationInfo:
    struct_name: str
    base_name: str | None
    methods: dict[str, MethodSig]
    virtual_slots: list[VirtualSlot]
    is_foreign: bool
    method_asts: dict[str, Method]


OPERATOR_NAMES = frozenset({
    "operator+", "operator-", "operator*", "operator/",
    "operator==", "operator!=", "operator<", "operator<=", "operator>", "operator>=",
    "operator+=", "operator-=", "operator*=", "operator/=",
    "operator[]", "operator->",
})


class SemanticAnalyzer:
    def __init__(self):
        self.structs: dict[str, StructType] = {}
        self.implementations: dict[str, ImplementationInfo] = {}
        self.diagnostics: list[Diagnostic] = []
        self.methods: dict[str, dict[str, MethodSig]] = {}

    def analyze(self, module: Module):
        for decl in module.top_level:
            if decl.kind == "struct" and decl.body:
                self._register_struct(decl.body, module.path)
            elif decl.kind == "template" and decl.body:
                if isinstance(decl.body.body, StructDecl):
                    self._register_struct(decl.body.body, module.path)
        for decl in module.top_level:
            if decl.kind == "impl" and decl.body:
                self._register_implementation(decl.body, module.path)
            elif decl.kind == "template" and decl.body:
                if isinstance(decl.body.body, Implementation):
                    self._register_implementation(decl.body.body, module.path)

    def _register_struct(self, sd: StructDecl, module_path: str):
        name = sd.name_token.spelling
        base_name: str | None = None
        fields = list(sd.fields)

        if sd.base_name_token:
            base_name = sd.base_name_token.spelling
            for fd in fields:
                ids = [t for t in fd.tokens if t.kind == "identifier"]
                if ids and ids[-1].spelling == "base":
                    self._diag("E020", f"struct '{name}' inherits from '{base_name}' but has an explicit 'base' field",
                               span=sd.base_name_token.span)
                    break
            else:
                base_token = Token("keyword", "struct", sd.base_name_token.span)
                name_token = Token("identifier", base_name, sd.base_name_token.span)
                field_name = Token("identifier", "base", sd.base_name_token.span)
                semi = Token(";", ";", sd.base_name_token.span)
                fields.insert(0, FieldDecl(tokens=[base_token, name_token, field_name, semi]))

        st = StructType(
            name=name,
            fields=tuple(fields),
            is_coda_owned=True,
            module_path=module_path,
            base_name=base_name,
        )
        self.structs[name] = st

    def _register_implementation(self, impl: Implementation, module_path: str):
        struct_name = "".join(t.spelling for t in impl.name_tokens)
        st = self.structs.get(struct_name)
        base_name = st.base_name if st else None

        info = ImplementationInfo(
            struct_name=struct_name,
            base_name=base_name,
            methods={},
            virtual_slots=[],
            is_foreign=impl.is_foreign_struct,
            method_asts={},
        )

        for m in impl.methods:
            sig = self._method_to_sig(m)
            if sig is None:
                continue

            if sig.name in info.methods:
                self._diag("E014", f"duplicate method name '{sig.name}' in implementation of '{struct_name}'",
                           span=m.name_token.span)
                continue

            self._validate_method(sig, m, struct_name, base_name)
            info.methods[sig.name] = sig
            info.method_asts[sig.name] = m

        if struct_name not in self.implementations:
            self.implementations[struct_name] = info

    def _method_to_sig(self, m: Method) -> MethodSig | None:
        name = m.name_token.spelling
        if m.is_operator and m.operator_token:
            name = f"operator{m.operator_token}"
        result_type = None
        if not m.is_init and not m.is_deinit:
            result_type = " ".join(t.spelling for t in m.return_type_tokens) or "void"
        else:
            result_type = "void"
        param_types = tuple(
            " ".join(t.spelling for t in param) if param else "void"
            for param in m.param_tokens
        )
        return MethodSig(
            name=name, result_type=result_type, param_types=param_types,
            is_init=m.is_init, is_deinit=m.is_deinit,
            is_virtual=m.is_virtual, is_override=m.is_override,
            is_operator=m.is_operator,
            operator_token=m.operator_token, ast=m,
        )

    def _validate_method(self, sig: MethodSig, m: Method, struct_name: str, base_name: str | None):
        if sig.is_init and sig.param_types and sig.param_types[0] == "void":
            pass
        if sig.is_deinit:
            if not (len(sig.param_types) == 0 or (len(sig.param_types) == 1 and sig.param_types[0] == "void")):
                self._diag("E013", f"deinit must have (void) signature",
                           span=m.name_token.span)

        if sig.is_operator:
            if sig.name not in OPERATOR_NAMES:
                self._diag("E050", f"unsupported operator '{sig.name}'",
                           span=m.name_token.span)

        if base_name and base_name in self.implementations:
            base = self.implementations[base_name]
            if sig.name in base.methods:
                base_sig = base.methods[sig.name]
                if base_sig.is_virtual:
                    if sig.is_override:
                        pass
                    elif sig.is_virtual:
                        self._diag("E024", f"use 'override' instead of 'virtual' for method '{sig.name}'",
                                   span=m.name_token.span)
                    else:
                        self._diag("E026", f"overriding method '{sig.name}' must use 'override' keyword",
                                   span=m.name_token.span)
                else:
                    if sig.is_virtual:
                        self._diag("E023", f"cannot override non-virtual method '{sig.name}'",
                                   span=m.name_token.span)
                    elif sig.is_override:
                        self._diag("E025", f"no virtual method '{sig.name}' to override",
                                   span=m.name_token.span)
            else:
                if sig.is_override:
                    self._diag("E025", f"no virtual method '{sig.name}' to override",
                               span=m.name_token.span)
        else:
            if sig.is_override:
                name = base_name if base_name else "(no base)"
                self._diag("E025", f"no virtual method '{sig.name}' to override",
                           span=m.name_token.span)

    def _diag(self, code: str, msg: str, span: Span | None = None):
        self.diagnostics.append(Diagnostic(code=code, message=msg, span=span))

    def get_struct(self, name: str) -> StructType | None:
        return self.structs.get(name)

    def get_implementation(self, name: str) -> ImplementationInfo | None:
        return self.implementations.get(name)

    def has_deinit(self, type_name: str) -> bool:
        impl = self.implementations.get(type_name)
        if impl is not None and "deinit" in impl.methods:
            return True
        st = self.structs.get(type_name)
        if st is not None and st.base_name:
            return self.has_deinit(st.base_name)
        return False
