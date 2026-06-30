#!/usr/bin/env python3
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt
import yaml


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


@dataclass
class Device:
    id: str
    name: str
    mac: str = ""


def load_config(path: str = "/app/config.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_mac(value: str) -> str:
    return value.strip().lower().replace("-", ":") if value else ""


def run_pla_util(interface: str) -> str:
    """
    Essaie plusieurs variantes de commande, car pla-util a eu quelques différences
    selon les versions/builds.
    """
    candidates = [
        ["pla-util", "-i", interface, "scan"],
        ["pla-util", "--interface", interface, "scan"],
        ["pla-util", "scan", "-i", interface],
        ["pla-util", "scan", "--interface", interface],
    ]

    errors = []
    for cmd in candidates:
        try:
            logging.debug("Trying command: %s", " ".join(cmd))
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=25,
            )
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout
            errors.append(f"{' '.join(cmd)} -> rc={p.returncode} stderr={p.stderr.strip()}")
        except Exception as e:
            errors.append(f"{' '.join(cmd)} -> {e}")

    raise RuntimeError("Aucune commande pla-util exploitable. " + " | ".join(errors))


def parse_pla_output(output: str) -> List[Dict[str, Any]]:
    """
    Parse volontairement tolérant.
    On extrait au minimum les adresses MAC et, si disponibles, les débits.
    """
    devices: Dict[str, Dict[str, Any]] = {}

    mac_re = re.compile(r"([0-9a-fA-F]{2}(?::|-)){5}[0-9a-fA-F]{2}")
    rate_re = re.compile(r"(?i)(tx|rx|phy|rate|speed|throughput)[^\d]{0,20}(\d+(?:\.\d+)?)\s*(mbps|mb/s|mbit|m)?")

    lines = output.splitlines()
    current_mac: Optional[str] = None

    for line in lines:
        macs = [normalize_mac(m.group(0)) for m in mac_re.finditer(line)]
        if macs:
            current_mac = macs[0]
            devices.setdefault(current_mac, {"mac": current_mac, "raw": []})
            devices[current_mac]["raw"].append(line.strip())

        if current_mac:
            devices[current_mac].setdefault("raw", []).append(line.strip())
            for r in rate_re.finditer(line):
                key = r.group(1).lower()
                val = float(r.group(2))
                if key in ("speed", "rate", "phy", "throughput"):
                    devices[current_mac]["throughput_mbps"] = val
                elif key == "tx":
                    devices[current_mac]["tx_mbps"] = val
                elif key == "rx":
                    devices[current_mac]["rx_mbps"] = val

    return list(devices.values())


