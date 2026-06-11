# Quickstart — lokaal draaien op je workstation

## 1. Uitpakken & dependencies

```bash
tar -xzf ipfs-cluster-manager.tar.gz
cd ipfs-cluster-manager

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Werkt met Python 3.10+.

## 2. Config

```bash
cp .env.example .env
nano .env   # of editor naar keuze
```

Vul in elk geval in:

```ini
CLUSTER_API_URL=https://ford.ilse-ai.eu/cluster

KEYCLOAK_URL=https://ford.ilse-ai.eu/keycloak
KEYCLOAK_REALM=ilse-ai
KEYCLOAK_CLIENT_ID=ilse-ai-services
KEYCLOAK_CLIENT_SECRET=<jouw secret>
KEYCLOAK_USERNAME=jaco
KEYCLOAK_PASSWORD=<jouw wachtwoord>

VERIFY_TLS=true
HOST=127.0.0.1
PORT=8765
```

> Voor self-signed TLS: `VERIFY_TLS=false`.
> Token-refresh staat automatisch aan zodra `KEYCLOAK_URL` ingevuld is.

## 3. Starten

```bash
python server.py
```

Output:

```
============================================================
  IPFS Cluster Manager
============================================================
  Dashboard:  http://127.0.0.1:8765/
  API docs:   http://127.0.0.1:8765/docs
  Health:     http://127.0.0.1:8765/api/health
============================================================
```

Open het dashboard in je browser.

## 4. Tests draaien (optioneel)

```bash
pip install pytest pytest-asyncio
pytest -q
```

Verwacht: **33 passed**.

## 5. Bulk-unpin flow (kort)

1. Filter pins op het dashboard (op tag, timestamp, peer)
2. Klik **Preview** → backend retourneert lijst + sha256-fingerprint
3. Klik **Confirm unpin** → fingerprint moet matchen, anders 409
4. Single-use: na bevestiging is dezelfde token niet meer bruikbaar

`MAX_BULK_UNPIN=5000` (in `.env`) is een harde bovengrens per operatie.

## Troubleshooting

| Symptoom | Oorzaak / fix |
|---|---|
| `401 Unauthorized` op `/api/health` | Keycloak creds kloppen niet — check token endpoint met `curl` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed cert → `VERIFY_TLS=false` in `.env` |
| Dashboard blijft leeg | Check browserconsole + `python server.py` logs voor errors |
| `Address already in use` | Andere proces op poort 8765 → `PORT=8766` in `.env` |

## Systemd unit (optioneel, voor server-deploy)

```ini
# /etc/systemd/system/ipfs-cluster-manager.service
[Unit]
Description=IPFS Cluster Manager
After=network.target

[Service]
Type=simple
User=jaco
WorkingDirectory=/opt/ipfs-cluster-manager
EnvironmentFile=/opt/ipfs-cluster-manager/.env
ExecStart=/opt/ipfs-cluster-manager/.venv/bin/python server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ipfs-cluster-manager
journalctl -u ipfs-cluster-manager -f
```
