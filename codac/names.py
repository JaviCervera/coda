import hashlib
import os


def mangle_type_name(type_name: str) -> str:
    result = []
    for ch in type_name:
        if ch.isalnum() or ch == "_":
            result.append(ch)
        elif ch == " ":
            result.append("_")
        elif ch == "*":
            result.append("_ptr")
        elif ch == "[":
            result.append("_arr")
        elif ch == "]":
            result.append("")
        elif ch == "<":
            result.append("__")
        elif ch == ">":
            result.append("")
        elif ch == ",":
            result.append("_")
        else:
            result.append(f"_{ord(ch):02x}")
    mangled = "".join(result)
    while mangled.startswith("_"):
        mangled = mangled[1:]
    while mangled.endswith("_"):
        mangled = mangled[:-1]
    return mangled


def mangle_template_name(template_name: str, args: list[str]) -> str:
    base = mangle_type_name(template_name)
    arg_part = "__".join(mangle_type_name(a) for a in args)
    mangled = f"coda_{base}__{arg_part}"
    if len(mangled) > 100:
        h = hashlib.sha1(mangled.encode()).hexdigest()[:8]
        mangled = mangled[:90] + "_" + h
    return mangled


def method_c_name(struct_name: str, method_name: str) -> str:
    if method_name.startswith("operator"):
        op_map = {
            "operator+": "operator_add",
            "operator-": "operator_sub",
            "operator*": "operator_mul",
            "operator/": "operator_div",
            "operator==": "operator_eq",
            "operator!=": "operator_ne",
            "operator<": "operator_lt",
            "operator<=": "operator_le",
            "operator>": "operator_gt",
            "operator>=": "operator_ge",
            "operator+=": "operator_add_assign",
            "operator-=": "operator_sub_assign",
            "operator*=": "operator_mul_assign",
            "operator/=": "operator_div_assign",
            "operator[]": "operator_index",
            "operator->": "operator_arrow",
        }
        method_name = op_map.get(method_name, method_name)
    return f"{struct_name}_{method_name}"


def impl_c_name(struct_name: str, method_name: str) -> str:
    safe_name = method_c_name(struct_name, method_name).removeprefix(f"{struct_name}_")
    return f"coda_{struct_name}_{safe_name}_impl"


def thunk_c_name(struct_name: str, method_name: str, base_name: str) -> str:
    return f"coda_{struct_name}_{method_name}_as_{base_name}"


def vtable_type_name(struct_name: str) -> str:
    return f"{struct_name}_vtable"


def vtable_instance_name(struct_name: str) -> str:
    return f"coda_{struct_name}_vtable"


def include_guard(path: str) -> str:
    guard = path.upper().replace(".", "_").replace("/", "_").replace("-", "_")
    return f"CODA_{guard}"


def include_path(module_path: str) -> str:
    root, _ = os.path.splitext(module_path)
    return root + ".h"
