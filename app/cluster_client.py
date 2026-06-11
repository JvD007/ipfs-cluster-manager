"""Async client voor de IPFS Cluster REST API.

Documentatie: https://ipfscluster.io/documentation/reference/api/

De Cluster API levert streaming endpoints (NDJSON) voor /allocations, /pins en
/peers. Deze client wikkelt die af in async generators én convenience-methodes
die de volledige lijst materialiseren.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

import httpx

from .config import settings
from .token_provider import StaticTokenProvider, TokenProvider, build_token_provider


class ClusterAPIError(RuntimeError):
    """Wrapper voor fouten vanuit de IPFS Cluster API."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.message = message


class ClusterClient:
    """Async REST-client voor IPFS Cluster.

    Gebruikt JWT als die is geconfigureerd, anders Basic Auth, anders geen auth
    (alleen geschikt voor lokale dev-clusters zonder authenticatie).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        jwt: Optional[str] = None,
        verify: Optional[bool] = None,
        timeout: Optional[float] = None,
        token_provider: Optional[TokenProvider] = None,
    ) -> None:
        self.base_url = (base_url or settings.cluster_api_url).rstrip("/")
        self._username = username if username is not None else settings.cluster_username
        self._password = password if password is not None else settings.cluster_password
        self._verify = settings.verify_tls if verify is None else verify
        self._timeout = timeout or settings.request_timeout
        # TokenProvider: of expliciet meegegeven, of statisch met de jwt-arg,
        # of de globale Keycloak/static provider uit config.
        if token_provider is not None:
            self._token_provider: TokenProvider = token_provider
        elif jwt is not None:
            self._token_provider = StaticTokenProvider(jwt)
        else:
            self._token_provider = build_token_provider()

    # --------- Interne helpers ---------

    async def _auth_and_headers(self) -> tuple[Optional[httpx.BasicAuth], dict[str, str]]:
        """Bepaal auth + headers op dít moment (refresht token indien nodig)."""
        headers = {"Accept": "application/json"}
        token = await self._token_provider.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            return None, headers
        if self._username and self._password:
            return httpx.BasicAuth(self._username, self._password), headers
        return None, headers

    async def _client(self) -> httpx.AsyncClient:
        auth, headers = await self._auth_and_headers()
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            headers=headers,
            verify=self._verify,
            timeout=self._timeout,
        )

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        client = await self._client()
        async with client:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code >= 400:
                raise ClusterAPIError(resp.status_code, resp.text)
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    async def _stream_ndjson(self, path: str, **kwargs: Any) -> AsyncIterator[dict]:
        """Cluster's streaming endpoints leveren één JSON-object per regel."""
        client = await self._client()
        async with client:
            async with client.stream("GET", path, **kwargs) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ClusterAPIError(resp.status_code, body.decode("utf-8", "replace"))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("_stream_ndjson: onparseerbare regel (pad=%s): %.120r", path, line)
                        continue
                    if isinstance(obj, list):
                        for item in obj:
                            yield item
                    else:
                        yield obj

    # --------- Publieke endpoints ---------

    async def health(self) -> bool:
        """`GET /health` — vereist geen auth, returnt 204 als peer leeft."""
        client = await self._client()
        async with client:
            resp = await client.get("/health")
            return resp.status_code == 204

    async def id(self) -> dict:
        """`GET /id` — informatie over de peer waarmee we verbonden zijn."""
        return await self._request_json("GET", "/id")

    async def version(self) -> dict:
        """`GET /version`"""
        return await self._request_json("GET", "/version")

    async def peers(self) -> list[dict]:
        """`GET /peers` (streaming) — alle peers in de cluster."""
        return [p async for p in self._stream_ndjson("/peers")]

    async def allocations(
        self,
        filter: Optional[str] = None,
        cids: Optional[list[str]] = None,
    ) -> list[dict]:
        """`GET /allocations` — de pinset (consensus state).

        :param filter: cluster-DSL filter (bv. 'pin', 'meta-pin') — meestal None
        :param cids: lijst van CIDs om te filteren
        """
        params: dict[str, Any] = {}
        if filter:
            params["filter"] = filter
        if cids:
            params["cids"] = ",".join(cids)
        return [p async for p in self._stream_ndjson("/allocations", params=params)]

    async def allocation(self, cid: str) -> dict:
        """`GET /allocations/{cid}`"""
        return await self._request_json("GET", f"/allocations/{cid}")

    async def pins_status(
        self,
        filter: Optional[str] = None,
        local: bool = False,
        cids: Optional[list[str]] = None,
    ) -> list[dict]:
        """`GET /pins` — globale status van alle gevolgde CIDs.

        :param filter: bv. 'pinned', 'pinning', 'pin_error', 'unpinned', 'all'
        :param local: alleen status van de peer waarmee we praten
        """
        params: dict[str, Any] = {}
        if filter:
            params["filter"] = filter
        if local:
            params["local"] = "true"
        if cids:
            params["cids"] = ",".join(cids)
        return [p async for p in self._stream_ndjson("/pins", params=params)]

    async def pin_status(self, cid: str, local: bool = False) -> dict:
        """`GET /pins/{cid}`"""
        params = {"local": "true"} if local else None
        return await self._request_json("GET", f"/pins/{cid}", params=params)

    async def unpin(self, cid: str) -> dict:
        """`DELETE /pins/{cid}` — verwijder een CID uit de pinset."""
        return await self._request_json("DELETE", f"/pins/{cid}")

    async def gc(self) -> dict:
        """`POST /ipfs/gc` — voer garbage collection uit op de IPFS nodes."""
        return await self._request_json("POST", "/ipfs/gc")

    async def metrics(self, metric: str) -> list[dict]:
        """`GET /monitor/metrics/{metric}`"""
        return await self._request_json("GET", f"/monitor/metrics/{metric}") or []

    async def alerts(self) -> list[dict]:
        """`GET /health/alerts`"""
        return await self._request_json("GET", "/health/alerts") or []
