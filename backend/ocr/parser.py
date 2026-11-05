"""Turns the text an OCR engine read from a lab report into lab values.

OCR engines give either plain lines ("FBS  5.6  mmol/L  3.9 - 5.5") or table rows
("| FBS | 5.6 | mmol/L | 3.9 - 5.5 |", as the configured engine writes them: PaddleOCR-VL, whose
HTML tables engines.py turns into such rows; Chandra optional). For each line we look for a
label we know (backend/labs.py), the first number after it, and a unit. Reference ranges,
H/L flags and lines we do not understand are skipped: the patient reviews every value and types
in anything OCR missed, so it is better to skip a line than to guess.
"""

import re
from dataclasses import dataclass

from backend import labs

_NUMBER = re.compile(r"^\d[\d,]*(\.\d+)?$")
_FLAGS = {"h", "l", "high", "low", "*", "**", "hh", "ll", "(h)", "(l)", "a", "abnormal"}
_CELL_SPLIT = re.compile(r"\s*\|\s*|\t| {2,}")
_RANGE = re.compile(r"^\d[\d.,]*\s*[-–]\s*\d[\d.,]*$")


@dataclass(frozen=True)
class ParsedLine:
    test_code: str
    value: str
    unit: str


def _code_for(label: str) -> str | None:
    """Tries the label as written, without "(...)", and the text inside "(...)"."""
    candidates = [label, re.sub(r"\(.*?\)", " ", label), *re.findall(r"\((.*?)\)", label)]
    for candidate in candidates:
        try:
            return labs.test_code_for(candidate)
        except labs.MappingError:
            continue
    return None


def _split_value(token: str) -> str | None:
    """'5.6' -> '5.6'; '5.6H' or '5.6*' -> '5.6'; '<0.1' or text -> None."""
    token = token.strip()
    token = re.sub(r"(?i)(\*+|\(?[hl]\)?)$", "", token) if token[:1].isdigit() else token
    return token if _NUMBER.match(token) else None


def _unit_from(tokens: list[str]) -> str | None:
    """The first unit in tokens. Units may be written with spaces ('x 10^9/L'), so up to three
    tokens are joined. Flags and reference ranges before the unit are skipped."""
    for start in range(len(tokens)):
        if tokens[start].lower() in _FLAGS or _RANGE.match(tokens[start]):
            continue
        for length in (3, 2, 1):
            piece = " ".join(tokens[start:start + length])
            if len(tokens[start:start + length]) < length:
                continue
            try:
                labs.normalize_unit(piece)
                return piece
            except labs.MappingError:
                continue
    return None


def _from_cells(cells: list[str]) -> ParsedLine | None:
    """A table row: label in the first cell, then a value cell and a unit cell in any order."""
    code = _code_for(cells[0])
    if code is None:
        return None
    rest = cells[1:]
    for index, cell in enumerate(rest):
        parts = cell.split()
        value = _split_value(parts[0]) if parts else None
        if value is None:
            if cell.strip()[:1] in "<>":  # "<0.1": below what the lab can measure; skip the line
                return None
            continue
        unit = _unit_from(parts[1:]) or _unit_from(rest[index + 1:])
        return ParsedLine(code, value, unit) if unit else None
    return None


def _from_words(line: str) -> ParsedLine | None:
    """Plain text: try the longest label that is followed by a number."""
    tokens = line.replace(":", " ").replace("=", " ").split()
    for end in range(len(tokens) - 1, 0, -1):
        code = _code_for(" ".join(tokens[:end]))
        if code is None:
            continue
        if tokens[end][:1] in "<>":
            return None
        value = _split_value(tokens[end])
        if value is None:
            continue
        unit = _unit_from(tokens[end + 1:])
        return ParsedLine(code, value, unit) if unit else None
    return None


def parse_line(line: str) -> ParsedLine | None:
    line = line.strip().strip("|").strip()
    if not line or set(line) <= set("-|: "):  # empty or a markdown table border
        return None
    cells = [cell for cell in _CELL_SPLIT.split(line) if cell.strip()]
    if len(cells) >= 2:
        found = _from_cells(cells)
        if found:
            return found
    return _from_words(line)


@dataclass(frozen=True)
class Extraction:
    values: list[labs.MappedLab]
    skipped_lines: int  # lines with a known test that could not be read safely (bad unit, etc.)


def extract(text: str) -> Extraction:
    """All lab values in an OCR text, at most one per test (the first one found)."""
    values: dict[str, labs.MappedLab] = {}
    skipped = 0
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed is None:
            continue
        try:
            mapped = labs.convert(parsed.test_code, labs.parse_number(parsed.value), parsed.unit)
        except labs.MappingError:
            skipped += 1
            continue
        values.setdefault(mapped.test_code, mapped)
    return Extraction(values=list(values.values()), skipped_lines=skipped)
