# REST API

Other programs control Ember Emu through its REST API: a test rig, a show
control system, a shell script, a continuous integration job. Everything the
Provider and Consumer views do is available here.

- [Basics](#basics)
- [Login](#login)
- [Endpoints](#endpoints)
- [Paths](#paths)
- [Values](#values)
- [Provider](#provider)
- [Fields of an element](#fields-of-an-element)
- [Consumer](#consumer)
- [Polling without load](#polling-without-load)
- [Errors](#errors)
- [Recipes](#recipes)

## Basics

| | |
|---|---|
| Base address | `http://localhost:8089/api/v1/` |
| Format | JSON in both directions |
| Reading | `GET` |
| Changing settings and elements | `PATCH` with a JSON object that names only the fields to change |
| Actions and new things | `POST`, with `{}` as body if there is nothing to send |
| Replacing, removing | `PUT`, `DELETE` |

Every request that changes something (`PATCH`, `POST`, `PUT`, `DELETE`) must
carry the header `Content-Type: application/json`. A request without it is
refused with status 415. This keeps web pages in a browser from triggering
actions.

The API can be switched off in the Settings view or with `--api off`. It then
answers 403. It is independent of the web interface: either can run without
the other.

By default the web server answers on the local machine only. Start with
`--host 0.0.0.0` to reach the API from other machines, and then give it a
login and HTTPS, see [security.md](security.md).

The API has two halves. `/provider` is the virtual tree that Ember Emu serves.
`/consumer` is the provider that Ember Emu is connected to as a consumer.

## Login

Without a login configured, no credentials are needed.

With a login (Settings view, or `--api-login USER`), every request needs HTTP
Basic credentials:

```bash
curl -u robot http://localhost:8089/api/v1/provider
```

curl asks for the password. A request without valid credentials gets 401.
After five failed logins from one address, that address has to wait: first 30
seconds, twice as long with every further failure, at most 15 minutes. The
answer is then 429 with a `Retry-After` header.

Without HTTPS the password crosses the network readable. Use `--tls-cert` and
`--tls-key` whenever the API is reachable from other machines.

## Endpoints

| Method | Path | Does |
|---|---|---|
| GET | `/api/v1/` | Name, version and this list |
| GET | `/api/v1/provider` | Settings, status, consumers, activity and the tree as a flat list |
| PATCH | `/api/v1/provider` | Change provider settings |
| POST | `/api/v1/provider/start` | Open the Ember+ port |
| POST | `/api/v1/provider/stop` | Close the Ember+ port |
| POST | `/api/v1/provider/clear-log` | Forget the activity list |
| GET | `/api/v1/provider/tree` | The whole tree, nested |
| PUT | `/api/v1/provider/tree` | Replace the whole tree |
| DELETE | `/api/v1/provider/tree` | Remove every element |
| POST | `/api/v1/provider/tree/example` | Load the example tree |
| POST | `/api/v1/provider/elements` | Add an element |
| GET | `/api/v1/provider/elements/PATH` | One element |
| PATCH | `/api/v1/provider/elements/PATH` | Change the value or other properties |
| DELETE | `/api/v1/provider/elements/PATH` | Remove an element and everything below it |
| POST | `/api/v1/provider/elements/PATH/connect` | Set the sources of a matrix target |
| GET | `/api/v1/consumer` | Settings, status, pins, activity and the tree read so far |
| PATCH | `/api/v1/consumer` | Change consumer settings |
| POST | `/api/v1/consumer/connect` | Connect to the provider and read its root elements |
| POST | `/api/v1/consumer/disconnect` | Close the connection |
| POST | `/api/v1/consumer/clear-log` | Forget the activity list |
| POST | `/api/v1/consumer/expand` | Load the children of a node |
| GET | `/api/v1/consumer/elements/PATH` | One element of the provider |
| PATCH | `/api/v1/consumer/elements/PATH` | Ask the provider to set a value |
| POST | `/api/v1/consumer/elements/PATH/invoke` | Call a function |
| POST | `/api/v1/consumer/elements/PATH/connect` | Ask for new sources of a matrix target |
| GET | `/api/v1/consumer/pins` | The pinned parameters with their latest values |
| POST | `/api/v1/consumer/pins` | Pin a parameter |
| DELETE | `/api/v1/consumer/pins/PATH` | Remove a pin |

## Paths

`PATH` names an element in one of two ways:

| Form | Example | Notes |
|---|---|---|
| Numbers | `1.2.1` | Short. The numbers of a device may differ from one session to the next |
| Identifiers | `EmberEmu/Audio/Gain` | Readable and lasting. Escape characters that an address does not allow, such as spaces: `Test%20Matrix` |

Both work everywhere: in the address, and in the fields `path` and `parent`.

## Values

Values travel as JSON values: `-6.5`, `true`, `"CAM 1"`, `2`.

| Type | JSON | Also accepted |
|---|---|---|
| integer | number | text: `"7"`, `"0x10"` |
| real | number | text with point or comma: `"-6,5"` |
| string | text | |
| boolean | `true`, `false` | `"true"`, `"false"`, `"on"`, `"off"`, 1, 0 |
| enum | the number of the entry | the name of the entry: `"Stereo"` |
| octets | hex digits as text: `"01 ff"` | `"01FF"`, `"{01}{FF}"` |
| trigger | anything, `null` | |

Answers show a value twice: `value` is the plain value, `display` is what a
user interface shows, with enumeration name, factor and format applied.

## Provider

### Reading the state

```bash
curl http://localhost:8089/api/v1/provider
```

```json
{
  "instance": "0d7288cf",
  "revision": 9,
  "settings": {
    "bind": "0.0.0.0",
    "port": 9000,
    "autostart": false,
    "animate": true
  },
  "status": {
    "running": true,
    "description": "TCP 0.0.0.0:9000",
    "error": "",
    "clients": [
      {
        "address": "192.168.1.20:56083",
        "since": 1794471757.91,
        "requests": 6,
        "glow_version": "2.50"
      }
    ],
    "element_count": 45,
    "log": [
      {
        "time": 1794471757.92,
        "client": "192.168.1.20:56083",
        "action": "refused",
        "path": "1.1.4",
        "identifier_path": "EmberEmu/Identity/Serial",
        "text": "5: read only"
      }
    ]
  },
  "tree": [
    {
      "path": "1",
      "depth": 0,
      "kind": "node",
      "number": 1,
      "identifier": "EmberEmu",
      "description": "Ember Emu example device",
      "identifier_path": "EmberEmu",
      "children": 7,
      "loaded": false,
      "online": true
    }
  ]
}
```

`tree` lists every element, parents before children, `depth` says how far
below the root it sits. Up to 5000 elements are listed. `element_count` is the
true number. Times are seconds since 1970.

| Field of `status` | Content |
|---|---|
| `running` | Whether the Ember+ port is open |
| `description` | For example `TCP 0.0.0.0:9000`, empty when not running |
| `error` | Why the last start failed, or the last problem with data from a consumer |
| `clients` | The connected consumers |
| `element_count` | Number of elements in the tree |
| `log` | The last 60 entries of the activity list. `action` is `connected`, `disconnected`, `directory`, `set`, `refused`, `subscribe`, `unsubscribe`, `invoke`, `connect`, `unknown` or `error` |

### Settings

```bash
curl -X PATCH http://localhost:8089/api/v1/provider -H 'Content-Type: application/json' -d '{"bind": "0.0.0.0", "port": 9000}'
```

| Setting | Values | Default |
|---|---|---|
| `bind` | Listen address | `"0.0.0.0"` |
| `port` | 1 to 65535 | 9000 |
| `autostart` | Open the port at program start | `false` |
| `animate` | Run the animations | `true` |

The answer is the state. Changing `bind` or `port` stops a running provider.
A value that is not valid changes nothing and gets 400 with the state and an
`error` field.

### Starting and stopping

```bash
curl -X POST http://localhost:8089/api/v1/provider/start -H 'Content-Type: application/json' -d '{}'
```

The answer is the state with `ok`. If `ok` is false, `status.error` says why,
for example that the port is in use.

### One element

```bash
curl http://localhost:8089/api/v1/provider/elements/EmberEmu/Audio/Gain
```

```json
{
  "path": "1.2.1",
  "depth": 2,
  "kind": "parameter",
  "number": 1,
  "identifier": "Gain",
  "description": "Real number with a range",
  "identifier_path": "EmberEmu/Audio/Gain",
  "children": 0,
  "loaded": false,
  "online": true,
  "type": "real",
  "access": "read_write",
  "writable": true,
  "value": -6.5,
  "display": "-6.5 dB",
  "minimum": -60.0,
  "maximum": 12.0,
  "format": "%.1f dB",
  "step": null,
  "factor": null,
  "entries": [],
  "stream_identifier": null,
  "stream_format": "",
  "stream_offset": null,
  "animate": ""
}
```

Every element has the first ten fields. `children` is the number of children.
`loaded` only matters on the consumer side. What follows depends on the kind:

| Kind | Further fields |
|---|---|
| parameter | `type`, `access`, `writable`, `value`, `display`, `minimum`, `maximum`, `format`, `step`, `factor`, `entries` (pairs of name and number of an enumeration), `stream_identifier`, `stream_format`, `stream_offset`, `animate` |
| function | `arguments`, `result`: pairs of type and name |
| matrix | `matrix_type`, `addressing_mode`, `target_count`, `source_count`, `targets`, `sources`, `connections` (target number to list of sources) |

### Changing an element

Set a value. Every connected consumer that looked at the parameter is told:

```bash
curl -X PATCH http://localhost:8089/api/v1/provider/elements/EmberEmu/Audio/Gain -H 'Content-Type: application/json' -d '{"value": -6.5}'
```

Change other properties, several at once:

```bash
curl -X PATCH http://localhost:8089/api/v1/provider/elements/1.2.1 -H 'Content-Type: application/json' -d '{"maximum": 20, "description": "Input gain", "access": "read"}'
```

The answer is the element as it is now. The provider itself may set every
value, also of parameters that consumers can only read. `kind` and `number`
cannot be changed. Changing `type` without giving a `value` starts the value
again.

### Adding and removing elements

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements -H 'Content-Type: application/json' -d '{"parent": "EmberEmu/Audio", "kind": "parameter", "identifier": "Pan", "type": "real", "access": "read_write", "minimum": -1, "maximum": 1, "value": 0}'
```

`parent` is the node to add to. Without it the element goes to the root. The
answer, with status 201, is the new element. Its `path` tells the number it
was given. An element may bring `children` along.

```bash
curl -X DELETE http://localhost:8089/api/v1/provider/elements/EmberEmu/Audio/Pan -H 'Content-Type: application/json' -d '{}'
```

### Matrix connections

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements/EmberEmu/Router/Matrix/connect -H 'Content-Type: application/json' -d '{"target": 2, "sources": [0]}'
```

| Field | Content |
|---|---|
| `target` | Number of the target |
| `sources` | List of source numbers. An empty list disconnects |
| `operation` | `"absolute"` (replace, the default), `"connect"` (add), `"disconnect"` (remove) |

### The tree as a whole

```bash
curl http://localhost:8089/api/v1/provider/tree > tree.json
```

```bash
curl -X PUT http://localhost:8089/api/v1/provider/tree -H 'Content-Type: application/json' --data-binary @tree.json
```

`GET` gives the tree nested, children inside their parents, in the form
Export writes. `PUT` replaces the tree with one in that form and answers
`{"ok": true, "elements": 45}`. If the tree is not valid, nothing changes and
the answer is 400 with the element and the reason. `DELETE` empties the tree,
`POST .../tree/example` loads the example tree.

A request body may be 4 MB, a tree may hold 20,000 elements, nested 64 levels
deep.

## Fields of an element

For `POST /provider/elements`, `PATCH /provider/elements/PATH` and the
elements of a tree file.

| Field | For | Content |
|---|---|---|
| `kind` | all | `"node"`, `"parameter"`, `"function"`, `"matrix"` |
| `identifier` | all | The name: starts with a letter or underscore, no slash, unique among its siblings |
| `description` | all | Text for people |
| `number` | all | Number among the siblings. Given by the program if left out |
| `children` | node | List of elements, in a tree and when adding |
| `is_online` | node, parameter | `false` marks it as not available |
| `type` | parameter | `"integer"`, `"real"`, `"string"`, `"boolean"`, `"trigger"`, `"enum"`, `"octets"` |
| `access` | parameter | `"read"` (default), `"write"`, `"read_write"`, `"none"` |
| `value` | parameter | See [Values](#values). Without it the parameter starts at zero, empty or its first entry |
| `minimum`, `maximum` | integer, real | Range. Values outside are refused |
| `format` | integer, real | C notation, such as `"%.1f dB"` |
| `step` | integer, real | Step for user interfaces |
| `factor` | integer | The value is shown divided by this |
| `enumeration` | enum | List of names. The value is the position, from 0 |
| `enum_map` | enum | List of `[name, number]`, for entries with their own numbers |
| `stream_identifier` | parameter | Makes it a meter |
| `stream_descriptor` | integer, real | `[format, byte offset]`: the value travels inside a block of bytes shared by all parameters with the same stream identifier. Formats: `uint8`, `int8`, and `uint16`, `int16`, `uint32`, `int32`, `uint64`, `int64`, `float32`, `float64`, each followed by `_be` or `_le` |
| `animate` | parameter | `"clock"` (string), `"counter"` (integer), `"sine"`, `"random"` (numbers), or `""` |
| `arguments`, `result` | function | Lists of `[type, name]` |
| `type` | matrix | `"one_to_n"` (default), `"one_to_one"`, `"n_to_n"` |
| `target_count`, `source_count` | matrix | 1 to 1024 |
| `connections` | matrix | Object: target number to list of source numbers |
| `labels` | matrix | List of `[path of the label node, description]` |

## Consumer

### Reading the state

```bash
curl http://localhost:8089/api/v1/consumer
```

```json
{
  "instance": "ec293064",
  "revision": 29,
  "settings": {
    "host": "192.168.1.50",
    "port": 9000,
    "keepalive": true,
    "auto_reconnect": false,
    "pins": ["EmberEmu/Audio/Gain"]
  },
  "status": {
    "connected": true,
    "connecting": false,
    "loading": false,
    "description": "TCP 192.168.1.50:9000",
    "error": "",
    "glow_version": "2.50",
    "root_loaded": true,
    "element_count": 19,
    "pins": [
      {
        "identifier_path": "EmberEmu/Audio/Gain",
        "found": true,
        "error": "",
        "changed": 1794471757.92,
        "changes": 1,
        "path": "1.2.1",
        "kind": "parameter",
        "type": "real",
        "access": "read_write",
        "writable": true,
        "value": -6.5,
        "display": "-6.5 dB"
      }
    ],
    "log": [
      {
        "time": 1794471757.92,
        "action": "refused",
        "path": "1.1.4",
        "identifier_path": "EmberEmu/Identity/Serial",
        "text": "asked for 5, the provider keeps 100042"
      }
    ]
  },
  "tree": [
    {
      "path": "1",
      "depth": 0,
      "kind": "node",
      "identifier": "EmberEmu",
      "children": 7,
      "loaded": true,
      "online": true,
      "pinned": false
    }
  ]
}
```

The examples here are shortened: a pin and a tree row carry all fields of an
element. `tree` holds what was read so far. `loaded` says whether the children
of a node were read.

| Field of `status` | Content |
|---|---|
| `connected`, `connecting` | State of the connection |
| `loading` | True while "load everything" runs |
| `error` | Why the connection failed or ended |
| `glow_version` | Version of the Ember+ schema the provider announced |
| `root_loaded` | Whether the provider has answered the request for its root elements |
| `element_count` | Number of elements read so far |
| `pins` | The pinned parameters. `found` is false, with a reason in `error`, if the provider has no such parameter |
| `log` | The last 60 entries of the activity list. `action` is `connected`, `disconnected`, `set`, `refused`, `change`, `invoke`, `connect` or `error` |

### Settings and connecting

```bash
curl -X PATCH http://localhost:8089/api/v1/consumer -H 'Content-Type: application/json' -d '{"host": "192.168.1.50", "port": 9000}'
```

```bash
curl -X POST http://localhost:8089/api/v1/consumer/connect -H 'Content-Type: application/json' -d '{}'
```

| Setting | Values | Default |
|---|---|---|
| `host` | Name or address of the provider | `"127.0.0.1"` |
| `port` | 1 to 65535 | 9000 |
| `keepalive` | Notice a provider that has gone silent | `true` |
| `auto_reconnect` | Connect again after the connection was lost | `false` |
| `pins` | List of identifier paths, at most 200 | `[]` |

`connect` waits until the root elements are read and answers with the state
and `ok`. If `ok` is false, `status.error` says why. Changing `host`, `port`
or `keepalive` ends an open connection.

### Reading elements

```bash
curl http://localhost:8089/api/v1/consumer/elements/Device/Audio/Gain
```

The nodes on the way are read from the provider if need be, so any element can
be asked for right after connecting. The answer has the fields of an element
and `pinned`.

To load the children of a node into the tree of the state:

```bash
curl -X POST http://localhost:8089/api/v1/consumer/expand -H 'Content-Type: application/json' -d '{"path": "Device/Audio"}'
```

| Field | Content |
|---|---|
| `path` | The node. Empty or left out: the root |
| `all` | `true` loads everything below, in the background, up to 5000 elements. Watch `status.loading` |
| `refresh` | `true` asks again and drops what the provider no longer lists |

### Changing values

```bash
curl -X PATCH http://localhost:8089/api/v1/consumer/elements/Device/Audio/Gain -H 'Content-Type: application/json' -d '{"value": -6}'
```

The request waits for the provider. The answer is the element with the value
the provider reports back. If the provider refused, that is the value it kept:
compare `value` with what you sent.

Before sending, the value is checked against the type and range the provider
announced. A value that does not fit gets 400 and is not sent. To send it
anyway, and see how the provider reacts:

```bash
curl -X PATCH http://localhost:8089/api/v1/consumer/elements/Device/Audio/Gain -H 'Content-Type: application/json' -d '{"value": 99, "check": false}'
```

A trigger is fired by setting any value, such as `null`.

### Functions and matrices

```bash
curl -X POST http://localhost:8089/api/v1/consumer/elements/EmberEmu/Control/Add/invoke -H 'Content-Type: application/json' -d '{"arguments": [2, 40]}'
```

```json
{"result": [42]}
```

```bash
curl -X POST http://localhost:8089/api/v1/consumer/elements/EmberEmu/Router/Matrix/connect -H 'Content-Type: application/json' -d '{"target": 2, "sources": [0]}'
```

The fields are those of the provider side. The provider answers a connection
request with a report that updates the matrix in the tree.

### Pins

```bash
curl -X POST http://localhost:8089/api/v1/consumer/pins -H 'Content-Type: application/json' -d '{"path": "Device/Audio/Gain"}'
```

```bash
curl http://localhost:8089/api/v1/consumer/pins
```

```bash
curl -X DELETE http://localhost:8089/api/v1/consumer/pins/Device/Audio/Gain -H 'Content-Type: application/json' -d '{}'
```

Pinning needs a connection, because the parameter is looked up. The answer
names the identifier path the pin is remembered by. `GET` gives
`{"pins": [...]}` with the latest values, the number of changes since the
connect and the time of the last one. Pinning a meter subscribes to it.

To set pins without a connection, write the list: `PATCH /consumer` with
`{"pins": ["Device/Audio/Gain", "Device/Audio/Mute"]}`. They are looked up at
the next connect.

## Polling without load

`GET /provider` and `GET /consumer` return `instance` and `revision`. Send
both back and the answer stays short for as long as nothing changed:

```bash
curl 'http://localhost:8089/api/v1/consumer?since=29&instance=ec293064'
```

```json
{"instance": "ec293064", "revision": 29, "unchanged": true}
```

`revision` rises with every change. `instance` is new at every start of the
program, so a restart is noticed.

## Errors

| Status | Meaning |
|---|---|
| 400 | A value is not valid, the body is not a JSON object, the provider did not answer, or there is no connection. `error` says which |
| 401 | The API requires a login and the credentials are missing or wrong |
| 403 | The API is switched off, the request names another host than this machine, or it comes from another web page |
| 404 | A `GET`, or a change of the virtual tree, names an element that does not exist |
| 415 | A changing request without `Content-Type: application/json` |
| 429 | Too many failed logins. `Retry-After` gives the seconds to wait |

```json
{"error": "Gain: 99.0 is above the maximum 12.0"}
```

## Recipes

### A virtual device for a test, from nothing

Empty the tree, add a node with two parameters, start serving:

```bash
curl -X DELETE http://localhost:8089/api/v1/provider/tree -H 'Content-Type: application/json' -d '{}'
```

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements -H 'Content-Type: application/json' -d '{"kind": "node", "identifier": "Camera", "children": [{"kind": "parameter", "identifier": "Tally", "type": "boolean", "access": "read_write"}, {"kind": "parameter", "identifier": "Iris", "type": "integer", "access": "read_write", "minimum": 0, "maximum": 100, "value": 50}]}'
```

```bash
curl -X POST http://localhost:8089/api/v1/provider/start -H 'Content-Type: application/json' -d '{}'
```

### Did the control system switch the tally?

After the control system under test has done its work, read the value it
should have set:

```bash
curl http://localhost:8089/api/v1/provider/elements/Camera/Tally
```

The activity list in `GET /provider` shows which consumer set it and when, and
every request that was refused.

### A meter that every consumer can read

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements -H 'Content-Type: application/json' -d '{"parent": "Camera", "kind": "parameter", "identifier": "Level", "type": "integer", "minimum": -600, "maximum": 0, "factor": 10, "format": "%.1f dB", "stream_identifier": 1, "animate": "sine"}'
```

### Two meters in one block of bytes

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements -H 'Content-Type: application/json' -d '{"parent": "Camera", "kind": "parameter", "identifier": "Left", "type": "real", "minimum": -60, "maximum": 0, "stream_identifier": 2, "stream_descriptor": ["float32_le", 0], "animate": "sine"}'
```

```bash
curl -X POST http://localhost:8089/api/v1/provider/elements -H 'Content-Type: application/json' -d '{"parent": "Camera", "kind": "parameter", "identifier": "Right", "type": "real", "minimum": -60, "maximum": 0, "stream_identifier": 2, "stream_descriptor": ["float32_le", 4], "animate": "random"}'
```

### Watch a value of a real device

```bash
curl -X PATCH http://localhost:8089/api/v1/consumer -H 'Content-Type: application/json' -d '{"host": "192.168.1.50", "port": 9000, "auto_reconnect": true}'
```

```bash
curl -X POST http://localhost:8089/api/v1/consumer/connect -H 'Content-Type: application/json' -d '{}'
```

```bash
curl -X POST http://localhost:8089/api/v1/consumer/pins -H 'Content-Type: application/json' -d '{"path": "Device/Audio/Gain"}'
```

```bash
curl http://localhost:8089/api/v1/consumer/pins
```

### From Python

[examples/api_client.py](../examples/api_client.py) holds a small client class
that needs nothing but the standard library:

```python
from api_client import EmberEmuApi

api = EmberEmuApi("http://localhost:8089")

# The virtual tree that Ember Emu serves
api.serve()
api.local_set("EmberEmu/Audio/Gain", -6.5)
api.local_add("EmberEmu/Audio", kind="parameter", identifier="Pan", type="real",
              access="read_write", minimum=-1, maximum=1, value=0)

# A provider that Ember Emu reads as a consumer
if api.connect("192.168.1.50", 9000):
    gain = api.get("Device/Audio/Gain")
    print(gain["value"], gain["display"])
    print(api.set("Device/Audio/Gain", -6)["value"])     # what the provider reports back
    api.pin("Device/Audio/Gain")
    for pin in api.pins():
        print(pin["identifier_path"], pin.get("display"), pin["changes"])
```

The same file works as a command: `python examples/api_client.py status`.
