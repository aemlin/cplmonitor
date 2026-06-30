# plc-monitor

Moniteur Docker pour CPL TP-Link/HomePlug AV.

Il utilise `pla-util`, publie les états vers MQTT, et crée automatiquement les entités dans Home Assistant via MQTT Discovery.

## Installation

```bash
mkdir -p /opt/plc-monitor
cd /opt/plc-monitor
```

Copier les fichiers dans ce dossier, puis adapter `config.yaml`.

## Démarrage

```bash
docker compose up -d --build
```

## Logs

```bash
docker logs -f plc-monitor
```

## Redémarrage après modification de config.yaml

```bash
docker compose restart plc-monitor
```

## Test manuel de pla-util dans le conteneur

```bash
docker exec -it plc-monitor pla-util -i eth0 scan
```

Si cela ne répond pas, tester l'interface réelle :

```bash
ip link
docker exec -it plc-monitor pla-util -i eth0 scan
docker exec -it plc-monitor pla-util -i eth0.4 scan
```

## Home Assistant

Les entités apparaissent automatiquement via MQTT Discovery.

Topics principaux :

- `plc_monitor/status`
- `plc_monitor/<device_id>/state`
- `homeassistant/.../config`
