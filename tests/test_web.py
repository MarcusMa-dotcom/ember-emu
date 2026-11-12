"""Tests of the web interface and the REST API, through Flask's test client."""

import base64
import time

import pytest

from emberemu_app.access import MAX_FAILURES, Access
from emberemu_app.consumer_engine import ConsumerEngine, ConsumerSettings
from emberemu_app.provider_engine import ProviderEngine, ProviderSettings
from emberemu_app.web import API_ENDPOINTS, WebServer, create_app

LOCAL = {"base_url": "http://localhost:8089"}
PASSWORD = "correct horse battery"


def basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def provider():
    engine = ProviderEngine(ProviderSettings(bind="127.0.0.1", port=0, animate=False))
    yield engine
    engine.close()


@pytest.fixture
def consumer():
    engine = ConsumerEngine(ConsumerSettings())
    yield engine
    engine.close()


@pytest.fixture
def access():
    return Access()


@pytest.fixture
def client(provider, consumer, access):
    return create_app(provider, consumer, access).test_client()


@pytest.fixture
def linked(client, provider, consumer):
    """The consumer of the app connected to the provider of the app, through the API."""
    assert client.post("/api/v1/provider/start", json={}, **LOCAL).get_json()["ok"]
    consumer.update(port=provider.provider.port)
    assert client.post("/api/v1/consumer/connect", json={}, **LOCAL).get_json()["ok"]
    return client


# -- pages ------------------------------------------------------------------

def test_page_loads(client):
    response = client.get("/", **LOCAL)
    assert response.status_code == 200
    for text in (b"Ember Emu", b"Pinned parameters", b"read_write", b"Clock (text)"):
        assert text in response.data
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    for name in ("app.js", "provider.js", "consumer.js", "settings.js", "app.css", "logo.png"):
        assert client.get(f"/static/{name}", **LOCAL).status_code == 200


def test_api_index_lists_every_route(client):
    data = client.get("/api/v1/", **LOCAL).get_json()
    assert data["name"] == "Ember Emu" and data["endpoints"] == API_ENDPOINTS
    listed = {(item["method"], item["path"].replace("PATH", "<path:path>")) for item in API_ENDPOINTS}
    served = {(method, rule.rule) for rule in client.application.url_map.iter_rules()
              if rule.rule.startswith("/api/v1/")
              for method in rule.methods - {"HEAD", "OPTIONS"}}
    assert listed == served


# -- provider ---------------------------------------------------------------

def test_provider_state_and_polling(client):
    data = client.get("/api/v1/provider", **LOCAL).get_json()
    assert data["status"]["running"] is False and data["status"]["element_count"] == 45
    assert data["tree"][0]["identifier"] == "EmberEmu"
    query = f"?since={data['revision']}&instance={data['instance']}"
    assert client.get("/api/v1/provider" + query, **LOCAL).get_json() == {
        "instance": data["instance"], "revision": data["revision"], "unchanged": True}
    client.patch("/api/v1/provider/elements/1.2.1", json={"value": 1}, **LOCAL)
    assert "tree" in client.get("/api/v1/provider" + query, **LOCAL).get_json()
    assert "tree" in client.get(f"/api/v1/provider?since={data['revision']}&instance=other",
                                **LOCAL).get_json()


def test_provider_settings_start_and_stop(client, provider):
    response = client.patch("/api/v1/provider", json={"port": 99999}, **LOCAL)
    assert response.status_code == 400 and "Port" in response.get_json()["error"]
    assert client.patch("/api/v1/provider", json={"animate": True}, **LOCAL).get_json()["settings"]["animate"]
    started = client.post("/api/v1/provider/start", json={}, **LOCAL).get_json()
    assert started["ok"] and started["status"]["running"]
    stopped = client.post("/api/v1/provider/stop", json={}, **LOCAL).get_json()
    assert stopped["ok"] and not stopped["status"]["running"]


def test_provider_elements(client):
    response = client.post("/api/v1/provider/elements", json={
        "parent": "EmberEmu/Audio", "kind": "parameter", "identifier": "Pan", "type": "real",
        "access": "read_write", "minimum": -1, "maximum": 1, "value": 0.5}, **LOCAL)
    assert response.status_code == 201
    assert response.get_json()["path"] == "1.2.7"
    by_name = client.get("/api/v1/provider/elements/EmberEmu/Audio/Pan", **LOCAL).get_json()
    assert by_name["value"] == 0.5 and by_name["identifier_path"] == "EmberEmu/Audio/Pan"
    changed = client.patch("/api/v1/provider/elements/1.2.7", json={"value": -0.25}, **LOCAL)
    assert changed.get_json()["value"] == -0.25
    renamed = client.patch("/api/v1/provider/elements/1.2.7",
                           json={"identifier": "Balance", "description": "L/R"}, **LOCAL).get_json()
    assert renamed["identifier"] == "Balance" and renamed["description"] == "L/R"
    assert client.patch("/api/v1/provider/elements/1.2.7", json={"value": 5}, **LOCAL).status_code == 400
    assert client.patch("/api/v1/provider/elements/9.9", json={"value": 5}, **LOCAL).status_code == 404
    assert client.post("/api/v1/provider/elements", json={"kind": "node", "identifier": "1x"},
                       **LOCAL).status_code == 400
    matrix = client.post("/api/v1/provider/elements/1.6.1/connect",
                         json={"target": 2, "sources": [0]}, **LOCAL).get_json()
    assert matrix["connections"]["2"] == [0]
    assert client.delete("/api/v1/provider/elements/1.2.7", json={}, **LOCAL).get_json() == {"ok": True}
    assert client.get("/api/v1/provider/elements/1.2.7", **LOCAL).status_code == 404
    assert client.delete("/api/v1/provider/elements/1.2.7", json={}, **LOCAL).status_code == 404


