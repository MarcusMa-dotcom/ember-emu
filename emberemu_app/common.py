"""Helpers shared by the provider and the consumer side of the app.

Validation of settings, reading values typed into a form, and the plain-data
description of a tree element that the window, the web page and the REST API
all show.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from emberemu import Element, Function, Matrix, Parameter, ParameterType, StreamFormat

MAX_MATRIX_SNAPSHOT = 4096   # targets x sources up to which a matrix is shown cell by cell
TRUE_WORDS = frozenset({"true", "1", "on", "yes", "ja", "wahr"})
FALSE_WORDS = frozenset({"false", "0", "off", "no", "nein", "falsch"})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def to_int(name: str, value: Any, low: int, high: int) -> int:
    """Return ``value`` as a whole number between ``low`` and ``high``.

    Args:
        name: What the number is, for the error message.
        value: A number, or text that holds one.
        low: Smallest allowed value.
        high: Largest allowed value.

    Raises:
        ValueError: ``value`` is not a whole number in that range.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            raise ValueError(f"{name} must be a whole number") from None
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be a whole number")
        value = int(value)
    elif not isinstance(value, int):
        raise ValueError(f"{name} must be a number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low} to {high}")
    return value


def to_bool(name: str, value: Any) -> bool:
    """Return ``value`` if it is true or false.

    Raises:
        ValueError: ``value`` is anything else.
    """
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def check_host(label: str, value: Any) -> str:
    """Return a host name or address: not empty, no spaces, no control characters.

    Raises:
        ValueError: ``value`` is not usable as a host.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    value = value.strip()
    if len(value) > 255:
        raise ValueError(f"{label} is too long")
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} must not contain spaces or control characters")
    return value


def default_config_path() -> Path:
    """Where the settings live when no ``--config`` is given."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ember-emu" / "settings.json"


def load_json(path: Path) -> Any:
    """Read a JSON file. Returns ``None`` if it is missing or not valid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_json(path: Path, data: Any) -> None:
    """Write a JSON file in one step, so a crash never leaves half a file.

    Raises:
        OSError: The file cannot be written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def value_json(value: Any) -> Any:
    """A parameter value as JSON can carry it: bytes as hex, no infinity or NaN."""
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def parse_value(parameter: Parameter, raw: Any) -> Any:
    """Turn what a user typed, or what JSON delivered, into the type of the parameter.

    Text is read according to the type: ``"-6,5"`` for a real, ``"true"`` for
    a boolean, the entry name or number for an enumeration, hex digits for
    bytes. Range and enumeration entries are not checked here.

    Args:
        parameter: The parameter the value is meant for.
        raw: A JSON value or text.

    Returns:
        The value as ``bool``, ``int``, ``float``, ``str`` or ``bytes``.

    Raises:
        ValueError: ``raw`` cannot be read as a value of that type.
    """
    kind = parameter.type
    name = parameter.identifier or "The value"
    if raw is None:
        return None
    if kind == ParameterType.BOOLEAN:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, int) and raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str):
            word = raw.strip().lower()
            if word in TRUE_WORDS:
                return True
            if word in FALSE_WORDS:
                return False
        raise ValueError(f"{name} must be true or false")
    if kind in (ParameterType.INTEGER, ParameterType.ENUM):
        if isinstance(raw, str):
            text = raw.strip()
            if kind == ParameterType.ENUM:
                for entry, number in parameter.enum_entries():
                    if entry == text:
                        return number
            try:
                return int(text)
            except ValueError:
                try:
                    return int(text, 0)  # 0x10, 0b101
                except ValueError:
                    raise ValueError(f"{name} must be a whole number") from None
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{name} must be a whole number")
        return raw
    if kind == ParameterType.REAL:
        if isinstance(raw, str):
            try:
                return float(raw.strip().replace(",", "."))
            except ValueError:
                raise ValueError(f"{name} must be a number") from None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{name} must be a number")
        return float(raw)
    if kind == ParameterType.STRING:
        if isinstance(raw, (dict, list)):
            raise ValueError(f"{name} must be text")
        return raw if isinstance(raw, str) else str(raw)
    if kind == ParameterType.OCTETS:
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if isinstance(raw, str):
            digits = raw.replace("{", " ").replace("}", " ").replace("0x", " ").replace(",", " ")
            try:
                return bytes.fromhex(digits)
            except ValueError:
                raise ValueError(f"{name} must be written as hex bytes, for example 01 FF") from None
        raise ValueError(f"{name} must be written as hex bytes, for example 01 FF")
    if kind == ParameterType.TRIGGER:
        return raw if isinstance(raw, (bool, int, float, str)) else None
    raise ValueError(f"{name} has no value")


