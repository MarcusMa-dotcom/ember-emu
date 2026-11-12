"""Flask web interface and REST API of Ember Emu.

Two doors lead to the same virtual provider and the same consumer:

* ``/`` with ``/ui/api/...`` is the web interface. Its optional login uses a
  form and a session cookie.
* ``/api/v1/...`` is the REST API for other programs. Its optional login is
  HTTP Basic.

Each door has its own switch and its own login in the access settings.
"""

from __future__ import annotations

import logging
import math
import secrets
import ssl
import threading
from datetime import timedelta

from emberemu import Access as ParameterAccess
from emberemu import MatrixType, ParameterType
from flask import Blueprint, Flask, jsonify, redirect, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.serving import WSGIRequestHandler, make_server

from . import __version__, about
from .access import MIN_PASSWORD_LENGTH, Access
from .consumer_engine import ConsumerEngine
from .provider_engine import ANIMATION_LABELS, KINDS, ProviderEngine

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SESSION_HOURS = 12
MAX_BODY_BYTES = 4 * 1024 * 1024   # a whole tree may arrive in one request
CHANGING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy":
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}
API_ENDPOINTS = [
    {"method": "GET", "path": "/api/v1/", "does": "This list"},
    {"method": "GET", "path": "/api/v1/provider", "does": "Provider settings, status and tree"},
    {"method": "PATCH", "path": "/api/v1/provider", "does": "Change provider settings"},
    {"method": "POST", "path": "/api/v1/provider/start", "does": "Open the Ember+ port"},
    {"method": "POST", "path": "/api/v1/provider/stop", "does": "Close the Ember+ port"},
    {"method": "POST", "path": "/api/v1/provider/clear-log", "does": "Forget the provider activity log"},
    {"method": "GET", "path": "/api/v1/provider/tree", "does": "The whole tree, nested"},
    {"method": "PUT", "path": "/api/v1/provider/tree", "does": "Replace the whole tree"},
    {"method": "DELETE", "path": "/api/v1/provider/tree", "does": "Remove every element"},
    {"method": "POST", "path": "/api/v1/provider/tree/example", "does": "Load the example tree"},
    {"method": "POST", "path": "/api/v1/provider/elements", "does": "Add an element"},
    {"method": "GET", "path": "/api/v1/provider/elements/PATH", "does": "One element"},
    {"method": "PATCH", "path": "/api/v1/provider/elements/PATH", "does": "Change value or properties"},
    {"method": "DELETE", "path": "/api/v1/provider/elements/PATH", "does": "Remove an element"},
    {"method": "POST", "path": "/api/v1/provider/elements/PATH/connect", "does": "Set sources of a matrix target"},
    {"method": "GET", "path": "/api/v1/consumer", "does": "Consumer settings, status, pins and tree"},
    {"method": "PATCH", "path": "/api/v1/consumer", "does": "Change consumer settings"},
    {"method": "POST", "path": "/api/v1/consumer/connect", "does": "Connect to the provider"},
    {"method": "POST", "path": "/api/v1/consumer/disconnect", "does": "Close the connection"},
    {"method": "POST", "path": "/api/v1/consumer/clear-log", "does": "Forget the consumer activity log"},
    {"method": "POST", "path": "/api/v1/consumer/expand", "does": "Load the children of a node"},
    {"method": "GET", "path": "/api/v1/consumer/elements/PATH", "does": "One element of the provider"},
    {"method": "PATCH", "path": "/api/v1/consumer/elements/PATH", "does": "Ask the provider to set a value"},
    {"method": "POST", "path": "/api/v1/consumer/elements/PATH/invoke", "does": "Call a function"},
    {"method": "POST", "path": "/api/v1/consumer/elements/PATH/connect", "does": "Ask for sources of a matrix target"},
    {"method": "GET", "path": "/api/v1/consumer/pins", "does": "Pinned parameters with their values"},
    {"method": "POST", "path": "/api/v1/consumer/pins", "does": "Pin a parameter"},
    {"method": "DELETE", "path": "/api/v1/consumer/pins/PATH", "does": "Remove a pin"},
]


LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


class _QuietHandler(WSGIRequestHandler):
    """Answers without telling which server and Python version run here."""

    def version_string(self) -> str:
        """The server name sent with every answer."""
        return "Ember-Emu"


