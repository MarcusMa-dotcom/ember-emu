"use strict";

// Shared by the Provider and the Consumer view: requests to the server,
// the notice line, and the drawing of tree tables with editable values.

const POLL_MS = 400;
const UNREACHABLE = "Ember Emu is not reachable.";

const $ = (id) => document.getElementById(id);

// Requests that change something go out one after another, so the server
// sees them in the order they were made.
let queue = Promise.resolve();
function enqueue(task) {
  queue = queue.then(task, task);
  return queue;
}

// Send a request to the server and return its JSON answer.
//   path:   address below the page, for example "/ui/api/provider"
//   body:   data to send as JSON; without it the request is a GET
//   method: HTTP method for requests with a body, POST unless given
// The answer carries "error" if the server refused. A lost session leads to the login page.
async function api(path, body, method) {
  const options = body === undefined
    ? {}
    : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (response.status === 401) {
    location.href = "/login";  // the session ended
    throw new Error("login required");
  }
  const data = await response.json();
  if (!response.ok && !data.error) data.error = `Request failed (${response.status})`;
  return data;
}

// Show a line at the top of the page, or hide it with an empty message.
//   info: true for a plain message, otherwise it is shown as an error
function showNotice(message, info) {
  const notice = $("notice");
  notice.textContent = message;
  notice.hidden = !message;
  notice.classList.toggle("info", Boolean(info));
}

// Run a request that changes something, report a refusal, then redraw.
//   work:    function that returns the promise of api(...)
//   refresh: function that fetches and draws the new state
// Returns the answer, or null if the server refused or cannot be reached.
function act(work, refresh) {
  return enqueue(async () => {
    try {
      const data = await work();
      showNotice(data.error || "");
      if (refresh) await refresh();
      return data.error ? null : data;
    } catch (error) {
      showNotice(UNREACHABLE);
      return null;
    }
  });
}

// Seconds since 1970 as local time of day, "never" for nothing.
function clockTime(epochSeconds) {
  if (!epochSeconds) return "never";
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour12: false });
}

// Make an element with a class and a text.
function make(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

// Write a value into a settings control unless the user is editing it right now.
function put(id, value) {
  const element = $(id);
  if (element.type === "checkbox") {
    element.checked = Boolean(value);
    return;
  }
  const text = value === null || value === undefined ? "" : String(value);
  if (document.activeElement !== element && element.value !== text) element.value = text;
}

// True while the user types into a field of this row. Such a row is left alone.
function isTyping(row) {
  const active = document.activeElement;
  if (!active || !row.contains(active)) return false;
  return active.tagName === "SELECT" || (active.tagName === "INPUT" && active.type !== "checkbox");
}

// Keep one table row per entry and rebuild a row only when its data changed.
//   body:  the <tbody>
//   items: the entries to show, in order
//   key:   function giving the unchanging name of an entry
//   state: function giving extra text that also forces a redraw, such as "selected"
//   build: function (entry, row) that returns the cells of the row
function syncRows(body, items, key, state, build) {
  const known = new Map(Array.from(body.children).map((row) => [row.dataset.key, row]));
  const seen = new Set();
  let previous = null;
  items.forEach((item) => {
    const name = key(item);
    seen.add(name);
    let row = known.get(name);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.key = name;
    }
    const signature = JSON.stringify(item) + state(item);
    if (row.dataset.signature !== signature && !isTyping(row)) {
      // A button that had the focus gets it back after the redraw.
      const active = document.activeElement;
      const role = active && row.contains(active) ? active.dataset.role : null;
      row.dataset.signature = signature;
      row.replaceChildren(...build(item, row));
      if (role) {
        const again = row.querySelector(`[data-role="${role}"]`);
        if (again) again.focus();
      }
    }
    const expected = previous ? previous.nextSibling : body.firstChild;
    if (expected !== row) body.insertBefore(row, expected);
    previous = row;
  });
  known.forEach((row, name) => { if (!seen.has(name)) row.remove(); });
}

// The rows of a tree that are visible: everything below a closed node is left out.
//   rows:   all elements, parents before children, each with "depth"
//   isOpen: function telling whether the children of an element are shown
function visibleRows(rows, isOpen) {
  const visible = [];
  let hiddenBelow = null;
  rows.forEach((row) => {
    if (hiddenBelow !== null && row.depth > hiddenBelow) return;
    hiddenBelow = null;
    visible.push(row);
    if (row.children > 0 || row.kind === "node") {
      if (!isOpen(row)) hiddenBelow = row.depth;
    }
  });
  return visible;
}

// Type column of a tree row.
function typeText(row) {
  if (row.kind === "parameter") return row.type;
  if (row.kind === "matrix") return `matrix ${row.matrix_type.replace(/_/g, " ")}`;
  return row.kind;
}