def test_provider_tree_as_a_whole(client):
    tree = client.get("/api/v1/provider/tree", **LOCAL).get_json()
    assert tree["children"][0]["identifier"] == "EmberEmu"
    assert client.delete("/api/v1/provider/tree", json={}, **LOCAL).get_json()["elements"] == 0
    assert client.put("/api/v1/provider/tree", json=tree, **LOCAL).get_json() == {"ok": True, "elements": 45}
    assert client.get("/api/v1/provider/tree", **LOCAL).get_json() == tree
    bad = client.put("/api/v1/provider/tree", json={"children": [{"kind": "what"}]}, **LOCAL)
    assert bad.status_code == 400 and "error" in bad.get_json()
    small = {"children": [{"kind": "node", "identifier": "Only"}]}
    assert client.put("/api/v1/provider/tree", json=small, **LOCAL).get_json()["elements"] == 1
    assert client.post("/api/v1/provider/tree/example", json={}, **LOCAL).get_json()["elements"] == 45


# -- consumer ---------------------------------------------------------------

def test_consumer_reads_and_writes(linked, provider):
    data = linked.get("/api/v1/consumer", **LOCAL).get_json()
    assert data["status"]["connected"] and [row["identifier"] for row in data["tree"]] == ["EmberEmu"]
    expanded = linked.post("/api/v1/consumer/expand", json={"path": "1"}, **LOCAL).get_json()
    assert expanded["ok"] and len(expanded["tree"]) == 8
    gain = linked.get("/api/v1/consumer/elements/EmberEmu/Audio/Gain", **LOCAL).get_json()
    assert gain["value"] == -12.0 and gain["path"] == "1.2.1" and gain["pinned"] is False
    answer = linked.patch("/api/v1/consumer/elements/EmberEmu/Audio/Gain", json={"value": "-3,5"}, **LOCAL)
    assert answer.get_json()["value"] == -3.5 and provider.element("1.2.1")["value"] == -3.5
    kept = linked.patch("/api/v1/consumer/elements/1.1.4", json={"value": 1}, **LOCAL).get_json()
    assert kept["value"] == 100042                                    # read only: the provider refuses
    assert linked.patch("/api/v1/consumer/elements/1.2.1", json={"value": 99}, **LOCAL).status_code == 400
    unchecked = linked.patch("/api/v1/consumer/elements/1.2.1", json={"value": 99, "check": False}, **LOCAL)
    assert unchecked.status_code == 200 and unchecked.get_json()["value"] == -3.5
    assert linked.patch("/api/v1/consumer/elements/1.2.1", json={}, **LOCAL).status_code == 400
    assert linked.get("/api/v1/consumer/elements/EmberEmu/Nothing", **LOCAL).status_code == 404
    assert linked.post("/api/v1/consumer/expand", json={"path": "No/Such"}, **LOCAL).status_code == 400


def test_consumer_function_matrix_and_pins(linked, provider):
    called = linked.post("/api/v1/consumer/elements/EmberEmu/Control/Add/invoke",
                         json={"arguments": [2, 40]}, **LOCAL)
    assert called.get_json() == {"result": [42]}
    assert linked.post("/api/v1/consumer/elements/EmberEmu/Control/Add/invoke",
                       json={"arguments": [2]}, **LOCAL).status_code == 400
    assert linked.post("/api/v1/consumer/elements/EmberEmu/Router/Matrix/connect",
                       json={"target": 1, "sources": [3]}, **LOCAL).get_json() == {"ok": True}
    wait_for(lambda: provider.element("1.6.1")["connections"]["1"] == [3])

    pinned = linked.post("/api/v1/consumer/pins", json={"path": "EmberEmu/Audio/Gain"}, **LOCAL)
    assert pinned.status_code == 201 and pinned.get_json()["identifier_path"] == "EmberEmu/Audio/Gain"
    assert linked.post("/api/v1/consumer/pins", json={"path": "EmberEmu/Audio"}, **LOCAL).status_code == 400
    provider.update_element("1.2.1", {"value": 6.0})
    wait_for(lambda: linked.get("/api/v1/consumer/pins", **LOCAL).get_json()["pins"][0]["value"] == 6.0)
    assert linked.get("/api/v1/consumer/elements/1.2.1", **LOCAL).get_json()["pinned"] is True
    removed = linked.delete("/api/v1/consumer/pins/EmberEmu/Audio/Gain", json={}, **LOCAL)
    assert removed.get_json() == {"ok": True, "pins": []}
    assert linked.delete("/api/v1/consumer/pins/EmberEmu/Audio/Gain", json={}, **LOCAL).status_code == 404

    done = linked.post("/api/v1/consumer/disconnect", json={}, **LOCAL).get_json()
    assert done["ok"] and not done["status"]["connected"]
    assert linked.patch("/api/v1/consumer/elements/1.2.1", json={"value": 1}, **LOCAL).status_code == 400


