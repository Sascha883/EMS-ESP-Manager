"""v1.24.0: Kern genau einmal, Herstellerblöcke, Migration, parallele Einrichtung."""
import asyncio
import random

import pytest
from unittest.mock import MagicMock
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ems_esp_manager import panel as P
from custom_components.ems_esp_manager.const import DOMAIN, VENDORS
from custom_components.ems_esp_manager.vendors import REGISTRY, active_keys

OLD = {
    "kiosk_key": "abc",
    "map": {"sensor.a": "sensor.b"},
    "hc_names": {"1": "EG"},
    "created": {"x": {}},
    "show_cooling": False,
    "dhw_opt": {"enabled": True},
    "plim": {"mode": "a"},
    "dimprog": {"x": 1},
    "heater": {"enabled": True, "topic": "novolto/1"},
    "heater_state": {"p": 1},
    "bsb": {"kind": "esphome"},
    "bsb_user_params": [{"line": 700}],
    "vendors": {"samsung": {"map": {"s.x": "s.y"}, "hc_names": {}}, "bsb": {"map": {"b.1": "b.2"}, "hc_names": {}}},
}


async def test_registry():
    assert active_keys() == VENDORS
    assert not REGISTRY["viessmann"].active and not REGISTRY["vaillant"].active
    assert P._SIDEBAR["samsung"][2] == "samsung-ehs"


@pytest.fixture
def fake_http(hass):
    hass.http = MagicMock()
    return hass


async def test_migration(fake_http, hass, hass_storage):
    hass_storage[P._STORE_KEY] = {"version": 1, "minor_version": 1, "key": P._STORE_KEY, "data": OLD}
    mgr = await P.async_setup_core(hass)
    d = mgr.data
    assert d["schema"] == 2 and d["kiosk_key"] == "abc"
    for k in ("map", "hc_names", "created", "dhw_opt", "plim", "dimprog", "heater", "bsb"):
        assert k not in d, k
    b = d["vendors"]["bosch"]
    assert b["map"] == {"sensor.a": "sensor.b"} and b["show_cooling"] is False and b["dhw_opt"] == {"enabled": True}
    assert d["vendors"]["heater"]["heater"]["topic"] == "novolto/1"
    assert d["vendors"]["bsb"]["bsb"] == {"kind": "esphome"} and d["vendors"]["bsb"]["map"] == {"b.1": "b.2"}
    assert d["vendors"]["samsung"]["map"] == {"s.x": "s.y"}
    await hass.async_block_till_done()
    assert hass_storage[P._STORE_KEY + "_vor_v1.24"]["data"]["map"] == {"sensor.a": "sensor.b"}
    # zweiter Start: nichts mehr umziehen, Sicherung bleibt die alte
    hass.data.pop(P._DATA)
    mgr2 = await P.async_setup_core(hass)
    assert mgr2.vdata("bosch")["map"] == {"sensor.a": "sensor.b"}
    # kiosk-Link je Hersteller wie bisher
    assert mgr2.kiosk_url("bsb").endswith("key=abc&vendor=bsb&page=bsb")
    assert mgr2.kiosk_url("bosch").endswith("?key=abc")


@pytest.mark.parametrize("seed", range(6))
async def test_parallel_setup(fake_http, hass, hass_storage, seed):
    """Alle Hersteller gleichzeitig in zufälliger Reihenfolge – ein Manager, alle gelistet."""
    hass_storage[P._STORE_KEY] = {"version": 1, "minor_version": 1, "key": P._STORE_KEY, "data": dict(OLD)}
    order = list(VENDORS)
    random.Random(seed).shuffle(order)
    mgrs = await asyncio.gather(*(P.async_setup_panel(hass, {}, v) for v in order))
    assert len({id(m) for m in mgrs}) == 1
    assert P.get_manager(hass).vendors == VENDORS
    P.async_remove_panel(hass)
    await hass.async_block_till_done()


async def test_entries_samsung_heater_bsb(hass, hass_storage):
    """Wie bei Sascha: Samsung + Heizstab + ESP-Bus-Adapter als Einträge, HA-Start."""
    hass_storage[P._STORE_KEY] = {"version": 1, "minor_version": 1, "key": P._STORE_KEY, "data": dict(OLD)}
    entries = [
        MockConfigEntry(domain=DOMAIN, title="Samsung EHS", data={"vendor": "samsung"}, unique_id="samsung_ehs"),
        MockConfigEntry(domain=DOMAIN, title="Heizstab", data={"vendor": "heater", "topic": "novolto/1", "host": ""}, unique_id="heater1"),
        MockConfigEntry(domain=DOMAIN, title="BSB", data={"vendor": "bsb"}, unique_id="bsb_adapter"),
    ]
    for e in entries:
        e.add_to_hass(hass)
    from unittest.mock import patch
    from homeassistant.setup import async_setup_component

    async def _ok(*a, **k):
        return True

    with patch("homeassistant.components.frontend.async_setup", _ok):
        assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    states = {e.title: e.state.value for e in entries}
    mgr = P.get_manager(hass)
    assert mgr is not None
    assert set(mgr.vendors) == {"samsung", "heater", "bsb"}, (mgr.vendors, states)
    assert all(s == "loaded" for s in states.values()), states
    from custom_components.ems_esp_manager.heater import get as hget
    from custom_components.ems_esp_manager.bsb import get as bget
    assert hget(hass).cfg["topic"] == "novolto/1"
    assert bget(hass).cfg["kind"] == "esphome" and bget(hass).user_params == [{"line": 700}]
    assert hget(hass).mgr is mgr and bget(hass).mgr is mgr
    for e in entries:
        assert await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
