"""Read all entries of the HA device / entity registry – independent of the HA version.

Home Assistant is moving the registries away from dict-like access:
``dev_reg.devices.values()`` already logs a deprecation warning and stops
working in 2027.9.0, the entity registry is expected to follow the same way.
Iterating the registry container is the recommended access:

  * newer Home Assistant: iteration yields the entries themselves,
  * older Home Assistant: iteration yields the keys (device id / entity id).

These helpers handle both, so the integration never touches ``.values()``,
``.items()`` or ``[...]`` on the registry containers.
"""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr, entity_registry as er


def all_devices(dev_reg: dr.DeviceRegistry) -> list[dr.DeviceEntry]:
    """Every device entry of the device registry."""
    out: list[dr.DeviceEntry] = []
    for item in list(dev_reg.devices):
        if isinstance(item, str):  # older HA: iteration gives the device id
            item = dev_reg.async_get(item)
        if item is not None:
            out.append(item)
    return out


def all_entities(ent_reg: er.EntityRegistry) -> list[er.RegistryEntry]:
    """Every entity entry of the entity registry."""
    out: list[er.RegistryEntry] = []
    for item in list(ent_reg.entities):
        if isinstance(item, str):  # older HA: iteration gives the entity id
            item = ent_reg.async_get(item)
        if item is not None:
            out.append(item)
    return out
