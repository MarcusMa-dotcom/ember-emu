"use strict";

// Provider view: the server, the virtual tree and the form that edits it.
// Uses $, api, act, put, make, syncRows, visibleRows, typeText, detailsText,
// valueControl, nameCell, renderLog, clockTime and showNotice from app.js.

const PR = "/ui/api/provider";
let prState = null;
let prInstance = null;
let prRevision = -1;
let prSelected = null;          // path of the selected element
let prFormFor = null;           // path whose data the form shows
const prClosed = new Set();     // paths of nodes whose children are hidden

// Fetch the provider state and draw it. Skips the drawing while nothing changed.
async function prPoll() {
  try {
    const query = prInstance === null ? "" : `?since=${prRevision}&instance=${prInstance}`;
    const response = await fetch(PR + query);
    if (response.status === 401) {
      location.href = "/login";
      return;
    }
    const data = await response.json();
    if (!response.ok) {
      showNotice(data.error || `Request failed (${response.status})`);
      return;
    }
    if ($("notice").textContent === UNREACHABLE) showNotice("");
    if (!data.unchanged) prRender(data);
  } catch (error) {
    showNotice(UNREACHABLE);
  }
}

// Fetch the full state now, after a change made here.
async function prRefresh() {
  prInstance = null;
  await prPoll();
}

function prChange(changes) {
  return act(() => api(PR, changes, "PATCH"), prRefresh);
}

function prElementPath(path) {
  return `${PR}/elements/${path}`;
}

// Draw the whole Provider view from a state of the server.
function prRender(data) {
  prState = data;
  prInstance = data.instance;
  prRevision = data.revision;
  const s = data.settings;
  const status = data.status;

  put("pr_bind", s.bind);
  put("pr_port", s.port);
  put("pr_autostart", s.autostart);
  put("pr_animate", s.animate);
  $("pr-start").textContent = status.running ? "Stop" : "Start";
  $("pr-error").textContent = status.error;
  const state = $("pr-state");
  const count = status.clients.length;
  state.textContent = status.running
    ? `Serving: ${status.description}, ${count} consumer${count === 1 ? "" : "s"}`
    : "Not serving";
  state.classList.toggle("on", status.running);

  const clients = $("pr-clients");
  clients.replaceChildren();
  status.clients.forEach((client) => {
    const item = make("li");
    item.append(client.address, " ", make("span", "when",
      `since ${clockTime(client.since)}, ${client.requests} request${client.requests === 1 ? "" : "s"}`
      + (client.glow_version ? `, Glow ${client.glow_version}` : "")));
    clients.append(item);
  });
  $("pr-clients-note").hidden = count > 0;
  $("pr-clients-note").textContent = status.running
    ? "No consumer connected."
    : "Not serving. Press Start to let consumers connect.";

  const rows = visibleRows(data.tree, (row) => !prClosed.has(row.path));
  syncRows($("pr-tree").tBodies[0], rows, (row) => row.path,
    (row) => (row.path === prSelected ? "selected" : "") + (prClosed.has(row.path) ? "closed" : ""),
    prBuildRow);
  $("pr-count").textContent = status.element_count > data.tree.length
    ? `Showing ${data.tree.length} of ${status.element_count} elements.`
    : `${status.element_count} element${status.element_count === 1 ? "" : "s"}`;

  if (prSelected !== null && !data.tree.some((row) => row.path === prSelected)) prSelect(null);
  prShowSelection();
  renderLog($("pr-log"), status.log, "Nothing happened yet.");
}

// The cells of one row of the provider tree.
function prBuildRow(row, tr) {
  tr.className = `pick ${row.kind}${row.online ? "" : " offline"}${row.path === prSelected ? " selected" : ""}`;
  tr.onclick = (event) => {
    if (event.target.closest("input, select, button")) return;  // editing a value is not selecting
    prSelect(row.path === prSelected ? null : row.path);
  };
  const open = !prClosed.has(row.path);
  const name = nameCell(row, open, () => {
    if (open) prClosed.add(row.path);
    else prClosed.delete(row.path);
    prRender(prState);
  });
  const value = valueControl(row, true,
    (newValue) => act(() => api(prElementPath(row.path), { value: newValue }, "PATCH"), prRefresh),
    () => showNotice("A trigger has no value. Consumers fire it.", true));
  if (row.kind === "matrix") {
    const button = make("button", "small", "Connect");
    button.type = "button";
    button.dataset.role = "connect";
    button.addEventListener("click", () => prConnect(row));
    value.append(button);
  }
  return [name, make("td", "path", row.path), make("td", "", typeText(row)), value,
    make("td", "", row.kind === "parameter" ? row.access.replace(/_/g, " ") : ""),
    make("td", "details", detailsText(row))];
}

