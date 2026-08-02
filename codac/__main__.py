import argparse
import os
import sys

from codac.diagnostics import Diagnostic, format_diagnostic
from codac.emit import Emitter
from codac.lower import Lowerer
from codac.modules import ModuleLoader
from codac.typesys import SemanticAnalyzer
from codac.specialize import Specializer


def main():
    parser = argparse.ArgumentParser(
        prog="codac",
        description="Coda-to-C compiler — translates .co files to C",
    )
    parser.add_argument("root", metavar="ROOT.co", help="Entry module")
    parser.add_argument("--out-dir", required=True, help="Output directory for generated .h and .c files")
    parser.add_argument("-I", "--include", dest="include_dirs", action="append", default=[], help="Search path for #import")
    parser.add_argument("--emit-deps", metavar="FILE", help="Write make-style dependency file")
    parser.add_argument("--keep-going", action="store_true", help="Attempt to continue after non-fatal errors")

    args = parser.parse_args()

    if not args.root.endswith(".co"):
        print(f"error: root module must have .co extension: {args.root}", file=sys.stderr)
        sys.exit(1)

    all_diagnostics: list[Diagnostic] = []

    loader = ModuleLoader(args.include_dirs)
    root_module = loader.load(args.root, all_diagnostics)
    all_diagnostics.extend(loader.diagnostics)
    all_modules = loader.all_modules

    if args.emit_deps:
        loader.write_deps(args.emit_deps, args.root, args.out_dir)

    analyzer = SemanticAnalyzer()
    for mod in all_modules:
        analyzer.analyze(mod)
    all_diagnostics.extend(analyzer.diagnostics)

    specializer = Specializer(analyzer)
    for mod in all_modules:
        specializer.collect_templates(mod)
    all_diagnostics.extend(specializer.diagnostics)

    for mod in all_modules:
        specializer.discover(mod)
    all_diagnostics.extend(specializer.diagnostics)

    for module_obj, str_decl, impl_decl in specializer.synthetic:
        if str_decl is not None:
            module_obj.top_level.append(str_decl)
        if impl_decl is not None:
            module_obj.top_level.append(impl_decl)

    lowerer = Lowerer(analyzer)
    for mod in all_modules:
        lowerer.lower(mod, finalize=False)
    lowerer.compute_virtual_layouts()

    emitter = Emitter(args.out_dir)
    for mod in all_modules:
        try:
            emitter.emit(mod, lowerer, analyzer)
        except Exception as e:
            print(f"emission error for {mod.path}: {e}", file=sys.stderr)
            sys.exit(1)

    for d in all_diagnostics:
        print(format_diagnostic(d), file=sys.stderr)

    if any(d.severity == "error" for d in all_diagnostics):
        sys.exit(1)


if __name__ == "__main__":
    main()
