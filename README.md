# IPFS Cluster Manager

A lightweight web dashboard for [IPFS Cluster](https://ipfscluster.io/) — monitor peers, inspect pins, and safely bulk-unpin by name, timestamp, metadata tag, or CID.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Dashboard** — cluster status, peer overview, pin count per node, version info, and active health alerts. Auto-refreshes every 30 seconds.
- **Flexible filtering** — by name (substring or regex), `timestamp` (before/after), metadata key/value (with `*` wildcards or `re:` regex prefix), tracker status, or explicit CID list.
- **Safe bulk-unpin workflow**:
  1. Preview (dry-run) shows matched pins and returns a one-time `confirm_token`;
  2. Confirm checkbox + browser confirm dialog before execution;
  3. Server verifies the token matches the exact same CID set (sha256 fingerprint), and the token is single-use;
  4. Hard per-operation limit (`MAX_BULK_UNPIN`, default 5000);
  5. Optional: run IPFS GC after a successful unpin.
- **Peer restart buttons** — restart the IPFS daemon (via IPFS API shutdown) or trigger a cluster daemon restart via webhook, per peer.
- **Background refresh** — proactively warms the auth token and refreshes cached cluster data on a configurable interval.
- **Auth** — Keycloak/OIDC with automatic token refresh, static JWT, or basic auth.
- **No external state** — preview tokens live in-memory with a 10-minute TTL.

---

## Installation

```bash
git clone https://github.com/JvD007/ipfs-cluster-manager.git
cd ipfs-cluster-manager

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your cluster URL and credentials
python run.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

For development with auto-reload:

```bash
uvicorn app.main:app --reload
```

---

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `CLUSTER_API_URL` | `http://127.0.0.1:9094` | IPFS Cluster REST API endpoint |
| `CLUSTER_USERNAME` / `CLUSTER_PASSWORD` | – | Basic auth credentials |
| `CLUSTER_JWT` | – | Static JWT bearer token (no auto-refresh) |
| `KEYCLOAK_URL` | – | OIDC issuer base (e.g. `https://host/keycloak`) |
| `KEYCLOAK_REALM` | – | Realm name |
| `KEYCLOAK_CLIENT_ID` | – | OIDC client ID |
| `KEYCLOAK_CLIENT_SECRET` | – | Client secret (for confidential clients) |
| `KEYCLOAK_USERNAME` / `KEYCLOAK_PASSWORD` | – | User credentials (password grant) |
| `KEYCLOAK_REFRESH_SKEW` | `60` | Seconds before expiry to proactively refresh |
| `VERIFY_TLS` | `true` | Set `false` for self-signed certs |
| `REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds (raise for large pin sets) |
| `MAX_BULK_UNPIN` | `5000` | Hard per-operation unpin limit |
| `REQUIRE_CONFIRM_TOKEN` | `true` | Enforce server-side dry-run before unpin |
| `IPFS_API_URLS` | – | Per-peer IPFS API URLs for daemon restart (comma-separated `name=url` pairs) |
| `RESTART_WEBHOOK_URL` | – | Webhook for cluster daemon restart; `{peer_id}` is substituted |
| `BACKGROUND_REFRESH_INTERVAL` | `30` | Token/data refresh interval in seconds (0 = disabled) |
| `HOST` / `PORT` | `127.0.0.1` / `8765` | Web UI bind address |

### Connecting to a Cluster behind Nginx + Keycloak

```env
CLUSTER_API_URL=https://cluster.example.com/cluster
KEYCLOAK_URL=https://cluster.example.com/keycloak
KEYCLOAK_REALM=myrealm
KEYCLOAK_CLIENT_ID=my-client
KEYCLOAK_CLIENT_SECRET=...
KEYCLOAK_USERNAME=admin
KEYCLOAK_PASSWORD=...
```

The tool fetches an initial access + refresh token at startup and silently refreshes it before it expires. If the refresh token itself expires, it falls back to a fresh password grant automatically. A lock prevents concurrent requests from racing to refresh simultaneously.

### Enabling peer IPFS restart

```env
IPFS_API_URLS=peer1=https://node1.example.com/api/v0,peer2=https://node2.example.com/api/v0
```

This sends `POST /api/v0/shutdown` to the target node. Your process manager (systemd, Kubernetes) is expected to restart the daemon.

---

## Architecture

```
┌──────────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Browser (vanilla   │───▶│  FastAPI + httpx │───▶│  IPFS Cluster    │
│   JS dashboard)      │    │  (app/main.py)   │    │  REST API        │
└──────────────────────┘    └──────────────────┘    │  (port 9094)     │
                                    │                └──────────────────┘
                            in-memory preview tokens
                            (sha256 fingerprint, TTL 10m)
```

```
ipfs-cluster-manager/
├── app/
│   ├── cluster_client.py      # async REST client (httpx)
│   ├── token_provider.py      # Keycloak OIDC token fetch + auto-refresh
│   ├── filters.py             # PinFilter — timestamp, metadata, regex, etc.
│   ├── config.py              # env config
│   ├── main.py                # FastAPI app and API routes
│   ├── templates/dashboard.html
│   └── static/{style.css, app.js}
├── tests/
│   ├── test_filters.py        # 14 unit tests for filter logic
│   ├── test_api.py            # 10 integration tests with mocked Cluster API
│   └── test_token_provider.py # 9 tests for Keycloak token refresh
├── requirements.txt
├── run.py
└── .env.example
```

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard (HTML) |
| `GET` | `/api/health` | Cluster peer health check |
| `GET` | `/api/status` | Cluster info, peers, and pin counts |
| `GET` | `/api/pins` | All pins (allocations) with metadata |
| `POST` | `/api/preview-unpin` | Apply filter, returns dry-run list + token |
| `POST` | `/api/bulk-unpin` | Execute unpin; requires `confirm_token` |
| `POST` | `/api/peers/{peer_id}/restart-ipfs` | Restart IPFS daemon on a peer |
| `POST` | `/api/peers/{peer_id}/restart` | Trigger cluster daemon restart webhook |
| `POST` | `/api/gc` | Run IPFS garbage collection |

Interactive docs at `/docs`.

---

## Filter examples

**All staging pins older than 30 days:**
- Name regex: `^staging-`
- Added before: `2026-05-11T00:00:00Z`

**All ephemeral pins via metadata:**
```
env=staging
tag=ephemeral
```

**Wildcard on metadata value:**
```
tag=model-v0.*
```

**Regex on metadata value:**
```
tag=re:^(ephemeral|temp-.*)$
```

---

## Tests

```bash
pip install pytest pytest-asyncio
pytest -q
```

33 tests: filter logic, full preview → token validation → bulk-unpin → single-use token flow, and Keycloak token refresh (cache, refresh flow, `invalid_grant` fallback, concurrent locking).

---

## Security design

1. **Dry-run required**: the token paradigm makes it impossible to call `/api/bulk-unpin` without having first seen the exact matched CID set in a preview. The server stores a sha256 fingerprint of those CIDs and rejects unpins that deviate (including subsets).
2. **Single-use tokens**: consumed on successful unpin, preventing replay and double-submit.
3. **Bulk limit**: `MAX_BULK_UNPIN` prevents an accidental filter from wiping the entire pin set. Refine the filter if you hit the limit.
4. **Local binding**: default `HOST=127.0.0.1`. Only expose on `0.0.0.0` behind a reverse proxy with its own authentication layer.
5. **No blind delete-by-tag**: there is no "delete all with tag=X" endpoint that bypasses the preview step.

---

## Production deployment

- Run behind Nginx with TLS and authentication (Keycloak or similar). This tool has no built-in user auth.
- For very large pin sets (>100k): increase `REQUEST_TIMEOUT` and use `before=` to narrow the allocation list. Cluster's own `filter=` query param does not support metadata filtering, so filtering happens client-side.
- Logs go to stdout in standard Python format — compatible with journald, Prometheus/Loki, or any log aggregator.

### systemd user service

```ini
[Unit]
Description=IPFS Cluster Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ipfs-cluster-manager
EnvironmentFile=/opt/ipfs-cluster-manager/.env
ExecStart=/opt/ipfs-cluster-manager/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now ipfs-cluster-manager
journalctl --user -u ipfs-cluster-manager -f
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Unauthorized` on `/api/health` | Keycloak credentials wrong — verify token endpoint with `curl` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed cert → set `VERIFY_TLS=false` in `.env` |
| Dashboard stays empty | Check browser console and `python run.py` logs |
| `Address already in use` | Port conflict → set `PORT=8766` in `.env` |

---

## License

MIT
