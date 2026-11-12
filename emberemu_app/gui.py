"""Tkinter desktop window: the Provider tab is built here, the other tabs in their modules."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from emberemu import Access as ParameterAccess
from emberemu import MatrixType, ParameterType

from . import __version__
from .access import Access
from .consumer_engine import ConsumerEngine
from .gui_colors import error_color
from .gui_common import clock, make_tree, show_log, sync_tree
from .gui_consumer import ConsumerTab
from .gui_settings import AboutTab, SettingsTab
from .provider_engine import ANIMATION_LABELS, KINDS, ProviderEngine

POLL_MS = 100
PARAMETER_TYPES = [member.name.lower() for member in ParameterType if member != ParameterType.NULL]
ACCESS_VALUES = [member.name.lower() for member in ParameterAccess]
MATRIX_TYPES = [member.name.lower() for member in MatrixType]
ENTRY_FIELDS = ("bind", "port")
FORM_ENTRIES = ("identifier", "description", "value", "minimum", "maximum", "step", "factor", "format",
                "stream_identifier", "arguments", "result", "target_count", "source_count")
FORM_CHOICES = {"kind": KINDS, "type": PARAMETER_TYPES, "access": ACCESS_VALUES,
                "animate": tuple(ANIMATION_LABELS.values()), "matrix_type": MATRIX_TYPES}


def _tuple_text(items: list) -> str:
    """``[["integer", "a"]]`` as ``"integer a"``."""
    return ", ".join(f"{kind} {name}" if name else kind for kind, name in items)


def _parse_tuple(text: str) -> list[list[Any]]:
    """``"integer a, real b"`` as ``[["integer", "a"], ["real", "b"]]``."""
    items = []
    for part in text.split(","):
        words = part.split()
        if words:
            items.append([words[0].lower(), " ".join(words[1:]) or None])
    return items


class ProviderTab(ttk.Frame):
    """The Provider tab: server with its consumers, the virtual tree, the element form and the activity log.

    The tab never talks to the network itself. Every input goes to the
    provider engine, and :meth:`poll` redraws the widgets when the engine
    changed. That keeps window and web interface in step.
    """

    def __init__(self, parent: tk.Widget, engine: ProviderEngine):
        """Build the tab.

        Args:
            parent: The notebook the tab belongs to.
            engine: The provider engine.
        """
        super().__init__(parent, padding=10)
        self.engine = engine
        self._loading = False
        self._seen_revision = -1
        self._input_error = ""
        self._shown: dict[str, tuple] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._form_for: str | None = None
        self._pending_selection: str | None = None   # path to select once it shows up in the tree

        self.vars: dict[str, tk.Variable] = {name: tk.StringVar() for name in ENTRY_FIELDS}
        self.vars["autostart"] = tk.BooleanVar()
        self.vars["animate"] = tk.BooleanVar()
        self.entries: dict[str, ttk.Entry] = {}
        self.form: dict[str, tk.Variable] = {name: tk.StringVar() for name in FORM_ENTRIES}
        for name in FORM_CHOICES:
            self.form[name] = tk.StringVar()
        self.form["is_online"] = tk.BooleanVar(value=True)

        left = ttk.Frame(self)
        right = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="n", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_server(left)
        self._build_form(left)
        self._build_tree(right)
        self._build_log(right)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self._new_form()

    # -- layout ----------------------------------------------------------

    def _group(self, parent: tk.Widget, title: str) -> ttk.LabelFrame:
        """A titled frame, stacked below the previous one."""
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _entry(self, parent: tk.Widget, name: str, width: int) -> ttk.Entry:
        """A text field of a setting. It is sent when the field is left or Return is pressed."""
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=width)
        entry.bind("<FocusOut>", lambda _event: self._push(name, self.vars[name].get()))
        entry.bind("<Return>", lambda _event: self._push(name, self.vars[name].get()))
        self.entries[name] = entry
        return entry

    def _build_server(self, parent: tk.Widget) -> None:
        """The Server group: address, port, switches, Start button and state."""
        frame = self._group(parent, "Server")
        ttk.Label(frame, text="Listen address").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Port").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(frame, "bind", 18).grid(row=1, column=0, sticky="w")
        self._entry(frame, "port", 7).grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(frame, text="Start with the program", variable=self.vars["autostart"],
                        command=lambda: self._push("autostart", bool(self.vars["autostart"].get()))
                        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Animate values", variable=self.vars["animate"],
                        command=lambda: self._push("animate", bool(self.vars["animate"].get()))
                        ).grid(row=3, column=0, columnspan=2, sticky="w")
        self.start_button = ttk.Button(frame, text="Start", command=self._toggle_serving)
        self.start_button.grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.state_label = ttk.Label(frame, text="Not serving")
        self.state_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.clients_label = ttk.Label(frame, text="", wraplength=330, justify="left")
        self.clients_label.grid(row=6, column=0, columnspan=2, sticky="w")
        self.error_label = ttk.Label(frame, text="", wraplength=330, justify="left")
        self.error_label.grid(row=7, column=0, columnspan=2, sticky="w")

    def _build_form(self, parent: tk.Widget) -> None:
        """The Element group: the fields of one element and the buttons that use them."""
        frame = self._group(parent, "Element")
        self.selected_label = ttk.Label(frame, text="", wraplength=330, justify="left")
        self.selected_label.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.form_widgets: dict[str, tuple[ttk.Label, tk.Widget]] = {}

        def field(name: str, label: str, row: int, column: int, width: int, span: int = 1) -> None:
            """One labelled field of the form. Choices become a combo box."""
            title = ttk.Label(frame, text=label)
            title.grid(row=row, column=column, columnspan=span, sticky="w", padx=(0, 8))
            if name in FORM_CHOICES:
                widget: tk.Widget = ttk.Combobox(frame, textvariable=self.form[name], width=width,
                                                 values=FORM_CHOICES[name], state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event: self._show_fields())
            else:
                widget = ttk.Entry(frame, textvariable=self.form[name], width=width)
            widget.grid(row=row + 1, column=column, columnspan=span, sticky="w", padx=(0, 8), pady=(0, 4))
            self.form_widgets[name] = (title, widget)

        field("kind", "Kind", 1, 0, 10)
        field("identifier", "Identifier", 1, 1, 22, span=3)
        field("description", "Description", 3, 0, 38, span=4)
        field("type", "Type", 5, 0, 10)
        field("access", "Access", 5, 1, 11)
        field("value", "Value", 5, 2, 12, span=2)
        field("minimum", "Minimum", 7, 0, 10)
        field("maximum", "Maximum", 7, 1, 11)
        field("step", "Step", 7, 2, 5)
        field("factor", "Factor", 7, 3, 5)
        field("format", "Format", 9, 0, 10)
        field("stream_identifier", "Stream id", 9, 1, 11)
        field("animate", "Animate", 9, 2, 14, span=2)
        field("arguments", "Arguments, e.g. integer a, integer b", 11, 0, 38, span=4)
        field("result", "Result, e.g. integer sum", 13, 0, 38, span=4)
        field("matrix_type", "Matrix type", 15, 0, 10)
        field("target_count", "Targets", 15, 1, 11)
        field("source_count", "Sources", 15, 2, 5)
        self.enumeration_label = ttk.Label(frame, text="Enumeration, one entry per line (Name=5 for own numbers)")
        self.enumeration_label.grid(row=17, column=0, columnspan=4, sticky="w")
        self.enumeration_text = tk.Text(frame, width=40, height=3, font="TkFixedFont", relief="solid",
                                        borderwidth=1)
        self.enumeration_text.grid(row=18, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.online_check = ttk.Checkbutton(frame, text="Online", variable=self.form["is_online"])
        self.online_check.grid(row=19, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=20, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.apply_button = ttk.Button(buttons, text="Apply to selected", command=lambda: self._submit(None))
        self.apply_button.grid(row=0, column=0, padx=(0, 6))
        self.add_child_button = ttk.Button(buttons, text="Add below selected", command=self._add_child)
        self.add_child_button.grid(row=0, column=1)
        ttk.Button(buttons, text="Add at the root", command=lambda: self._submit("")).grid(
            row=1, column=0, padx=(0, 6), pady=(4, 0))
        self.remove_button = ttk.Button(buttons, text="Remove selected", command=self._remove)
        self.remove_button.grid(row=1, column=1, pady=(4, 0))
        self.form_message = ttk.Label(frame, text="", wraplength=330, justify="left")
        self.form_message.grid(row=21, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_tree(self, parent: tk.Widget) -> None:
        """The Tree group: buttons and the tree table."""
        frame = ttk.LabelFrame(parent, text="Tree", padding=8)
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 6))
        for text, command in (("Load example", self._load_example), ("Remove all", self._clear),
                              ("Export...", self._export), ("Import...", self._import),
                              ("Set value...", self._edit_value)):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=(0, 6))
        self.count_label = ttk.Label(bar, text="")
        self.count_label.pack(side="left", padx=(6, 0))
        self.tree = make_tree(frame, 15)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._selection_changed())
        self.tree.bind("<Double-1>", lambda _event: self._edit_value())
        ttk.Label(frame, text="Select a row to edit the element. Double-click a parameter to set its value: "
                              "here every value can be set, the access only limits what consumers may do.",
                  wraplength=640, justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_log(self, parent: tk.Widget) -> None:
        """The Activity group."""
        frame = ttk.LabelFrame(parent, text="Activity", padding=8)
        frame.grid(row=1, column=0, sticky="we")
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="we")
        ttk.Label(top, text="What consumers ask for and change.").pack(side="left")
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
        self._seen_revision = -1  # redraw on the next poll, also after an error

    def _toggle_serving(self) -> None:
        """Start or stop serving."""
        if self.engine.running:
            self.engine.stop_serving()
        else:
            self.engine.start()
        self._seen_revision = -1

    def _selected(self) -> dict[str, Any] | None:
        """The element selected in the tree, or None."""
        chosen = self.tree.selection()
        return self._rows.get(chosen[0]) if chosen else None

    def _try(self, work, done: str = "") -> bool:
        """Run a change of the tree and show its error, if any, below the form."""
        try:
            work()
        except ValueError as exc:
            self.form_message.configure(text=str(exc), foreground=error_color(self))
            return False
        self.form_message.configure(text=done, foreground="")
        self._seen_revision = -1
        return True

    def _load_example(self) -> None:
        """Replace the tree with the example tree, after asking."""
        if messagebox.askokcancel("Ember Emu", "Replace the whole tree with the example tree?", parent=self):
            self._try(self.engine.load_example)

    def _clear(self) -> None:
        """Remove all elements, after asking."""
        if messagebox.askokcancel("Ember Emu", "Remove every element of the tree?", parent=self):
            self._try(self.engine.clear_tree)

    def _export(self) -> None:
        """Save the tree as a JSON file."""
        name = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                            initialfile="ember-emu-tree.json",
                                            filetypes=[("JSON", "*.json")])
        if not name:
            return
        try:
            Path(name).write_text(json.dumps(self.engine.export_tree(), indent=2, ensure_ascii=False),
                                  encoding="utf-8")
            self.form_message.configure(text=f"Saved {name}", foreground="")
        except OSError as exc:
            self.form_message.configure(text=f"Cannot save: {exc}", foreground=error_color(self))

    def _import(self) -> None:
        """Replace the tree with one from a JSON file, after asking."""
        name = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not name:
            return
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.form_message.configure(text=f"Cannot read the file: {exc}", foreground=error_color(self))
            return
        if messagebox.askokcancel("Ember Emu", "Replace the whole tree with the one from the file?",
                                  parent=self):
            self._try(lambda: self.engine.import_tree(data), "Imported.")

    def _edit_value(self) -> None:
        """Set the value of the selected parameter, or a connection of the selected matrix."""
        row = self._selected()
        if row is None:
            return
        if row["kind"] == "matrix":
            target = simpledialog.askstring("Connect", f"{row['identifier']}: target number", parent=self)
            if target is None:
                return
            sources = simpledialog.askstring(
                "Connect", "Source numbers, separated by commas. Empty for none.", parent=self)
            if sources is None:
                return
            numbers = [part.strip() for part in sources.split(",") if part.strip()]
            self._try(lambda: self.engine.connect_matrix(row["path"], target, numbers))
            return
        if row["kind"] != "parameter" or row["type"] == "trigger":
            return
        if row["type"] == "boolean":
            self._try(lambda: self.engine.update_element(row["path"], {"value": not row["value"]}))
            return
        prompt = f"New value for {row['identifier']} ({row['type']})"
        if row["entries"]:
            prompt += "\nEntries: " + ", ".join(name for name, _number in row["entries"])
        answer = simpledialog.askstring("Set value", prompt, parent=self,
                                        initialvalue="" if row["value"] is None else str(row["value"]))
        if answer is not None:
            self._try(lambda: self.engine.update_element(row["path"], {"value": answer}))

    # -- the element form -----------------------------------------------

    def _new_form(self) -> None:
        """Fill the form with sensible values for a new parameter."""
        self.form["kind"].set("parameter")
        self.form["type"].set("integer")
        self.form["access"].set("read_write")
        self.form["animate"].set(ANIMATION_LABELS[""])
        self.form["matrix_type"].set("one_to_n")
        self.form["target_count"].set("4")
        self.form["source_count"].set("4")
        self._show_fields()

    def _selection_changed(self) -> None:
        """Load a newly selected element into the form."""
        row = self._selected()
        if row is not None and self._form_for != row["path"]:
            self._form_for = row["path"]
            self._fill_form(row)
        elif row is None:
            self._form_for = None
        self._show_selection()

    def _show_selection(self) -> None:
        """Name the selected element and switch the buttons that need one."""
        row = self._selected()
        self.apply_button.configure(state="normal" if row else "disabled")
        self.remove_button.configure(state="normal" if row else "disabled")
        self.add_child_button.configure(state="normal" if row and row["kind"] == "node" else "disabled")
        self.form_widgets["kind"][1].configure(state="disabled" if row else "readonly")
        self.selected_label.configure(
            text=f"Selected: {row['path']} {row['identifier_path']} ({row['kind']})" if row
            else "Nothing selected. Fill in the fields and add a new element.")

    def _fill_form(self, row: dict[str, Any]) -> None:
        """Put the properties of an element into the form."""
        form = self.form
        form["kind"].set(row["kind"])
        form["identifier"].set(row["identifier"])
        form["description"].set(row["description"])
        form["is_online"].set(row["online"])
        if row["kind"] == "parameter":
            form["type"].set(row["type"])
            form["access"].set(row["access"])
            form["value"].set("" if row["value"] is None else str(row["value"]))
            for name in ("minimum", "maximum", "step", "factor", "stream_identifier"):
                form[name].set("" if row[name] is None else str(row[name]))
            form["format"].set(row["format"])
            form["animate"].set(ANIMATION_LABELS[row.get("animate") or ""])
            plain = all(number == index for index, (_name, number) in enumerate(row["entries"]))
            self.enumeration_text.delete("1.0", "end")
            self.enumeration_text.insert("1.0", "\n".join(
                name if plain else f"{name}={number}" for name, number in row["entries"]))
        elif row["kind"] == "function":
            form["arguments"].set(_tuple_text(row["arguments"]))
            form["result"].set(_tuple_text(row["result"]))
        elif row["kind"] == "matrix":
            form["matrix_type"].set(row["matrix_type"])
            form["target_count"].set(str(row["target_count"]))
            form["source_count"].set(str(row["source_count"]))
        self.form_message.configure(text="")
        self._show_fields()

    def _show_fields(self) -> None:
        """Show only the fields that belong to the chosen kind and type."""
        kind = self.form["kind"].get()
        kind_of_value = self.form["type"].get()
        numeric = kind_of_value in ("integer", "real")
        visible = {"kind", "identifier", "description"}
        if kind == "parameter":
            visible |= {"type", "access", "stream_identifier", "animate"}
            if kind_of_value != "trigger":
                visible.add("value")
            if numeric:
                visible |= {"minimum", "maximum", "step", "format"}
            if kind_of_value == "integer":
                visible.add("factor")
        elif kind == "function":
            visible |= {"arguments", "result"}
        elif kind == "matrix":
            visible |= {"matrix_type", "target_count", "source_count"}
        for name, (title, widget) in self.form_widgets.items():
            for item in (title, widget):
                if name in visible:
                    item.grid()
                else:
                    item.grid_remove()
        for item in (self.enumeration_label, self.enumeration_text):
            if kind == "parameter" and kind_of_value == "enum":
                item.grid()
            else:
                item.grid_remove()
        if kind in ("node", "parameter"):
            self.online_check.grid()
        else:
            self.online_check.grid_remove()

    def _read_form(self) -> dict[str, Any]:
        """The form as the description of an element, as the engine takes it."""
        form = self.form
        kind = form["kind"].get()
        data: dict[str, Any] = {"kind": kind, "identifier": form["identifier"].get().strip(),
                                "description": form["description"].get()}
        if kind in ("node", "parameter"):
            data["is_online"] = bool(form["is_online"].get())
        if kind == "parameter":
            data["type"] = form["type"].get()
            data["access"] = form["access"].get()
            for name in ("value", "minimum", "maximum", "step", "factor", "format", "stream_identifier"):
                data[name] = form[name].get()
            data["animate"] = next(key for key, label in ANIMATION_LABELS.items()
                                   if label == form["animate"].get())
            lines = [line.strip() for line in self.enumeration_text.get("1.0", "end").splitlines()
                     if line.strip()]
            pairs = [line.rsplit("=", 1) for line in lines]
            if lines and all(len(pair) == 2 and pair[1].strip().lstrip("-").isdigit() for pair in pairs):
                data["enum_map"] = [[name.strip(), int(number)] for name, number in pairs]
                data["enumeration"] = None
            else:
                data["enumeration"] = lines
                data["enum_map"] = None
        elif kind == "function":
            data["arguments"] = _parse_tuple(form["arguments"].get())
            data["result"] = _parse_tuple(form["result"].get())
        elif kind == "matrix":
            data["type"] = form["matrix_type"].get()
            data["target_count"] = form["target_count"].get()
            data["source_count"] = form["source_count"].get()
        return data

    def _add_child(self) -> None:
        """Add the element of the form below the selected node."""
        row = self._selected()
        if row is not None:
            self._submit(row["path"])

    def _submit(self, parent: str | None) -> None:
        """Send the form: change the selected element, or add a new one below ``parent``."""
        data = self._read_form()
        if parent is None:
            row = self._selected()
            if row is not None and self._try(lambda: self.engine.update_element(row["path"], data), "Applied."):
                self._form_for = None
            return
        added: list[str] = []
        if self._try(lambda: added.append(self.engine.add_element(parent, data))):
            self.form_message.configure(text=f"Added {added[0]}.", foreground="")
            self._pending_selection = added[0]

    def _remove(self) -> None:
        """Remove the selected element, after asking."""
        row = self._selected()
        if row is None:
            return
        below = " and everything below it" if row["children"] else ""
        if messagebox.askokcancel("Ember Emu", f"Remove {row['identifier_path']}{below}?", parent=self):
            self._try(lambda: self.engine.remove_element(row["path"]))

    # -- engine -> widgets ----------------------------------------------

    def poll(self) -> None:
        """Redraw the tab if the provider engine changed since the last look."""
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
            for name in ("autostart", "animate"):
                if self.vars[name].get() != settings[name]:
                    self.vars[name].set(settings[name])
        finally:
            self._loading = False

        count = len(status["clients"])
        self.start_button.configure(text="Stop" if status["running"] else "Start")
        self.state_label.configure(
            text=f"Serving: {status['description']}, {count} consumer{'' if count == 1 else 's'}"
            if status["running"] else "Not serving")
        self.error_label.configure(text=self._input_error or status["error"],
                                   foreground=error_color(self))
        self.clients_label.configure(text="\n".join(
            f"{client['address']}  since {clock(client['since'])}, {client['requests']} requests"
            for client in status["clients"][:6]))

        self._rows = {row["path"]: row for row in data["tree"]}
        sync_tree(self.tree, data["tree"], self._shown, open_new=True, lazy=False)
        pending = self._pending_selection
        if pending and self.tree.exists(pending):
            self.tree.selection_set(pending)
            self.tree.see(pending)
            self._pending_selection = None
        total = status["element_count"]
        self.count_label.configure(
            text=f"Showing {len(data['tree'])} of {total} elements" if total > len(data["tree"])
            else f"{total} element{'' if total == 1 else 's'}")
        self._show_selection()
        show_log(self.log_text, status["log"], "Nothing happened yet.")


class MainWindow:
    """The desktop window with its tabs: Provider, Consumer, Settings and About.

    Args:
        root: The Tk root window.
        provider: The provider engine.
        consumer: The consumer engine.
        access: The access settings. Without them there is no Settings tab.
        server: The web server, for its address in status bar and Settings.
    """

    def __init__(self, root: tk.Tk, provider: ProviderEngine, consumer: ConsumerEngine,
                 access: Access | None = None, server=None):
        """Build all tabs and start the poll. See the class description for the arguments."""
        self.root = root
        self.server = server
        root.title(f"Ember Emu {__version__}")
        try:
            self.logo = tk.PhotoImage(file=str(Path(__file__).with_name("static") / "logo.png"))
            root.iconphoto(True, self.logo)
        except tk.TclError:
            self.logo = None  # the window works without its picture

        notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.provider_tab = ProviderTab(notebook, provider)
        notebook.add(self.provider_tab, text="Provider")
        self.consumer_tab = ConsumerTab(notebook, consumer)
        notebook.add(self.consumer_tab, text="Consumer")
        self.settings_tab = None
        if access is not None:
            self.settings_tab = SettingsTab(notebook, access, server)
            notebook.add(self.settings_tab, text="Settings")
        notebook.add(AboutTab(notebook, self.logo), text="About")
        self.web_label = ttk.Label(root, text="", padding=(10, 0, 10, 6))
        self.web_label.grid(row=1, column=0, sticky="e")
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self._poll()

    def _poll(self) -> None:
        """Let every tab redraw itself if needed, ten times a second."""
        self.provider_tab.poll()
        self.consumer_tab.poll()
        if self.settings_tab is not None:
            self.settings_tab.poll()
        self.web_label.configure(text=self._web_state())
        self.root.after(POLL_MS, self._poll)

    def _web_state(self) -> str:
        """Where the web interface is, for the line at the bottom."""
        server = self.server
        if server is None:
            return ""
        if server.error:
            return "Web server: error, see Settings"
        if not server.running:
            return "Web interface and REST API: off"
        return f"Web: {server.url}"


def run_gui(provider: ProviderEngine, consumer: ConsumerEngine, access: Access | None = None,
            server=None) -> None:
    """Open the window and run until it is closed.

    Args:
        provider: The provider engine.
        consumer: The consumer engine.
        access: The access settings, for the Settings tab.
        server: The web server, for its address and state.
    """
    root = tk.Tk()
    MainWindow(root, provider, consumer, access, server)
    root.mainloop()
