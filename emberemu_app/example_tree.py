"""The example tree: one of everything that occurs in Ember+ trees.

It is what a new installation serves, and what "Load example" restores.
The format is the one of the tree file and of ``PUT /api/v1/provider/tree``.
"""

from __future__ import annotations

from typing import Any

from . import __version__


def _labels(prefix: str, names: list[str]) -> list[dict[str, Any]]:
    """Label parameters for a matrix. Their numbers are the signal numbers."""
    return [
        {"kind": "parameter", "number": number, "identifier": f"{prefix}{number + 1}",
         "type": "string", "access": "read_write", "value": name}
        for number, name in enumerate(names)
    ]


def example_tree() -> dict[str, Any]:
    """Return the example tree as plain data."""
    return {"children": [{
        "kind": "node", "number": 1, "identifier": "EmberEmu",
        "description": "Ember Emu example device",
        "children": [
            {"kind": "node", "number": 1, "identifier": "Identity", "description": "Who this device is",
             "children": [
                 {"kind": "parameter", "identifier": "Product", "type": "string", "value": "Ember Emu"},
                 {"kind": "parameter", "identifier": "Company", "type": "string", "value": "Marcus Mathy"},
                 {"kind": "parameter", "identifier": "Version", "type": "string", "value": __version__},
                 {"kind": "parameter", "identifier": "Serial", "type": "integer", "value": 100042,
                  "description": "Serial number, read only"},
                 {"kind": "parameter", "identifier": "DeviceName", "type": "string",
                  "access": "read_write", "value": "Studio A", "description": "Name, editable"},
             ]},
            {"kind": "node", "number": 2, "identifier": "Audio", "description": "Numbers, switches and a meter",
             "children": [
                 {"kind": "parameter", "identifier": "Gain", "type": "real", "access": "read_write",
                  "value": -12.0, "minimum": -60.0, "maximum": 12.0, "format": "%.1f dB",
                  "description": "Real number with a range"},
                 {"kind": "parameter", "identifier": "Mute", "type": "boolean", "access": "read_write",
                  "value": False},
                 {"kind": "parameter", "identifier": "InputMode", "type": "enum", "access": "read_write",
                  "value": 2, "enumeration": ["Off", "Mono", "Stereo", "Surround"],
                  "description": "Enumeration"},
                 {"kind": "parameter", "identifier": "Channels", "type": "integer",
                  "access": "read_write", "value": 2, "minimum": 1, "maximum": 64, "step": 1,
                  "description": "Whole number with a range"},
                 {"kind": "parameter", "identifier": "Fader", "type": "integer", "access": "read_write",
                  "value": -125, "minimum": -1200, "maximum": 100, "factor": 10, "format": "%.1f dB",
                  "description": "Whole number shown divided by its factor 10"},
                 {"kind": "parameter", "identifier": "Level", "type": "real", "value": -40.0,
                  "minimum": -60.0, "maximum": 0.0, "format": "%.1f dB", "stream_identifier": 1,
                  "animate": "sine", "description": "Meter: a stream, sent only to subscribers"},
             ]},
            {"kind": "node", "number": 3, "identifier": "Video", "description": "Text and states",
             "children": [
                 {"kind": "parameter", "identifier": "Label", "type": "string", "access": "read_write",
                  "value": "CAM 1"},
                 {"kind": "parameter", "identifier": "Standard", "type": "enum", "access": "read_write",
                  "value": 50, "enum_map": [["1080i50", 25], ["1080p50", 50], ["2160p50", 51]],
                  "description": "Enumeration with its own numbers"},
                 {"kind": "parameter", "identifier": "Tally", "type": "boolean", "access": "read_write",
                  "value": False},
                 {"kind": "parameter", "identifier": "Genlock", "type": "boolean", "value": True,
                  "description": "Read only"},
             ]},
            {"kind": "node", "number": 4, "identifier": "Status", "description": "Values that change by themselves",
             "children": [
                 {"kind": "parameter", "identifier": "Clock", "type": "string", "value": "00:00:00",
                  "animate": "clock"},
                 {"kind": "parameter", "identifier": "Uptime", "type": "integer", "value": 0,
                  "minimum": 0, "maximum": 86400, "format": "%d s", "animate": "counter"},
                 {"kind": "parameter", "identifier": "Temperature", "type": "real", "value": 42.0,
                  "minimum": 35.0, "maximum": 55.0, "format": "%.1f °C", "animate": "random"},
                 {"kind": "node", "identifier": "Slot2", "description": "A module that is not there",
                  "is_online": False},
             ]},
            {"kind": "node", "number": 5, "identifier": "Control", "description": "Trigger, bytes and a function",
             "children": [
                 {"kind": "parameter", "identifier": "Identify", "type": "trigger", "access": "write",
                  "description": "Trigger: has no value, only fires"},
                 {"kind": "parameter", "identifier": "Blob", "type": "octets", "access": "read_write",
                  "value": "0102feff", "description": "Bytes"},
                 {"kind": "function", "identifier": "Add", "description": "Returns the sum",
                  "arguments": [["integer", "a"], ["integer", "b"]], "result": [["integer", "sum"]]},
             ]},
            {"kind": "node", "number": 6, "identifier": "Router", "description": "A 4 x 4 matrix with labels",
             "children": [
                 {"kind": "matrix", "number": 1, "identifier": "Matrix", "type": "one_to_n",
                  "target_count": 4, "source_count": 4, "labels": [["1.6.2", "Primary"]],
                  "connections": {"0": [0], "1": [1], "2": [2], "3": [3]}},
                 {"kind": "node", "number": 2, "identifier": "Labels", "children": [
                     {"kind": "node", "number": 1, "identifier": "targets",
                      "children": _labels("T", ["PGM", "PVW", "AUX 1", "AUX 2"])},
                     {"kind": "node", "number": 2, "identifier": "sources",
                      "children": _labels("S", ["CAM 1", "CAM 2", "VTR", "GFX"])},
                 ]},
             ]},
            {"kind": "node", "number": 7, "identifier": "Meters",
             "description": "Streams in the two other forms devices use",
             "children": [
                 {"kind": "parameter", "identifier": "LevelInteger", "type": "integer", "value": -400,
                  "minimum": -600, "maximum": 0, "factor": 10, "format": "%.1f dB",
                  "stream_identifier": 2, "animate": "sine",
                  "description": "Stream of whole numbers, in tenths of a dB"},
                 {"kind": "parameter", "identifier": "Left", "type": "real", "value": -40.0,
                  "minimum": -60.0, "maximum": 0.0, "format": "%.1f dB", "stream_identifier": 3,
                  "stream_descriptor": ["float32_le", 0], "animate": "sine",
                  "description": "Stream as block of bytes: 32 bit float at byte 0"},
                 {"kind": "parameter", "identifier": "Right", "type": "real", "value": -40.0,
                  "minimum": -60.0, "maximum": 0.0, "format": "%.1f dB", "stream_identifier": 3,
                  "stream_descriptor": ["float32_le", 4], "animate": "random",
                  "description": "The same block: 32 bit float at byte 4"},
             ]},
        ],
    }]}
