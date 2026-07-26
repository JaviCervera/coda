from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass(frozen=True)
class Span:
    path: str
    offset: int
    line: int
    col: int


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: Optional[Span] = None
    notes: list[tuple[str, Optional[Span]]] = dataclasses.field(default_factory=list)
    severity: str = "error"  # "error" or "warning"


ERROR_CODES = {
    "E001": "malformed import",
    "E002": "import not found",
    "E003": "conditional import unsupported",
    "E010": "malformed implementation",
    "E011": "unknown implementation target",
    "E012": "duplicate implementation",
    "E013": "invalid init/deinit signature",
    "E014": "duplicate or colliding method name",
    "E020": "invalid inheritance layout",
    "E021": "foreign type cannot inherit or be virtual",
    "E022": "inheritance cycle",
    "E023": "invalid virtual override",
    "E030": "unknown method",
    "E031": "invalid method receiver",
    "E032": "invalid method argument list",
    "E040": "malformed template use",
    "E041": "template argument count",
    "E042": "recursive value layout",
    "E050": "unsupported operator",
    "E051": "invalid operator signature",
    "E052": "invalid operator operand",
    "E060": "Coda syntax in unsupported opaque C extension",
    "E070": "explicit deinit on automatic variable with scope cleanup",
    "E071": "discarded return value of type with deinit",
    "E072": "goto across variable with deinit",
}


def format_diagnostic(d: Diagnostic) -> str:
    parts = []
    if d.span:
        parts.append(f"{d.span.path}:{d.span.line}:{d.span.col}:")
    parts.append(f"{d.severity} {d.code}: {d.message}")
    result = " ".join(parts)
    for note_msg, note_span in d.notes:
        if note_span:
            result += f"\n{note_span.path}:{note_span.line}:{note_span.col}: note: {note_msg}"
        else:
            result += f"\nnote: {note_msg}"
    return result


class SourceMap:
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self._line_offsets = self._build_line_offsets(source)

    @staticmethod
    def _build_line_offsets(source: str) -> list[int]:
        offsets = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                offsets.append(i + 1)
        return offsets

    def offset_to_line_col(self, offset: int) -> tuple[int, int]:
        lo = 0
        hi = len(self._line_offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._line_offsets[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        line = lo + 1
        col = offset - self._line_offsets[lo] + 1
        return line, col

    def span(self, offset: int, length: int = 1) -> Span:
        line, col = self.offset_to_line_col(offset)
        return Span(path=self.path, offset=offset, line=line, col=col)

    def spans(self, offset: int, length: int) -> tuple[Span, Span]:
        return self.span(offset), self.span(offset + length - 1)
