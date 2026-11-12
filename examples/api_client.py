"""Control Ember Emu from Python through its REST API.

Needs nothing but the standard library. Run it against a started Ember Emu:

    python api_client.py status
    python api_client.py serve
    python api_client.py local-set EmberEmu/Audio/Gain -6.5
    python api_client.py connect 192.168.1.50 --ember-port 9000
    python api_client.py get Device/Audio/Gain
    python api_client.py set Device/Audio/Gain -6
    python api_client.py pin Device/Audio/Gain
    python api_client.py pins
    python api_client.py --base https://emu.example:8089 --user robot status

``local-set`` changes a value of the virtual tree that Ember Emu serves.
``get``, ``set`` and ``pin`` talk to the provider Ember Emu is connected to as
a consumer. With ``--user`` the password is asked for, or read from
EMBER_EMU_API_PASSWORD.

The class ``EmberEmuApi`` below is small enough to copy into your own program.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(Exception):
    """The API answered with an error. ``status`` is the HTTP status code."""

    def __init__(self, status: int, message: str):
        """Keep the HTTP status code next to the message."""
        super().__init__(f"{status}: {message}")
        self.status = status


class EmberEmuApi:
    """A thin client for the REST API of Ember Emu."""

    def __init__(self, base: str = "http://localhost:8089", user: str | None = None,
                 password: str | None = None, timeout: float = 15.0):
        """Remember where Ember Emu runs and, if needed, the API login.

        Args:
            base: Address of Ember Emu, for example ``http://localhost:8089``.
            user: API user name, if the API requires a login.
            password: Password of that user.
            timeout: Seconds to wait for an answer.

        Raises:
            ValueError: ``base`` is not an http or https address.
        """
        if not base.startswith(("http://", "https://")):
            raise ValueError("base must start with http:// or https://")
        self.base = base.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if user is not None:
            token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Send one request and return the decoded JSON answer.

        Args:
            method: ``GET``, ``POST``, ``PATCH``, ``PUT`` or ``DELETE``.
            path: Path below ``/api/v1``, starting with a slash.
            body: Data to send as JSON. Requests that change something send ``{}`` without it.

        Raises:
            ApiError: The API answered with an error.
            OSError: Ember Emu cannot be reached.
        """
        data = None
        if method != "GET":
            data = json.dumps({} if body is None else body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error", exc.reason)
            except ValueError:
                message = exc.reason
            raise ApiError(exc.code, str(message)) from None

    @staticmethod
    def _element(path: str) -> str:
        """An element path as part of an address: slashes stay, the rest is escaped."""
        return urllib.parse.quote(path, safe="/")

    # -- the virtual tree that Ember Emu serves --------------------------

    def provider(self) -> dict:
        """Settings, status and tree of the virtual provider."""
        return self.request("GET", "/provider")

    def serve(self, on: bool = True) -> bool:
        """Open or close the Ember+ port. Returns whether it worked."""
        return bool(self.request("POST", "/provider/start" if on else "/provider/stop")["ok"])

    def local_element(self, path: str) -> dict:
        """One element of the virtual tree."""
        return self.request("GET", f"/provider/elements/{self._element(path)}")

    def local_set(self, path: str, value: Any) -> dict:
        """Set a value in the virtual tree. Connected consumers are told."""
        return self.request("PATCH", f"/provider/elements/{self._element(path)}", {"value": value})

    def local_add(self, parent: str, **fields: Any) -> dict:
        """Add an element to the virtual tree, below ``parent`` (empty: at the root)."""
        return self.request("POST", "/provider/elements", {"parent": parent, **fields})

    # -- the provider Ember Emu is connected to --------------------------

    def consumer(self) -> dict:
        """Settings, status, pins and the tree read so far."""
        return self.request("GET", "/consumer")

    def connect(self, host: str, port: int = 9000) -> bool:
        """Connect the consumer to a provider. Returns whether it worked."""
        self.request("PATCH", "/consumer", {"host": host, "port": port})
        return bool(self.request("POST", "/consumer/connect")["ok"])

    def disconnect(self) -> None:
        """Close the connection of the consumer."""
        self.request("POST", "/consumer/disconnect")

    def get(self, path: str) -> dict:
        """One element of the provider. Nodes on the way are loaded."""
        return self.request("GET", f"/consumer/elements/{self._element(path)}")

    def set(self, path: str, value: Any, check: bool = True) -> dict:
        """Ask the provider to set a value. The answer shows what the provider reports back."""
        return self.request("PATCH", f"/consumer/elements/{self._element(path)}",
                            {"value": value, "check": check})

    def invoke(self, path: str, *arguments: Any) -> list:
        """Call a function of the provider and return its result values."""
        return self.request("POST", f"/consumer/elements/{self._element(path)}/invoke",
                            {"arguments": list(arguments)})["result"]

    def pin(self, path: str) -> str:
        """Pin a parameter. Returns the identifier path it is remembered by."""
        return self.request("POST", "/consumer/pins", {"path": path})["identifier_path"]

    def unpin(self, path: str) -> None:
        """Remove a pin."""
        self.request("DELETE", f"/consumer/pins/{self._element(path)}")

    def pins(self) -> list[dict]:
        """The pinned parameters with their latest values."""
        return self.request("GET", "/consumer/pins")["pins"]


def show(element: dict) -> str:
    """One line about an element."""
    text = f"{element['path']}  {element['identifier_path']}  [{element['kind']}]"
    if element["kind"] == "parameter":
        text += f"  {element['type']} {element['access']} = {element['display'] or element['value']!r}"
    return text


def main() -> int:
    """Read the command line and run one command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:8089", help="address of Ember Emu")
    parser.add_argument("--user", default=None, help="API user, if the API requires a login")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="state of provider and consumer")
    commands.add_parser("serve", help="open the Ember+ port of the virtual provider")
    local = commands.add_parser("local-set", help="set a value of the virtual tree")
    local.add_argument("path")
    local.add_argument("value")
    connecting = commands.add_parser("connect", help="connect the consumer to a provider")
    connecting.add_argument("host")
    connecting.add_argument("--ember-port", type=int, default=9000)
    getting = commands.add_parser("get", help="read an element of the provider")
    getting.add_argument("path")
    setting = commands.add_parser("set", help="set a parameter of the provider")
    setting.add_argument("path")
    setting.add_argument("value")
    pinning = commands.add_parser("pin", help="pin a parameter of the provider")
    pinning.add_argument("path")
    commands.add_parser("pins", help="list the pinned parameters")
    args = parser.parse_args()

    password = None
    if args.user is not None:
        password = os.environ.get("EMBER_EMU_API_PASSWORD") or getpass.getpass("API password: ")
    api = EmberEmuApi(args.base, args.user, password)
    try:
        if args.command == "status":
            provider, consumer = api.provider()["status"], api.consumer()["status"]
            print("Provider:", f"serving on {provider['description']}, {len(provider['clients'])} consumers, "
                  f"{provider['element_count']} elements" if provider["running"] else "not serving")
            print("Consumer:", f"connected to {consumer['description']}, {consumer['element_count']} elements read"
                  if consumer["connected"] else "not connected")
        elif args.command == "serve":
            print("Serving." if api.serve() else "Could not open the port: " + api.provider()["status"]["error"])
        elif args.command == "local-set":
            print(show(api.local_set(args.path, args.value)))
        elif args.command == "connect":
            if api.connect(args.host, args.ember_port):
                print("Connected.")
            else:
                print("Not connected:", api.consumer()["status"]["error"])
                return 1
        elif args.command == "get":
            print(show(api.get(args.path)))
        elif args.command == "set":
            print(show(api.set(args.path, args.value)))
        elif args.command == "pin":
            print("Pinned", api.pin(args.path))
        elif args.command == "pins":
            for pin in api.pins():
                print(show(pin) if pin["found"] else f"{pin['identifier_path']}  ({pin['error'] or 'not found yet'})")
    except ApiError as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Ember Emu is not reachable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
