"""Consumer tab of the Tkinter window: connection, pinned parameters and the tree of a provider."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import simpledialog, ttk
from typing import Any

from .consumer_engine import ConsumerEngine
from .gui_colors import error_color, muted_color
from .gui_common import clock, in_background, make_tree, show_log, sync_tree, value_text

ENTRY_FIELDS = ("host", "port")
PIN_COLUMNS = (("value", "Value", 110), ("changes", "Changes", 58), ("changed", "Last change", 78))
# Why a pin has no value, short enough for the value column.
PIN_REASONS = {"Not found at this provider": "not found", "Not a parameter at this provider": "no parameter",
               "The provider did not answer": "no answer"}


class ConsumerTab(ttk.Frame):
    """The Consumer tab.

    Requests that wait for the provider run in background threads, so the
    window stays usable. :meth:`poll` redraws the widgets when the consumer
    engine changed.
    """

    def __init__(self, parent: tk.Widget, engine: ConsumerEngine):
        """Build the tab.

        Args:
            parent: The notebook the tab belongs to.
            engine: The consumer engine.
        """
        super().__init__(parent, padding=10)
        self.engine = engine
        self._loading = False
        self._seen_revision = -1
        self._input_error = ""
        self._shown: dict[str, tuple] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._pins: dict[str, dict[str, Any]] = {}
        self._open_all = False
        self._result = ""               # outcome of the last function call

        self.vars: dict[str, tk.Variable] = {name: tk.StringVar() for name in ENTRY_FIELDS}
        self.vars["keepalive"] = tk.BooleanVar()
        self.vars["auto_reconnect"] = tk.BooleanVar()
        self.check_var = tk.BooleanVar(value=True)
        self.entries: dict[str, ttk.Entry] = {}

        left = ttk.Frame(self)
        right = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="n", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_connection(left)
        self._build_pins(left)
        self._build_tree(right)
        self._build_log(right)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

    # -- layout ----------------------------------------------------------

    def _entry(self, parent: tk.Widget, name: str, width: int) -> ttk.Entry:
        """A text field of a setting. It is sent when the field is left or Return is pressed."""
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=width)
        entry.bind("<FocusOut>", lambda _event: self._push(name, self.vars[name].get()))
        entry.bind("<Return>", lambda _event: self._push(name, self.vars[name].get()))
        self.entries[name] = entry
        return entry

    def _build_connection(self, parent: tk.Widget) -> None:
        """The Connection group: host, port, switches, Connect button and state."""
        frame = ttk.LabelFrame(parent, text="Connection", padding=8)
        frame.pack(fill="x", pady=(0, 8))
        ttk.Label(frame, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Port").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(frame, "host", 22).grid(row=1, column=0, sticky="w")
        self._entry(frame, "port", 7).grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(frame, text="Keep-alive", variable=self.vars["keepalive"],
                        command=lambda: self._push("keepalive", bool(self.vars["keepalive"].get()))
                        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Reconnect by itself", variable=self.vars["auto_reconnect"],
                        command=lambda: self._push("auto_reconnect",
                                                   bool(self.vars["auto_reconnect"].get()))
                        ).grid(row=3, column=0, columnspan=2, sticky="w")
        self.connect_button = ttk.Button(frame, text="Connect", command=self._toggle_connection)
        self.connect_button.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.state_label = ttk.Label(frame, text="Disconnected", wraplength=470, justify="left")
        self.state_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.error_label = ttk.Label(frame, text="", wraplength=470, justify="left")
        self.error_label.grid(row=6, column=0, columnspan=2, sticky="w")

    def _build_pins(self, parent: tk.Widget) -> None:
        """The group with the table of pinned parameters."""
        frame = ttk.LabelFrame(parent, text="Pinned parameters", padding=8)
        frame.pack(fill="both", expand=True, pady=(0, 8))
        self.pins = ttk.Treeview(frame, columns=[column[0] for column in PIN_COLUMNS], height=12,
                                 selectmode="browse")
        self.pins.heading("#0", text="Parameter", anchor="w")
        self.pins.column("#0", width=250, anchor="w", stretch=True)
        for key, title, width in PIN_COLUMNS:
            self.pins.heading(key, text=title, anchor="w")
            self.pins.column(key, width=width, anchor="w", stretch=False)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.pins.yview)
        self.pins.configure(yscrollcommand=scroll.set)
        self.pins.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.pins.tag_configure("missing", foreground=error_color(self))
        self.pins.bind("<Double-1>", lambda _event: self._act(self._selected_pin()))
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Set value...", command=lambda: self._act(self._selected_pin())).pack(
            side="left", padx=(0, 6))
        ttk.Button(buttons, text="Unpin", command=self._unpin_selected).pack(side="left")
        ttk.Label(frame, wraplength=470, justify="left",
                  text="Pins are remembered by name and found again after every connect."
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_tree(self, parent: tk.Widget) -> None:
        """The Tree group: buttons and the tree table."""
        frame = ttk.LabelFrame(parent, text="Tree", padding=8)
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 6))
        for text, command in (("Load and expand all", self._expand_all), ("Read again", self._read_again),
                              ("Pin / Unpin", self._toggle_pin),
                              ("Set value / Call...", lambda: self._act(self._selected_row()))):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(bar, text="Check values before sending", variable=self.check_var).pack(
            side="left", padx=(6, 0))
        self.tree = make_tree(frame, 15)
        self.tree.bind("<<TreeviewOpen>>", lambda _event: self._opened())
        self.tree.bind("<Double-1>", lambda _event: self._act(self._selected_row()))
        self.tree_note = ttk.Label(frame, text="Not connected.", wraplength=640, justify="left")
        self.tree_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_log(self, parent: tk.Widget) -> None:
        """The Activity group."""
        frame = ttk.LabelFrame(parent, text="Activity", padding=8)
        frame.grid(row=1, column=0, sticky="we")
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="we")
        ttk.Label(top, text="What was sent to the provider, and changes of pinned parameters.").pack(side="left")
        ttk.Button(top, text="Clear", command=self.engine.clear_log).pack(side="right")
        self.log_text = tk.Text(frame, width=60, height=6, wrap="word", font="TkFixedFont",
                                state="disabled", relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        frame.columnconfigure(0, weight=1)

    # -- user input -> engine -------------------------------------------

    def _push(self, name: str, value: Any) -> None:
        """Send one setting to the engine and remember an error for the display."""
        if self._loading:
            return
        try:
            self.engine.update(**{name: value})
            self._input_error = ""
        except ValueError as exc:
            self._input_error = str(exc)
        self._seen_revision = -1

    def _failed(self, message: str) -> None:
        """Called from a background thread with the outcome of a request."""
        self._input_error = message
        if message:
            self._result = ""
        self._seen_revision = -1

    def _toggle_connection(self) -> None:
        """Connect or disconnect."""
        if self.engine.connected:
            self.engine.disconnect()
        else:
            # Connecting can take seconds, keep the window responsive.
            threading.Thread(target=self.engine.connect, daemon=True).start()
        self._input_error = ""
        self._seen_revision = -1

    def _selected_row(self) -> dict[str, Any] | None:
        """The element selected in the tree, or None."""
        chosen = self.tree.selection()
        return self._rows.get(chosen[0]) if chosen else None

    def _selected_pin(self) -> dict[str, Any] | None:
        """The selected pin if its parameter was found, or None."""
        chosen = self.pins.selection()
        pin = self._pins.get(chosen[0]) if chosen else None
        return pin if pin and pin["found"] else None

    def _opened(self) -> None:
        """A node was opened: load its children if they are not there yet."""
        path = self.tree.focus()
        row = self._rows.get(path)
        if row is not None and row["kind"] == "node" and not row["loaded"]:
            in_background(lambda: self.engine.expand(path), self._failed)

    def _expand_all(self) -> None:
        """Load the whole tree and open every node as it arrives."""
        self._open_all = True
        in_background(self.engine.expand_all, self._failed)

    def _read_again(self) -> None:
        """Ask the provider again for the root and for every open node."""
        opened = [path for path, row in self._rows.items()
                  if row["kind"] == "node" and row["loaded"] and self.tree.exists(path)
                  and self.tree.item(path, "open")][:100]

        def work() -> None:
            """Ask for the root, then for each open node."""
            self.engine.expand("", refresh=True)
            for path in opened:
                self.engine.expand(path, refresh=True)
        in_background(work, self._failed)

    def _toggle_pin(self) -> None:
        """Pin the selected parameter, or unpin it if it is pinned."""
        row = self._selected_row()
        if row is None or row["kind"] != "parameter":
            self._failed("Select a parameter in the tree to pin it")
            return
        if row["pinned"]:
            in_background(lambda: self.engine.unpin(row["identifier_path"]), self._failed)
        else:
            in_background(lambda: self.engine.pin(row["path"]), self._failed)

    def _unpin_selected(self) -> None:
        """Remove the pin selected in the pin table."""
        chosen = self.pins.selection()
        if chosen:
            in_background(lambda: self.engine.unpin(chosen[0]), self._failed)

    def _act(self, row: dict[str, Any] | None) -> None:
        """Do what fits the element: set a value, fire a trigger, call a function, connect a matrix."""
        if row is None:
            return
        self._result = ""
        path = row["path"]
        check = bool(self.check_var.get())
        if row["kind"] == "function":
            values: list[str] = []
            if row["arguments"]:
                names = ", ".join(f"{name} ({kind})" if name else kind for kind, name in row["arguments"])
                answer = simpledialog.askstring("Call", f"{row['identifier']}: {names}\n"
                                                "Values, separated by commas", parent=self)
                if answer is None:
                    return
                values = [part.strip() for part in answer.split(",")]

            def call() -> None:
                result = self.engine.invoke(path, values)
                self._result = f"{row['identifier']} returned: {', '.join(str(v) for v in result) or 'nothing'}"
            in_background(call, self._failed)
        elif row["kind"] == "matrix":
            target = simpledialog.askstring("Connect", f"{row['identifier']}: target number", parent=self)
            if target is None:
                return
            sources = simpledialog.askstring(
                "Connect", "Source numbers, separated by commas. Empty for none.", parent=self)
            if sources is None:
                return
            numbers = [part.strip() for part in sources.split(",") if part.strip()]
            in_background(lambda: self.engine.set_connection(path, target, numbers), self._failed)
        elif row["kind"] == "parameter":
            if row["type"] == "trigger":
                in_background(lambda: self.engine.trigger(path), self._failed)
                return
            if check and not row["writable"]:
                self._failed(f"{row['identifier']} is read only. Switch off the check to try anyway.")
                return
            if row["type"] == "boolean":
                in_background(lambda: self.engine.set_value(path, not row["value"], check), self._failed)
                return
            prompt = f"New value for {row['identifier']} ({row['type']})"
            if row["entries"]:
                prompt += "\nEntries: " + ", ".join(name for name, _number in row["entries"])
            answer = simpledialog.askstring("Set value", prompt, parent=self,
                                            initialvalue="" if row["value"] is None else str(row["value"]))
            if answer is not None:
                in_background(lambda: self.engine.set_value(path, answer, check), self._failed)

    # -- engine -> widgets ----------------------------------------------

    def poll(self) -> None:
        """Redraw the tab if the consumer engine changed since the last look."""
        if self.engine.revision != self._seen_revision:
            self._load(self.engine.snapshot())

    def _load(self, data: dict) -> None:
        """Draw a snapshot of the engine."""
        self._seen_revision = data["revision"]
        settings = data["settings"]
        status = data["status"]
        focused = self.focus_get()

        self._loading = True
        try:
            for name in ENTRY_FIELDS:
                if self.entries[name] is not focused and self.vars[name].get() != str(settings[name]):
                    self.vars[name].set(str(settings[name]))
            for name in ("keepalive", "auto_reconnect"):
                if self.vars[name].get() != settings[name]:
                    self.vars[name].set(settings[name])
        finally:
            self._loading = False

        if status["connecting"]:
            self.connect_button.configure(text="Connecting...", state="disabled")
        else:
            self.connect_button.configure(text="Disconnect" if status["connected"] else "Connect",
                                          state="normal")
        state = "Disconnected"
        if status["connected"]:
            count = status["element_count"]
            state = f"Connected: {status['description']}, {count} element{'' if count == 1 else 's'} read"
            if status["glow_version"]:
                state += f", Glow {status['glow_version']}"
            if status["loading"]:
                state += ". Loading the whole tree..."
        self.state_label.configure(text=state)
        self.error_label.configure(
            text=self._input_error or status["error"] or self._result,
            foreground=error_color(self) if (self._input_error or status["error"]) else "")

        # Pins
        self._pins = {pin["identifier_path"]: pin for pin in status["pins"]}
        for iid in self.pins.get_children():
            if iid not in self._pins:
                self.pins.delete(iid)
        for pin in status["pins"]:
            iid = pin["identifier_path"]
            if pin["found"]:
                values = (value_text(pin), pin["changes"], clock(pin["changed"]))
                tags: tuple = ()
            else:
                reason = PIN_REASONS.get(pin["error"], pin["error"]) or (
                    "searching" if status["connected"] else "not connected")
                values = (reason, "", "")
                tags = ("missing",)
            if self.pins.exists(iid):
                self.pins.item(iid, values=values, tags=tags)
            else:
                self.pins.insert("", "end", iid=iid, text=iid, values=values, tags=tags)

        # Tree
        self._rows = {row["path"]: row for row in data["tree"]}
        self.tree.tag_configure("offline", foreground=muted_color(self))
        sync_tree(self.tree, data["tree"], self._shown, open_new=False, lazy=True)
        if self._open_all:
            for row in data["tree"]:
                if row["loaded"] and row["children"] and self.tree.exists(row["path"]):
                    self.tree.item(row["path"], open=True)
            if not status["loading"]:
                self._open_all = False
        if not status["connected"]:
            note = ("Not connected. The tree shows what was read before the connection ended."
                    if data["tree"] else "Not connected.")
        elif not data["tree"]:
            note = ("The provider has no elements." if status["root_loaded"]
                    else "Waiting for the root elements of the provider.")
        else:
            note = ("Open a node to load its children. Double-click a parameter to set its value. "
                    "The provider then reports changes of what was loaded by itself.")
        self.tree_note.configure(text=note)
        show_log(self.log_text, status["log"], "Nothing happened yet.")
