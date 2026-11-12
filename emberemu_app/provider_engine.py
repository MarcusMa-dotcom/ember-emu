"""Provider engine: holds the virtual tree and serves it.

The desktop window, the web interface and the REST API all talk to one
``ProviderEngine``. Every change raises ``revision``, so each front end can
poll cheaply and redraw only when something changed.
"""

from __future__ import annotations

import math
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from emberemu import (Element, Function, Matrix, Node, Parameter, ParameterType, Provider,
                      ProviderError, StreamFormat, check_identifier, element_from_dict)

from .common import (check_host, describe, flatten, load_json, parse_value, save_json, to_bool,
                     to_int)
from .example_tree import example_tree

LISTEN_FIELDS = frozenset({"bind", "port"})
ANIMATIONS = ("", "clock", "counter", "sine", "random")
ANIMATION_LABELS = {"": "None", "clock": "Clock (text)", "counter": "Counter (whole number)",
                    "sine": "Sine (number)", "random": "Random walk (number)"}
KINDS = ("node", "parameter", "function", "matrix")
PARAMETER_FIELDS = ("type", "access", "value", "minimum", "maximum", "format", "enumeration",
                    "enum_map", "step", "factor", "stream_identifier", "is_online")
TICK_SECONDS = 0.1
SAVE_DELAY_SECONDS = 1.0
SINE_PERIOD_SECONDS = 8.0
LOG_SIZE = 300
LOG_SNAPSHOT_ENTRIES = 60
MAX_ELEMENTS = 20000          # elements the virtual tree may hold
MAX_DEPTH = 64                # levels the virtual tree may be nested
SNAPSHOT_ELEMENTS = 5000      # elements a snapshot lists
MAX_TEXT = 4096


@dataclass
class ProviderSettings:
    """What the provider side remembers between starts."""

    bind: str = "0.0.0.0"
    port: int = 9000
    autostart: bool = False
    animate: bool = True


def coerce(name: str, value: Any) -> Any:
    """Validate one provider setting and return it in its stored type.

    Raises:
        ValueError: The value is not valid, or there is no such setting.
    """
    if name == "bind":
        return check_host("Listen address", value)
    if name == "port":
        return to_int("Port", value, 1, 65535)
    if name in ("autostart", "animate"):
        return to_bool(name, value)
    raise ValueError(f"Unknown setting {name!r}")


def load_settings(path: Path) -> ProviderSettings:
    """Read settings from JSON. Missing or invalid values keep their default."""
    settings = ProviderSettings()
    data = load_json(path)
    if not isinstance(data, dict):
        return settings
    for item in fields(ProviderSettings):
        if item.name in data:
            try:
                setattr(settings, item.name, coerce(item.name, data[item.name]))
            except ValueError:
                pass
    return settings


def _optional_int(name: str, value: Any, low: int = -2 ** 31, high: int = 2 ** 31 - 1) -> int | None:
    """A whole number in a range, or ``None`` for nothing and for empty text."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return to_int(name, value, low, high)


def _optional_text(name: str, value: Any) -> str | None:
    """Text, or ``None`` for nothing and for blank text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{name} is too long")
    return value if value.strip() else None


def _default_function(function: Function) -> Callable[..., list]:
    """What a function of the virtual tree answers: something plausible for its result types."""
    def handler(*arguments: Any) -> list:
        """Answer a call with something plausible."""
        numbers = [item for item in arguments
                   if isinstance(item, (int, float)) and not isinstance(item, bool)]
        answer: list = []
        for item_type, _name in function.result:
            if item_type == ParameterType.INTEGER:
                answer.append(int(sum(numbers)))
            elif item_type == ParameterType.REAL:
                answer.append(float(sum(numbers)))
            elif item_type == ParameterType.STRING:
                answer.append(" ".join(str(item) for item in arguments))
            elif item_type == ParameterType.BOOLEAN:
                answer.append(all(bool(item) for item in arguments))
            elif item_type == ParameterType.OCTETS:
                answer.append(b"")
            else:
                answer.append(0)
        return answer
    return handler


