"use strict";

// Consumer view: connection, pinned parameters and the tree of the provider.
// Uses $, api, act, put, make, syncRows, visibleRows, typeText, detailsText,
// valueControl, nameCell, renderLog, clockTime and showNotice from app.js.

const CO = "/ui/api/consumer";

let coState = null;
let coInstance = null;
let coRevision = -1;
let coOpenAll = false;          // open every node as soon as its children have arrived
const coOpen = new Set();       // paths of nodes whose children are shown

// Fetch the consumer state and draw it. Skips the drawing while nothing changed.
async function coPoll() {
  try {
    const query = coInstance === null ? "" : `?since=${coRevision}&instance=${coInstance}`;
    const response = await fetch(CO + query);
    if (!response.ok) return;   // provider.js reports a lost session or server
    const data = await response.json();
    if (!data.unchanged) coRender(data);
  } catch (error) {
    // provider.js already reports an unreachable server.
  }
}

// Fetch the full state now, after a change made here.
async function coRefresh() {
  coInstance = null;
  await coPoll();
}

function coChange(changes) {
  return act(() => api(CO, changes, "PATCH"), coRefresh);
}

// A path inside an address: the slashes of identifier paths stay, the rest is escaped.
const coAddress = (path) => path.split("/").map(encodeURIComponent).join("/");

const coCheck = () => $("co-check").checked;

// Ask the provider to set a value.
function coSet(path, value) {
  return act(() => api(`${CO}/elements/${coAddress(path)}`, { value, check: coCheck() }, "PATCH"), coRefresh);
}

// Draw the whole Consumer view from a state of the server.
function coRender(data) {
  coState = data;
  coInstance = data.instance;
  coRevision = data.revision;
  const s = data.settings;
  const status = data.status;

  put("co_host", s.host);
  put("co_port", s.port);
  put("co_keepalive", s.keepalive);
  put("co_auto_reconnect", s.auto_reconnect);
  const button = $("co-connect");
  button.textContent = status.connecting ? "Connecting..." : (status.connected ? "Disconnect" : "Connect");
  button.disabled = status.connecting;
  const state = $("co-state");
  state.textContent = status.connected ? `Connected: ${status.description}` : "Disconnected";
  state.classList.toggle("on", status.connected);
  $("co-error").textContent = status.error;
  $("co-info").textContent = status.connected
    ? `${status.element_count} element${status.element_count === 1 ? "" : "s"} read`
      + (status.glow_version ? `, the provider speaks Glow ${status.glow_version}` : "")
      + (status.loading ? ". Loading the whole tree..." : ".")
    : "";

  // Pins
  syncRows($("co-pins").tBodies[0], status.pins, (pin) => pin.identifier_path,
    () => `${status.connected}${coCheck()}`, coBuildPin);
  $("co-pins-note").hidden = status.pins.length > 0;

  // Tree
  if (coOpenAll) {
    data.tree.forEach((row) => { if (row.loaded && row.children > 0) coOpen.add(row.path); });
    if (!status.loading) coOpenAll = false;
  }
  const rows = visibleRows(data.tree, (row) => coOpen.has(row.path));
  syncRows($("co-tree").tBodies[0], rows, (row) => row.path,
    (row) => `${coOpen.has(row.path)}${status.connected}${coCheck()}`, coBuildRow);
  $("co-count").textContent = status.element_count > data.tree.length
    ? `Showing ${data.tree.length} of ${status.element_count} elements.` : "";
  let note = "";
  if (!status.connected) {
    note = data.tree.length
      ? "Not connected. The tree shows what was read before the connection ended."
      : "Not connected.";
  } else if (data.tree.length === 0) {
    note = status.root_loaded ? "The provider has no elements." : "Waiting for the root elements of the provider.";
  } else {
    note = "Open a node to load its children. The provider then reports changes of what was loaded by itself.";
  }
  $("co-tree-note").textContent = note;
  ["co-expand-all", "co-refresh"].forEach((id) => { $(id).disabled = !status.connected || status.loading; });

  renderLog($("co-log"), status.log, "Nothing happened yet.");
}

// The value cell of a parameter, a function or a matrix as the consumer uses them.
function coValueCell(row) {
  const connected = coState.status.connected;
  const editable = connected && (row.writable || !coCheck());
  const cell = valueControl(row, editable, (value) => coSet(row.path, value),
    () => coSet(row.path, null));
  if (row.kind === "function") {
    const button = make("button", "small", "Call");
    button.type = "button";
    button.dataset.role = "call";
    button.disabled = !connected;
    button.addEventListener("click", () => coInvoke(row));
    cell.append(button);
  } else if (row.kind === "matrix") {
    const button = make("button", "small", "Connect");
    button.type = "button";
    button.dataset.role = "connect";
    button.disabled = !connected;
    button.addEventListener("click", () => coConnectMatrix(row));
    cell.append(button);
  }
  return cell;
}

// The cells of one row of the consumer tree.
function coBuildRow(row, tr) {
  tr.className = `${row.kind}${row.online ? "" : " offline"}`;
  const open = coOpen.has(row.path);
  const name = nameCell(row, open, () => coToggle(row));
  const pin = make("td");
  if (row.kind === "parameter") {
    const button = make("button", "small", row.pinned ? "Unpin" : "Pin");
    button.type = "button";
    button.dataset.role = "pin";
    button.setAttribute("aria-label", `${row.pinned ? "Unpin" : "Pin"} ${row.identifier}`);
    button.disabled = !row.pinned && !coState.status.connected;
    button.addEventListener("click", () => (row.pinned ? coUnpin(row.identifier_path) : coPin(row.path)));
    pin.append(button);
  }
  return [name, make("td", "path", row.path), make("td", "", typeText(row)), coValueCell(row),
    make("td", "", row.kind === "parameter" ? row.access.replace(/_/g, " ") : ""),
    make("td", "details", detailsText(row)), pin];
}