# ---------------------------------------------------------------------------
# Description of an element for the front ends
# ---------------------------------------------------------------------------

def describe(element: Element, depth: int = 0) -> dict[str, Any]:
    """One tree element as plain data.

    Args:
        element: A node, parameter, function or matrix.
        depth: How far below the root elements it sits, for indenting.

    Returns:
        A dictionary that JSON can carry. Every kind has ``path``, ``depth``,
        ``kind``, ``number``, ``identifier``, ``description``,
        ``identifier_path``, ``children`` and ``online``. The rest depends on
        the kind.
    """
    data: dict[str, Any] = {
        "path": element.path_text,
        "depth": depth,
        "kind": element.kind,
        "number": element.number,
        "identifier": element.identifier,
        "description": element.description or "",
        "identifier_path": element.identifier_path,
        "children": len(element),
        "loaded": element.children_loaded,
        "online": bool(getattr(element, "is_online", True)),
    }
    if isinstance(element, Parameter):
        data.update(
            type=element.type.name.lower(),
            access=element.access.name.lower(),
            writable=element.writable,
            value=value_json(element.value),
            display=element.display_value(),
            minimum=value_json(element.minimum),
            maximum=value_json(element.maximum),
            format=element.format or "",
            step=element.step,
            factor=element.factor,
            entries=[[name, number] for name, number in element.enum_entries()],
            stream_identifier=element.stream_identifier,
            stream_format="",
            stream_offset=None,
        )
        if element.stream_descriptor is not None:
            # The stream carries a block of bytes. This says where the value sits in it.
            try:
                data["stream_format"] = StreamFormat(element.stream_descriptor[0]).name.lower()
            except ValueError:
                data["stream_format"] = str(element.stream_descriptor[0])
            data["stream_offset"] = element.stream_descriptor[1]
    elif isinstance(element, Function):
        data.update(
            arguments=[[item_type.name.lower(), name or ""] for item_type, name in element.arguments],
            result=[[item_type.name.lower(), name or ""] for item_type, name in element.result],
        )
    elif isinstance(element, Matrix):
        targets = element.target_numbers()
        sources = element.source_numbers()
        small = len(targets) * max(len(sources), 1) <= MAX_MATRIX_SNAPSHOT
        data.update(
            matrix_type=element.type.name.lower(),
            addressing_mode=element.addressing_mode.name.lower(),
            target_count=element.target_count,
            source_count=element.source_count,
            targets=targets if small else [],
            sources=sources if small else [],
            connections={str(target): list(connected)
                         for target, connected in sorted(element.connections.items()) if connected},
        )
    return data


def flatten(root: Element, limit: int) -> tuple[list[dict[str, Any]], int]:
    """The tree below ``root`` as a flat list, parents before children.

    Args:
        root: The top of the tree. It is not part of the list itself.
        limit: Largest number of elements to describe.

    Returns:
        The descriptions, and the number of elements the tree really has.
    """
    rows: list[dict[str, Any]] = []
    total = 0
    stack: list[tuple[Element, int]] = [(child, 0) for child in reversed(root.children)]
    while stack:
        element, depth = stack.pop()
        total += 1
        if len(rows) < limit:
            rows.append(describe(element, depth))
        stack.extend((child, depth + 1) for child in reversed(element.children))
    return rows, total


__all__ = ["check_host", "default_config_path", "describe", "flatten", "load_json", "parse_value",
           "save_json", "to_bool", "to_int", "value_json"]
