"""Small JSON fixture loader for controlled local tests."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LoadedJsonFixture:
    """Parsed fixture payload and whether comments were tolerated."""

    data: Any
    tolerant_parsing_used: bool = False


def _strip_line_comments(text: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def load_json_fixture(path: str | Path, *, allow_comments: bool = False) -> LoadedJsonFixture:
    """Load JSON, optionally tolerating // comments without modifying the file."""

    fixture_path = Path(path)
    text = fixture_path.read_text(encoding="utf-8")
    try:
        return LoadedJsonFixture(data=json.loads(text), tolerant_parsing_used=False)
    except json.JSONDecodeError:
        if not allow_comments:
            raise
    return LoadedJsonFixture(data=json.loads(_strip_line_comments(text)), tolerant_parsing_used=True)
