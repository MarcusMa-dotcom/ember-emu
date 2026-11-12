# How Ember Emu is built

For people who want to read or change the code.

## The parts

```
          window (Tkinter)            web page            other programs
                 |                        |                      |
                 |                  /ui/api/...            /api/v1/...
                 |                        |                      |
                 |                     web.py  (Flask, login, switches)
                 |                        |
                 +------------+-----------+
                              |
     provider_engine.py              consumer_engine.py            access.py
     (virtual tree, serving)         (connection, tree, pins)
                              |
                 emberemu library (other repository)
                 Provider, Consumer, tree classes, Glow, BER, S101
                              |
                             TCP
```

| Module | Task |
|---|---|
| `emberemu_app/provider_engine.py` | `ProviderEngine`: settings, the virtual tree with adding, changing and removing, import and export, animations, saving, the activity list |
| `emberemu_app/consumer_engine.py` | `ConsumerEngine`: settings, connecting and reconnecting, loading the tree, setting values, calling functions, pins, the activity list |
| `emberemu_app/example_tree.py` | The example tree as plain data |
| `emberemu_app/common.py` | Checks for settings, reading typed values, the description of an element that all front ends show |
| `emberemu_app/access.py` | `Access`: switches and logins of web interface and REST API, password hashing, login throttle |
| `emberemu_app/web.py` | The Flask app and `WebServer`, which opens the port only while a door is switched on |
| `emberemu_app/gui.py`, `gui_consumer.py`, `gui_settings.py`, `gui_common.py`, `gui_colors.py` | The desktop window |
| `emberemu_app/__main__.py` | Command line, wiring of all parts |
| `emberemu_app/templates/`, `static/` | The web page: one HTML file, one script per view |
| `packaging/` | Recipe for the packaged Mac app, and the script that draws logo and icon |

The protocol is not in this repository. Encoding, framing, the provider that
answers consumers and the consumer that reads providers come from the library
[ember-emu-lib](https://github.com/MarcusMa-dotcom/ember-emu-lib), which is
installed at a fixed tag. The engines here add what a program needs around
it: settings, files, text input, logs, and one state for all front ends.

`access.py`, the web server around the routes, the Settings and About views
and the launcher are the ones of
[Tally Tapir](https://github.com/MarcusMa-dotcom/tally-tapir), with the names
changed.

## One state, many front ends

The window, every browser and every API client work on the same
`ProviderEngine` and `ConsumerEngine`. No front end keeps state of its own,
except what is open and selected on its screen.

- A change of settings goes in through `update(**changes)`. It checks all
  values first and applies either all or none.
- A change of the tree goes in through `add_element`, `update_element`,
  `remove_element` or `import_tree`. The new element is built and checked
  completely before the tree is touched, so a refused change leaves no trace.
- Every change raises a counter, `revision`.
- `snapshot()` returns settings, status and the tree as plain data.
- The window asks every 100 ms, the web page every 400 ms. Both redraw only
  when `revision` changed. The web page sends the revision it knows along and
  gets a short answer while nothing changed.

That is why a click in the window shows up in the browser and the other way
round, without the two knowing each other.

## From the tree to the screen

`common.describe(element)` turns an element of the library into a dictionary
of plain values: path, kind, identifier, and for a parameter its type, access,
value, shown value, range and so on. `common.flatten(tree)` lists a whole tree
this way, parents before children, each with its depth. Window, web page and
REST API all show these dictionaries. `common.parse_value` goes the other
way: it reads what was typed, or what JSON delivered, according to the type of
the parameter.

## The virtual tree

The provider engine holds one `emberemu.Provider`. Its tree is made of the
classes of the library. Two things are added per element:

- an **animation**, kept in a table beside the tree and applied by the timer,
- for functions a **handler** that answers with something plausible.

The tree file (`settings.tree.json`) and the tree in the REST API are the
`to_dict()` form of the library plus the key `animate`.
`clean_element_data` checks every element that comes in from a form, a file or
the API, and turns text into numbers where the type asks for it.

## The consumer and its pins

The consumer engine holds one `emberemu.Consumer` per connection. The library
keeps the tree up to date. The engine adds:

- **Pins.** A pin is an identifier path. After every connect a background
  thread looks each one up with `consumer.get(path)`, which loads the nodes on
  the way. A found pin is tied to its element, and a stream parameter is
  subscribed.
- **The log.** The library reports every change of an element. The engine
  notes those of pinned parameters, except streams.
- **Reconnecting.** The timer connects again while the user wants a
  connection and has none.

Requests that wait for the provider (`connect`, `expand`, `set_value`,
`invoke`) run outside the lock of the engine. The network thread of the
library must be able to deliver the answer and report changes meanwhile.

## Threads

| Thread | Does |
|---|---|
| Main thread | The Tk window, or waiting for Ctrl+C without window |
| `ember-provider-engine` | Ten steps a second: animations, delayed saving |
| `ember-consumer-engine` | Four steps a second: delayed saving, reconnecting |
| `ember-provider-accept`, `-timer`, `-send`, `-receive` | The provider of the library: one thread accepts, one sends stream values, two per consumer |
| `ember-consumer-receive`, `-keepalive` | The consumer of the library, while connected |
| `ember-web` and its request threads | The web server |

`ProviderEngine`, `ConsumerEngine` and `Access` each guard their state with a
lock, so all methods may be called from any thread. The order is always
engine lock first, library lock second. Callbacks of the library arrive
without its lock held.

## The two doors of the web server

The same routes for provider and consumer are mounted twice:

| Prefix | For | Protected by |
|---|---|---|
| `/ui/api/` | The web page | Switch "web interface", session cookie after the login form |
| `/api/v1/` | Other programs | Switch "REST API", HTTP Basic |

`web.py` checks every request before it reaches a route: the host name, the
content type and origin of changing requests, then the switch and the login of
the door. Login and access settings exist only under `/ui/api/`.

## Tests

`tests/` runs the real parts against each other on the local machine: the
consumer engine connects to the provider engine over TCP, the web routes are
called through Flask's test client.

| File | Covers |
|---|---|
| `test_engines.py` | Both engines: example tree, editing, checks, import and export, animations, saving, connecting, values, refusals, pins, reconnecting |
| `test_web.py` | The REST API route by route, the page, the guards, the logins |
| `test_access.py` | Password hashes, the access file, rules for names and passwords, the login throttle |
| `test_main.py` | The command line |

```bash
pip install -r requirements.txt pytest
```

```bash
python -m pytest
```

The protocol itself is tested in the library, including a run against an
independent Ember+ implementation.
