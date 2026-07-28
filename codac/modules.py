from __future__ import annotations

import os
from typing import Optional

from codac.ast import Module
from codac.diagnostics import Diagnostic
from codac.parser import Parser


class ModuleLoader:
    def __init__(self, include_dirs: list[str] | None = None):
        self.include_dirs = include_dirs or []
        self.loaded: dict[str, Module] = {}
        self.loading: set[str] = set()
        self.diagnostics: list[Diagnostic] = []

    @property
    def all_modules(self) -> list[Module]:
        return list(self.loaded.values())

    def load(self, path: str, diagnostics: list[Diagnostic] | None = None) -> Module:
        if path in self.loaded:
            return self.loaded[path]

        if path in self.loading:
            self._diag("E022", f"inheritance cycle detected involving {path}")
            return Module(path=path, top_level=[], imports=[])

        abs_path = self._resolve_path(path)
        if abs_path is None:
            self._diag("E002", f"import not found: {path}")
            return Module(path=path, top_level=[], imports=[])

        self.loading.add(path)

        try:
            with open(abs_path, "r") as f:
                source = f.read()
        except FileNotFoundError:
            self._diag("E002", f"import not found: {abs_path}")
            self.loading.discard(path)
            return Module(path=path, top_level=[], imports=[])

        parser = Parser(abs_path, source)
        module = parser.parse()
        module.path = abs_path
        self.diagnostics.extend(parser.diagnostics)

        if diagnostics is not None:
            diagnostics.extend(parser.diagnostics)

        for imp in module.imports:
            resolved = self._resolve_import_path(imp.path, abs_path)
            if resolved:
                imp.resolved_path = resolved
                dep_module = self.load(resolved, diagnostics)
                if dep_module:
                    pass
            else:
                self._diag("E002", f"import not found: {imp.path}", span=imp.token.span)

        self.loaded[path] = module
        self.loading.discard(path)
        return module

    def _resolve_path(self, path: str) -> str | None:
        if os.path.isabs(path):
            return path if os.path.exists(path) else None
        for base in self.include_dirs:
            candidate = os.path.join(base, path)
            if os.path.exists(candidate):
                return os.path.abspath(candidate)
        return None if not os.path.exists(path) else os.path.abspath(path)

    def _resolve_import_path(self, import_path: str, current_path: str) -> str | None:
        current_dir = os.path.dirname(current_path)
        local = os.path.join(current_dir, import_path)
        if os.path.exists(local):
            return os.path.abspath(local)
        for base in self.include_dirs:
            candidate = os.path.join(base, import_path)
            if os.path.exists(candidate):
                return os.path.abspath(candidate)
        return None

    def write_deps(self, dep_file: str, root_path: str, out_dir: str):
        out_targets = []
        for mod_path in self.loaded:
            mod_name = os.path.splitext(os.path.basename(mod_path))[0]
            out_targets.append(os.path.join(out_dir, f"{mod_name}.c"))
            out_targets.append(os.path.join(out_dir, f"{mod_name}.h"))
        all_sources = sorted(self.loaded.keys())
        target_str = " ".join(sorted(out_targets))
        dep_str = f"{target_str}: \\\n"
        for p in all_sources:
            dep_str += f"  {p} \\\n"
        dep_str += "\n"
        with open(dep_file, "w") as f:
            f.write(dep_str)

    def _diag(self, code: str, msg: str, span=None):
        d = Diagnostic(code=code, message=msg, span=span)
        self.diagnostics.append(d)
