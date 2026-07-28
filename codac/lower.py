from __future__ import annotations

from codac.ast import (
    Expr, Implementation, Method, Module, StructDecl, Token, TopLevelDecl,
)
from codac.expr import lower_method_body
from codac.names import (
    impl_c_name, method_c_name, thunk_c_name, vtable_instance_name, vtable_type_name,
)
from codac.typesys import ImplementationInfo, MethodSig, SemanticAnalyzer, StructType, VirtualSlot


VTABLE_PTR = "const void *__coda_vptr"


class LoweredField:
    def __init__(self, name: str, type_str: str, tokens: list[Token] | None = None):
        self.name = name
        self.type_str = type_str
        self.tokens = tokens


class LoweredStruct:
    def __init__(self, name: str):
        self.name = name
        self.fields: list[LoweredField] = []
        self.has_vtable = False
        self.is_virtual_root = False
        self.base_name: str | None = None


class LoweredMethod:
    def __init__(self, c_name: str, struct_name: str, sig: MethodSig):
        self.c_name = c_name
        self.struct_name = struct_name
        self.sig = sig
        self.is_virtual = sig.is_virtual or sig.is_override
        self.impl_name = impl_c_name(struct_name, sig.name)
        self.thunk_name: str | None = None
        self.slot_name: str = sig.name
        self.lowered_body: str | None = None



class LoweredVTable:
    def __init__(self, struct_name: str):
        self.struct_name = struct_name
        self.slots: list[tuple[str, str]] = []  # (method_name, impl_c_name)


class VirtualLayout:
    def __init__(self):
        self.vtable_types: dict[str, LoweredVTable] = {}
        self.thunks: list[tuple[str, str, str, str]] = []  # (thunk_name, struct, method, base)


class Lowerer:
    def __init__(self, analyzer: SemanticAnalyzer):
        self.analyzer = analyzer
        self.structs: dict[str, LoweredStruct] = {}
        self.methods: dict[str, list[LoweredMethod]] = {}
        self.virtual_layout = VirtualLayout()

    def lower(self, module: Module, finalize: bool = True) -> list[TopLevelDecl]:
        lowered: list[TopLevelDecl] = []
        for decl in module.top_level:
            if decl.kind == "struct" and decl.body:
                lowered_struct = self._lower_struct(decl.body)
                self.structs[decl.body.name_token.spelling] = lowered_struct
                lowered.append(decl)
            elif decl.kind == "impl" and decl.body:
                self._lower_implementation(decl.body)
                lowered.append(decl)
            elif decl.kind == "function":
                if decl.body_tokens:
                    decl.lowered_body = lower_method_body(
                        decl.body_tokens, "", self.analyzer
                    )
                lowered.append(decl)
            elif decl.kind in ("include", "import", "preserved"):
                lowered.append(decl)
            elif decl.kind == "template" and decl.body:
                lowered.append(decl)
        if finalize:
            self.compute_virtual_layouts()
        return lowered

    def _lower_struct(self, sd: StructDecl) -> LoweredStruct:
        ls = LoweredStruct(name=sd.name_token.spelling)
        st = self.analyzer.get_struct(ls.name)
        field_decls = st.fields if st else sd.fields
        for field in field_decls:
            token_str = " ".join(t.spelling for t in field.tokens) if field.tokens else ""
            if token_str.strip():
                first_token = field.tokens[0]
                ls.fields.append(LoweredField(
                    name=first_token.spelling, type_str=token_str, tokens=field.tokens,
                ))
        return ls

    def _lower_implementation(self, impl: Implementation):
        struct_name = "".join(t.spelling for t in impl.name_tokens)
        info = self.analyzer.get_implementation(struct_name)
        if info is None:
            return

        if struct_name not in self.methods:
            self.methods[struct_name] = []

        for m in impl.methods:
            sig = info.methods.get(m.name_token.spelling)
            if sig is None:
                sig = self._method_to_sig(m)
            cname = method_c_name(struct_name, m.name_token.spelling)
            ls = LoweredMethod(
                c_name=cname,
                struct_name=struct_name,
                sig=sig,
            )
            ls.slot_name = cname.removeprefix(f"{struct_name}_")
            if m.body_tokens:
                ls.lowered_body = lower_method_body(m.body_tokens, struct_name, self.analyzer)
            self.methods[struct_name].append(ls)
            if sig and (sig.is_virtual or sig.is_override):
                self._register_virtual_slot(struct_name, sig, ls)

    def _register_virtual_slot(self, struct_name: str, sig: MethodSig, ls: LoweredMethod):
        vt = self.virtual_layout.vtable_types.get(struct_name)
        if vt is None:
            vt = LoweredVTable(struct_name=struct_name)
            self.virtual_layout.vtable_types[struct_name] = vt
        vt.slots.append((ls.slot_name, ls.impl_name))

    def compute_virtual_layouts(self):
        for struct_name, info in self.analyzer.implementations.items():
            if not info.virtual_slots and not any(
                m.is_virtual or m.is_override for m in info.methods.values()
            ):
                continue

            if struct_name not in self.structs:
                continue

            ls = self.structs[struct_name]
            ls.has_vtable = True

            st = self.analyzer.get_struct(struct_name)
            base_name = st.base_name if st else None
            if base_name and base_name in self.structs:
                if not self.structs[base_name].has_vtable:
                    self.structs[base_name].has_vtable = True
                    self.structs[base_name].is_virtual_root = True

            for mname, msig in info.methods.items():
                if (msig.is_virtual or msig.is_override) and base_name and base_name in self.analyzer.implementations:
                    base_info = self.analyzer.implementations[base_name]
                    if mname in base_info.methods and base_info.methods[mname].is_virtual:
                        cname = method_c_name(struct_name, mname)
                        impl = impl_c_name(struct_name, mname)
                        thunk = thunk_c_name(struct_name, mname, base_name)
                        self.virtual_layout.thunks.append((thunk, struct_name, mname, base_name))

        self._inject_vptr_fields()

    def _inject_vptr_fields(self):
        for name, ls in self.structs.items():
            st = self.analyzer.get_struct(name)
            if st is None:
                continue
            if st.base_name and st.base_name in self.structs:
                base_ls = self.structs[st.base_name]
                if base_ls.has_vtable:
                    ls.has_vtable = True

    def _method_to_sig(self, m: Method) -> MethodSig:
        from codac.typesys import MethodSig
        name = m.name_token.spelling
        result_type = "void"
        if not m.is_init and not m.is_deinit:
            result_type = " ".join(t.spelling for t in m.return_type_tokens) or "void"
        param_types = tuple(
            " ".join(t.spelling for t in p) if p else "void" for p in m.param_tokens
        )
        return MethodSig(
            name=name, result_type=result_type, param_types=param_types,
            is_init=m.is_init, is_deinit=m.is_deinit,
            is_virtual=m.is_virtual, is_operator=m.is_operator,
            operator_token=m.operator_token, ast=m,
        )