// Ask for target and sources and set a connection of a matrix in the tree.
function prConnect(row) {
  const target = prompt(`${row.identifier}: target number (0 to ${row.target_count - 1})`, "0");
  if (target === null) return;
  const sources = prompt("Source numbers, separated by commas. Empty for none.", "");
  if (sources === null) return;
  const list = sources.split(",").map((part) => part.trim()).filter(Boolean);
  act(() => api(`${prElementPath(row.path)}/connect`, { target, sources: list }), prRefresh);
}

function prSelect(path) {
  prSelected = path;
  if (prState) prRender(prState);
}

// Show which element is selected, switch the buttons, and load it into the form once.
function prShowSelection() {
  const row = prSelected === null ? null : prState.tree.find((item) => item.path === prSelected);
  $("el-apply").disabled = !row;
  $("el-remove").disabled = !row;
  $("el-add-child").disabled = !(row && row.kind === "node");
  $("el_kind").disabled = Boolean(row);
  $("el-selected").textContent = row
    ? `Selected: ${row.path} ${row.identifier_path} (${row.kind}). Change the fields and apply, or use them for a new element.`
    : "Nothing selected. Fill in the fields and add a new element.";
  if (row && prFormFor !== row.path) {
    prFormFor = row.path;
    prFillForm(row);
  } else if (!row) {
    prFormFor = null;
  }
}

const tupleText = (items) => items.map(([type, name]) => (name ? `${type} ${name}` : type)).join(", ");

// Put the properties of an element into the form.
function prFillForm(row) {
  $("el_kind").value = row.kind;
  $("el_identifier").value = row.identifier;
  $("el_description").value = row.description;
  $("el_is_online").checked = row.online;
  if (row.kind === "parameter") {
    $("el_type").value = row.type;
    $("el_access").value = row.access;
    $("el_value").value = row.value === null ? "" : String(row.value);
    ["minimum", "maximum", "step", "factor", "stream_identifier"].forEach((key) => {
      $(`el_${key}`).value = row[key] === null ? "" : String(row[key]);
    });
    $("el_format").value = row.format;
    // Entries with their own numbers are written "Name=5".
    const plain = row.entries.every(([, number], index) => number === index);
    $("el_enumeration").value = row.entries
      .map(([name, number]) => (plain ? name : `${name}=${number}`)).join("\n");
    $("el_animate").value = row.animate || "";
  } else if (row.kind === "function") {
    $("el_arguments").value = tupleText(row.arguments);
    $("el_result").value = tupleText(row.result);
  } else if (row.kind === "matrix") {
    $("el_matrix_type").value = row.matrix_type;
    $("el_target_count").value = row.target_count;
    $("el_source_count").value = row.source_count;
  }
  $("el-message").textContent = "";
  prShowFields();
}

// Show only the fields that belong to the chosen kind and type.
function prShowFields() {
  const kind = $("el_kind").value;
  const type = $("el_type").value;
  const numeric = type === "integer" || type === "real";
  $("el-parameter").hidden = kind !== "parameter";
  $("el-function").hidden = kind !== "function";
  $("el-matrix").hidden = kind !== "matrix";
  $("el-online-field").hidden = kind !== "node" && kind !== "parameter";
  $("el-number-fields").hidden = !numeric;
  $("el-factor-field").hidden = type !== "integer";
  $("el-enumeration-field").hidden = type !== "enum";
  $("el-value-field").hidden = type === "trigger";
}

// "integer a, real b" as [["integer", "a"], ["real", "b"]].
function parseTuple(text) {
  return text.split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
    const [type, ...name] = part.split(/\s+/);
    return [type.toLowerCase(), name.join(" ") || null];
  });
}