def is_loopback(host: str) -> bool:
    """True if a web server bound to ``host`` can be reached from this machine only."""
    return host in LOOPBACK_HOSTS


def _request_hostname() -> str:
    """The host name the caller asked for, without port and IPv6 brackets."""
    host = request.host
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.split(":", 1)[0]


def _client() -> str:
    """Network address of the caller, used by the login throttle."""
    return request.remote_addr or "unknown"


def _error(message: str, status: int, **headers: str):
    """A JSON error answer.

    Args:
        message: Text for the ``error`` field.
        status: HTTP status code.
        **headers: Extra headers. Underscores in the names become dashes.
    """
    response = jsonify(error=message)
    response.status_code = status
    for name, value in headers.items():
        response.headers[name.replace("_", "-")] = value
    return response


def _too_many(wait: float):
    """Answer 429 with the waiting time, also as ``Retry-After`` header."""
    seconds = max(1, math.ceil(wait))
    return _error(f"Too many failed logins. Try again in {seconds} seconds.", 429,
                  Retry_After=str(seconds))


def _json_object():
    """The request body as dict, or None if it is not a JSON object."""
    try:
        data = request.get_json(silent=True)
    except RecursionError:  # nested too deeply to be read
        return None
    return data if isinstance(data, dict) else None


def _state(engine, **extra):
    """The snapshot of an engine as JSON answer.

    A caller that passes ``since`` (a revision) and ``instance`` gets a short
    answer with ``unchanged`` instead of the whole snapshot while nothing has
    changed. That keeps polling a large tree cheap.
    """
    since = request.args.get("since")
    if not extra and since is not None and request.args.get("instance") == engine.instance \
            and since == str(engine.revision):
        return jsonify(instance=engine.instance, revision=engine.revision, unchanged=True)
    data = engine.snapshot()
    data.update(extra)
    return jsonify(data)


def _state_error(engine, exc: Exception):
    """Answer 400 with the snapshot and the reason, for a setting that was refused."""
    data = engine.snapshot()
    data["error"] = str(exc)
    return jsonify(data), 400