// The cells of one row of the pin table.
function coBuildPin(pin) {
  const name = make("td", "name", pin.identifier_path);
  const remove = make("td");
  const button = make("button", "small", "Unpin");
  button.type = "button";
  button.dataset.role = "unpin";
  button.setAttribute("aria-label", `Unpin ${pin.identifier_path}`);
  button.addEventListener("click", () => coUnpin(pin.identifier_path));
  remove.append(button);
  if (!pin.found) {
    const missing = make("td", "missing", pin.error
      || (coState.status.connected ? "Looking for it..." : "Not connected"));
    missing.colSpan = 5;
    return [name, missing, remove];
  }
  return [name, coValueCell(pin), make("td", "", pin.type), make("td", "", pin.access.replace(/_/g, " ")),
    make("td", "", String(pin.changes)), make("td", "", clockTime(pin.changed)), remove];
}

// Open or close a node. Opening loads its children from the provider first.
function coToggle(row) {
  if (coOpen.has(row.path)) {
    coOpen.delete(row.path);
    coRender(coState);
    return;
  }
  coOpen.add(row.path);
  if (row.loaded || !coState.status.connected) {
    coRender(coState);
    return;
  }
  act(() => api(`${CO}/expand`, { path: row.path }), coRefresh);
}

function coPin(path) {
  return act(() => api(`${CO}/pins`, { path }), coRefresh);
}

function coUnpin(identifierPath) {
  return act(() => api(`${CO}/pins/${coAddress(identifierPath)}`, {}, "DELETE"), coRefresh);
}

// Ask for the arguments of a function, call it and show the result.
async function coInvoke(row) {
  let values = [];
  if (row.arguments.length > 0) {
    const names = row.arguments.map(([type, name]) => (name ? `${name} (${type})` : type)).join(", ");
    const text = prompt(`${row.identifier}: ${names}\nValues, separated by commas`, "");
    if (text === null) return;
    values = text.split(",").map((part) => part.trim());
  }
  const answer = await act(() => api(`${CO}/elements/${coAddress(row.path)}/invoke`, { arguments: values }),
    coRefresh);
  if (answer) showNotice(`${row.identifier} returned: ${answer.result.length ? answer.result.join(", ") : "nothing"}`, true);
}

// Ask for target and sources and request a connection of a matrix.
function coConnectMatrix(row) {
  const target = prompt(`${row.identifier}: target number`, "0");
  if (target === null) return;
  const sources = prompt("Source numbers, separated by commas. Empty for none.", "");
  if (sources === null) return;
  const list = sources.split(",").map((part) => part.trim()).filter(Boolean);
  act(() => api(`${CO}/elements/${coAddress(row.path)}/connect`, { target, sources: list }), coRefresh);
}

function coBind() {
  $("co_host").addEventListener("change", () => coChange({ host: $("co_host").value }));
  $("co_port").addEventListener("change", () => {
    if ($("co_port").value !== "") coChange({ port: Number($("co_port").value) });
  });
  for (const key of ["keepalive", "auto_reconnect"]) {
    $(`co_${key}`).addEventListener("change", () => coChange({ [key]: $(`co_${key}`).checked }));
  }
  $("co-connect").addEventListener("click", () => {
    const connected = coState && coState.status.connected;
    if (!connected) {
      coOpen.clear();
      $("co-connect").textContent = "Connecting...";
      $("co-connect").disabled = true;
    }
    act(() => api(`${CO}/${connected ? "disconnect" : "connect"}`, {}), coRefresh);
  });
  $("co-clear-log").addEventListener("click", () => act(() => api(`${CO}/clear-log`, {}), coRefresh));
  $("co-expand-all").addEventListener("click", () => {
    coOpenAll = true;
    act(() => api(`${CO}/expand`, { path: "", all: true }), coRefresh);
  });
  $("co-collapse").addEventListener("click", () => {
    coOpenAll = false;
    coOpen.clear();
    if (coState) coRender(coState);
  });
  $("co-refresh").addEventListener("click", async () => {
    // Ask again for the root and for every node that is open.
    const paths = ["", ...coState.tree.filter((row) => coOpen.has(row.path) && row.kind === "node")
      .map((row) => row.path)].slice(0, 100);
    for (const path of paths) {
      await act(() => api(`${CO}/expand`, { path, refresh: true }), null);
    }
    coRefresh();
  });
  $("co-check").addEventListener("change", () => { if (coState) coRender(coState); });
}

// -- views -----------------------------------------------------------------

const VIEWS = ["provider", "consumer", "settings", "about"];

// Switch to one of the views and remember it in the address.
function showView(name) {
  if (!VIEWS.includes(name)) name = "provider";
  VIEWS.forEach((view) => {
    $(`view-${view}`).hidden = view !== name;
    $(`tab-${view}`).setAttribute("aria-pressed", String(view === name));
  });
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "settings") loadAccess();
}

const viewFromAddress = () => showView(location.hash.slice(1));
VIEWS.forEach((view) => $(`tab-${view}`).addEventListener("click", () => showView(view)));
window.addEventListener("hashchange", viewFromAddress);
viewFromAddress();

coBind();
coPoll();
setInterval(coPoll, POLL_MS);
