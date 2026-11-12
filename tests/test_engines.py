"""Tests of the two engines of the app. They talk to each other over TCP on this machine."""

import json
import time
from datetime import datetime

import pytest

from emberemu_app.consumer_engine import ConsumerEngine, ConsumerSettings
from emberemu_app.provider_engine import ProviderEngine, ProviderSettings, clean_element_data


def wait_for(condition, timeout=3.0):
    """Wait until ``condition()`` is true. Fails the test if it never is."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def provider():
    engine = ProviderEngine(ProviderSettings(bind="127.0.0.1", port=0, animate=False))
    assert engine.start()
    yield engine
    engine.close()


@pytest.fixture
def consumer(provider):
    engine = ConsumerEngine(ConsumerSettings(host="127.0.0.1", port=provider.provider.port))
    assert engine.connect()
    yield engine
    engine.close()


def row(snapshot, path):
    return next(item for item in snapshot["tree"] if item["path"] == path)


# -- provider engine ----------------------------------------------------------

def test_example_tree_has_one_of_everything(provider):
    snapshot = provider.snapshot()
    kinds = {item["kind"] for item in snapshot["tree"]}
    types = {item["type"] for item in snapshot["tree"] if item["kind"] == "parameter"}
    access = {item["access"] for item in snapshot["tree"] if item["kind"] == "parameter"}
    assert kinds == {"node", "parameter", "function", "matrix"}
    assert types == {"integer", "real", "string", "boolean", "enum", "octets", "trigger"}
    assert {"read", "read_write", "write"} <= access
    assert snapshot["status"]["element_count"] == len(snapshot["tree"]) == 45
    assert row(snapshot, "1.2.1")["display"] == "-12.0 dB"
    assert row(snapshot, "1.2.5")["display"] == "-12.5 dB"          # factor 10
    assert row(snapshot, "1.3.2")["display"] == "1080p50"           # enumeration with own numbers
    assert row(snapshot, "1.4.4")["online"] is False
    assert row(snapshot, "1.2.6")["stream_identifier"] == 1
    assert row(snapshot, "1.6.1")["connections"] == {"0": [0], "1": [1], "2": [2], "3": [3]}
    # Streams in three forms: a real, a whole number, and two values in one block of bytes.
    assert (row(snapshot, "1.7.1")["type"], row(snapshot, "1.7.1")["display"]) == ("integer", "-40.0 dB")
    assert (row(snapshot, "1.7.2")["stream_format"], row(snapshot, "1.7.2")["stream_offset"]) == ("float32_le", 0)
    assert (row(snapshot, "1.7.3")["stream_identifier"], row(snapshot, "1.7.3")["stream_offset"]) == (3, 4)


def test_settings_are_checked(provider):
    with pytest.raises(ValueError):
        provider.update(port=70000)
    with pytest.raises(ValueError):
        provider.update(bind="")
    with pytest.raises(ValueError):
        provider.update(nonsense=1)
    assert provider.running
    provider.update(bind="127.0.0.2")                    # changing the address stops serving
    assert not provider.running


def test_port_in_use_is_reported(provider):
    other = ProviderEngine(ProviderSettings(bind="127.0.0.1", port=provider.provider.port))
    assert not other.start()
    assert "Cannot listen" in other.snapshot()["status"]["error"]
    other.close()


def test_values_can_be_set_as_text(provider):
    provider.update_element("1.2.1", {"value": "-6,5"})
    provider.update_element("1.2.2", {"value": "true"})
    provider.update_element("1.2.3", {"value": "Mono"})
    provider.update_element("1.5.2", {"value": "0A ff"})
    provider.update_element("1.1.4", {"value": 7})           # read only for consumers, not for us
    snapshot = provider.snapshot()
    assert row(snapshot, "1.2.1")["value"] == -6.5
    assert row(snapshot, "1.2.2")["value"] is True
    assert row(snapshot, "1.2.3")["value"] == 1
    assert row(snapshot, "1.5.2")["value"] == "0a ff"
    assert row(snapshot, "1.1.4")["value"] == 7
    for path, bad in (("1.2.1", "loud"), ("1.2.1", 99), ("1.2.3", "Quad"), ("1.2.2", "maybe"),
                      ("1.5.2", "xyz"), ("9.9", 1)):
        with pytest.raises(ValueError):
            provider.update_element(path, {"value": bad})


def test_add_change_and_remove_elements(provider):
    path = provider.add_element("1.2", {"kind": "parameter", "identifier": "Pan", "type": "real",
                                        "access": "read_write", "minimum": "-1", "maximum": "1",
                                        "value": "0,25", "format": "%.2f"})
    assert path == "1.2.7"
    assert provider.element(path)["value"] == 0.25
    provider.update_element(path, {"description": "Panorama", "maximum": 2, "identifier": "Balance"})
    changed = provider.element(path)
    assert (changed["identifier"], changed["description"], changed["maximum"]) == ("Balance", "Panorama", 2.0)
    assert changed["value"] == 0.25
    provider.update_element("1.2.2", {"type": "integer"})                 # the value starts again
    assert provider.element("1.2.2")["value"] == 0
    node = provider.add_element("", {"kind": "node", "identifier": "Second"})
    child = provider.add_element(node, {"kind": "function", "identifier": "Join",
                                        "arguments": [["string", "a"], ["string", "b"]],
                                        "result": [["string", "joined"]]})
    assert provider.element(child)["arguments"] == [["string", "a"], ["string", "b"]]
    provider.remove_element(node)
    with pytest.raises(ValueError):
        provider.element(child)


@pytest.mark.parametrize("data, message", [
    ({"kind": "parameter", "identifier": "9x", "type": "integer"}, "identifier"),
    ({"kind": "parameter", "identifier": "a/b", "type": "integer"}, "identifier"),
    ({"kind": "parameter", "identifier": "E", "type": "enum"}, "at least one entry"),
    ({"kind": "parameter", "identifier": "Gain", "type": "integer"}, "already used"),
    ({"kind": "parameter", "identifier": "R", "type": "real", "minimum": 5, "maximum": 1}, "Minimum"),
    ({"kind": "parameter", "identifier": "C", "type": "integer", "animate": "clock"}, "clock"),
    ({"kind": "parameter", "identifier": "N", "type": "null"}, "null"),
    ({"kind": "parameter", "identifier": "T", "type": "integer", "value": "many"}, "whole number"),
    ({"kind": "parameter", "identifier": "F", "type": "integer", "factor": 0}, "Factor"),
    ({"kind": "gadget", "identifier": "G"}, "Kind"),
    ({"kind": "matrix", "identifier": "M", "target_count": 0}, "Targets"),
    ("not an object", "JSON object"),
])
def test_bad_elements_are_refused(provider, data, message):
    with pytest.raises(ValueError, match=message):
        provider.add_element("1.2", data)
    assert provider.snapshot()["status"]["element_count"] == 45


def test_tree_nested_too_deeply_is_refused(provider):
    tree = leaf = {"kind": "node", "identifier": "Level0"}
    for depth in range(1, 70):
        child = {"kind": "node", "identifier": f"Level{depth}"}
        leaf["children"] = [child]
        leaf = child
    with pytest.raises(ValueError, match="at most 64 levels"):
        provider.import_tree({"children": [tree]})
    assert provider.snapshot()["status"]["element_count"] == 45


def test_children_only_below_nodes(provider):
    with pytest.raises(ValueError, match="node"):
        provider.add_element("1.2.1", {"kind": "node", "identifier": "Below"})
    with pytest.raises(ValueError, match="kind"):
        provider.update_element("1.2.1", {"kind": "node"})


def test_export_and_import_round_trip(provider):
    exported = provider.export_tree()
    provider.clear_tree()
    assert provider.snapshot()["tree"] == []
    provider.import_tree(json.loads(json.dumps(exported)))
    assert provider.export_tree() == exported
    with pytest.raises(ValueError):
        provider.import_tree({"children": [{"kind": "node", "identifier": "A"},
                                           {"kind": "node", "identifier": "A"}]})
    with pytest.raises(ValueError):
        provider.import_tree({"no": "children"})
    assert provider.export_tree() == exported               # a refused import changes nothing


def test_animated_values(provider):
    provider = ProviderEngine(ProviderSettings(bind="127.0.0.1", port=0),
                              now=lambda: datetime(2026, 11, 10, 12, 34, 56))
    provider.tick(100.0)
    provider.tick(101.5)
    snapshot = provider.snapshot()
    assert row(snapshot, "1.4.1")["value"] == "12:34:56"
    assert row(snapshot, "1.4.2")["value"] >= 1
    assert -60.0 <= row(snapshot, "1.2.6")["value"] <= 0.0
    assert 35.0 <= row(snapshot, "1.4.3")["value"] <= 55.0
    provider.update(animate=False)
    before = provider.snapshot()["tree"]
    provider.tick(200.0)
    assert provider.snapshot()["tree"] == before
    provider.close()


def test_settings_and_tree_survive_a_restart(tmp_path):
    config = tmp_path / "settings.json"
    first = ProviderEngine(config_path=config)
    first.update(bind="127.0.0.1", port=9123, animate=False)
    first.update_element("1.1.5", {"value": "Studio B"})
    first.add_element("1", {"kind": "parameter", "identifier": "Extra", "type": "string",
                            "value": "kept", "animate": "clock"})
    first.close()
    assert json.loads(config.read_text())["port"] == 9123
    second = ProviderEngine(config_path=config)
    assert (second.settings.bind, second.settings.port, second.settings.animate) == ("127.0.0.1", 9123, False)
    assert second.element("1.1.5")["value"] == "Studio B"
    assert second.element("EmberEmu/Extra")["animate"] == "clock"
    second.close()
    (tmp_path / "settings.tree.json").write_text("{ broken")
    third = ProviderEngine(config_path=config)                # a broken file gives the example tree
    assert third.snapshot()["status"]["element_count"] == 45
    third.close()


def test_clean_element_data_reads_forms():
    clean = clean_element_data({"kind": "parameter", "identifier": "Mode", "type": "enum",
                                "enumeration": "Off\n On \n\nAuto", "value": "On", "description": "  "})
    assert clean["enumeration"] == ["Off", "On", "Auto"] and clean["value"] == 1
    assert clean["description"] is None
    clean = clean_element_data({"kind": "parameter", "identifier": "L", "type": "real",
                                "minimum": "-60", "maximum": "", "value": ""})
    assert clean["minimum"] == -60.0 and "maximum" not in clean and clean["value"] == 0.0
    clean = clean_element_data({"kind": "parameter", "identifier": "L", "type": "integer",
                                "minimum": 5, "maximum": 9})
    assert clean["value"] == 5                              # the start value lies inside the range


# -- consumer engine ----------------------------------------------------------

def test_connect_reads_the_root(provider, consumer):
    snapshot = consumer.snapshot()
    status = snapshot["status"]
    assert status["connected"] and status["root_loaded"] and status["glow_version"] == "2.50"
    assert [item["identifier"] for item in snapshot["tree"]] == ["EmberEmu"]
    assert snapshot["tree"][0]["loaded"] is False
    wait_for(lambda: len(provider.snapshot()["status"]["clients"]) == 1)


def test_expand_loads_children(provider, consumer):
    consumer.expand("1")
    consumer.expand("EmberEmu/Audio")
    snapshot = consumer.snapshot()
    assert row(snapshot, "1.2.1")["identifier_path"] == "EmberEmu/Audio/Gain"
    assert row(snapshot, "1.2")["loaded"] and not row(snapshot, "1.3")["loaded"]
    with pytest.raises(ValueError, match="no element"):
        consumer.expand("EmberEmu/Nothing")
    consumer.expand_all()
    wait_for(lambda: consumer.snapshot()["status"]["element_count"] == 45
             and not consumer.snapshot()["status"]["loading"])


def test_set_values_and_refusals(provider, consumer):
    assert consumer.set_value("EmberEmu/Audio/Gain", "-3,5") == -3.5
    assert provider.element("1.2.1")["value"] == -3.5
    assert consumer.set_value("1.2.3", "Surround") == 3
    assert consumer.set_value("EmberEmu/Identity/Serial", 5) == 100042       # read only
    with pytest.raises(ValueError, match="maximum"):
        consumer.set_value("EmberEmu/Audio/Gain", 99)
    assert consumer.set_value("EmberEmu/Audio/Gain", 99, check=False) == -3.5   # sent, and refused
    with pytest.raises(ValueError, match="not a parameter"):
        consumer.set_value("EmberEmu/Audio", 1)
    actions = [entry["action"] for entry in consumer.snapshot()["status"]["log"]]
    assert actions.count("set") == 2 and actions.count("refused") == 2
    provider_actions = [entry["action"] for entry in provider.snapshot()["status"]["log"]]
    assert provider_actions.count("refused") == 2


def test_trigger_function_and_matrix(provider, consumer):
    consumer.set_value("EmberEmu/Control/Identify", None)
    assert consumer.invoke("EmberEmu/Control/Add", ["2", 40]) == [42]
    with pytest.raises(ValueError, match="takes 2 arguments"):
        consumer.invoke("EmberEmu/Control/Add", [1])
    with pytest.raises(ValueError, match="whole number"):
        consumer.invoke("EmberEmu/Control/Add", ["x", 1])
    consumer.set_connection("EmberEmu/Router/Matrix", 0, [3])
    wait_for(lambda: provider.element("1.6.1")["connections"]["0"] == [3])
    wait_for(lambda: consumer.element("1.6.1")["connections"]["0"] == [3])
    wait_for(lambda: any(entry["text"] == "trigger" for entry in provider.snapshot()["status"]["log"]))


def test_pins_follow_values(provider, consumer):
    assert consumer.pin("1.2.1") == "EmberEmu/Audio/Gain"
    consumer.pin("EmberEmu/Audio/Level")                       # a stream: subscribed by the pin
    with pytest.raises(ValueError, match="Only parameters"):
        consumer.pin("EmberEmu/Audio")
    provider.update_element("1.2.1", {"value": 4.0})
    provider.update_element("1.2.6", {"value": -7.0})

    def pins():
        return {pin["identifier_path"]: pin for pin in consumer.snapshot()["status"]["pins"]}
    wait_for(lambda: pins()["EmberEmu/Audio/Gain"]["value"] == 4.0)
    wait_for(lambda: pins()["EmberEmu/Audio/Level"]["value"] == -7.0)
    assert pins()["EmberEmu/Audio/Gain"]["changes"] == 1
    assert consumer.settings.pins == ["EmberEmu/Audio/Gain", "EmberEmu/Audio/Level"]
    assert any(entry["action"] == "change" and entry["identifier_path"] == "EmberEmu/Audio/Gain"
               for entry in consumer.snapshot()["status"]["log"])
    consumer.unpin("1.2.6")                                    # by numbers
    consumer.unpin("EmberEmu/Audio/Gain")                      # by name
    assert consumer.snapshot()["status"]["pins"] == []
    with pytest.raises(ValueError):
        consumer.unpin("EmberEmu/Audio/Gain")


def test_pinned_meters_of_every_kind_follow(provider, consumer):
    for name in ("EmberEmu/Audio/Level", "EmberEmu/Meters/LevelInteger", "EmberEmu/Meters/Left",
                 "EmberEmu/Meters/Right"):
        consumer.pin(name)
    provider.update_element("EmberEmu/Audio/Level", {"value": -7.0})
    provider.update_element("EmberEmu/Meters/LevelInteger", {"value": -125})
    provider.update_element("EmberEmu/Meters/Left", {"value": -18.5})
    provider.update_element("EmberEmu/Meters/Right", {"value": -3.25})

    def values():
        return [pin["value"] for pin in consumer.snapshot()["status"]["pins"]]
    wait_for(lambda: values() == [-7.0, -125, -18.5, -3.25])
    assert consumer.snapshot()["status"]["pins"][1]["display"] == "-12.5 dB"


def test_stream_descriptor_is_checked_and_kept(provider):
    path = provider.add_element("1.7", {"kind": "parameter", "identifier": "Centre", "type": "real",
                                        "stream_identifier": 3, "stream_descriptor": ["float32_le", 8]})
    assert provider.element(path)["stream_offset"] == 8
    provider.update_element(path, {"description": "Third value of the block"})     # as the form does
    assert provider.element(path)["stream_format"] == "float32_le"
    provider.update_element(path, {"stream_identifier": ""})                       # no stream any more
    assert provider.element(path)["stream_format"] == ""
    for bad in (["float128", 0], ["float32_le"], "float32_le", ["float32_le", -1]):
        with pytest.raises(ValueError):
            provider.add_element("1.7", {"kind": "parameter", "identifier": "Bad", "type": "real",
                                         "stream_identifier": 3, "stream_descriptor": bad})
    with pytest.raises(ValueError, match="integer or real"):
        provider.add_element("1.7", {"kind": "parameter", "identifier": "Text", "type": "string",
                                     "stream_identifier": 3, "stream_descriptor": ["float32_le", 0]})
    exported = provider.export_tree()
    provider.import_tree(json.loads(json.dumps(exported)))
    assert provider.export_tree() == exported


def test_pins_are_found_again_after_connecting(provider, tmp_path):
    config = tmp_path / "settings.consumer.json"
    first = ConsumerEngine(ConsumerSettings(host="127.0.0.1", port=provider.provider.port),
                           config_path=config)
    assert first.connect()
    first.pin("EmberEmu/Video/Label")
    first.close()
    second = ConsumerEngine(config_path=config)
    assert second.settings.pins == ["EmberEmu/Video/Label"]
    second.update(pins=["EmberEmu/Video/Label", "EmberEmu/No/Such"])
    assert second.snapshot()["status"]["pins"][0]["found"] is False        # not connected yet
    assert second.connect()
    pins = second.snapshot()["status"]["pins"]
    assert pins[0]["found"] and pins[0]["value"] == "CAM 1"
    assert not pins[1]["found"] and pins[1]["error"] == "Not found at this provider"
    second.close()


def test_connection_failures(provider):
    engine = ConsumerEngine(ConsumerSettings(host="127.0.0.1", port=1))
    assert not engine.connect()
    assert "Cannot connect" in engine.snapshot()["status"]["error"]
    with pytest.raises(ValueError, match="Not connected"):
        engine.expand("")
    with pytest.raises(ValueError):
        engine.update(port=0)
    engine.close()


def test_lost_connection_and_reconnect(provider):
    engine = ConsumerEngine(ConsumerSettings(host="127.0.0.1", port=provider.provider.port,
                                             auto_reconnect=True, pins=["EmberEmu/Audio/Gain"]))
    engine.start_timer()
    assert engine.connect()
    port = provider.provider.port
    provider.stop_serving()
    wait_for(lambda: not engine.connected)
    assert "closed the connection" in engine.snapshot()["status"]["error"]
    assert engine.snapshot()["tree"]                           # what was read stays visible
    provider.update(port=port)
    assert provider.start()
    wait_for(lambda: engine.connected, timeout=8.0)
    wait_for(lambda: engine.snapshot()["status"]["pins"][0]["found"])
    engine.disconnect()
    time.sleep(0.5)
    assert not engine.connected                                # a wanted disconnect stays one
    engine.close()