// Details column of a tree row: range, enumeration, stream, signature, connections.
function detailsText(row) {
  const parts = [];
  if (row.kind === "parameter") {
    if (row.minimum !== null || row.maximum !== null) {
      parts.push(`${row.minimum === null ? "" : row.minimum} to ${row.maximum === null ? "" : row.maximum}`);
    }
    if (row.step !== null) parts.push(`step ${row.step}`);
    if (row.factor !== null) parts.push(`factor ${row.factor}`);
    if (row.stream_identifier !== null) {
      parts.push(`stream ${row.stream_identifier}`
        + (row.stream_format ? `, ${row.stream_format} at byte ${row.stream_offset}` : ""));
    }
    if (row.animate) parts.push(`animated: ${row.animate}`);
  } else if (row.kind === "function") {
    const list = (items) => items.map(([type, name]) => (name ? `${type} ${name}` : type)).join(", ");
    parts.push(`(${list(row.arguments)}) gives (${list(row.result)})`);
  } else if (row.kind === "matrix") {
    parts.push(`${row.target_count} targets, ${row.source_count} sources`);
    const pairs = Object.entries(row.connections).map(([target, sources]) => `${target} from ${sources.join("+")}`);
    if (pairs.length) parts.push(pairs.slice(0, 12).join(", ") + (pairs.length > 12 ? " ..." : ""));
  }
  if (!row.online) parts.push("offline");
  if (row.description) parts.push(row.description);
  return parts.join(" · ");
}

// The control that shows and changes the value of a parameter.
//   row:      description of the parameter from the server
//   editable: whether the user may change it here
//   commit:   function called with the new value
//   fire:     function called when the button of a trigger is pressed
function valueControl(row, editable, commit, fire) {
  const cell = make("td", "value");
  if (row.kind !== "parameter") return cell;
  if (row.type === "trigger") {
    const button = make("button", "small", "Trigger");
    button.type = "button";
    button.dataset.role = "trigger";
    button.disabled = !editable;
    button.addEventListener("click", () => fire());
    cell.append(button);
    return cell;
  }
  if (!editable) {
    cell.append(make("span", "fixed", row.display !== "" ? row.display : String(row.value ?? "")));
    return cell;
  }
  let control;
  if (row.type === "boolean") {
    control = make("input");
    control.type = "checkbox";
    control.checked = Boolean(row.value);
    control.addEventListener("change", () => commit(control.checked));
  } else if (row.type === "enum") {
    control = make("select");
    row.entries.forEach(([name, number]) => control.add(new Option(name, String(number))));
    if (!row.entries.some(([, number]) => number === row.value)) {
      control.add(new Option(String(row.value), String(row.value)));
    }
    control.value = String(row.value);
    control.addEventListener("change", () => { commit(Number(control.value)); control.blur(); });
  } else {
    control = make("input");
    const numeric = row.type === "integer" || row.type === "real";
    control.type = numeric ? "number" : "text";
    if (numeric) {
      control.step = row.type === "real" ? "any" : "1";
      if (row.minimum !== null) control.min = row.minimum;
      if (row.maximum !== null) control.max = row.maximum;
    }
    control.spellcheck = false;
    control.value = row.value === null ? "" : String(row.value);
    control.addEventListener("change", () => {
      // The server reads the text according to the type, so "-6,5" works too.
      commit(numeric && control.value !== "" && !Number.isNaN(Number(control.value))
        ? Number(control.value) : control.value);
      control.blur();
    });
  }
  control.dataset.role = "value";
  control.setAttribute("aria-label", `Value of ${row.identifier}`);
  cell.append(control);
  if (row.type !== "boolean" && row.display !== "" && row.display !== String(row.value)) {
    cell.append(make("span", "shown", row.display));
  }
  return cell;
}

// The first cell of a tree row: indent, open/close button and identifier.
//   open:   whether the children are shown
//   toggle: function called when the open/close button is pressed
function nameCell(row, open, toggle) {
  const cell = make("td", "name");
  cell.style.paddingLeft = `${row.depth * 18}px`;
  const button = make("button", "toggle", open ? "▾" : "▸");
  button.type = "button";
  button.dataset.role = "toggle";
  if (row.kind === "node" || row.children > 0) {
    button.setAttribute("aria-label", `${open ? "Close" : "Open"} ${row.identifier}`);
    button.setAttribute("aria-expanded", String(open));
    button.addEventListener("click", (event) => { event.stopPropagation(); toggle(); });
  } else {
    button.classList.add("none");
    button.disabled = true;
  }
  cell.append(button, row.identifier || `(${row.number})`);
  return cell;
}

// Draw an activity log, newest entry first.
//   list:    the <ol>
//   entries: log entries from the server
//   empty:   text for an empty log
function renderLog(list, entries, empty) {
  list.replaceChildren();
  if (entries.length === 0) list.append(make("li", "empty", empty));
  entries.slice().reverse().forEach((entry) => {
    const item = make("li", entry.action === "refused" || entry.action === "error" ? "problem-line" : "");
    item.append(make("span", "when", clockTime(entry.time)));
    item.append(make("span", "size", [entry.client, entry.action].filter(Boolean).join("  ")));
    const what = [entry.identifier_path, entry.text].filter(Boolean).join(": ");
    if (what) item.append(make("span", "summary", what));
    list.append(item);
  });
}
