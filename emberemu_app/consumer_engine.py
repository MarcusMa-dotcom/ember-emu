"""Consumer engine: connects to a provider, mirrors its tree and keeps the pins.

A pin is a parameter the user wants to keep an eye on. Pins are remembered by
their identifier path (``Device/Audio/Gain``), because the numbers of a tree
may differ from one session to the next. After every connect the pins are
looked up again, and stream parameters among them are subscribed.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from emberemu import (Consumer, ConsumerError, Element, Function, InvocationError, Matrix,
                      Parameter, ParameterType, Tree)

from .common import (check_host, describe, flatten, load_json, parse_value, save_json, to_bool,
                     to_int, value_json)

CONNECTION_FIELDS = frozenset({"host", "port", "keepalive"})
REQUEST_TIMEOUT = 4.0
KEEPALIVE_SECONDS = 5.0
RECONNECT_SECONDS = 3.0
TICK_SECONDS = 0.25
SAVE_DELAY_SECONDS = 1.0
LOG_SIZE = 300
LOG_SNAPSHOT_ENTRIES = 60
MAX_PINS = 200
MAX_PIN_LENGTH = 1024
SNAPSHOT_ELEMENTS = 5000      # elements a snapshot lists
EXPAND_ALL_ELEMENTS = 5000    # elements "expand all" loads at most


@dataclass
class ConsumerSettings:
    """What the consumer side remembers between starts."""

    host: str = "127.0.0.1"
    port: int = 9000
    keepalive: bool = True
    auto_reconnect: bool = False
    pins: list[str] = field(default_factory=list)


def coerce(name: str, value: Any) -> Any:
    """Validate one consumer setting and return it in its stored type.

    Raises:
        ValueError: The value is not valid, or there is no such setting.
    """
    if name == "host":
        return check_host("Host", value)
    if name == "port":
        return to_int("Port", value, 1, 65535)
    if name in ("keepalive", "auto_reconnect"):
        return to_bool(name, value)
    if name == "pins":
        if not isinstance(value, list) or len(value) > MAX_PINS:
            raise ValueError(f"Pins must be a list of at most {MAX_PINS} paths")
        pins: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > MAX_PIN_LENGTH:
                raise ValueError("A pin must be the identifier path of a parameter")
            if item.strip() not in pins:
                pins.append(item.strip())
        return pins
    raise ValueError(f"Unknown setting {name!r}")


def load_settings(path: Path) -> ConsumerSettings:
    """Read settings from JSON. Missing or invalid values keep their default."""
    settings = ConsumerSettings()
    data = load_json(path)
    if not isinstance(data, dict):
        return settings
    for item in fields(ConsumerSettings):
        if item.name in data:
            try:
                setattr(settings, item.name, coerce(item.name, data[item.name]))
            except ValueError:
                pass
    return settings


class ConsumerEngine:
    """The consumer side of the app: one connection, its tree and the pins."""

    def __init__(self, settings: ConsumerSettings | None = None,
                 config_path: Path | str | None = None):
        """Create the engine.

        Args:
            settings: Settings to start with. Without them they are read from
                ``config_path``, or defaults are used.
            config_path: Settings file. Without a path nothing is saved.
        """
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        if settings is None:
            settings = load_settings(self._config_path) if self._config_path else ConsumerSettings()
        self.settings = settings
        self._instance = uuid.uuid4().hex[:8]
        self._revision = 0
        self._consumer: Consumer | None = None
        self._connecting = False
        self._loading = False
        self._wanted = False            # the user asked for a connection and has not ended it
        self._retry_at = 0.0
        self._error = ""
        self._log: deque[dict] = deque(maxlen=LOG_SIZE)
        self._pins: dict[str, dict[str, Any]] = {path: self._new_pin(path) for path in settings.pins}
        self._pin_of: dict[int, dict[str, Any]] = {}   # id(element) -> pin
        self._dirty_since = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

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
    def connected(self) -> bool:
        """True while the connection to the provider is open."""
        consumer = self._consumer
        return consumer is not None and consumer.connected

    @staticmethod
    def _new_pin(identifier_path: str) -> dict[str, Any]:
        """A pin that has not been looked up at a provider yet."""
        return {"identifier_path": identifier_path, "element": None, "error": "",
                "changed": None, "changes": 0}

    def _touch(self) -> None:
        """Mark the state as changed."""
        self._revision += 1

    def _record(self, action: str, element: Element | None, text: str) -> None:
        """Add an entry to the activity log."""
        self._log.append({
            "time": time.time(),
            "action": action,
            "path": element.path_text if element is not None else "",
            "identifier_path": element.identifier_path if element is not None else "",
            "text": text,
        })
        self._touch()

    def snapshot(self) -> dict:
        """Return settings, status, pins and the tree read so far as plain data."""
        with self._lock:
            consumer = self._consumer
            rows: list[dict[str, Any]] = []
            total = 0
            root_loaded = False
            if consumer is not None:
                with consumer.lock:
                    rows, total = flatten(consumer.tree, SNAPSHOT_ELEMENTS)
                    root_loaded = consumer.tree.children_loaded
            pins = [self._pin_row(pin) for pin in self._pins.values()]
            pinned = set(self._pins)
            for row in rows:
                row["pinned"] = row["identifier_path"] in pinned
            version = consumer.provider_glow_version if consumer is not None else None
            return {
                "instance": self._instance,
                "revision": self._revision,
                "settings": asdict(self.settings),
                "status": {
                    "connected": self.connected,
                    "connecting": self._connecting,
                    "loading": self._loading,
                    "description": consumer.description if consumer is not None and self.connected else "",
                    "error": self._error,
                    "glow_version": ".".join(str(n) for n in version) if version else "",
                    "root_loaded": root_loaded,
                    "element_count": total,
                    "pins": pins,
                    "log": list(self._log)[-LOG_SNAPSHOT_ENTRIES:],
                },
                "tree": rows,
            }

    def _pin_row(self, pin: dict[str, Any]) -> dict[str, Any]:
        """A pin as plain data, with the parameter it points to if that was found."""
        element = pin["element"]
        row: dict[str, Any] = {
            "identifier_path": pin["identifier_path"],
            "found": element is not None,
            "error": pin["error"],
            "changed": pin["changed"],
            "changes": pin["changes"],
        }
        if element is not None:
            row.update(describe(element, 0))
            row["identifier_path"] = pin["identifier_path"]
        return row

    # -- settings --------------------------------------------------------

    def update(self, **changes: Any) -> dict:
        """Validate and apply setting changes.

        Changing host, port or keep-alive ends an open connection.

        Raises:
            ValueError: A value is not valid. Nothing is changed in that case.
        """
        clean = {name: coerce(name, value) for name, value in changes.items()}
        drop = False
        with self._lock:
            changed = {name for name, value in clean.items() if getattr(self.settings, name) != value}
            if changed:
                for name in changed:
                    setattr(self.settings, name, clean[name])
                if "pins" in changed:
                    self._sync_pins()
                drop = bool(changed & CONNECTION_FIELDS) and self._consumer is not None
                self._mark_dirty()
                self._touch()
        if drop:
            self.disconnect()
        return self.snapshot()

    def _sync_pins(self) -> None:
        """Bring the pin table in line with ``settings.pins``."""
        wanted = self.settings.pins
        for path in list(self._pins):
            if path not in wanted:
                pin = self._pins.pop(path)
                if pin["element"] is not None:
                    self._pin_of.pop(id(pin["element"]), None)
        self._pins = {path: self._pins.get(path) or self._new_pin(path) for path in wanted}
        consumer = self._consumer
        if consumer is not None and consumer.connected and any(
                pin["element"] is None for pin in self._pins.values()):
            threading.Thread(target=self._resolve_pins, args=(consumer,), daemon=True).start()

    def clear_log(self) -> None:
        """Forget the activity log."""
        with self._lock:
            self._log.clear()
            self._touch()

    # -- connection ------------------------------------------------------

    def connect(self) -> bool:
        """Connect and read the root elements. Returns False and sets the error on failure."""
        with self._lock:
            if self._connecting:
                return False
            old = self._consumer
            self._consumer = None
            self._connecting = True
            self._wanted = True
            self._error = ""
            self._forget_elements()
            self._touch()
            settings = self.settings
            consumer = Consumer(settings.host, settings.port, timeout=REQUEST_TIMEOUT,
                                keepalive=KEEPALIVE_SECONDS if settings.keepalive else None)
            consumer.on_change = self._changed
            consumer.on_disconnect = lambda reason: self._lost(consumer, reason)
            consumer.on_error = lambda message: self._problem(consumer, message)
        if old is not None:
            old.close()
        error = ""
        failed = False
        try:
            consumer.connect()        # outside the lock: this may take seconds
            consumer.get_directory()
        except ConsumerError as exc:
            error = str(exc)
            failed = True
        except TimeoutError:
            error = "Connected, but the provider did not answer the request for its root elements"
        except Exception as exc:      # never leave the engine stuck in "connecting"
            error = f"Cannot connect: {type(exc).__name__}"
            failed = True
        with self._lock:
            self._connecting = False
            self._error = error
            if failed:
                consumer.close()
                self._retry_at = time.monotonic() + RECONNECT_SECONDS
                self._touch()
                return False
            self._consumer = consumer
            self._record("connected", None, consumer.description)
        self._resolve_pins(consumer)
        return True

    def disconnect(self) -> None:
        """Close the connection. The tree read so far stays visible."""
        with self._lock:
            self._wanted = False
            consumer = self._consumer
            was_connected = self.connected
            self._error = ""
            self._touch()
        if consumer is not None:
            consumer.close()
        if was_connected:
            with self._lock:
                self._record("disconnected", None, "")

    def _forget_elements(self) -> None:
        """Detach all pins from the elements of the previous connection."""
        self._pin_of.clear()
        for pin in self._pins.values():
            pin["element"] = None
            pin["error"] = ""

    def _lost(self, consumer: Consumer, reason: str) -> None:
        """The network thread reports that the connection ended by itself."""
        with self._lock:
            if consumer is not self._consumer:
                return
            self._error = reason
            self._retry_at = time.monotonic() + RECONNECT_SECONDS
            self._record("disconnected", None, reason)

    def _problem(self, consumer: Consumer, message: str) -> None:
        """The network thread reports bad data from the provider."""
        with self._lock:
            if consumer is self._consumer:
                self._record("error", None, message)

    def _changed(self, element: Element, changed: set) -> None:
        """The network thread reports that the provider sent something about an element."""
        with self._lock:
            pin = self._pin_of.get(id(element))
            if pin is not None and "value" in changed and isinstance(element, Parameter):
                pin["changed"] = time.time()
                pin["changes"] += 1
                if not element.is_stream:   # a meter would flood the log
                    self._record("change", element, element.display_value())
                    return
            self._touch()

    # -- reading the tree -----------------------------------------------

    def _need(self) -> Consumer:
        """The consumer, if it is connected. Raises ``ValueError`` otherwise."""
        consumer = self._consumer
        if consumer is None or not consumer.connected:
            raise ValueError("Not connected")
        return consumer

    def _find(self, consumer: Consumer, path: Any) -> Element:
        """An element by its numbers (``1.2.3``) or identifiers (``Device/Audio/Gain``)."""
        if not isinstance(path, str):
            raise ValueError("A path must be text")
        if not path.strip():
            return consumer.tree
        try:
            with consumer.lock:
                element = consumer.tree.find(path)
            return element if element is not None else consumer.get(path)
        except KeyError:
            raise ValueError(f"The provider has no element {path!r}") from None
        except TimeoutError:
            raise ValueError("The provider did not answer") from None
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None

    def expand(self, path: str = "", refresh: bool = False) -> None:
        """Load the children of a node. An empty path loads the root elements.

        Args:
            path: Numbers or identifiers of the node.
            refresh: Ask again even if the children were loaded before, and
                drop children the provider no longer lists.

        Raises:
            ValueError: Not connected, no such element, or no answer.
        """
        consumer = self._need()
        element = self._find(consumer, path)
        try:
            consumer.get_directory(element, refresh=refresh)
        except TimeoutError:
            raise ValueError("The provider did not answer") from None
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None
        with self._lock:
            self._touch()

    def expand_all(self, path: str = "") -> None:
        """Load everything below a node in the background, up to a few thousand elements.

        Raises:
            ValueError: Not connected, or no such element.
        """
        consumer = self._need()
        element = self._find(consumer, path)
        with self._lock:
            if self._loading:
                return
            self._loading = True
            self._touch()

        def work() -> None:
            """Load the tree, then clear the loading flag."""
            try:
                consumer.walk(element, max_elements=EXPAND_ALL_ELEMENTS)
            except (ConsumerError, ValueError):
                pass
            finally:
                with self._lock:
                    self._loading = False
                    self._touch()

        threading.Thread(target=work, name="ember-consumer-walk", daemon=True).start()

    def element(self, path: str) -> dict[str, Any]:
        """One element as plain data.

        Raises:
            ValueError: Not connected, or the provider has no such element.
        """
        consumer = self._consumer
        if consumer is None:
            raise ValueError("Not connected")
        element = self._find(consumer, path)
        if isinstance(element, Tree):
            raise ValueError("A path is needed")
        with consumer.lock:
            data = describe(element, len(element.path) - 1)
        data["pinned"] = data["identifier_path"] in self._pins
        return data

    # -- changing things at the provider --------------------------------

    def set_value(self, path: str, raw: Any, check: bool = True) -> Any:
        """Ask the provider to change a parameter and return the value it reports back.

        Args:
            path: Numbers or identifiers of the parameter.
            raw: The new value, as JSON value or as text.
            check: Refuse values outside the announced range before sending.
                Switch it off to test how a provider reacts to such values.

        Raises:
            ValueError: Not connected, not a parameter, a value that does not
                fit, or no answer from the provider.
        """
        consumer = self._need()
        element = self._find(consumer, path)
        if not isinstance(element, Parameter):
            raise ValueError(f"{path} is not a parameter")
        if element.type == ParameterType.TRIGGER:
            self.trigger(path)
            return None
        value = parse_value(element, raw)
        try:
            reported = consumer.set_value(element, value, check=check)
        except TimeoutError:
            raise ValueError("The provider did not answer") from None
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None
        with self._lock:
            if Parameter._same(reported, value) or (
                    isinstance(reported, float) and isinstance(value, int) and reported == value):
                self._record("set", element, element.display_value())
            else:
                self._record("refused", element,
                             f"asked for {value_json(value)!r}, the provider keeps {element.display_value()}")
        return value_json(reported)

    def trigger(self, path: str) -> None:
        """Fire a trigger parameter.

        Raises:
            ValueError: Not connected, or not a parameter.
        """
        consumer = self._need()
        element = self._find(consumer, path)
        if not isinstance(element, Parameter):
            raise ValueError(f"{path} is not a parameter")
        try:
            consumer.trigger(element)
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None
        with self._lock:
            self._record("set", element, "trigger")

    def invoke(self, path: str, arguments: Any = None) -> list:
        """Call a function at the provider and return its result values.

        Raises:
            ValueError: Not connected, not a function, wrong arguments, a
                failed call, or no answer.
        """
        consumer = self._need()
        function = self._find(consumer, path)
        if not isinstance(function, Function):
            raise ValueError(f"{path} is not a function")
        arguments = [] if arguments is None else arguments
        if not isinstance(arguments, list):
            raise ValueError("Arguments must be a list")
        if len(arguments) != len(function.arguments):
            raise ValueError(f"{function.identifier} takes {len(function.arguments)} "
                             f"argument{'' if len(function.arguments) == 1 else 's'}")
        values = [parse_value(Parameter(name or f"Argument {index + 1}", type=item_type), raw)
                  for index, ((item_type, name), raw) in enumerate(zip(function.arguments, arguments))]
        try:
            result = consumer.invoke(function, *values)
        except InvocationError:
            with self._lock:
                self._record("error", function, "the call failed")
            raise ValueError("The provider reports that the call failed") from None
        except TimeoutError:
            raise ValueError("The provider did not answer") from None
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None
        answer = [value_json(item) for item in result]
        with self._lock:
            self._record("invoke", function,
                         f"({', '.join(repr(value_json(v)) for v in values)}) -> {answer!r}")
        return answer

    def set_connection(self, path: str, target: Any, sources: Any, operation: Any = "absolute") -> None:
        """Ask the provider to change the sources of one matrix target.

        Raises:
            ValueError: Not connected, not a matrix, or values that are not numbers.
        """
        consumer = self._need()
        matrix = self._find(consumer, path)
        if not isinstance(matrix, Matrix):
            raise ValueError(f"{path} is not a matrix")
        if isinstance(sources, int) and not isinstance(sources, bool):
            sources = [sources]
        if not isinstance(sources, list):
            raise ValueError("Sources must be a list of numbers")
        numbers = [to_int("Source", source, 0, 2 ** 31 - 1) for source in sources]
        target = to_int("Target", target, 0, 2 ** 31 - 1)
        try:
            consumer.set_connection(matrix, target, numbers,
                                    operation if isinstance(operation, (str, int)) else "absolute")
        except ConsumerError as exc:
            raise ValueError(str(exc)) from None
        with self._lock:
            self._record("connect", matrix, f"target {target}: sources "
                         f"{', '.join(str(n) for n in numbers) or 'none'}")

    # -- pins ------------------------------------------------------------

    def pin(self, path: str) -> str:
        """Pin a parameter. Returns its identifier path, under which it is remembered.

        Raises:
            ValueError: Not connected, not a parameter, or too many pins.
        """
        consumer = self._need()
        element = self._find(consumer, path)
        if not isinstance(element, Parameter):
            raise ValueError("Only parameters can be pinned")
        identifier_path = element.identifier_path
        with self._lock:
            if identifier_path not in self._pins:
                if len(self._pins) >= MAX_PINS:
                    raise ValueError(f"At most {MAX_PINS} parameters can be pinned")
                self._pins[identifier_path] = self._new_pin(identifier_path)
                self.settings.pins = list(self._pins)
                self._mark_dirty()
            self._attach(consumer, self._pins[identifier_path], element)
            self._touch()
        return identifier_path

    def unpin(self, path: str) -> None:
        """Remove a pin, given by identifier path or by the numbers of the parameter.

        Raises:
            ValueError: There is no such pin.
        """
        with self._lock:
            key = path if path in self._pins else None
            if key is None:
                for identifier_path, pin in self._pins.items():
                    if pin["element"] is not None and pin["element"].path_text == path:
                        key = identifier_path
            if key is None:
                raise ValueError(f"{path!r} is not pinned")
            pin = self._pins.pop(key)
            element = pin["element"]
            if element is not None:
                self._pin_of.pop(id(element), None)
            self.settings.pins = list(self._pins)
            self._mark_dirty()
            self._touch()
            consumer = self._consumer
        if element is not None and element.is_stream and consumer is not None and consumer.connected:
            try:
                consumer.unsubscribe(element)
            except ConsumerError:
                pass

    def _attach(self, consumer: Consumer, pin: dict[str, Any], element: Parameter) -> None:
        """Tie a pin to its parameter, and subscribe the parameter if it is a stream."""
        pin["element"] = element
        pin["error"] = ""
        self._pin_of[id(element)] = pin
        if element.is_stream:
            try:
                consumer.subscribe(element)
            except ConsumerError:
                pass

    def _resolve_pins(self, consumer: Consumer) -> None:
        """Look every pin up at the provider. Runs after connecting, outside the lock."""
        with self._lock:
            waiting = [pin for pin in self._pins.values() if pin["element"] is None]
        for pin in waiting:
            error = ""
            element: Element | None = None
            try:
                element = consumer.get(pin["identifier_path"])
                if not isinstance(element, Parameter):
                    error = "Not a parameter at this provider"
            except KeyError:
                error = "Not found at this provider"
            except TimeoutError:
                error = "The provider did not answer"
            except ConsumerError:
                return
            with self._lock:
                if consumer is not self._consumer or pin["identifier_path"] not in self._pins:
                    continue
                if error:
                    pin["error"] = error
                else:
                    self._attach(consumer, pin, element)  # type: ignore[arg-type]
                self._touch()

    # -- background timer ------------------------------------------------

    def start_timer(self) -> None:
        """Start the timer thread for reconnecting and for saving."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ember-consumer-engine", daemon=True)
            self._thread.start()

    def close(self) -> None:
        """Stop the timer, save the settings and close the connection."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._save_if_dirty(force=True)
            self._wanted = False
            consumer = self._consumer
        if consumer is not None:
            consumer.close()

    def _run(self) -> None:
        """Thread: save the settings and reconnect when that is wanted."""
        while not self._stop.wait(TICK_SECONDS):
            now = time.monotonic()
            with self._lock:
                self._save_if_dirty(now=now)
                retry = (self.settings.auto_reconnect and self._wanted and not self._connecting
                         and not self.connected and now >= self._retry_at)
            if retry:
                self.connect()

    def _mark_dirty(self) -> None:
        """Note the time of the first change that is not saved yet."""
        if self._dirty_since == 0.0:
            self._dirty_since = time.monotonic()

    def _save_if_dirty(self, now: float | None = None, force: bool = False) -> None:
        """Save the settings once changes have settled, or at once with ``force``."""
        if self._config_path is None or self._dirty_since == 0.0:
            return
        if not force and (now is None or now - self._dirty_since < SAVE_DELAY_SECONDS):
            return
        self._dirty_since = 0.0
        try:
            save_json(self._config_path, asdict(self.settings))
        except OSError:
            pass


__all__ = ["ConsumerEngine", "ConsumerSettings"]
