"""Fixed entity names and state texts (German / English).

The device page sorts controls alphabetically; the German names are chosen so the
order is: Alle Werte neu abrufen, Update installieren, Updates prüfen,
Version auswählen, Version installieren.

Names are set directly instead of relying on the translation files, so every
entity shows a clear function name on the device page and in the dashboard.
"""

from __future__ import annotations

NAMES: dict[str, dict[str, str]] = {
    "de": {
        # buttons
        "restart": "EMS-ESP neu starten",
        "fetch_values": "Alle Werte neu abrufen",
        "check_updates": "Updates prüfen",
        "install_latest": "Update installieren",
        "install_selected": "Version installieren",
        # select
        "stable_version": "Version auswählen",
        "clear_log": "Log leeren",
        # selects
        "watch_mode": "Bus-Mitschnitt",
        "log_level": "Log-Level",
        "tx_mode": "Tx-Modus (Senden)",
        "locale": "Sprache der EMS-ESP-Werte",
        "analog_enabled": "Analogsensoren",
        "gateway_sensors": "Gateway-Sensoren anlegen",
        # binary sensor
        "update_available": "Firmware-Status",
        # sensors
        "system_state": "Systemzustand",
        "firmware_installed": "Installierte Firmware",
        "manager_version": "EMS-ESP Manager",
        "firmware_latest": "Neueste Firmware",
        "update_progress": "Update-Fortschritt",
        "hardware": "Hardware",
        "bus_status": "EMS-Bus",
        "num_devices": "Anzahl EMS-Geräte",
        "plant": "Erkannte Anlage",
        "network": "Netzwerkanschluss",
        "wifi_rssi": "WLAN-Signal",
        "uptime": "Laufzeit seit Neustart",
        "free_heap": "Freier Speicher",
        "bus_log": "Bus-Log",
    },
    "en": {
        "restart": "Restart EMS-ESP",
        "fetch_values": "Fetch all values",
        "check_updates": "Check for updates",
        "install_latest": "Install update",
        "install_selected": "Install version",
        "stable_version": "Select firmware version",
        "update_available": "Firmware status",
        "system_state": "System state",
        "firmware_installed": "Installed firmware",
        "manager_version": "EMS-ESP Manager",
        "firmware_latest": "Latest firmware",
        "update_progress": "Update progress",
        "hardware": "Hardware",
        "bus_status": "EMS bus",
        "num_devices": "Number of EMS devices",
        "plant": "Detected plant",
        "network": "Network connection",
        "wifi_rssi": "Wi-Fi signal",
        "uptime": "Uptime since restart",
        "free_heap": "Free memory",
        "bus_log": "Bus log",
        "clear_log": "Clear log",
        "watch_mode": "Bus capture",
        "log_level": "Log level",
        "tx_mode": "Tx mode (sending)",
        "locale": "Language of the EMS-ESP values",
        "analog_enabled": "Analog sensors",
        "gateway_sensors": "Create gateway sensors",
    },
}

STATES: dict[str, dict[str, str]] = {
    "de": {
        "normal": "Normal",
        "pending_upload": "Download wird vorbereitet",
        "uploading": "Firmware wird geladen",
        "upload_error": "Fehler beim Firmware-Upload",
        "pending_restart": "Neustart ausstehend",
        "restarting": "Neustart läuft",
        "invalid_gpio": "Ungültige GPIO-Einstellungen",
        "connected": "Verbunden",
        "tx_errors": "Sendefehler",
        "offline": "Getrennt",
        "lan": "LAN (Netzwerkkabel)",
        "wifi": "WLAN",
    },
    "en": {
        "normal": "Normal",
        "pending_upload": "Preparing download",
        "uploading": "Uploading firmware",
        "upload_error": "Firmware upload failed",
        "pending_restart": "Restart pending",
        "restarting": "Restarting",
        "invalid_gpio": "Invalid GPIO settings",
        "connected": "Connected",
        "tx_errors": "Tx errors",
        "offline": "Offline",
        "lan": "Ethernet (LAN)",
        "wifi": "Wi-Fi",
    },
}


def lang(language: str | None) -> str:
    return "de" if (language or "").lower().startswith("de") else "en"


# select options (values are what EMS-ESP expects)
WATCH_OPTIONS: dict[str, dict[str, str]] = {
    "de": {"Aus": "off", "Telegramme": "on", "Roh-Daten": "raw"},
    "en": {"Off": "off", "Telegrams": "on", "Raw data": "raw"},
}

# EMS-ESP tx_mode values (0 = off is left out, read-only is a separate setting)
TX_MODE_OPTIONS: dict[str, dict[str, int]] = {
    "de": {"EMS": 1, "EMS+": 2, "HT3": 3, "Hardware": 4, "Auto": 5},
    "en": {"EMS": 1, "EMS+": 2, "HT3": 3, "Hardware": 4, "Auto": 5},
}

# EMS-ESP 'locale' setting (src/core/locale_translations.h) – language of the
# entity names and enum texts EMS-ESP sends to Home Assistant via MQTT discovery
LOCALE_OPTIONS: dict[str, str] = {
    "Deutsch": "de", "English": "en", "Nederlands": "nl", "Svenska": "sv", "Polski": "pl",
    "Norsk": "no", "Français": "fr", "Türkçe": "tr", "Italiano": "it", "Slovenčina": "sk", "Čeština": "cs",
}

LOG_LEVEL_OPTIONS: dict[str, dict[str, int]] = {
    "de": {"Fehler": 3, "Warnung": 4, "Notiz": 5, "Info": 6, "Debug": 7, "Trace": 8, "Alles": 9},
    "en": {"Error": 3, "Warning": 4, "Notice": 5, "Info": 6, "Debug": 7, "Trace": 8, "All": 9},
}
