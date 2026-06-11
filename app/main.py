"""FastAPI app voor IPFS Cluster Manager.

Endpoints:
  GET  /                       Dashboard (HTML)
  GET  /api/status             Clusterstatus + peers + pins-per-node
  GET  /api/pins               Alle pins met metadata
  POST /api/preview-unpin      Filter toepassen en lijst tonen (dry-run)
  POST /api/bulk-unpin         Daadwerkelijke bulk-unpin (vereist token)
  POST /api/gc                 IPFS garbage collection draaien
  GET  /api/health             Health check van de Cluster peer
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cluster_client import ClusterAPIError, ClusterClient
from .config import settings
from .filters import PinFilter, _extract_cid, filter_pins
from .token_provider import TokenProvider, build_token_provider

logger = logging.getLogger("ipfs_cluster_manager")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent


def _render_dashboard(context: dict[str, Any]) -> str:
    """Render dashboard zonder Jinja2Templates afhankelijk te zijn van een
    framework-cache (sommige hosting-omgevingen patchen die met een dict-key
    die niet hashable is). We lezen het bestand direct in en doen een simpele
    Jinja-render via een verse Environment."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR / "templates")),
        autoescape=select_autoescape(["html"]),
        cache_size=0,
    )
    tmpl = env.get_template("dashboard.html")
    return tmpl.render(**context)


# In-memory store voor preview-tokens. Een token beschrijft welke CIDs in een
# bulk-unpin-aanvraag mogen worden verwerkt; de unpin-call moet hetzelfde
# token én dezelfde set CIDs opgeven, anders weigeren we.
_preview_tokens: dict[str, dict[str, Any]] = {}
_TOKEN_TTL_SECONDS = 600  # 10 minuten


def _prune_tokens() -> None:
    now = time.time()
    expired = [t for t, v in _preview_tokens.items() if v["expires"] < now]
    for t in expired:
        _preview_tokens.pop(t, None)


def _fingerprint(cids: list[str]) -> str:
    h = hashlib.sha256()
    for c in sorted(cids):
        h.update(c.encode())
        h.update(b"\n")
    return h.hexdigest()


# Singleton token-provider — hergebruikt cache + refresh tussen requests
_token_provider: Optional[TokenProvider] = None


def _get_token_provider() -> TokenProvider:
    global _token_provider
    if _token_provider is None:
        _token_provider = build_token_provider()
    return _token_provider


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("Starting IPFS Cluster Manager — cluster=%s", settings.cluster_api_url)
    if settings.keycloak_configured:
        logger.info(
            "Keycloak token-refresh actief (realm=%s, client=%s)",
            settings.keycloak_realm,
            settings.keycloak_client_id,
        )
        try:
            # Doe een initial fetch zodat we eventuele config-fouten direct zien
            await _get_token_provider().get_token()
        except Exception as exc:  # noqa: BLE001
            logger.error("Initiële Keycloak token-fetch faalde: %s", exc)
    elif settings.cluster_jwt:
        logger.info("Statisch CLUSTER_JWT in gebruik (geen auto-refresh)")
    yield
    if _token_provider is not None:
        await _token_provider.aclose()
    logger.info("Shutting down IPFS Cluster Manager")


app = FastAPI(
    title="IPFS Cluster Manager",
    version="1.0.0",
    description="Beheer-tool voor IPFS Cluster pins (monitoring & bulk-unpin).",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_client() -> ClusterClient:
    return ClusterClient(token_provider=_get_token_provider())


# --------- Pydantic schemas ---------


class FilterPayload(BaseModel):
    name_contains: Optional[str] = None
    name_regex: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)
    status: Optional[str] = None
    cids: list[str] = Field(default_factory=list)


class BulkUnpinPayload(BaseModel):
    confirm_token: str
    cids: list[str]
    run_gc: bool = False


class RestartIpfsPayload(BaseModel):
    peer_name: str


# --------- Helpers ---------


def _summarize_pin(pin: dict) -> dict:
    return {
        "cid": _extract_cid(pin),
        "name": pin.get("name") or "",
        "timestamp": pin.get("timestamp"),
        "metadata": pin.get("metadata") or {},
        "allocations": pin.get("allocations") or [],
        "replication_factor_min": pin.get("replication_factor_min"),
        "replication_factor_max": pin.get("replication_factor_max"),
    }


# --------- Routes ---------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Any:
    html = _render_dashboard(
        {
            "cluster_url": settings.cluster_api_url,
            "max_bulk_unpin": settings.max_bulk_unpin,
            "require_confirm_token": settings.require_confirm_token,
        }
    )
    return HTMLResponse(html)


@app.get("/api/health")
async def api_health(client: ClusterClient = Depends(get_client)) -> dict:
    try:
        ok = await client.health()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": ok}