// Read the form into the description of an element, as the server takes it.
function prReadForm() {
  const kind = $("el_kind").value;
  const data = {
    kind,
    identifier: $("el_identifier").value.trim(),
    description: $("el_description").value,
  };
  if (kind === "node" || kind === "parameter") data.is_online = $("el_is_online").checked;
  if (kind === "parameter") {
    data.type = $("el_type").value;
    data.access = $("el_access").value;
    ["value", "minimum", "maximum", "step", "factor", "format", "stream_identifier"].forEach((key) => {
      data[key] = $(`el_${key}`).value;
    });
    const lines = $("el_enumeration").value.split("\n").map((line) => line.trim()).filter(Boolean);
    const numbered = lines.map((line) => line.match(/^(.*)=\s*(-?\d+)$/));
    if (lines.length > 0 && numbered.every(Boolean)) {
      data.enum_map = numbered.map((match) => [match[1].trim(), Number(match[2])]);
      data.enumeration = null;
    } else {
      data.enumeration = lines;
      data.enum_map = null;
    }
    data.animate = $("el_animate").value;
  } else if (kind === "function") {
    data.arguments = parseTuple($("el_arguments").value);
    data.result = parseTuple($("el_result").value);
  } else if (kind === "matrix") {
    data.type = $("el_matrix_type").value;
    data.target_count = Number($("el_target_count").value);
    data.source_count = Number($("el_source_count").value);
  }
  return data;
}

// Send the form: change the selected element, or add a new one below "parent".
//   parent: path of the node to add to, "" for the root, null to change the selection
async function prSubmit(parent) {
  const data = prReadForm();
  const message = $("el-message");
  message.className = "hint";
  message.textContent = "";
  const adding = parent !== null;
  if (adding) data.parent = parent;
  const answer = await act(() => (adding
    ? api(`${PR}/elements`, data)
    : api(prElementPath(prSelected), data, "PATCH")), null);
  if (!answer) {
    message.className = "error";
    message.textContent = $("notice").textContent;
    showNotice("");
    return;
  }
  prFormFor = null;                      // show what the server made of it
  if (adding) {
    prClosed.delete(parent);
    prSelected = answer.path;
  }
  await prRefresh();                     // loads the element into the form again, which clears the message
  message.textContent = adding ? `Added ${answer.path} ${answer.identifier_path}.` : "Applied.";
}

function prBind() {
  $("pr_bind").addEventListener("change", () => prChange({ bind: $("pr_bind").value }));
  $("pr_port").addEventListener("change", () => {
    if ($("pr_port").value !== "") prChange({ port: Number($("pr_port").value) });
  });
  for (const key of ["autostart", "animate"]) {
    $(`pr_${key}`).addEventListener("change", () => prChange({ [key]: $(`pr_${key}`).checked }));
  }
  $("pr-start").addEventListener("click", () => {
    act(() => api(`${PR}/${prState && prState.status.running ? "stop" : "start"}`, {}), prRefresh);
  });
  $("pr-clear-log").addEventListener("click", () => act(() => api(`${PR}/clear-log`, {}), prRefresh));

  $("pr-example").addEventListener("click", () => {
    if (!confirm("Replace the whole tree with the example tree?")) return;
    prSelect(null);
    act(() => api(`${PR}/tree/example`, {}), prRefresh);
  });
  $("pr-clear").addEventListener("click", () => {
    if (!confirm("Remove every element of the tree?")) return;
    prSelect(null);
    act(() => api(`${PR}/tree`, {}, "DELETE"), prRefresh);
  });
  $("pr-import").addEventListener("click", () => $("pr-import-file").click());
  $("pr-import-file").addEventListener("change", async () => {
    const file = $("pr-import-file").files[0];
    $("pr-import-file").value = "";
    if (!file) return;
    let tree;
    try {
      tree = JSON.parse(await file.text());
    } catch (error) {
      showNotice("The file is not valid JSON.");
      return;
    }
    if (!confirm(`Replace the whole tree with the one from ${file.name}?`)) return;
    prSelect(null);
    act(() => api(`${PR}/tree`, tree, "PUT"), prRefresh);
  });
  $("pr-collapse").addEventListener("click", () => {
    prState.tree.forEach((row) => { if (row.children > 0) prClosed.add(row.path); });
    prRender(prState);
  });
  $("pr-expand").addEventListener("click", () => {
    prClosed.clear();
    prRender(prState);
  });

  $("el_kind").addEventListener("change", prShowFields);
  $("el_type").addEventListener("change", prShowFields);
  $("pr-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (prSelected !== null) prSubmit(null);
  });
  $("el-add-child").addEventListener("click", () => prSubmit(prSelected));
  $("el-add-root").addEventListener("click", () => prSubmit(""));
  $("el-remove").addEventListener("click", () => {
    const row = prState.tree.find((item) => item.path === prSelected);
    if (!row) return;
    const below = row.children > 0 ? " and everything below it" : "";
    if (!confirm(`Remove ${row.identifier_path}${below}?`)) return;
    const path = prSelected;
    prSelect(null);
    act(() => api(prElementPath(path), {}, "DELETE"), prRefresh);
  });
  prShowFields();
}

prBind();
prPoll();
setInterval(prPoll, POLL_MS);