def test_consumer_settings(client):
    response = client.patch("/api/v1/consumer", json={"host": "bad host"}, **LOCAL)
    assert response.status_code == 400 and "Host" in response.get_json()["error"]
    data = client.patch("/api/v1/consumer", json={"host": "192.0.2.1", "port": 9001,
                                                  "auto_reconnect": True}, **LOCAL).get_json()
    assert (data["settings"]["host"], data["settings"]["port"]) == ("192.0.2.1", 9001)
    failed = client.post("/api/v1/consumer/connect", json={}, base_url="http://localhost:8089")
    assert failed.get_json()["ok"] is False


# -- the web interface uses the same routes ------------------------------------

def test_ui_routes_mirror_the_api(client):
    assert client.get("/ui/api/provider", **LOCAL).get_json()["status"]["element_count"] == 45
    assert client.patch("/ui/api/provider/elements/1.2.2", json={"value": True}, **LOCAL).get_json()["value"]
    assert client.get("/ui/api/consumer", **LOCAL).get_json()["status"]["connected"] is False


# -- guards -----------------------------------------------------------------

def test_only_localhost_names_are_answered(client):
    assert client.get("/api/v1/provider", base_url="http://evil.example:8089").status_code == 403
    assert client.get("/api/v1/provider", base_url="http://127.0.0.1:8089").status_code == 200


def test_changes_need_json_and_the_same_origin(client):
    assert client.post("/api/v1/provider/start", data="x", **LOCAL).status_code == 415
    assert client.post("/api/v1/provider/start", json={}, headers={"Sec-Fetch-Site": "cross-site"},
                       **LOCAL).status_code == 403
    assert client.post("/api/v1/provider/start", json={}, headers={"Origin": "http://evil.example"},
                       **LOCAL).status_code == 403
    assert client.patch("/api/v1/provider", json=[1], **LOCAL).status_code == 400


def test_body_nested_absurdly_deep_is_refused(client):
    body = '{"children": [' * 100000 + "]}" * 100000
    response = client.put("/api/v1/provider/tree", data=body, content_type="application/json", **LOCAL)
    assert response.status_code == 400
    assert client.get("/api/v1/provider", **LOCAL).get_json()["status"]["element_count"] == 45


def test_api_login(client, access):
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    assert client.get("/api/v1/provider", **LOCAL).status_code == 401
    assert client.get("/api/v1/provider", headers=basic("robot", "wrong"), **LOCAL).status_code == 401
    assert client.get("/api/v1/provider", headers=basic("robot", PASSWORD), **LOCAL).status_code == 200
    assert client.get("/ui/api/provider", **LOCAL).status_code == 200      # the page has its own door
    for _ in range(MAX_FAILURES):
        client.get("/api/v1/provider", headers=basic("robot", "wrong"), **LOCAL)
    locked = client.get("/api/v1/provider", headers=basic("robot", PASSWORD), **LOCAL)
    assert locked.status_code == 429 and "Retry-After" in locked.headers


def test_web_login(client, access):
    access.update(web_user="marcus", web_password=PASSWORD, web_login=True)
    assert client.get("/", **LOCAL).status_code == 302
    assert client.get("/ui/api/provider", **LOCAL).status_code == 401
    assert client.get("/login", **LOCAL).status_code == 200
    assert client.post("/ui/api/login", json={"user": "marcus", "password": "wrong"}, **LOCAL).status_code == 401
    assert client.post("/ui/api/login", json={"user": "marcus", "password": PASSWORD}, **LOCAL).status_code == 200
    assert client.get("/", **LOCAL).status_code == 200
    assert client.get("/ui/api/consumer", **LOCAL).status_code == 200
    client.post("/ui/api/logout", json={}, **LOCAL)
    assert client.get("/ui/api/provider", **LOCAL).status_code == 401


def test_doors_can_be_switched_off(client, access):
    access.update(api_enabled=False)
    assert client.get("/api/v1/provider", **LOCAL).status_code == 403
    assert client.get("/ui/api/provider", **LOCAL).status_code == 200
    access.update(web_enabled=False)
    assert client.get("/", **LOCAL).status_code == 403


def test_web_server_opens_and_closes_its_port(provider, consumer):
    import urllib.request
    access = Access()
    server = WebServer(provider, consumer, access, "127.0.0.1", 0)
    assert server.start() and server.running
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/v1/", timeout=3) as response:
        assert response.status == 200 and response.headers["Server"] == "Ember-Emu"
    access.update(web_enabled=False, api_enabled=False)
    wait_for(lambda: not server.running)
    server.stop()