@app.get("/api/status")
async def api_status(client: ClusterClient = Depends(get_client)) -> dict:
    """Verzamel alle dashboard-gegevens in één call.

    Levert: cluster info, peers, alerts, en het aantal pins per node
    (afgeleid uit de allocations — dat is de canonieke replica-mapping).
    """
    peer_id, version, peers, alerts, allocations = await asyncio.gather(
        asyncio.create_task(client.id()),
        asyncio.create_task(client.version()),
        asyncio.create_task(client.peers()),
        asyncio.create_task(client.alerts()),
        asyncio.create_task(client.allocations()),
        return_exceptions=True,
    )

    def _ok(x: Any) -> Any:
        return None if isinstance(x, Exception) else x

    allocations = _ok(allocations) or []
    peers_list = _ok(peers) or []

    # Pins per node tellen op basis van het allocations veld
    pins_per_node: dict[str, int] = {}
    for pin in allocations:
        for alloc in pin.get("allocations") or []:
            pins_per_node[alloc] = pins_per_node.get(alloc, 0) + 1

    # Verrijk peers met pin-count en human-readable info
    peer_rows = []
    for p in peers_list:
        pid = p.get("id") or ""
        peer_rows.append(
            {
                "id": pid,
                "name": p.get("peername") or pid[:12],
                "addresses": p.get("addresses") or [],
                "cluster_version": p.get("version"),
                "ipfs_version": (p.get("ipfs") or {}).get("version"),
                "error": p.get("error") or None,
                "pins": pins_per_node.get(pid, 0),
                "rpc_protocol_version": p.get("rpc_protocol_version"),
            }
        )
    peer_rows.sort(key=lambda r: r["pins"], reverse=True)

    return {
        "cluster_url": settings.cluster_api_url,
        "peer_id": _ok(peer_id),
        "version": _ok(version),
        "alerts": _ok(alerts) or [],
        "total_pins": len(allocations),
        "peer_count": len(peer_rows),
        "peers": peer_rows,
    }


@app.get("/api/pins")
async def api_pins(client: ClusterClient = Depends(get_client)) -> dict:
    try:
        allocations = await client.allocations()
    except ClusterAPIError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"count": len(allocations), "pins": [_summarize_pin(p) for p in allocations]}


@app.post("/api/preview-unpin")
async def api_preview_unpin(
    payload: FilterPayload,
    client: ClusterClient = Depends(get_client),
) -> dict:
    """Pas filter toe en lever een dry-run lijst + bevestigings-token."""
    try:
        allocations = await client.allocations()
    except ClusterAPIError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc

    pin_filter = PinFilter.from_dict(payload.model_dump())
    matched = filter_pins(allocations, pin_filter)
    summaries = [_summarize_pin(p) for p in matched]
    cids = [s["cid"] for s in summaries if s["cid"]]

    if len(cids) > settings.max_bulk_unpin:
        raise HTTPException(
            400,
            f"Filter levert {len(cids)} pins op — meer dan toegestaan ({settings.max_bulk_unpin}). "
            "Verfijn het filter.",
        )

    _prune_tokens()
    token = secrets.token_urlsafe(24)
    _preview_tokens[token] = {
        "fingerprint": _fingerprint(cids),
        "count": len(cids),
        "expires": time.time() + _TOKEN_TTL_SECONDS,
    }

    return {
        "matched_count": len(cids),
        "total_pins": len(allocations),
        "confirm_token": token,
        "token_expires_in": _TOKEN_TTL_SECONDS,
        # Stuur alleen de eerste 500 voor het overzicht — verminder payload
        "sample": summaries[:500],
        "truncated": len(summaries) > 500,
        "cids": cids,
    }


@app.post("/api/bulk-unpin")
async def api_bulk_unpin(
    payload: BulkUnpinPayload,
    client: ClusterClient = Depends(get_client),
) -> dict:
    """Voer de daadwerkelijke unpin uit. Vereist een geldig confirm_token."""
    cids = [c for c in payload.cids if c]
    if not cids:
        raise HTTPException(400, "Geen CIDs opgegeven.")

    if len(cids) > settings.max_bulk_unpin:
        raise HTTPException(
            400, f"Aantal CIDs ({len(cids)}) overschrijdt limiet ({settings.max_bulk_unpin})."
        )

    if settings.require_confirm_token:
        _prune_tokens()
        entry = _preview_tokens.get(payload.confirm_token)
        if not entry:
            raise HTTPException(403, "Onbekend of verlopen bevestigingstoken — start een nieuwe preview.")
        if entry["fingerprint"] != _fingerprint(cids):
            raise HTTPException(
                403,
                "Bevestigingstoken hoort niet bij deze CID-set. Start een nieuwe preview.",
            )
        # Token is single-use
        _preview_tokens.pop(payload.confirm_token, None)

    # Voer unpins parallel uit, maar bewaak gelijktijdigheid
    semaphore = asyncio.Semaphore(8)
    results: list[dict] = []

    async def _do_unpin(cid: str) -> None:
        async with semaphore:
            try:
                await client.unpin(cid)
                results.append({"cid": cid, "ok": True})
            except ClusterAPIError as exc:
                results.append({"cid": cid, "ok": False, "error": exc.message, "status": exc.status_code})
            except Exception as exc:  # noqa: BLE001
                results.append({"cid": cid, "ok": False, "error": str(exc)})

    logger.info("Bulk-unpin gestart voor %d CIDs", len(cids))
    await asyncio.gather(*[_do_unpin(c) for c in cids])
    succeeded = sum(1 for r in results if r["ok"])
    failed = len(results) - succeeded
    logger.info("Bulk-unpin klaar: %d ok, %d fout", succeeded, failed)

    gc_result: Optional[dict] = None
    if payload.run_gc and succeeded > 0:
        try:
            gc_result = await client.gc()
        except ClusterAPIError as exc:
            gc_result = {"error": exc.message, "status": exc.status_code}

    return {
        "requested": len(cids),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
        "gc": gc_result,
    }


