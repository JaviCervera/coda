from __future__ import annotations

import os

from codac.ast import (
    Expr, Implementation, Method, Module, StructDecl, Token, TopLevelDecl,
)
from codac.lower import LoweredMethod, LoweredStruct, Lowerer
from codac.modules import ModuleLoader
from codac.names import (
    impl_c_name, include_guard, include_path, method_c_name,
    thunk_c_name, vtable_instance_name, vtable_type_name,
)
from codac.typesys import SemanticAnalyzer


class Emitter:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.emitted_modules: set[str] = set()
        self.analyzer: SemanticAnalyzer | None = None

    def emit(self, module: Module, lowerer: Lowerer, analyzer: SemanticAnalyzer):
        if module.path in self.emitted_modules:
            return
        self.emitted_modules.add(module.path)
        self.analyzer = analyzer

        module_name = os.path.splitext(os.path.basename(module.path))[0]
        guard = include_guard(module.path)

        h_lines: list[str] = []
        c_lines: list[str] = []

        self._emit_header(h_lines, module, guard, lowerer, analyzer)
        self._emit_source(c_lines, module, guard, module_name, lowerer, analyzer)

        h_path = os.path.join(self.out_dir, f"{module_name}.h")
        c_path = os.path.join(self.out_dir, f"{module_name}.c")

        os.makedirs(self.out_dir, exist_ok=True)
        with open(h_path, "w") as f:
            f.writelines(h_lines)
        with open(c_path, "w") as f:
            f.writelines(c_lines)

    def _emit_header(self, lines: list[str], module: Module, guard: str, lowerer: Lowerer, analyzer: SemanticAnalyzer):
        lines.append(f"#ifndef {guard}\n")
        lines.append(f"#define {guard}\n\n")

        for decl in module.top_level:
            if decl.kind == "include":
                text = decl.token.spelling
                lines.append(f"{text}\n")
            elif decl.kind == "import":
                if decl.body and decl.body.resolved_path:
                    h = include_path(decl.body.path)
                    lines.append(f'#include "{h}"\n')

        lines.append("\n")

        for decl in module.top_level:
            if decl.kind == "struct" and decl.body:
                sd = decl.body
                struct_name = sd.name_token.spelling
                ls = lowerer.structs.get(struct_name)

                lines.append(f"struct {struct_name} {{\n")
                if ls and ls.has_vtable:
                    st = analyzer.get_struct(struct_name)
                    if st is None or not st.base_name:
                        lines.append(f"    {VTABLE_PTR};\n")
                for field in (ls.fields if ls else []):
                    ft = field.type_str.rstrip(";").strip()
                    if ft:
                        lines.append(f"    {ft};\n")
                lines.append("};\n\n")

        for struct_name in sorted(self._get_vtable_types(module, lowerer)):
            lines.append(f"struct {vtable_type_name(struct_name)} {{\n")
            vt = lowerer.virtual_layout.vtable_types.get(struct_name)
            if vt:
                for mname, _ in vt.slots:
                    result_type = self._get_virtual_result_type(struct_name, mname)
                    lines.append(f"    {result_type} (*{mname})(struct {struct_name} *);\n")
            lines.append("};\n\n")

        for struct_name, methods in lowerer.methods.items():
            for lm in methods:
                params = self._format_params(lm, struct_name)
                lines.append(f"{lm.sig.result_type} {lm.c_name}({params});\n")
            lines.append("\n")

        lines.append(f"#endif /* {guard} */\n")

    def _emit_source(self, lines: list[str], module: Module, guard: str, module_name: str, lowerer: Lowerer, analyzer: SemanticAnalyzer):
        lines.append(f'#include "{module_name}.h"\n\n')

        for decl in module.top_level:
            if decl.kind == "preserved":
                for t in decl.preserved_tokens:
                    lines.append(t.spelling)
                lines.append("\n")

        for struct_name, methods in lowerer.methods.items():
            for lm in methods:
                params = self._format_params(lm, struct_name)
                lines.append(f"{lm.sig.result_type} {lm.impl_name}({params});\n")
        lines.append("\n")

        for struct_name in sorted(self._get_vtable_types(module, lowerer)):
            vt = lowerer.virtual_layout.vtable_types.get(struct_name)
            if vt:
                lines.append(f"const struct {vtable_type_name(struct_name)} {vtable_instance_name(struct_name)} = {{\n")
                for mname, impl in vt.slots:
                    lines.append(f"    .{mname} = {impl},\n")
                lines.append("};\n\n")

        for struct_name, methods in lowerer.methods.items():
            for lm in methods:
                params = self._format_params(lm, struct_name)
                lines.append(f"{lm.sig.result_type} {lm.impl_name}({params})")
                lines.append("{\n")
                lines.append("    (void)self;\n")
                body = self._get_body_text(struct_name, lm)
                if body:
                    lines.append(body)
                    if not body.endswith("\n"):
                        lines.append("\n")
                lines.append("}\n\n")

            for lm in methods:
                if lm.is_virtual:
                    params = self._format_params(lm, struct_name)
                    lines.append(f"{lm.sig.result_type} {lm.c_name}({params})")
                    lines.append("{\n")
                    lines.append("    (void)self;\n")
                    vt = vtable_type_name(struct_name)
                    vptr = self._vptr_path(struct_name)
                    lines.append(f"    const struct {vt} *vt = (const struct {vt} *){vptr};\n")
                    lines.append(f"    return vt->{lm.slot_name}(self);\n")
                    lines.append("}\n\n")

            for lm in methods:
                if not lm.is_virtual:
                    params = self._format_params(lm, struct_name)
                    lines.append(f"{lm.sig.result_type} {lm.c_name}({params})")
                    lines.append("{\n")
                    lines.append("    (void)self;\n")
                    body = self._get_body_text(struct_name, lm)
                    if body:
                        lines.append(body)
                        if not body.endswith("\n"):
                            lines.append("\n")
                    else:
                        arg_names = ["self"]
                        for pt in lm.sig.param_types:
                            if pt == "void":
                                continue
                            parts = pt.split()
                            if parts:
                                arg_names.append(parts[-1])
                        lines.append(f"    return {lm.impl_name}({', '.join(arg_names)});\n")
                    ls = lowerer.structs.get(struct_name)
                    if lm.sig.is_init and ls and ls.has_vtable:
                        vptr = self._vptr_path(struct_name)
                        lines.append(f"    {vptr} = &{vtable_instance_name(struct_name)};\n")
                    lines.append("}\n\n")

        for thunk_name, struct_name, mname, base_name in lowerer.virtual_layout.thunks:
            lines.append(f"void {thunk_name}(struct {base_name} *base)")
            lines.append("{\n")
            lines.append(f"    {impl_c_name(struct_name, mname)}((struct {struct_name} *)base);\n")
            lines.append("}\n\n")

    def _get_virtual_result_type(self, struct_name: str, slot_name: str) -> str:
        if self.analyzer:
            impl_info = self.analyzer.get_implementation(struct_name)
            if impl_info:
                for mname, msig in impl_info.methods.items():
                    expected = method_c_name("", mname).removeprefix("_")
                    if expected == slot_name:
                        return msig.result_type
        return "void"

    def _format_params(self, lm: LoweredMethod, struct_name: str) -> str:
        parts = [f"struct {struct_name} *self"]
        for pt in lm.sig.param_types:
            if pt == "void":
                continue
            parts.append(pt)
        return ", ".join(parts) if parts else "void"

    def _get_body_text(self, struct_name: str, lm: LoweredMethod) -> str:
        if lm.lowered_body is not None:
            return lm.lowered_body
        body_tokens: list = []
        if lm.sig.ast and lm.sig.ast.body_tokens:
            body_tokens = lm.sig.ast.body_tokens
        elif self.analyzer:
            impl_info = self.analyzer.get_implementation(struct_name)
            if impl_info and lm.sig.name in impl_info.method_asts:
                ast = impl_info.method_asts[lm.sig.name]
                if ast.body_tokens:
                    body_tokens = ast.body_tokens
        if not body_tokens:
            return ""
        result = []
        for t in body_tokens:
            result.append(t.leading_trivia)
            result.append(t.spelling)
        return "".join(result)

    def _vptr_path(self, struct_name: str) -> str:
        if self.analyzer:
            st = self.analyzer.get_struct(struct_name)
            if st and st.base_name and st.fields:
                first_field = st.fields[0]
                name_parts = [t.spelling for t in first_field.tokens if t.kind == "identifier"]
                if name_parts:
                    field_name = name_parts[-1]
                    return f"self->{field_name}.__coda_vptr"
        return "self->__coda_vptr"

    def _get_vtable_types(self, module: Module, lowerer: Lowerer) -> set[str]:
        names: set[str] = set()
        for struct_name in lowerer.virtual_layout.vtable_types:
            names.add(struct_name)
            st = lowerer.analyzer.get_struct(struct_name)
            if st and st.base_name:
                names.add(st.base_name)
        return names


VTABLE_PTR = "const void *__coda_vptr"