def make_mqtt_client(cfg: Dict[str, Any]) -> mqtt.Client:
    mcfg = cfg["mqtt"]
    client = mqtt.Client(client_id=mcfg.get("client_id", "plc-monitor"), callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

    username = mcfg.get("username") or None
    password = mcfg.get("password") or None
    if username:
        client.username_pw_set(username, password)

    availability_topic = cfg["monitor"].get("availability_topic", "plc_monitor/status")
    client.will_set(availability_topic, payload="offline", qos=1, retain=True)

    client.connect(mcfg["host"], int(mcfg.get("port", 1883)), keepalive=60)
    client.loop_start()
    client.publish(availability_topic, "online", qos=1, retain=True)
    return client


def publish_discovery(client: mqtt.Client, cfg: Dict[str, Any], dev: Device) -> None:
    prefix = cfg["mqtt"].get("discovery_prefix", "homeassistant")
    base = cfg["mqtt"].get("base_topic", "plc_monitor")
    availability_topic = cfg["monitor"].get("availability_topic", "plc_monitor/status")

    device_info = {
        "identifiers": [f"plc_monitor_{dev.id}"],
        "name": dev.name,
        "manufacturer": "TP-Link / HomePlug AV",
        "model": "CPL",
    }

    sensors = {
        "online": {
            "name": "Online",
            "component": "binary_sensor",
            "device_class": "connectivity",
        },
        "warning": {
            "name": "Warning",
            "component": "binary_sensor",
            "device_class": "problem",
        },
        "throughput_mbps": {
            "name": "Throughput",
            "component": "sensor",
            "unit": "Mbit/s",
            "device_class": "data_rate",
            "state_class": "measurement",
        },
        "tx_mbps": {
            "name": "TX",
            "component": "sensor",
            "unit": "Mbit/s",
            "device_class": "data_rate",
            "state_class": "measurement",
        },
        "rx_mbps": {
            "name": "RX",
            "component": "sensor",
            "unit": "Mbit/s",
            "device_class": "data_rate",
            "state_class": "measurement",
        },
    }

    for key, meta in sensors.items():
        component = meta["component"]
        unique_id = f"plc_monitor_{dev.id}_{key}"
        object_id = f"{dev.id}_{key}"
        config_topic = f"{prefix}/{component}/{object_id}/config"

        payload = {
            "name": meta["name"],
            "unique_id": unique_id,
            "state_topic": f"{base}/{dev.id}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "availability_topic": availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        }

        if component == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        else:
            if "unit" in meta:
                payload["unit_of_measurement"] = meta["unit"]
            if "device_class" in meta:
                payload["device_class"] = meta["device_class"]
            if "state_class" in meta:
                payload["state_class"] = meta["state_class"]

        if "device_class" in meta and component == "binary_sensor":
            payload["device_class"] = meta["device_class"]

        client.publish(config_topic, json.dumps(payload), qos=1, retain=True)


def publish_state(client: mqtt.Client, cfg: Dict[str, Any], dev: Device, state: Dict[str, Any]) -> None:
    base = cfg["mqtt"].get("base_topic", "plc_monitor")
    topic = f"{base}/{dev.id}/state"
    client.publish(topic, json.dumps(state), qos=1, retain=True)


def match_device(config_device: Device, scanned: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    wanted_mac = normalize_mac(config_device.mac)
    if wanted_mac:
        for item in scanned:
            if normalize_mac(item.get("mac", "")) == wanted_mac:
                return item
    return None


def main() -> None:
    cfg = load_config()
    interface = cfg["monitor"].get("interface", "eth0")
    poll_interval = int(cfg["monitor"].get("poll_interval", 60))

    devices = [
        Device(
            id=d["id"],
            name=d.get("name", d["id"]),
            mac=d.get("mac", ""),
        )
        for d in cfg.get("devices", [])
    ]

    client = make_mqtt_client(cfg)

    for dev in devices:
        publish_discovery(client, cfg, dev)

    logging.info("plc-monitor started on interface %s with %d configured devices", interface, len(devices))

    while True:
        try:
            output = run_pla_util(interface)
            scanned = parse_pla_output(output)
            scanned_macs = {normalize_mac(x.get("mac", "")) for x in scanned if x.get("mac")}
            logging.info("Detected PLC devices: %s", ", ".join(sorted(scanned_macs)) or "none")

            for dev in devices:
                found = match_device(dev, scanned)

                if dev.mac:
                    online = found is not None
                else:
                    # Tant que les MAC ne sont pas renseignées, on garde les entités vivantes
                    # et on signale qu'il faut compléter config.yaml.
                    online = False

                state = {
                    "online": "ON" if online else "OFF",
                    "warning": "OFF" if online else "ON",
                    "mac": dev.mac,
                    "throughput_mbps": None,
                    "tx_mbps": None,
                    "rx_mbps": None,
                    "raw": "",
                }

                if found:
                    state["throughput_mbps"] = found.get("throughput_mbps")
                    state["tx_mbps"] = found.get("tx_mbps")
                    state["rx_mbps"] = found.get("rx_mbps")
                    state["raw"] = "\n".join(found.get("raw", []))[:2000]

                publish_state(client, cfg, dev, state)

        except Exception as e:
            logging.exception("Monitoring loop failed: %s", e)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