@app.get("/api/metrics")
async def api_metrics(client: ClusterClient = Depends(get_client)) -> dict:
    """Cluster-brede metrics: vrije schijfruimte, pinqueue, ping-validiteit en pin-errors."""
    freespace_raw, pinqueue_raw, ping_raw, pin_errors = await asyncio.gather(
        asyncio.create_task(client.metrics("freespace")),
        asyncio.create_task(client.metrics("pinqueue")),
        asyncio.create_task(client.metrics("ping")),
        asyncio.create_task(client.count_pins_by_status("pin_error")),
        return_exceptions=True,
    )

    def _safe(x: Any) -> list:
        return [] if isinstance(x, Exception) else (x or [])

    by_peer: dict[str, dict] = {}
    stale: list[str] = []

    for m in _safe(freespace_raw):
        pid = m.get("peer", "")
        valid = bool(m.get("valid"))
        if not valid and pid not in stale:
            stale.append(pid)
        by_peer.setdefault(pid, {})["freespace_bytes"] = (
            int(m["value"]) if valid and m.get("value") is not None else None
        )

    for m in _safe(pinqueue_raw):
        pid = m.get("peer", "")
        valid = bool(m.get("valid"))
        by_peer.setdefault(pid, {})["pinqueue"] = (
            int(m["value"]) if valid and m.get("value") is not None else None
        )

    for m in _safe(ping_raw):
        pid = m.get("peer", "")
        valid = bool(m.get("valid"))
        val: dict = {}
        try:
            val = json.loads(m.get("value") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        addrs = val.get("ipfs_addresses") or []
        by_peer.setdefault(pid, {}).update({
            "ping_valid": valid,
            "ipfs_id": val.get("ipfs_id", ""),
            "ipfs_address": addrs[0] if addrs else "",
        })
        if not valid and pid not in stale:
            stale.append(pid)

    return {
        "by_peer": by_peer,
        "pin_errors": pin_errors if isinstance(pin_errors, int) else 0,
        "stale_peers": stale,
        "restart_enabled": bool(settings.restart_webhook_url),
        "ipfs_restart_configured": list(settings.ipfs_api_urls_map.keys()),
    }


@app.post("/api/peers/{peer_id}/restart-ipfs")
async def api_restart_ipfs(peer_id: str, payload: RestartIpfsPayload) -> dict:
    """Stuur IPFS daemon shutdown voor een peer (process manager herstart hem)."""
    url_map = settings.ipfs_api_urls_map
    if not url_map:
        raise HTTPException(501, "IPFS_API_URLS niet geconfigureerd in .env.")
    ipfs_url = url_map.get(payload.peer_name)
    if not ipfs_url:
        raise HTTPException(
            400, f"Geen IPFS API URL geconfigureerd voor peer '{payload.peer_name}'."
        )
    headers: dict[str, str] = {}
    token = await _get_token_provider().get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10, verify=settings.verify_tls) as http:
        try:
            resp = await http.post(f"{ipfs_url}/shutdown", headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(
                    resp.status_code, f"IPFS API gaf {resp.status_code}: {resp.text[:200]}"
                )
        except (httpx.RemoteProtocolError, httpx.ReadError):
            # IPFS sluit de verbinding direct bij shutdown — dit is normaal gedrag
            pass
    logger.info("IPFS shutdown verstuurd naar peer %s (%s)", payload.peer_name, ipfs_url)
    return {"ok": True, "peer_id": peer_id, "peer_name": payload.peer_name}


@app.post("/api/peers/{peer_id}/restart")
async def api_restart_peer(peer_id: str) -> dict:
    """Stuur een restart-webhook voor een specifieke peer."""
    if not settings.restart_webhook_url:
        raise HTTPException(501, "RESTART_WEBHOOK_URL is niet geconfigureerd in .env.")
    url = settings.restart_webhook_url.replace("{peer_id}", peer_id)
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(url, json={"peer_id": peer_id})
        if resp.status_code >= 400:
            raise HTTPException(
                resp.status_code,
                f"Webhook antwoordde {resp.status_code}: {resp.text[:200]}",
            )
    return {"ok": True, "peer_id": peer_id}


@app.post("/api/gc")
async def api_gc(client: ClusterClient = Depends(get_client)) -> dict:
    try:
        result = await client.gc()
    except ClusterAPIError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True, "result": result}


@app.exception_handler(ClusterAPIError)
async def cluster_error_handler(_: Request, exc: ClusterAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "status": exc.status_code},
    )
