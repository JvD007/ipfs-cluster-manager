"""Keycloak / OIDC token provider met automatische refresh.

Werkwijze:
- ``get_token()`` levert altijd een geldig access_token. Als het cached token
  binnen ``refresh_skew`` seconden verloopt, wordt het ververst.
- Refresh probeert eerst ``grant_type=refresh_token``. Faalt dat (refresh-token
  zelf verlopen, server-side sessie weg, etc.), dan valt het terug op de
  initi\u00eble grant (``password`` of ``client_credentials``).
- Concurrent callers krijgen \u00e9\u00e9nzelfde refresh \u2014 een asyncio.Lock zorgt dat
  er niet tien refreshes tegelijk lopen.
- Een ``StaticTokenProvider`` is er voor het geval iemand alleen een statisch
  ``CLUSTER_JWT`` heeft ingesteld.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Protocol

import httpx

from .config import Settings, settings as default_settings

logger = logging.getLogger(__name__)


class TokenProvider(Protocol):
    async def get_token(self) -> Optional[str]: ...
    async def aclose(self) -> None: ...


class StaticTokenProvider:
    """Levert altijd hetzelfde token (of None). Gebruikt voor basic-auth / dev."""

    def __init__(self, token: Optional[str]) -> None:
        self._token = token

    async def get_token(self) -> Optional[str]:
        return self._token

    async def aclose(self) -> None:  # noqa: D401
        return None


class KeycloakTokenProvider:
    """Haalt en ververst tokens bij een Keycloak OIDC token-endpoint.

    Kiest grant_type op basis van wat is geconfigureerd:
      - ``username`` + ``password`` aanwezig  → ``grant_type=password``
      - alleen ``client_secret`` aanwezig     → ``grant_type=client_credentials``
    """

    def __init__(self, cfg: Optional[Settings] = None, verify: Optional[bool] = None) -> None:
        cfg = cfg or default_settings
        if not cfg.keycloak_configured:
            raise ValueError(
                "KeycloakTokenProvider vereist KEYCLOAK_URL, KEYCLOAK_REALM en KEYCLOAK_CLIENT_ID."
            )
        if not (cfg.keycloak_username and cfg.keycloak_password) and not cfg.keycloak_client_secret:
            raise ValueError(
                "Geef \u00f3f KEYCLOAK_USERNAME+PASSWORD \u00f3f KEYCLOAK_CLIENT_SECRET op."
            )

        base = cfg.keycloak_url.rstrip("/")
        self._token_url = f"{base}/realms/{cfg.keycloak_realm}/protocol/openid-connect/token"
        self._client_id = cfg.keycloak_client_id
        self._client_secret = cfg.keycloak_client_secret
        self._username = cfg.keycloak_username
        self._password = cfg.keycloak_password
        self._skew = cfg.keycloak_refresh_skew
        self._verify = cfg.verify_tls if verify is None else verify
        self._timeout = cfg.request_timeout

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._access_expires_at: float = 0.0      # epoch seconds
        self._refresh_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        # Een persistente httpx client \u2014 hergebruikt connecties richting Keycloak
        self._http: Optional[httpx.AsyncClient] = None

    # ------- publiek -------

    async def get_token(self) -> str:
        """Geeft een geldig access_token terug (refresht indien nodig)."""
        if self._access_token and time.time() < self._access_expires_at - self._skew:
            return self._access_token

        async with self._lock:
            # Dubbele check: misschien heeft een andere coroutine het al gedaan
            if self._access_token and time.time() < self._access_expires_at - self._skew:
                return self._access_token
            await self._refresh_or_acquire()
            assert self._access_token is not None
            return self._access_token

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------- intern -------

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(verify=self._verify, timeout=self._timeout)
        return self._http

    async def _refresh_or_acquire(self) -> None:
        """Probeer refresh, val terug op initial grant."""
        if self._refresh_token and time.time() < self._refresh_expires_at - self._skew:
            try:
                await self._do_request({
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    **({"client_secret": self._client_secret} if self._client_secret else {}),
                    "refresh_token": self._refresh_token,
                })
                logger.info("Keycloak token ververst via refresh_token")
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Refresh-token grant faalde (%s) \u2014 val terug op initial grant", exc)

        await self._initial_grant()

    async def _initial_grant(self) -> None:
        if self._username and self._password:
            data = {
                "grant_type": "password",
                "client_id": self._client_id,
                "username": self._username,
                "password": self._password,
                "scope": "openid",
            }
            if self._client_secret:
                data["client_secret"] = self._client_secret
        else:
            data = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret or "",
            }
        await self._do_request(data)
        logger.info("Keycloak initi\u00eble token opgehaald (%s)", data["grant_type"])

    async def _do_request(self, data: dict) -> None:
        client = await self._client()
        resp = await client.post(
            self._token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Keycloak token-endpoint gaf {resp.status_code}: {resp.text[:300]}"
            )
        body = resp.json()
        now = time.time()
        self._access_token = body["access_token"]
        self._access_expires_at = now + int(body.get("expires_in", 300))
        # Niet alle grants leveren een refresh_token (client_credentials doet dat
        # bv. doorgaans niet \u2014 dat is by design)
        rt = body.get("refresh_token")
        if rt:
            self._refresh_token = rt
            self._refresh_expires_at = now + int(body.get("refresh_expires_in", 1800))
        else:
            self._refresh_token = None
            self._refresh_expires_at = 0.0


def build_token_provider(cfg: Optional[Settings] = None) -> TokenProvider:
    """Kies de juiste provider op basis van config."""
    cfg = cfg or default_settings
    if cfg.keycloak_configured:
        return KeycloakTokenProvider(cfg)
    return StaticTokenProvider(cfg.cluster_jwt)
