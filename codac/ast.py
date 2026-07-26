from __future__ import annotations

import dataclasses
from typing import Optional, Union

from codac.diagnostics import Span


@dataclasses.dataclass(frozen=True)
class Token:
    kind: str
    spelling: str
    span: Span
    leading_trivia: str = ""


TOKEN_PRESERVED = object()


@dataclasses.dataclass
class SourceFile:
    path: str
    source: str
    tokens: list[Token] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Import:
    token: Token
    path: str
    resolved_path: Optional[str] = None


@dataclasses.dataclass
class FieldDecl:
    tokens: list[Token]


@dataclasses.dataclass
class StructDecl:
    name_token: Token
    fields: list[FieldDecl]
    template_params: list[Token] = dataclasses.field(default_factory=list)
    is_forward: bool = False
    base_name_token: Token | None = None


@dataclasses.dataclass
class Method:
    is_virtual: bool
    is_init: bool
    is_deinit: bool
    is_operator: bool
    operator_token: Optional[str]
    name_token: Token
    return_type_tokens: list[Token]
    param_tokens: list[list[Token]]
    body_tokens: list[Token]


@dataclasses.dataclass
class Implementation:
    struct_token: Token
    is_foreign_struct: bool
    name_tokens: list[Token]
    template_args: list[list[Token]]
    methods: list[Method]


@dataclasses.dataclass
class TemplateDecl:
    params: list[Token]
    body: Union[StructDecl, Implementation]


@dataclasses.dataclass
class Expr:
    kind: str
    token: Token
    children: list[Expr] = dataclasses.field(default_factory=list)
    value: Optional[str] = None


@dataclasses.dataclass
class Stmt:
    kind: str
    tokens: list[Token]
    children: list[Stmt] = dataclasses.field(default_factory=list)
    expr: Optional[Expr] = None
    var_name: str | None = None
    var_type: str | None = None
    has_init_call: bool = False
    var_init_arg_tokens: list[Token] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class TopLevelDecl:
    kind: str  # "include", "import", "struct", "implementation", "template", "preserved"
    token: Token
    body: Optional[Union[Import, StructDecl, Implementation, TemplateDecl]] = None
    preserved_tokens: list[Token] = dataclasses.field(default_factory=list)
    is_foreign: bool = False


@dataclasses.dataclass
class Module:
    path: str
    top_level: list[TopLevelDecl]
    imports: list[Import]
    errors: list[str] = dataclasses.field(default_factory=list)