def clean_element_data(data: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the description of an element as it comes from a form or from JSON.

    Text fields that are empty become "not set", numbers written as text
    become numbers, and the value is read according to the parameter type.

    Args:
        data: The fields to set.
        base: The current description of an element that is being changed.
            ``data`` is laid over it.

    Returns:
        A description that :func:`emberemu.element_from_dict` accepts, plus
        the key ``animate``.

    Raises:
        ValueError: A field is not valid.
    """
    if not isinstance(data, dict):
        raise ValueError("An element must be a JSON object")
    merged = dict(base or {})
    merged.update(data)
    merged.pop("children", None)
    kind = merged.get("kind", "node")
    if kind not in KINDS:
        raise ValueError(f"Kind must be one of {', '.join(KINDS)}")
    clean: dict[str, Any] = {
        "kind": kind,
        "identifier": check_identifier(merged.get("identifier")),
        "description": _optional_text("Description", merged.get("description")),
        "number": _optional_int("Number", merged.get("number"), 0),
    }
    if len(clean["identifier"]) > 255:
        raise ValueError("Identifier is too long")
    if kind in ("node", "parameter"):
        clean["is_online"] = to_bool("Online", merged.get("is_online", True))
    if kind == "parameter":
        _clean_parameter(merged, data, base, clean)
    elif kind == "function":
        for name in ("arguments", "result"):
            items = merged.get(name) or []
            if not isinstance(items, list) or len(items) > 32:
                raise ValueError(f"{name} must be a list of at most 32 entries")
            clean[name] = [[str(item[0]), _optional_text("Name", item[1] if len(item) > 1 else None)]
                           if isinstance(item, (list, tuple)) and item else [str(item), None]
                           for item in items]
    elif kind == "matrix":
        clean["type"] = merged.get("type", "one_to_n")
        clean["target_count"] = to_int("Targets", merged.get("target_count", 4), 1, 1024)
        clean["source_count"] = to_int("Sources", merged.get("source_count", 4), 1, 1024)
        for name in ("labels", "maximum_total_connects", "maximum_connects_per_target"):
            if merged.get(name) is not None:
                clean[name] = merged[name]
        connections = merged.get("connections") or {}
        if not isinstance(connections, dict):
            raise ValueError("Connections must be an object: target number to list of sources")
        kept: dict[str, list[int]] = {}
        for target, sources in connections.items():
            # Connections that no longer fit a matrix that was made smaller are dropped.
            if not str(target).isdigit() or int(target) >= clean["target_count"]:
                continue
            if not isinstance(sources, list):
                raise ValueError("The sources of a target must be a list of numbers")
            fitting = [to_int("Source", source, 0, 2 ** 31 - 1) for source in sources]
            kept[str(target)] = [source for source in fitting if source < clean["source_count"]]
        clean["connections"] = kept
    return clean


def _clean_parameter(merged: dict, data: dict, base: dict | None, clean: dict) -> None:
    """Check the fields that only parameters have, and work out the value."""
    type_name = merged.get("type") or "integer"
    if not isinstance(type_name, str) or type_name.upper() not in ParameterType.__members__:
        raise ValueError("Type must be one of " + ", ".join(
            member.name.lower() for member in ParameterType if member != ParameterType.NULL))
    type_name = type_name.lower()
    if type_name == "null":
        raise ValueError("A parameter of type null cannot be created")
    kind = ParameterType[type_name.upper()]
    clean["type"] = type_name
    clean["access"] = merged.get("access") or "read"

    enumeration = merged.get("enumeration")
    if isinstance(enumeration, str):
        enumeration = [line.strip() for line in enumeration.splitlines() if line.strip()]
    if enumeration is not None and (
            not isinstance(enumeration, list) or not all(isinstance(e, str) for e in enumeration)):
        raise ValueError("Enumeration must be a list of names")
    enum_map = merged.get("enum_map")
    if kind == ParameterType.ENUM:
        if enum_map:
            clean["enum_map"] = [[str(pair[0]), to_int("Enumeration number", pair[1], -2 ** 31, 2 ** 31 - 1)]
                                 for pair in enum_map]
        elif enumeration:
            clean["enumeration"] = enumeration[:1024]
        else:
            raise ValueError("An enumeration needs at least one entry")

    numeric = kind in (ParameterType.INTEGER, ParameterType.REAL)
    for name in ("minimum", "maximum"):
        item = merged.get(name)
        if not numeric or item is None or (isinstance(item, str) and not item.strip()):
            continue
        if kind == ParameterType.REAL:
            try:
                clean[name] = float(item.replace(",", ".")) if isinstance(item, str) else float(item)
            except (TypeError, ValueError):
                raise ValueError(f"{name.capitalize()} must be a number") from None
        else:
            clean[name] = to_int(name.capitalize(), item, -2 ** 63, 2 ** 63 - 1)
    if "minimum" in clean and "maximum" in clean and clean["minimum"] > clean["maximum"]:
        raise ValueError("Minimum must not be above maximum")
    clean["format"] = _optional_text("Format", merged.get("format")) if numeric else None
    clean["step"] = _optional_int("Step", merged.get("step")) if numeric else None
    clean["factor"] = _optional_int("Factor", merged.get("factor")) if kind == ParameterType.INTEGER else None
    if clean["factor"] == 0:
        raise ValueError("Factor must not be 0")
    clean["stream_identifier"] = _optional_int("Stream identifier", merged.get("stream_identifier"), 0)
    descriptor = merged.get("stream_descriptor")
    if descriptor and clean["stream_identifier"] is not None:
        # The place of the value inside a block of bytes: [format, offset].
        if not isinstance(descriptor, (list, tuple)) or len(descriptor) != 2:
            raise ValueError("Stream descriptor must be a pair of format and byte offset")
        layout = descriptor[0]
        if isinstance(layout, str) and layout.upper() in StreamFormat.__members__:
            layout = StreamFormat[layout.upper()]
        try:
            layout = StreamFormat(layout)
        except ValueError:
            raise ValueError("Stream format must be one of " + ", ".join(
                member.name.lower() for member in StreamFormat)) from None
        if not numeric:
            raise ValueError("A stream descriptor needs a parameter of type integer or real")
        clean["stream_descriptor"] = [int(layout), to_int("Stream offset", descriptor[1], 0, 65535)]

    animate = merged.get("animate") or ""
    if animate not in ANIMATIONS:
        raise ValueError(f"Animation must be one of {', '.join(repr(a) for a in ANIMATIONS)}")
    if animate == "clock" and kind != ParameterType.STRING:
        raise ValueError("The clock animation needs a parameter of type string")
    if animate == "counter" and kind != ParameterType.INTEGER:
        raise ValueError("The counter animation needs a parameter of type integer")
    if animate in ("sine", "random") and not numeric:
        raise ValueError("This animation needs a parameter of type integer or real")
    clean["animate"] = animate

    # The value: as given, else the one the element has, else a start value for the type.
    probe = element_from_dict({key: value for key, value in clean.items()
                               if key not in ("animate", "value")} | {"value": None})
    assert isinstance(probe, Parameter)
    raw = merged.get("value")
    type_changed = base is not None and base.get("type") != type_name
    if type_changed and "value" not in data:
        raw = None
    if isinstance(raw, str) and kind != ParameterType.STRING and not raw.strip():
        raw = None
    if raw is None:
        entries = probe.enum_entries()
        value: Any = {ParameterType.INTEGER: 0, ParameterType.REAL: 0.0, ParameterType.STRING: "",
                      ParameterType.BOOLEAN: False, ParameterType.OCTETS: b"",
                      ParameterType.ENUM: entries[0][1] if entries else 0}.get(kind)
        if numeric and "minimum" in clean and value < clean["minimum"]:
            value = clean["minimum"]
        if numeric and "maximum" in clean and value > clean["maximum"]:
            value = clean["maximum"]
    else:
        value = parse_value(probe, raw)
    value = probe.coerce(value)
    clean["value"] = value.hex() if isinstance(value, bytes) else value


class ProviderEngine:
    """The virtual Ember+ tree of the app, and the provider that serves it."""

    def __init__(self, settings: ProviderSettings | None = None,
                 config_path: Path | str | None = None,
                 now: Callable[[], datetime] = datetime.now):
        """Create the engine and load the tree.

        Args:
            settings: Settings to start with. Without them they are read from
                ``config_path``, or defaults are used.
            config_path: Settings file. The tree is kept next to it, in a file
                whose name ends in ``.tree.json``. Without a path nothing is
                saved.
            now: Clock for the "clock" animation, replaceable in tests.
        """
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        if settings is None:
            settings = load_settings(self._config_path) if self._config_path else ProviderSettings()
        self.settings = settings
        self._now = now
        self._instance = uuid.uuid4().hex[:8]  # lets a front end notice a restart
        self._revision = 0
        self._error = ""
        self._log: deque[dict] = deque(maxlen=LOG_SIZE)
        self._animated: dict[Parameter, str] = {}
        self._started = time.monotonic()
        self._last_slow_step = 0.0
        self._last_second = 0.0
        self._dirty_settings = False
        self._dirty_tree = False
        self._dirty_since = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.provider = self._make_provider()
        stored = load_json(self._tree_path) if self._tree_path else None
        try:
            self._install(stored if isinstance(stored, dict) else example_tree())
        except ValueError:
            self._install(example_tree())

    # -- state -----------------------------------------------------------

    @property
    def instance(self) -> str:
        """A name that is new at every start, so front ends notice a restart."""
        return self._instance

    @property
    def revision(self) -> int:
        """Rises with every change. Front ends compare it to decide whether to redraw."""
        return self._revision

    @property
    def running(self) -> bool:
        """True while the provider accepts consumers."""
        return self.provider.running

    @property
    def _tree_path(self) -> Path | None:
        """Where the tree is saved, next to the settings file."""
        if self._config_path is None:
            return None
        return self._config_path.with_name(self._config_path.stem + ".tree.json")

    def _touch(self) -> None:
        """Mark the state as changed."""
        self._revision += 1

    def _make_provider(self) -> Provider:
        """Create the provider, with its callbacks wired to the activity log."""
        return Provider(
            port=self.settings.port, bind=self.settings.bind,
            on_change=self._on_change, on_connection=lambda matrix, target, client: self._touch(),
            on_connect=lambda client: self._record(client.address, "connected", None, ""),
            on_disconnect=lambda client: self._record(client.address, "disconnected", None, ""),
            on_request=lambda client, action, element, detail: self._record(
                client.address, action, element, detail),
            on_error=lambda message: self._record("", "error", None, message),
        )

    def _record(self, client: str, action: str, element: Element | None, text: str) -> None:
        """Add an entry to the activity log."""
        with self._lock:
            self._log.append({
                "time": time.time(),
                "client": client,
                "action": action,
                "path": element.path_text if element is not None else "",
                "identifier_path": element.identifier_path if element is not None else "",
                "text": text,
            })
            self._touch()

    def _on_change(self, parameter: Parameter, client: Any) -> None:
        """A consumer changed a value: note that the tree needs saving."""
        with self._lock:
            self._dirty_tree_now()
            self._touch()

    def snapshot(self) -> dict:
        """Return settings, status and the whole tree as plain data for the front ends."""
        with self._lock:
            rows, total = flatten(self.provider.tree, SNAPSHOT_ELEMENTS)
            animated = {parameter.path_text: kind for parameter, kind in self._animated.items()}
            for row in rows:
                if row["kind"] == "parameter":
                    row["animate"] = animated.get(row["path"], "")
            return {
                "instance": self._instance,
                "revision": self._revision,
                "settings": asdict(self.settings),
                "status": {
                    "running": self.running,
                    "description": self.provider.description if self.running else "",
                    "error": self._error,
                    "clients": [
                        {"address": client.address, "since": client.connected_at,
                         "requests": client.requests,
                         "glow_version": ".".join(str(n) for n in client.glow_version)
                         if client.glow_version else ""}
                        for client in self.provider.clients
                    ],
                    "element_count": total,
                    "log": list(self._log)[-LOG_SNAPSHOT_ENTRIES:],
                },
                "tree": rows,
            }

    # -- settings and serving -------------------------------------------

    def update(self, **changes: Any) -> dict:
        """Validate and apply setting changes.

        Changing the listen address or the port stops a running provider.

        Raises:
            ValueError: A value is not valid. Nothing is changed in that case.
        """
        clean = {name: coerce(name, value) for name, value in changes.items()}
        with self._lock:
            changed = {name for name, value in clean.items() if getattr(self.settings, name) != value}
            if not changed:
                return self.snapshot()
            for name in changed:
                setattr(self.settings, name, clean[name])
            if changed & LISTEN_FIELDS and self.running:
                self.provider.stop()
            self.provider.bind = self.settings.bind
            if not self.running:
                self.provider.port = self.settings.port
            self._dirty_settings = True
            self._mark_dirty()
            self._touch()
            return self.snapshot()

    def start(self) -> bool:
        """Open the port. Returns False and sets the error on failure."""
        with self._lock:
            if self.running:
                return True
            self.provider.bind = self.settings.bind
            self.provider.port = self.settings.port
            try:
                self.provider.start()
            except ProviderError as exc:
                self._error = str(exc)
                self._touch()
                return False
            self._error = ""
            self._touch()
            return True

    def stop_serving(self) -> None:
        """Close the port and drop all consumers."""
        with self._lock:
            if self.running:
                self.provider.stop()
                self.provider.port = self.settings.port
                self._error = ""
                self._touch()

    def clear_log(self) -> None:
        """Forget the activity log."""
        with self._lock:
            self._log.clear()
            self._touch()

    # -- the tree --------------------------------------------------------

    def _element(self, path: Any) -> Element:
        """The element at a path of numbers or identifiers. Raises ``ValueError`` if there is none."""
        text = path if isinstance(path, str) else str(path)
        try:
            element = self.provider.tree.find(text) if text.strip() else None
        except ValueError:
            element = None
        if element is None:
            raise ValueError(f"There is no element {text!r}")
        return element

    def _export(self, element: Element, children: bool = True) -> dict[str, Any]:
        """An element as plain data, with its animation and, if wanted, its children."""
        data = element.to_dict(children=False)
        if isinstance(element, Parameter) and self._animated.get(element):
            data["animate"] = self._animated[element]
        if children and len(element):
            data["children"] = [self._export(child) for child in element.children]
        return data

    def _build(self, data: Any, budget: list[int], depth: int = 1
               ) -> tuple[Element, list[tuple[Parameter, str]]]:
        """Build an element with everything below it. Nothing is attached to the tree yet."""
        if depth > MAX_DEPTH:
            raise ValueError(f"A tree may be nested at most {MAX_DEPTH} levels deep")
        try:
            clean = clean_element_data(data)
        except ValueError as exc:
            name = data.get("identifier") if isinstance(data, dict) else None
            # Say which element of a larger tree is meant, unless the message names it already.
            if isinstance(name, str) and name and name not in str(exc):
                raise ValueError(f"{name}: {exc}") from None
            raise
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError(f"A tree may hold at most {MAX_ELEMENTS} elements")
        animate = clean.pop("animate", "")
        element = element_from_dict(clean)
        animated: list[tuple[Parameter, str]] = []
        if isinstance(element, Parameter) and animate:
            animated.append((element, animate))
        if isinstance(element, Function):
            element.handler = _default_function(element)
        children = data.get("children") or []
        if not isinstance(children, list):
            raise ValueError("Children must be a list")
        if children and not isinstance(element, Node):
            raise ValueError(f"{element.identifier}: only a node can hold children")
        for child_data in children:
            child, more = self._build(child_data, budget, depth + 1)
            element.add(child)
            animated.extend(more)
        return element, animated

    def _install(self, data: dict[str, Any]) -> None:
        """Replace the whole tree. On an error the old tree stays."""
        children = data.get("children") if isinstance(data, dict) else None
        if not isinstance(children, list):
            raise ValueError('A tree must be an object with a list "children"')
        budget = [MAX_ELEMENTS]
        built = [self._build(child, budget) for child in children]
        numbers = [element.number for element, _ in built if element.number is not None]
        names = [element.identifier for element, _ in built]
        if len(set(numbers)) != len(numbers) or len(set(names)) != len(names):
            raise ValueError("Root elements must have different numbers and identifiers")
        self.provider.tree.clear()
        self._animated.clear()
        for element, animated in built:
            self.provider.tree.add(element)
            self._animated.update(animated)

    def export_tree(self) -> dict[str, Any]:
        """The tree as plain data, in the format :meth:`import_tree` reads."""
        with self._lock:
            return {"children": [self._export(child) for child in self.provider.tree.children]}

    def import_tree(self, data: Any) -> None:
        """Replace the tree with one given as plain data.

        Raises:
            ValueError: The data does not describe a valid tree. The old tree stays.
        """
        with self._lock:
            self._install(data)
            self._dirty_tree_now()
            self._touch()

    def load_example(self) -> None:
        """Replace the tree with the example tree."""
        self.import_tree(example_tree())

    def clear_tree(self) -> None:
        """Remove every element."""
        self.import_tree({"children": []})

    def element(self, path: str) -> dict[str, Any]:
        """One element as plain data.

        Raises:
            ValueError: There is no element at ``path``.
        """
        with self._lock:
            element = self._element(path)
            data = describe(element, len(element.path) - 1)
            if isinstance(element, Parameter):
                data["animate"] = self._animated.get(element, "")
            return data

    def add_element(self, parent: str, data: Any) -> str:
        """Add an element below the node at ``parent``. An empty ``parent`` adds a root element.

        Returns:
            The path of the new element.

        Raises:
            ValueError: The parent is missing or not a node, or ``data`` is not valid.
        """
        with self._lock:
            holder: Element = self.provider.tree if not str(parent).strip() else self._element(parent)
            if not isinstance(holder, Node):
                raise ValueError("Only a node can hold children")
            used = sum(1 for _ in self.provider.tree.walk())
            element, animated = self._build(data, [MAX_ELEMENTS - used], len(holder.path) + 1)
            holder.add(element)
            self._animated.update(animated)
            self._dirty_tree_now()
            self._touch()
            return element.path_text

    def update_element(self, path: str, changes: Any) -> None:
        """Change the value or other properties of an element.

        Consumers that know the element are told. The number of an element,
        its kind and its children cannot be changed.

        Raises:
            ValueError: There is no such element, or a change is not valid.
                Nothing is changed in that case.
        """
        if not isinstance(changes, dict):
            raise ValueError("Changes must be a JSON object")
        with self._lock:
            element = self._element(path)
            if set(changes) == {"value"} and isinstance(element, Parameter):
                element.value = element.coerce(parse_value(element, changes["value"]))
                self._dirty_tree_now()
                self._touch()
                return
            if "kind" in changes and changes["kind"] != element.kind:
                raise ValueError("The kind of an element cannot be changed")
            if "number" in changes and changes["number"] != element.number:
                raise ValueError("The number of an element cannot be changed")
            clean = clean_element_data(changes, self._export(element, children=False))
            animate = clean.pop("animate", "")
            fresh = element_from_dict(clean)
            parent = element.parent
            if fresh.identifier != element.identifier and parent is not None and parent.child(
                    fresh.identifier) is not None:
                raise ValueError(f"Identifier {fresh.identifier!r} is already used here")
            element.identifier = fresh.identifier
            element.description = fresh.description
            if isinstance(element, Parameter) and isinstance(fresh, Parameter):
                for name in ("type", "access", "minimum", "maximum", "format", "enumeration",
                             "enum_map", "step", "factor", "stream_identifier", "stream_descriptor",
                             "default"):
                    setattr(element, name, getattr(fresh, name))
                element._value = fresh.value
                element._is_online = fresh.is_online
                if animate:
                    self._animated[element] = animate
                else:
                    self._animated.pop(element, None)
            elif isinstance(element, Node) and isinstance(fresh, Node):
                element._is_online = fresh.is_online
            elif isinstance(element, Function) and isinstance(fresh, Function):
                element.arguments = fresh.arguments
                element.result = fresh.result
            elif isinstance(element, Matrix) and isinstance(fresh, Matrix):
                for name in ("type", "target_count", "source_count", "maximum_total_connects",
                             "maximum_connects_per_target", "labels"):
                    setattr(element, name, getattr(fresh, name))
                element.connections = fresh.connections
            self.provider.refresh(element)
            self._dirty_tree_now()
            self._touch()

    def remove_element(self, path: str) -> None:
        """Remove an element and everything below it.

        Raises:
            ValueError: There is no element at ``path``.
        """
        with self._lock:
            element = self._element(path)
            for item in element.walk():
                self._animated.pop(item, None)  # type: ignore[arg-type]
            parent = element.parent
            assert parent is not None
            parent.remove(element)
            self._dirty_tree_now()
            self._touch()

    def connect_matrix(self, path: str, target: Any, sources: Any, operation: Any = "absolute") -> None:
        """Change the sources of one target of a matrix in the tree.

        Raises:
            ValueError: The element is not a matrix, or the matrix does not allow this.
        """
        with self._lock:
            matrix = self._element(path)
            if not isinstance(matrix, Matrix):
                raise ValueError(f"{path} is not a matrix")
            if isinstance(sources, (str, int)) and not isinstance(sources, bool):
                sources = [sources]
            if not isinstance(sources, list):
                raise ValueError("Sources must be a list of numbers")
            numbers = [to_int("Source", source, 0, 2 ** 31 - 1) for source in sources]
            matrix.connect(to_int("Target", target, 0, 2 ** 31 - 1), numbers,
                           operation if isinstance(operation, (str, int)) else "absolute")
            self._dirty_tree_now()
            self._touch()

    # -- background timer ------------------------------------------------

    def start_timer(self) -> None:
        """Start the timer thread for the animated values and for saving."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ember-provider-engine", daemon=True)
            self._thread.start()
        if self.settings.autostart:
            self.start()

    def close(self) -> None:
        """Stop the timer, save settings and tree, and close the port."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._save_if_dirty(force=True)
        self.provider.stop()

    def _run(self) -> None:
        """Thread: step the timer ten times a second."""
        while not self._stop.wait(TICK_SECONDS):
            self.tick(time.monotonic())

    def tick(self, now: float) -> None:
        """One timer step. Public so tests can drive time themselves."""
        with self._lock:
            if self.settings.animate and self._animated:
                if self._animate(now):
                    self._touch()
            self._save_if_dirty(now=now)

    def _animate(self, now: float) -> bool:
        """Move the animated values on. Returns True if one of them changed."""
        changed = False
        slow = now - self._last_slow_step >= 0.5
        second = now - self._last_second >= 1.0
        if slow:
            self._last_slow_step = now
        if second:
            self._last_second = now
        for parameter, kind in list(self._animated.items()):
            low = parameter.minimum if isinstance(parameter.minimum, (int, float)) else 0
            high = parameter.maximum if isinstance(parameter.maximum, (int, float)) else 100
            before = parameter.value
            try:
                if kind == "clock":
                    parameter.value = self._now().strftime("%H:%M:%S")
                elif kind == "counter" and second:
                    following = (before or 0) + 1
                    parameter.value = low if following > high else following
                elif kind == "sine":
                    phase = math.sin(2 * math.pi * (now - self._started) / SINE_PERIOD_SECONDS)
                    level = low + (high - low) * (phase + 1) / 2
                    # Adding 0.0 turns a rounded -0.0 into 0.0.
                    parameter.value = round(level, 1) + 0.0 if parameter.type == ParameterType.REAL else round(level)
                elif kind == "random" and slow:
                    level = (before if before is not None else low) + random.uniform(-1, 1) * (high - low) * 0.04
                    level = min(high, max(low, level))
                    parameter.value = round(level, 1) if parameter.type == ParameterType.REAL else round(level)
            except ValueError:
                continue
            changed = changed or parameter.value != before
        return changed

    def _mark_dirty(self) -> None:
        """Note the time of the first change that is not saved yet."""
        if self._dirty_since == 0.0:
            self._dirty_since = time.monotonic()

    def _dirty_tree_now(self) -> None:
        """Note that the tree has changes that are not saved yet."""
        self._dirty_tree = True
        self._mark_dirty()

    def _save_if_dirty(self, now: float | None = None, force: bool = False) -> None:
        """Save settings and tree once changes have settled, or at once with ``force``."""
        if self._config_path is None or not (self._dirty_settings or self._dirty_tree):
            return
        if not force and (now is None or now - self._dirty_since < SAVE_DELAY_SECONDS):
            return
        try:
            if self._dirty_settings:
                save_json(self._config_path, asdict(self.settings))
            if self._dirty_tree and self._tree_path is not None:
                save_json(self._tree_path, self.export_tree())
        except OSError:
            pass
        self._dirty_settings = self._dirty_tree = False
        self._dirty_since = 0.0


__all__ = ["ANIMATIONS", "ANIMATION_LABELS", "KINDS", "ProviderEngine", "ProviderSettings",
           "clean_element_data"]
