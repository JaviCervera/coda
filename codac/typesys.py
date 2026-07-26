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


@dataclasses.dataclass(frozen=True)
class MethodSig:
    name: str
    result_type: str
    param_types: tuple[str, ...]
    is_init: bool
    is_deinit: bool
    is_virtual: bool
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
            self._analyze_decl(decl, module.path)

    def _analyze_decl(self, decl: TopLevelDecl, module_path: str):
        if decl.kind == "struct" and decl.body:
            self._register_struct(decl.body, module_path)
        elif decl.kind == "implementation" and decl.body:
            self._register_implementation(decl.body, module_path)
        elif decl.kind == "template" and decl.body:
            if isinstance(decl.body.body, StructDecl):
                self._register_struct(decl.body.body, module_path)
            elif isinstance(decl.body.body, Implementation):
                self._register_implementation(decl.body.body, module_path)

    def _register_struct(self, sd: StructDecl, module_path: str):
        name = sd.name_token.spelling
        st = StructType(
            name=name,
            fields=tuple(sd.fields),
            is_coda_owned=True,
            module_path=module_path,
        )
        self.structs[name] = st

    def _register_implementation(self, impl: Implementation, module_path: str):
        struct_name = "".join(t.spelling for t in impl.name_tokens)
        base_name = "".join(t.spelling for t in impl.base_name_tokens) if impl.base_name_tokens else None

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
            result_type = "".join(t.spelling for t in m.return_type_tokens) or "void"
        else:
            result_type = "void"
        param_types = tuple(
            " ".join(t.spelling for t in param) if param else "void"
            for param in m.param_tokens
        )
        return MethodSig(
            name=name, result_type=result_type, param_types=param_types,
            is_init=m.is_init, is_deinit=m.is_deinit,
            is_virtual=m.is_virtual, is_operator=m.is_operator,
            operator_token=m.operator_token, ast=m,
        )

    def _validate_method(self, sig: MethodSig, m: Method, struct_name: str, base_name: str | None):
        if sig.is_init and sig.param_types and sig.param_types[0] == "void":
            pass
        if sig.is_deinit:
            if not (len(sig.param_types) == 1 and sig.param_types[0] == "void"):
                self._diag("E013", f"deinit must have (void) signature",
                           span=m.name_token.span)

        if sig.is_operator:
            if sig.name not in OPERATOR_NAMES:
                self._diag("E050", f"unsupported operator '{sig.name}'",
                           span=m.name_token.span)

        if base_name and sig.is_virtual:
            if base_name in self.implementations:
                base = self.implementations[base_name]
                if sig.name in base.methods:
                    base_sig = base.methods[sig.name]
                    if not base_sig.is_virtual:
                        self._diag("E023", f"cannot override non-virtual method '{sig.name}'",
                                   span=m.name_token.span)

    def _diag(self, code: str, msg: str, span: Span | None = None):
        self.diagnostics.append(Diagnostic(code=code, message=msg, span=span))

    def get_struct(self, name: str) -> StructType | None:
        return self.structs.get(name)

    def get_implementation(self, name: str) -> ImplementationInfo | None:
        return self.implementations.get(name)