def _data_blueprint(name: str, provider: ProviderEngine, consumer: ConsumerEngine) -> Blueprint:
    """Provider and consumer routes. Mounted once for the web page, once as REST API."""
    bp = Blueprint(name, __name__)

    # -- provider: the virtual tree --------------------------------------

    @bp.get("/provider")
    def get_provider():
        """GET /provider: settings, status and tree of the virtual provider."""
        return _state(provider)

    @bp.route("/provider", methods=["PATCH"])
    def patch_provider():
        """PATCH /provider: change provider settings. Answers 400 with ``error`` for bad values."""
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        try:
            provider.update(**changes)
        except ValueError as exc:
            return _state_error(provider, exc)
        return _state(provider)

    @bp.post("/provider/start")
    def provider_start():
        """POST /provider/start: open the Ember+ port. ``ok`` tells whether it worked."""
        return _state(provider, ok=provider.start())

    @bp.post("/provider/stop")
    def provider_stop():
        """POST /provider/stop: close the Ember+ port."""
        provider.stop_serving()
        return _state(provider, ok=True)

    @bp.post("/provider/clear-log")
    def provider_clear_log():
        """POST /provider/clear-log: forget the activity log."""
        provider.clear_log()
        return _state(provider, ok=True)

    @bp.get("/provider/tree")
    def get_tree():
        """GET /provider/tree: the whole tree in the nested format that PUT takes."""
        return jsonify(provider.export_tree())

    @bp.route("/provider/tree", methods=["PUT"])
    def put_tree():
        """PUT /provider/tree: replace the whole tree."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        try:
            provider.import_tree(data)
        except ValueError as exc:
            return _error(str(exc), 400)
        return jsonify(ok=True, elements=provider.snapshot()["status"]["element_count"])

    @bp.route("/provider/tree", methods=["DELETE"])
    def delete_tree():
        """DELETE /provider/tree: remove every element."""
        provider.clear_tree()
        return jsonify(ok=True, elements=0)

    @bp.post("/provider/tree/example")
    def example():
        """POST /provider/tree/example: replace the tree with the example tree."""
        provider.load_example()
        return jsonify(ok=True, elements=provider.snapshot()["status"]["element_count"])

    @bp.post("/provider/elements")
    def add_element():
        """POST /provider/elements: add an element below ``parent`` (empty: at the root)."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        parent = data.pop("parent", "")
        try:
            path = provider.add_element(parent if isinstance(parent, str) else str(parent), data)
            return jsonify(provider.element(path)), 201
        except ValueError as exc:
            return _error(str(exc), 400)

    @bp.get("/provider/elements/<path:path>")
    def get_element(path: str):
        """GET /provider/elements/PATH: one element, by numbers or identifiers."""
        try:
            return jsonify(provider.element(path))
        except ValueError as exc:
            return _error(str(exc), 404)

    @bp.route("/provider/elements/<path:path>", methods=["PATCH"])
    def patch_element(path: str):
        """PATCH /provider/elements/PATH: change the value or other properties."""
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        try:
            provider.element(path)
        except ValueError as exc:
            return _error(str(exc), 404)
        try:
            provider.update_element(path, changes)
            return jsonify(provider.element(path))
        except ValueError as exc:
            return _error(str(exc), 400)

    @bp.route("/provider/elements/<path:path>", methods=["DELETE"])
    def delete_element(path: str):
        """DELETE /provider/elements/PATH: remove an element and everything below it."""
        try:
            provider.remove_element(path)
        except ValueError as exc:
            return _error(str(exc), 404)
        return jsonify(ok=True)

    @bp.post("/provider/elements/<path:path>/connect")
    def provider_connect(path: str):
        """POST /provider/elements/PATH/connect: set the sources of one matrix target."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        try:
            provider.connect_matrix(path, data.get("target"), data.get("sources", []),
                                    data.get("operation", "absolute"))
            return jsonify(provider.element(path))
        except ValueError as exc:
            return _error(str(exc), 400)

    # -- consumer: the tree of another provider --------------------------

    @bp.get("/consumer")
    def get_consumer():
        """GET /consumer: settings, status, pins and the tree read so far."""
        return _state(consumer)

    @bp.route("/consumer", methods=["PATCH"])
    def patch_consumer():
        """PATCH /consumer: change consumer settings. Answers 400 with ``error`` for bad values."""
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        try:
            consumer.update(**changes)
        except ValueError as exc:
            return _state_error(consumer, exc)
        return _state(consumer)

    @bp.post("/consumer/connect")
    def consumer_connect():
        """POST /consumer/connect: connect and read the root elements. ``ok`` tells whether it worked."""
        return _state(consumer, ok=consumer.connect())

    @bp.post("/consumer/disconnect")
    def consumer_disconnect():
        """POST /consumer/disconnect: close the connection."""
        consumer.disconnect()
        return _state(consumer, ok=True)

    @bp.post("/consumer/clear-log")
    def consumer_clear_log():
        """POST /consumer/clear-log: forget the activity log."""
        consumer.clear_log()
        return _state(consumer, ok=True)

    @bp.post("/consumer/expand")
    def expand():
        """POST /consumer/expand: load the children of ``path``, or everything below with ``all``."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        path = data.get("path", "")
        if not isinstance(path, str):
            return _error("path must be text", 400)
        try:
            if data.get("all") is True:
                consumer.expand_all(path)
            else:
                consumer.expand(path, refresh=data.get("refresh") is True)
        except ValueError as exc:
            return _error(str(exc), 400)
        return _state(consumer, ok=True)

    @bp.get("/consumer/elements/<path:path>")
    def consumer_element(path: str):
        """GET /consumer/elements/PATH: one element of the provider, loaded if need be."""
        try:
            return jsonify(consumer.element(path))
        except ValueError as exc:
            return _error(str(exc), 404)

    @bp.route("/consumer/elements/<path:path>", methods=["PATCH"])
    def consumer_set(path: str):
        """PATCH /consumer/elements/PATH: ask the provider to set ``value``."""
        data = _json_object()
        if data is None or "value" not in data:
            return _error('Body must be a JSON object with "value"', 400)
        try:
            consumer.set_value(path, data["value"], check=data.get("check") is not False)
            return jsonify(consumer.element(path))
        except ValueError as exc:
            return _error(str(exc), 400)

    @bp.post("/consumer/elements/<path:path>/invoke")
    def invoke(path: str):
        """POST /consumer/elements/PATH/invoke: call a function with ``arguments``."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        try:
            return jsonify(result=consumer.invoke(path, data.get("arguments", [])))
        except ValueError as exc:
            return _error(str(exc), 400)

    @bp.post("/consumer/elements/<path:path>/connect")
    def consumer_matrix(path: str):
        """POST /consumer/elements/PATH/connect: ask for new sources of one matrix target."""
        data = _json_object()
        if data is None:
            return _error("Body must be a JSON object", 400)
        try:
            consumer.set_connection(path, data.get("target"), data.get("sources", []),
                                    data.get("operation", "absolute"))
        except ValueError as exc:
            return _error(str(exc), 400)
        return jsonify(ok=True)

    @bp.get("/consumer/pins")
    def pins():
        """GET /consumer/pins: the pinned parameters with their latest values."""
        return jsonify(pins=consumer.snapshot()["status"]["pins"])

    @bp.post("/consumer/pins")
    def pin():
        """POST /consumer/pins: pin the parameter at ``path``."""
        data = _json_object()
        if data is None or not isinstance(data.get("path"), str):
            return _error('Body must be a JSON object with "path"', 400)
        try:
            identifier_path = consumer.pin(data["path"])
        except ValueError as exc:
            return _error(str(exc), 400)
        return jsonify(identifier_path=identifier_path, pins=consumer.snapshot()["status"]["pins"]), 201

    @bp.route("/consumer/pins/<path:path>", methods=["DELETE"])
    def unpin(path: str):
        """DELETE /consumer/pins/PATH: remove a pin, by identifier path or by numbers."""
        try:
            consumer.unpin(path)
        except ValueError as exc:
            return _error(str(exc), 404)
        return jsonify(ok=True, pins=consumer.snapshot()["status"]["pins"])

    return bp


def create_app(
    provider: ProviderEngine,
    consumer: ConsumerEngine | None = None,
    access: Access | None = None,
    local_only: bool = True,
    tls: bool = False,
) -> Flask:
    """Create the Flask app.

    With ``local_only`` the app answers only requests addressed to this
    machine by a loopback name, which blocks DNS rebinding from web pages.
    """
    app = Flask(__name__)
    app.json.sort_keys = False
    # New key at every start: sessions end when the program ends.
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        SESSION_COOKIE_NAME="ember_emu_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=tls,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )
    if consumer is None:
        consumer = ConsumerEngine()
    if access is None:
        access = Access()

    def logged_in() -> bool:
        """True if this browser holds a session for the current web login."""
        return session.get("g") == access.generation("web") and session.get("in") is True

    def start_session() -> None:
        """Begin a fresh session for the current web login."""
        session.clear()
        session.permanent = True
        session["in"] = True
        session["g"] = access.generation("web")

    # -- guards ----------------------------------------------------------

    def guard_api():
        """Gate of the REST API: first the switch, then HTTP Basic with the throttle."""
        if not access.enabled("api"):
            return _error("The REST API is switched off", 403)
        if not access.login_required("api"):
            return None
        challenge = {"WWW_Authenticate": 'Basic realm="Ember Emu API", charset="UTF-8"'}
        auth = request.authorization
        if auth is None or auth.type != "basic" or auth.username is None:
            return _error("Login required", 401, **challenge)
        ok, wait = access.attempt("api", _client(), auth.username, auth.password or "")
        if ok:
            return None
        if wait > 0:
            return _too_many(wait)
        return _error("User name or password is wrong", 401, **challenge)

    def guard_web():
        """Gate of the web interface: first the switch, then the session.

        The login page and the static files stay reachable without a session.
        """
        wants_json = request.path.startswith("/ui/api/")
        if not access.enabled("web"):
            if wants_json:
                return _error("The web interface is switched off", 403)
            return "The web interface is switched off.\n", 403, {"Content-Type": "text/plain"}
        if not access.login_required("web") or logged_in():
            return None
        if request.path in ("/login", "/ui/api/login") or request.path.startswith("/static/"):
            return None
        if wants_json:
            return _error("Login required", 401)
        return redirect("/login")

    @app.before_request
    def guard():
        """Runs before every request.

        It checks the host name, turns away requests that other web pages could
        trigger, and then asks the gate of the door the request goes to.
        """
        if local_only and _request_hostname() not in LOOPBACK_HOSTS:
            return _error("This interface only answers on localhost", 403)
        if request.method in CHANGING_METHODS:
            # A JSON content type cannot be sent by a plain cross-site form,
            # and browsers name the page a request comes from.
            if not request.is_json:
                return _error("Requests that change something must use Content-Type application/json", 415)
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return _error("Cross-site request refused", 403)
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return _error("Request from another origin refused", 403)
        if request.path.startswith("/api/"):
            return guard_api()
        return guard_web()

    @app.after_request
    def harden(response):
        """Add the security headers and forbid caching of everything but static files."""
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        """HTTP errors as JSON on API paths, as the usual page elsewhere."""
        if request.path.startswith(("/api/", "/ui/api/")):
            return _error(exc.description or exc.name, exc.code or 500)
        return exc

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        """Any other error: log it and answer 500 without details."""
        logging.getLogger("emberemu_app.web").exception("Request failed")
        return _error("Internal error", 500)

    # -- pages -----------------------------------------------------------

    @app.get("/")
    def index():
        """GET /: the page with all views."""
        return render_template(
            "index.html",
            version=__version__,
            kinds=KINDS,
            parameter_types=[member.name.lower() for member in ParameterType
                             if member != ParameterType.NULL],
            access_values=[member.name.lower() for member in ParameterAccess],
            matrix_types=[member.name.lower() for member in MatrixType],
            animations=ANIMATION_LABELS,
            min_password=MIN_PASSWORD_LENGTH,
            about=about,
        )

    @app.get("/login")
    def login_page():
        """GET /login: the login form, or a redirect to the page if no login is needed."""
        if not access.login_required("web") or logged_in():
            return redirect("/")
        return render_template("login.html", version=__version__)

    # -- web interface only: login and access settings -------------------

    def access_state() -> dict:
        """The access settings for the Settings view, plus what the page needs for its warnings."""
        data = access.public()
        data.update(
            logged_in=access.login_required("web") and logged_in(),
            exposed=not local_only,
            tls=tls,
            min_password=MIN_PASSWORD_LENGTH,
        )
        return data

    @app.post("/ui/api/login")
    def login():
        """POST /ui/api/login: check user and password and start a session."""
        if not access.login_required("web"):
            return jsonify(ok=True)
        data = _json_object() or {}
        user, password = data.get("user"), data.get("password")
        ok, wait = access.attempt(
            "web", _client(),
            user if isinstance(user, str) else "",
            password if isinstance(password, str) else "",
        )
        if not ok:
            if wait > 0:
                return _too_many(wait)
            return _error("User name or password is wrong", 401)
        start_session()
        return jsonify(ok=True)

    @app.post("/ui/api/logout")
    def logout():
        """POST /ui/api/logout: end the session."""
        session.clear()
        return jsonify(ok=True)

    @app.get("/ui/api/access")
    def get_access():
        """GET /ui/api/access: switches and logins, without the hashes."""
        return jsonify(access_state())

    @app.route("/ui/api/access", methods=["PATCH"])
    def patch_access():
        """PATCH /ui/api/access: change switches and logins.

        Without a web login only this machine may do that. With one, a change of
        the web login needs the current password in ``current_password``.
        """
        changes = _json_object()
        if changes is None:
            return _error("Body must be a JSON object", 400)
        if not access.login_required("web") and _client() not in LOCAL_CLIENTS:
            # Without a web login, only this machine may hand out logins and switches.
            # Otherwise anyone on the network could take the program over.
            return _error("Access settings can be changed from another machine "
                          "only when the web interface requires a login", 403)
        current = changes.pop("current_password", None)
        changes_web_login = any(key in changes for key in ("web_login", "web_user", "web_password"))
        if access.login_required("web") and changes_web_login:
            # Changing the web login needs the current password again.
            ok, wait = access.attempt(
                "web", _client(), access.settings.web_user,
                current if isinstance(current, str) else "",
            )
            if not ok:
                if wait > 0:
                    return _too_many(wait)
                return _error("Enter the current web password to change the web login", 403)
        try:
            access.update(**changes)
        except ValueError as exc:
            return _error(str(exc), 400)
        if access.login_required("web"):
            start_session()  # this browser made the change and stays logged in
        return jsonify(access_state())

    # -- REST API only ---------------------------------------------------

    @app.get("/api/v1/")
    def api_index():
        """GET /api/v1/: name, version and the list of endpoints."""
        return jsonify(name="Ember Emu", version=__version__, endpoints=API_ENDPOINTS)

    app.register_blueprint(_data_blueprint("ui", provider, consumer), url_prefix="/ui/api")
    app.register_blueprint(_data_blueprint("v1", provider, consumer), url_prefix="/api/v1")
    return app


class WebServer:
    """Runs the Flask app in a background thread, as long as a door is open.

    The server listens only while the web interface or the REST API is
    switched on. Switching both off closes the port.
    """

    def __init__(
        self,
        provider: ProviderEngine,
        consumer: ConsumerEngine | None = None,
        access: Access | None = None,
        host: str = "127.0.0.1",
        port: int = 8089,
        tls: tuple[str, str] | None = None,
    ):
        """Prepare the server. The port opens with :meth:`start`.

        Args:
            provider: The virtual provider.
            consumer: The consumer. A fresh one is made if left out.
            access: The access settings. Defaults to both doors open, no logins.
            host: Address to listen on. Anything but a loopback address makes the
                server reachable from other machines.
            port: Port to listen on, 0 for a free one.
            tls: Certificate file and key file for HTTPS, or None for HTTP.
        """
        self.host = host
        self.port = port
        self.error = ""
        self.access = access if access is not None else Access()
        self._tls = tls
        # The pages poll several times a second: keep the request log quiet.
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        self._app = create_app(provider, consumer, self.access,
                               local_only=is_loopback(host), tls=tls is not None)
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self.access.on_change(self._access_changed)

    @property
    def exposed(self) -> bool:
        """True if the server listens beyond this machine."""
        return not is_loopback(self.host)

    @property
    def tls(self) -> bool:
        """True if the server speaks HTTPS."""
        return self._tls is not None

    @property
    def wanted(self) -> bool:
        """True if at least one door is switched on."""
        return self.access.enabled("web") or self.access.enabled("api")

    @property
    def running(self) -> bool:
        """True while the port is open."""
        return self._server is not None

    @property
    def url(self) -> str:
        """Address of the web interface as a person would type it."""
        scheme = "https" if self._tls else "http"
        shown = "localhost" if self.host in ("0.0.0.0", "::") or is_loopback(self.host) else self.host
        return f"{scheme}://{shown}:{self.port}/"

    def start(self) -> bool:
        """Open the port if a door is switched on. Returns False if that failed."""
        self.apply()
        return not self.error

    def apply(self) -> None:
        """Open or close the port to match the access settings."""
        with self._lock:
            if self._closed:
                return
            if self.wanted and self._server is None:
                try:
                    server = make_server(self.host, self.port, self._app, threaded=True,
                                         ssl_context=self._tls, request_handler=_QuietHandler)
                except SystemExit:
                    # Werkzeug ends the program when it cannot bind. Keep running instead.
                    self.error = (f"Cannot serve on {self.host}:{self.port}: "
                                  "the port is in use or not allowed")
                    return
                except (OSError, ssl.SSLError, ValueError) as exc:
                    self.error = f"Cannot serve on {self.host}:{self.port}: {exc}"
                    return
                self.error = ""
                self.port = server.server_port
                self._server = server
                self._thread = threading.Thread(target=server.serve_forever, name="ember-web", daemon=True)
                self._thread.start()
            elif not self.wanted and self._server is not None:
                self._shutdown()

    def _shutdown(self) -> None:
        """Close the port. Called with the lock held."""
        server, thread = self._server, self._thread
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        # Only now: "running" must stay true until the port is really closed.
        self._server = None
        self._thread = None

    def _access_changed(self) -> None:
        """Listener for access changes: adjust the port a moment later."""
        # A moment later, so the answer to the request that made the change still goes out.
        timer = threading.Timer(0.3, self.apply)
        timer.daemon = True
        timer.start()

    def stop(self) -> None:
        """Close the port for good, at program end."""
        with self._lock:
            self._closed = True
            if self._server is not None:
                self._shutdown()
