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
        description="Coda-to-C compiler — translates .cod files to C",
    )
    parser.add_argument("root", metavar="ROOT.cod", help="Entry module")
    parser.add_argument("--out-dir", required=True, help="Output directory for generated .h and .c files")
    parser.add_argument("-I", "--include", dest="include_dirs", action="append", default=[], help="Search path for #import")
    parser.add_argument("--emit-deps", metavar="FILE", help="Write make-style dependency file")
    parser.add_argument("--keep-going", action="store_true", help="Attempt to continue after non-fatal errors")

    args = parser.parse_args()

    if not args.root.endswith(".cod"):
        print(f"error: root module must have .cod extension: {args.root}", file=sys.stderr)
        sys.exit(1)

    all_diagnostics: list[Diagnostic] = []

    loader = ModuleLoader(args.include_dirs)
    module = loader.load(args.root, all_diagnostics)
    all_diagnostics.extend(loader.diagnostics)

    if args.emit_deps:
        loader.write_deps(args.emit_deps, args.root, args.out_dir)

    analyzer = SemanticAnalyzer()
    analyzer.analyze(module)
    all_diagnostics.extend(analyzer.diagnostics)

    specializer = Specializer(analyzer)
    specializer.collect_templates(module)
    all_diagnostics.extend(specializer.diagnostics)

    lowerer = Lowerer(analyzer)
    lowerer.lower(module)

    emitter = Emitter(args.out_dir)
    try:
        emitter.emit(module, lowerer, analyzer)
    except Exception as e:
        print(f"emission error: {e}", file=sys.stderr)
        sys.exit(1)

    for d in all_diagnostics:
        print(format_diagnostic(d), file=sys.stderr)

    if any(d.severity == "error" for d in all_diagnostics):
        sys.exit(1)


if __name__ == "__main__":
    main()
