# Security of Ember Emu 0.1.0

Date: 12 November 2026. This covers the app (`emberemu_app`, this repository)
and the library (`emberemu`,
[ember-emu-lib](https://github.com/MarcusMa-dotcom/ember-emu-lib)) in the
state of release 0.1.0.

**What this is:** notes of a review by the author of the code, made while the
program was written and before it was published. It is not an independent
audit and not a penetration test. No static analysis tool was run over the
code. Where strangers can reach the program on the network, these notes do not
replace an independent review.

## In short

- As delivered, the web interface and the REST API can be reached from the
  local machine only and need no login. Once they are opened to the network
  (`--host 0.0.0.0`), logins and HTTPS belong with them. The program warns
  then.
- The Ember+ port is different: it is meant to be reached from other machines
  and has no login, because Ember+ knows none. Whoever reaches it can read the
  virtual tree and change what is writable.
- Data from the network is never trusted. What cannot be read is counted and
  reported, and the connection goes on.
- Four weaknesses were found during the review and fixed. Six risks remain on
  purpose and are listed below.

## Where the program can be reached

| Door | Default | Who can reach it | Protection |
|---|---|---|---|
| Web interface, port 8089 | on, this machine only | Browsers on this machine | Optional login, see below |
| REST API, port 8089 | on, this machine only | Programs on this machine | Optional login, see below |
| Ember+ provider, port 9000 | closed until Start, then all network interfaces | Every machine that can reach the port | None: the protocol has no login |
| Ember+ consumer | connects outward, to the address set in the Consumer view | The provider it connects to | Limits on what is accepted, see below |
| Settings files | folder of the user | The user | The file with password hashes is readable by the owner only |

## Web interface and REST API

These parts are the ones of Tally Tapir, whose
[security audit](https://github.com/MarcusMa-dotcom/tally-tapir/blob/main/docs/security-audit.md)
describes them and their tests in detail. In Ember Emu they behave the same:

| Protection | How |
|---|---|
| Reachable from this machine only, unless started with `--host` | The server binds to 127.0.0.1 |
| No access through a foreign name for this machine (DNS rebinding) | Requests whose host name is not a local one are refused while the server is local |
| Web pages elsewhere cannot trigger actions | Changing requests need `Content-Type: application/json`, and requests marked as cross-site or coming from another origin are refused |
| Logins | Separate for web interface and REST API. Passwords at least 8 characters, stored as salted scrypt hash, compared in constant time |
| Guessing passwords | After five failures an address has to wait, 30 seconds at first, doubling up to 15 minutes |
| Sessions | Cookie is HttpOnly and SameSite=Strict, Secure with HTTPS. Sessions end with the program and when the login changes |
| Changing the web login | Needs the current password. Without a web login, only this machine may change access settings |
| Content of pages | Strict Content-Security-Policy: scripts and styles from the program only, no inline code. Text from the network is put into the page as text, never as HTML |
| Request size | 4 MB |

`tests/test_web.py` and `tests/test_access.py` repeat these checks.

## Ember+ data from the network

Both sides read bytes that a stranger may have made up. The library keeps to
these limits:

| Limit | Value | Where |
|---|---|---|
| Length of one S101 frame | 65,536 bytes | both sides |
| Length of one message | 1 MiB from a consumer, 16 MiB from a provider | provider, consumer |
| Nesting of the encoding | 200 levels | both sides |
| Numbers in the path of an element | 100 | both sides |
| Consumers connected at once | 64 | provider |
| Frames waiting for a consumer that does not read | 20,000, then it is dropped | provider |
| Elements kept of one provider | 250,000 | consumer |
| Elements of the virtual tree, nesting | 20,000, 64 levels | app |
| Width and precision in a display format | 40, and 100 characters for the whole format | both sides |

A frame with a wrong checksum, a message that is not valid Ember+, a value of
the wrong type: each is counted, reported in the activity list and otherwise
ignored. A consumer can only change parameters whose access allows it, and
only to values that fit type and range. It cannot change the structure of the
virtual tree.

The tests of the library feed thousands of damaged messages to the decoder
and send oversized and broken data to a running provider.

## Weaknesses found and fixed

| | Weakness | Fix |
|---|---|---|
| F1 | A provider could announce a display format such as `%999999999d`. Showing the value then built a text of that length and could exhaust the memory of the consumer | Formats are checked before use. One that is too long or asks for excessive width or precision is ignored and the plain value is shown. Test: `test_format_from_a_provider_cannot_blow_up_the_text` |
| F2 | The provider accepted messages of up to 16 MiB from every consumer, although consumers send only short requests. 64 consumers could tie up a gigabyte | Messages from consumers are limited to 1 MiB. A longer one is dropped and reported once. Test: `test_oversized_request_is_dropped_and_the_connection_goes_on` |
| F3 | An element path with thousands of numbers made the consumer build a tree as deep, which later operations could not walk | Paths are limited to 100 numbers. Test: `test_path_of_absurd_length_is_refused` |
| F4 | A tree file nested thousands of levels deep ended in an internal error instead of a clear refusal | Trees are limited to 64 levels, and a request body nested too deeply to be read counts as not valid. Tests: `test_tree_nested_too_deeply_is_refused`, `test_body_nested_absurdly_deep_is_refused` |

## Risks that remain

| | Risk | What to do |
|---|---|---|
| R1 | **The Ember+ port has no login and no encryption.** With the listen address `0.0.0.0`, every machine in the network can read the virtual tree and change writable parameters | The virtual tree holds only what you put into it, and nothing in the program acts on its values. Use `127.0.0.1` when only this machine should connect. Do not open the port to the internet |
| R2 | **Whoever may use the web interface or the REST API can make this machine connect to any address and port** as a consumer, and learns whether something answers there | Give access only to people who should have it. Set logins when the web server is reachable from the network |
| R3 | **Without HTTPS, passwords and session cookies cross the network readable** | Use `--tls-cert` and `--tls-key` whenever the web server is reachable from other machines. The program warns in the terminal and in Settings |
| R4 | **A hostile provider can make the consumer use much memory.** The limits allow 250,000 elements and messages of 16 MiB | Connect only to devices you know. The limits keep the program alive, they do not keep it small |
| R5 | **A consumer can keep the provider busy** with requests. There is no limit on their rate | The provider serves at most 64 consumers and drops one that does not read. It is a test tool, not a device for a hostile network |
| R6 | **The Mac app is signed ad hoc, not with an Apple Developer ID, and not notarized.** macOS cannot tell whether a copy was altered on its way | Download the app from the release page of the repository only, and compare the SHA-256 printed there |

## Not checked

- No independent review, no penetration test, no static analysis.
- The behaviour on Windows and Linux.
- The packages the program depends on (Flask, Werkzeug, Jinja2 and the others
  in `requirements-tested.txt`) beyond using current versions.
- The third-party code inside the packaged Mac app (Python, Tcl/Tk).

## Reporting a weakness

Please open an issue in the repository, or write to the author before
publishing details if the weakness can be used against people who run the
program.
