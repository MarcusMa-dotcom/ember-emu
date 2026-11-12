"""What the Provider tab and the Consumer tab of the window share: tree tables and the log."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .gui_colors import error_color, muted_color

TREE_COLUMNS = (("path", "Path", 70), ("type", "Type", 90), ("value", "Value", 140),
                ("access", "Access", 75), ("details", "Details", 160))
PLACEHOLDER = "…"   # stands in for children that are not loaded yet


def clock(stamp: float | None) -> str:
    """Seconds since 1970 as local time of day, "never" for nothing."""
    return time.strftime("%H:%M:%S", time.localtime(stamp)) if stamp else "never"


def type_text(row: dict[str, Any]) -> str:
    """Type column of a tree row."""
    if row["kind"] == "parameter":
        return row["type"]
    if row["kind"] == "matrix":
        return f"matrix {row['matrix_type'].replace('_', ' ')}"
    return row["kind"]


def value_text(row: dict[str, Any]) -> str:
    """Value column of a tree row: the value as shown to a user."""
    if row["kind"] != "parameter":
        return ""
    if row["type"] == "trigger":
        return "(trigger)"
    return row["display"] if row["display"] != "" else str(row["value"] if row["value"] is not None else "")


def details_text(row: dict[str, Any]) -> str:
    """Details column of a tree row: range, stream, signature, connections, description."""
    parts: list[str] = []
    if row["kind"] == "parameter":
        if row["minimum"] is not None or row["maximum"] is not None:
            low = "" if row["minimum"] is None else row["minimum"]
            high = "" if row["maximum"] is None else row["maximum"]
            parts.append(f"{low} to {high}")
        if row["step"] is not None:
            parts.append(f"step {row['step']}")
        if row["factor"] is not None:
            parts.append(f"factor {row['factor']}")
        if row["stream_identifier"] is not None:
            block = f", {row['stream_format']} at byte {row['stream_offset']}" if row["stream_format"] else ""
            parts.append(f"stream {row['stream_identifier']}{block}")
        if row.get("animate"):
            parts.append(f"animated: {row['animate']}")
    elif row["kind"] == "function":
        def tuple_text(items: list) -> str:
            return ", ".join(f"{kind} {name}" if name else kind for kind, name in items)
        parts.append(f"({tuple_text(row['arguments'])}) gives ({tuple_text(row['result'])})")
    elif row["kind"] == "matrix":
        parts.append(f"{row['target_count']} targets, {row['source_count']} sources")
        pairs = [f"{target} from {'+'.join(str(s) for s in sources)}"
                 for target, sources in row["connections"].items()]
        if pairs:
            parts.append(", ".join(pairs[:12]) + (" ..." if len(pairs) > 12 else ""))
    if not row["online"]:
        parts.append("offline")
    if row["description"]:
        parts.append(row["description"])
    return " · ".join(parts)


def make_tree(parent: tk.Widget, height: int) -> ttk.Treeview:
    """A tree table with the usual columns and a scroll bar, placed in ``parent`` with grid."""
    tree = ttk.Treeview(parent, columns=[column[0] for column in TREE_COLUMNS], height=height,
                        selectmode="browse")
    tree.heading("#0", text="Identifier", anchor="w")
    tree.column("#0", width=200, anchor="w", stretch=False)
    for key, title, width in TREE_COLUMNS:
        tree.heading(key, text=title, anchor="w")
        tree.column(key, width=width, anchor="w", stretch=key == "details")
    scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=1, column=0, sticky="nsew")
    scroll.grid(row=1, column=1, sticky="ns")
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)
    tree.tag_configure("offline", foreground=muted_color(tree))
    return tree


def sync_tree(tree: ttk.Treeview, rows: list[dict[str, Any]], shown: dict[str, tuple],
              open_new: bool, lazy: bool) -> None:
    """Bring a tree table in line with the elements of a snapshot.

    Rows are added, changed and removed in place, so the selection and the
    open nodes stay as the user left them.

    Args:
        tree: The table.
        rows: The elements, parents before children.
        shown: What the table shows now, kept between calls: path to row content.
        open_new: Whether a new node starts opened.
        lazy: Whether nodes whose children are not loaded yet get a placeholder
            child, so they can be opened to load them.
    """
    wanted = {row["path"] for row in rows}
    for path in [path for path in shown if path not in wanted]:
        if tree.exists(path):
            tree.delete(path)
        del shown[path]
    for row in rows:
        path = row["path"]
        parent = path.rpartition(".")[0]
        content = (row["identifier"] or f"({row['number']})", path, type_text(row), value_text(row),
                   row["access"].replace("_", " ") if row["kind"] == "parameter" else "",
                   details_text(row), row["online"])
        if not tree.exists(path):
            if parent and not tree.exists(parent):
                continue  # the parent lies beyond what the snapshot lists
            tree.insert(parent, "end", iid=path, text=content[0], values=content[1:6],
                        open=open_new, tags=() if row["online"] else ("offline",))
        elif shown.get(path) != content:
            tree.item(path, text=content[0], values=content[1:6],
                      tags=() if row["online"] else ("offline",))
        shown[path] = content
        if lazy:
            placeholder = path + "/placeholder"
            needs = row["kind"] == "node" and not row["loaded"]
            if needs and not tree.exists(placeholder):
                tree.insert(path, "end", iid=placeholder, text=PLACEHOLDER)
            elif not needs and tree.exists(placeholder):
                tree.delete(placeholder)


def show_log(widget: tk.Text, entries: list[dict[str, Any]], empty: str) -> None:
    """Write an activity log into a text widget, newest entry first."""
    widget.tag_configure("problem", foreground=error_color(widget))
    widget.tag_configure("other", foreground=muted_color(widget))
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    for entry in reversed(entries):
        tag = ("problem",) if entry["action"] in ("refused", "error") else ()
        head = "  ".join(part for part in (clock(entry["time"]), entry.get("client", ""), entry["action"]) if part)
        what = ": ".join(part for part in (entry["identifier_path"], entry["text"]) if part)
        widget.insert("end", f"{head}  {what}\n" if what else f"{head}\n", tag)
    if not entries:
        widget.insert("end", empty, ("other",))
    widget.configure(state="disabled")


def in_background(work: Callable[[], Any], failed: Callable[[str], None]) -> None:
    """Run something that may wait for the network without freezing the window.

    Args:
        work: What to do.
        failed: Called with the message if ``work`` raises ``ValueError``.
    """
    def run() -> None:
        """Do the work and report how it went."""
        try:
            work()
            failed("")
        except ValueError as exc:
            failed(str(exc))
    threading.Thread(target=run, daemon=True).start()
