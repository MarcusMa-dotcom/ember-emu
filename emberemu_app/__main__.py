"""Command line entry: starts the virtual provider, the consumer, the web server and the window."""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__, about
from .access import Access
from .common import default_config_path
from .consumer_engine import ConsumerEngine
from .provider_engine import ProviderEngine

WEB_PASSWORD_ENV = "EMBER_EMU_WEB_PASSWORD"
API_PASSWORD_ENV = "EMBER_EMU_API_PASSWORD"


def _wait_for_interrupt() -> None:
    """Block until Ctrl+C or a termination signal arrives."""
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass


def _ask_password(label: str, env_name: str) -> str:
    """Take a new password from the environment or ask for it, never from the command line."""
    value = os.environ.get(env_name)
    if value:
        return value
    if not sys.stdin.isatty():
        raise SystemExit(f"Set {env_name} or start in a terminal to enter the password for the {label}.")
    first = getpass.getpass(f"New password for the {label}: ")
    if first != getpass.getpass("Repeat the password: "):
        raise SystemExit("The two passwords differ.")
    return first


def _apply_access_options(args: argparse.Namespace, access: Access) -> None:
    """Save the access options given on the command line.

    They are stored like changes made in the Settings tab. A value the access
    settings refuse ends the program with its message.
    """
    changes: dict = {}
    if args.web:
        changes["web_enabled"] = args.web == "on"
    if args.api:
        changes["api_enabled"] = args.api == "on"
    if args.web_login:
        changes.update(web_user=args.web_login, web_login=True,
                       web_password=_ask_password("web interface", WEB_PASSWORD_ENV))
    if args.no_web_login:
        changes["web_login"] = False
    if args.api_login:
        changes.update(api_user=args.api_login, api_login=True,
                       api_password=_ask_password("REST API", API_PASSWORD_ENV))
    if args.no_api_login:
        changes["api_login"] = False
    if changes:
        try:
            access.update(**changes)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None


def _report(server, access: Access) -> None:
    """Print where the doors are and warn about open ones."""
    state = access.public()
    if not server.running:
        print("Web interface and REST API are switched off, the port is closed.", flush=True)
        return
    for key, label, path in (("web", "Web interface", ""), ("api", "REST API", "api/v1/")):
        if state[f"{key}_enabled"]:
            login = "login required" if state[f"{key}_login"] else "no login"
            print(f"{label}: {server.url}{path} ({login})", flush=True)
        else:
            print(f"{label}: off", flush=True)
    if not server.exposed:
        return
    for key, label in (("web", "web interface"), ("api", "REST API")):
        if state[f"{key}_enabled"] and not state[f"{key}_login"]:
            print(f"WARNING: the {label} can be reached from the network without a login.",
                  file=sys.stderr, flush=True)
    if not server.tls and (state["web_login"] or state["api_login"]):
        print("WARNING: without TLS, passwords cross the network unencrypted. "
              "Use --tls-cert and --tls-key.", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    """The command line options of the program."""
    parser = argparse.ArgumentParser(
        prog="ember-emu",
        description="Serve a virtual Ember+ tree and read the tree of any Ember+ provider.",
        epilog=f"{about.LICENSE_NAME}. {about.COPYRIGHT}. {about.DISCLAIMER}",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="address the web server listens on (default: 127.0.0.1, "
                             "use 0.0.0.0 to reach it from other machines)")
    parser.add_argument("--port", type=int, default=8089,
                        help="port of the web server (default: 8089)")
    parser.add_argument("--no-gui", action="store_true", help="run without the desktop window")
    parser.add_argument("--no-web", action="store_true",
                        help="run without web server: no web interface and no REST API")
    parser.add_argument("--serve", action="store_true",
                        help="start serving the virtual tree on its Ember+ port right away")
    parser.add_argument("--config", type=Path, default=None,
                        help=f"settings file (default: {default_config_path()})")
    parser.add_argument("--tls-cert", type=Path, default=None, help="certificate file for HTTPS")
    parser.add_argument("--tls-key", type=Path, default=None, help="private key file for HTTPS")

    access = parser.add_argument_group(
        "access settings", "These are saved, like changes in the Settings tab.")
    access.add_argument("--web", choices=("on", "off"), help="switch the web interface on or off")
    access.add_argument("--api", choices=("on", "off"), help="switch the REST API on or off")
    web_login = access.add_mutually_exclusive_group()
    web_login.add_argument("--web-login", metavar="USER",
                           help=f"require a login for the web interface; the password is asked for "
                                f"or read from {WEB_PASSWORD_ENV}")
    web_login.add_argument("--no-web-login", action="store_true",
                           help="web interface without login")
    api_login = access.add_mutually_exclusive_group()
    api_login.add_argument("--api-login", metavar="USER",
                           help=f"require a login for the REST API; the password is asked for "
                                f"or read from {API_PASSWORD_ENV}")
    api_login.add_argument("--no-api-login", action="store_true", help="REST API without login")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the program: virtual provider, consumer, web server and window.

    Args:
        argv: The arguments without the program name. Defaults to ``sys.argv``.

    Returns:
        The exit code: 0 after a normal end, 1 if nothing could be started.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_gui and args.no_web and not args.serve:
        parser.error("--no-gui and --no-web together leave nothing to run, unless --serve is given")
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key belong together")

    config = args.config or default_config_path()
    access = Access(config.with_name(config.stem + ".access.json"))
    _apply_access_options(args, access)

    provider = ProviderEngine(config_path=config)
    consumer = ConsumerEngine(config_path=config.with_name(config.stem + ".consumer.json"))
    provider.start_timer()
    consumer.start_timer()
    serving = False
    if args.serve:
        serving = provider.start()
        if not serving:
            print(provider.snapshot()["status"]["error"], file=sys.stderr)
        else:
            print(f"Ember+ provider: {provider.provider.description}", flush=True)
    server = None
    try:
        if not args.no_web:
            from .web import WebServer
            tls = (str(args.tls_cert), str(args.tls_key)) if args.tls_cert else None
            server = WebServer(provider, consumer, access, args.host, args.port, tls)
            if not server.start():
                print(server.error, file=sys.stderr)
                if args.no_gui:
                    return 1
            else:
                _report(server, access)

        if args.no_gui:
            if (server is None or not server.running) and not serving:
                print("Nothing to reach without window. Start with --web on or --api on, or with --serve.",
                      file=sys.stderr)
                return 1
            print("Running without window. Press Ctrl+C to stop.", flush=True)
            _wait_for_interrupt()
            return 0

        try:
            from .gui import run_gui
        except ImportError as exc:
            print(f"Desktop window not available: {exc}", file=sys.stderr)
            print("Install Tk for Python (for example python3-tk on Linux, python-tk with "
                  "Homebrew) or start with --no-gui.", file=sys.stderr)
            if server is None or not server.running:
                return 1
            print("Continuing without window. Press Ctrl+C to stop.", flush=True)
            _wait_for_interrupt()
            return 0
        run_gui(provider, consumer, access, server)
        return 0
    finally:
        if server is not None:
            server.stop()
        consumer.close()
        provider.close()


if __name__ == "__main__":
    sys.exit(main())
