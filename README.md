# IPFS Cluster Manager

Python-gebaseerde beheer-tool voor [IPFS Cluster](https://ipfscluster.io/), met
een web dashboard voor monitoring én veilige bulk-unpin operaties op basis van
tijdstempel, naam, regex of metadata-tags.

## Features

- **Dashboard** — clusterstatus, peer-overzicht, pin-telling per node
  (afgeleid uit de consensus allocations), versie-info en actieve health
  alerts. Auto-refresh elke 30 seconden.
- **Filteren** op naam (substring/regex), `timestamp` (vóór/ná), metadata
  key/value (met `*` wildcards of `re:` regex prefix), tracker-status, of
  expliciete CID-lijst.
- **Veilige bulk-unpin workflow**:
  1. Preview (dry-run) toont gematchte pins én geeft een eenmalig
     `confirm_token` terug;
  2. Confirm-checkbox + browser confirm-dialog vóór uitvoering;
  3. Server controleert dat het token bij exact dezelfde CID-set hoort
     (sha256-fingerprint), en het token is single-use;
  4. Hard limiet op aantal pins per operatie (`MAX_BULK_UNPIN`, default 5000);
  5. Optioneel: `POST /ipfs/gc` ná succesvolle unpin.
- **Async** (httpx) — streaming endpoints (`/allocations`, `/pins`, `/peers`)
  worden via NDJSON afgehandeld, met parallelle unpins (max 8 gelijktijdig).
- **Auth** — Keycloak/OIDC met **automatische token-refresh** (refresh_token + fallback), of statisch JWT, of basic-auth.
- **Geen externe state** — alle state (preview-tokens) leeft in-memory met
  TTL van 10 minuten.

## Installatie

```bash
cd ipfs-cluster-manager
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# vul .env met jouw cluster URL en credentials
python run.py
```

Open daarna [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Configuratie

Alle instellingen via env vars (zie `.env.example`):

| Variabele | Default | Beschrijving |
|---|---|---|
| `CLUSTER_API_URL` | `http://127.0.0.1:9094` | REST API endpoint van Cluster |
| `CLUSTER_USERNAME` / `CLUSTER_PASSWORD` | – | Basic Auth credentials |
| `CLUSTER_JWT` | – | Statisch JWT bearer token (geen auto-refresh) |
| `KEYCLOAK_URL` | – | OIDC issuer base (bv. `https://host/keycloak`) |
| `KEYCLOAK_REALM` | – | Realm naam |
| `KEYCLOAK_CLIENT_ID` | – | OIDC client ID |
| `KEYCLOAK_CLIENT_SECRET` | – | Client secret (voor confidential clients) |
| `KEYCLOAK_USERNAME` / `KEYCLOAK_PASSWORD` | – | User-credentials (password-grant) |
| `KEYCLOAK_REFRESH_SKEW` | `60` | Seconden vóór expiry waarop alvast wordt ververst |
| `VERIFY_TLS` | `true` | Zet op `false` voor self-signed certs |
| `REQUEST_TIMEOUT` | `30` | Seconden, voor lange `/allocations` lijsten kan dit omhoog |
| `MAX_BULK_UNPIN` | `5000` | Hard limiet per operatie |
| `REQUIRE_CONFIRM_TOKEN` | `true` | Server-side dry-run verplichting |
| `HOST` / `PORT` | `127.0.0.1` / `8765` | Binding van de web UI |

### Verbinden met een Cluster achter Nginx + Keycloak (aanbevolen)

Vul Keycloak credentials in `.env`. De tool haalt zelf tokens op bij
Keycloak en ververst ze automatisch:

```env
CLUSTER_API_URL=https://ford.ilse-ai.eu/cluster
KEYCLOAK_URL=https://ford.ilse-ai.eu/keycloak
KEYCLOAK_REALM=ilse-ai
KEYCLOAK_CLIENT_ID=ilse-ai-services
KEYCLOAK_CLIENT_SECRET=...
KEYCLOAK_USERNAME=jaco
KEYCLOAK_PASSWORD=...
```

**Wat er onder de motorkap gebeurt:**

1. Bij startup haalt de tool een initieel access_token + refresh_token
   op via `grant_type=password` (of `client_credentials` als geen user
   is opgegeven).
2. Elke API-call gebruikt het cached token. Zodra het binnen
   `KEYCLOAK_REFRESH_SKEW` seconden verloopt, wordt het ververst via
   `grant_type=refresh_token` — in de meeste gevallen onzichtbaar voor
   de user.
3. Als de refresh-token zelf is verlopen of geweigerd, valt de tool
   automatisch terug op een verse password-grant.
4. Een `asyncio.Lock` voorkomt dat concurrent requests gelijktijdig een
   refresh proberen — 100 parallelle calls tijdens een refresh-window
   geven samen één token-request.

## Architectuur

```
┌──────────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Browser (vanilla   │───▶│  FastAPI + httpx │───▶│  IPFS Cluster    │
│   JS dashboard)      │    │  (app/main.py)   │    │  REST API        │
└──────────────────────┘    └──────────────────┘    │  (port 9094)     │
                                    │                └──────────────────┘
                            in-memory preview-tokens
                            (sha256 fingerprint, TTL 10m)
```

### Bestanden

```
ipfs-cluster-manager/
├── app/
│   ├── cluster_client.py   # async REST client (httpx) — alle Cluster endpoints
│   ├── token_provider.py   # Keycloak OIDC token-fetch + auto-refresh
│   ├── filters.py          # PinFilter — timestamp, metadata-tags, regex, etc.
│   ├── config.py           # env-config (incl. eenvoudige .env loader)
│   ├── main.py             # FastAPI app — /api/status, /api/preview-unpin, /api/bulk-unpin
│   ├── templates/dashboard.html
│   └── static/{style.css, app.js}
├── tests/
│   ├── test_filters.py        # 14 unit-tests voor filterregels
│   ├── test_api.py            # 10 integration-tests met gemockte Cluster API
│   └── test_token_provider.py # 9 tests voor Keycloak token-refresh
├── requirements.txt
├── run.py
└── .env.example
```

## API endpoints (server-side)

| Methode | Pad | Beschrijving |
|---|---|---|
| `GET` | `/` | Dashboard (HTML) |
| `GET` | `/api/health` | Health check van de Cluster peer |
| `GET` | `/api/status` | Cluster info + peers + pins per node |
| `GET` | `/api/pins` | Alle pins (allocations) met metadata |
| `POST` | `/api/preview-unpin` | Filter toepassen, levert dry-run + token |
| `POST` | `/api/bulk-unpin` | Voert unpin uit; vereist `confirm_token` |
| `POST` | `/api/gc` | Voer IPFS GC uit |

OpenAPI/Swagger docs draaien op `/docs`.

## Filters — voorbeelden

**Alle staging-pins ouder dan 30 dagen:**
- *Naam regex:* `^staging-`
- *Toegevoegd vóór:* `2026-05-11T00:00:00Z`

**Alle ephemeral pins (metadata):**
```
env=staging
tag=ephemeral
```

**Modellen ouder dan een specifieke release (wildcard):**
```
tag=model-v0.*
```

**Regex op metadata-waarde:**
```
tag=re:^(ephemeral|temp-.*)$
```

## Tests draaien

```bash
pip install pytest
pytest -q
```

Levert 33 tests op: filter-logica, API-flow (preview → token validatie →
bulk-unpin → token-single-use), én Keycloak token-refresh (cache,
refresh-flow, fallback bij invalid_grant, concurrent locking).

## Veiligheid — designkeuzes

1. **Dry-run verplicht**: het token-paradigma maakt het onmogelijk om
   `/api/bulk-unpin` direct aan te roepen zonder eerst de exacte CID-set
   in een preview te hebben gezien. De server bewaart een
   sha256-fingerprint van de gematchte CIDs en accepteert geen unpin
   wanneer de payload daarvan afwijkt (ook niet een subset).
2. **Single-use tokens**: na een succesvolle unpin wordt het token
   verwijderd. Replay-aanvallen / dubbele submits worden zo voorkomen.
3. **Bulk-limiet**: `MAX_BULK_UNPIN` voorkomt dat één filter per ongeluk
   de hele pinset verwijdert. Verfijn het filter wanneer de limiet wordt
   geraakt.
4. **Lokale binding**: default `HOST=127.0.0.1`. Zet alleen op `0.0.0.0`
   wanneer je hem áchter een reverse proxy met eigen authenticatie zet.
5. **Geen unpin-by-tag in één call**: er is geen "delete all with tag=X"
   endpoint zonder voorafgaande preview — exact om diezelfde reden.

## Productie-deployment tips

- Draai achter Nginx met TLS en (Keycloak) authenticatie. De tool zelf
  doet géén eigen user-auth — die laag schuif je ervoor.
- Voor zeer grote pinsets (>100k): verhoog `REQUEST_TIMEOUT` en overweeg
  je filter te beginnen met `before=` om de allocations-lijst aan
  client-side te verkleinen. Cluster's eigen `filter=` query param
  ondersteunt geen metadata-filtering, daarom doen wij dat client-side.
- Logging gaat naar stdout in standaard Python format — koppel aan je
  bestaande Prometheus/Grafana of journald setup.

## Licentie

MIT.
