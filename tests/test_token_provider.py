"""Tests voor KeycloakTokenProvider.

Mockt het Keycloak token-endpoint via httpx.MockTransport, zodat we kunnen
verifieren dat:
- het juiste grant_type wordt gebruikt (password vs client_credentials)
- het cached access_token wordt hergebruikt totdat het bijna verloopt
- refresh-token-flow wordt aangeroepen wanneer expiry binnen skew valt
- bij een refresh-fout wordt teruggevallen op een nieuwe initial grant
- concurrent get_token() calls maar \u00e9\u00e9n refresh triggeren
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.token_provider import KeycloakTokenProvider, build_token_provider, StaticTokenProvider  # noqa: E402


def _cfg(**overrides) -> Settings:
    base = dict(
        keycloak_url="https://kc.example/keycloak",
        keycloak_realm="test-realm",
        keycloak_client_id="my-client",
        keycloak_client_secret="s3cret",
        keycloak_username="alice",
        keycloak_password="hunter2",
        keycloak_refresh_skew=30,
        verify_tls=True,
        request_timeout=10,
    )
    base.update(overrides)
    # Settings is frozen dataclass; we maken een schoon Settings-object
    return Settings(**{k: v for k, v in base.items() if k in Settings.__dataclass_fields__})


class MockKeycloak:
    """Vervangt httpx.AsyncClient.post om Keycloak responses te leveren."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[httpx.Response] = []

    def add(self, *, access_token: str, expires_in: int = 300,
            refresh_token: str | None = "rt-xyz", refresh_expires_in: int = 1800,
            status_code: int = 200) -> None:
        body = {"access_token": access_token, "expires_in": expires_in, "token_type": "Bearer"}
        if refresh_token:
            body["refresh_token"] = refresh_token
            body["refresh_expires_in"] = refresh_expires_in
        self.responses.append(httpx.Response(status_code, json=body))

    def add_error(self, status_code: int = 400, body: str = "invalid_grant") -> None:
        self.responses.append(httpx.Response(status_code, text=body))

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            data = dict(httpx.QueryParams(request.content.decode()))
            self.calls.append(data)
            if not self.responses:
                return httpx.Response(500, text="no mock response queued")
            return self.responses.pop(0)
        return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_initial_grant_uses_password():
    mock = MockKeycloak()
    mock.add(access_token="tok1", expires_in=300)
    provider = KeycloakTokenProvider(_cfg())
    provider._http = httpx.AsyncClient(transport=mock.transport())

    token = await provider.get_token()
    assert token == "tok1"
    assert mock.calls[0]["grant_type"] == "password"
    assert mock.calls[0]["username"] == "alice"
    await provider.aclose()


@pytest.mark.asyncio
async def test_client_credentials_when_no_user():
    mock = MockKeycloak()
    mock.add(access_token="cc1", expires_in=300, refresh_token=None)
    cfg = _cfg(keycloak_username=None, keycloak_password=None)
    provider = KeycloakTokenProvider(cfg)
    provider._http = httpx.AsyncClient(transport=mock.transport())

    token = await provider.get_token()
    assert token == "cc1"
    assert mock.calls[0]["grant_type"] == "client_credentials"
    await provider.aclose()


@pytest.mark.asyncio
async def test_token_cached_until_skew():
    """Tweede call binnen expiry-skew moet hetzelfde token retourneren."""
    mock = MockKeycloak()
    mock.add(access_token="tok-cached", expires_in=300)
    provider = KeycloakTokenProvider(_cfg())
    provider._http = httpx.AsyncClient(transport=mock.transport())

    t1 = await provider.get_token()
    t2 = await provider.get_token()
    assert t1 == t2 == "tok-cached"
    assert len(mock.calls) == 1  # geen tweede refresh
    await provider.aclose()


@pytest.mark.asyncio
async def test_refresh_when_expired():
    """Als het access_token verlopen is, moet refresh_token grant gebruikt worden."""
    mock = MockKeycloak()
    mock.add(access_token="first", expires_in=300, refresh_token="rt1")
    mock.add(access_token="second", expires_in=300, refresh_token="rt2")
    provider = KeycloakTokenProvider(_cfg())
    provider._http = httpx.AsyncClient(transport=mock.transport())

    t1 = await provider.get_token()
    assert t1 == "first"

    # Forceer expiry: zet de cache zo dat we binnen skew zitten
    provider._access_expires_at = time.time()  # nu = "verlopen"
    t2 = await provider.get_token()
    assert t2 == "second"
    assert len(mock.calls) == 2
    assert mock.calls[1]["grant_type"] == "refresh_token"
    assert mock.calls[1]["refresh_token"] == "rt1"
    await provider.aclose()


@pytest.mark.asyncio
async def test_refresh_failure_falls_back_to_initial():
    """Als refresh_token niet meer geldig is (bv. server-sessie weg), val terug op password grant."""
    mock = MockKeycloak()
    mock.add(access_token="initial", expires_in=300, refresh_token="bad-rt")
    mock.add_error(status_code=400, body="invalid_grant")  # refresh faalt
    mock.add(access_token="fresh", expires_in=300, refresh_token="rt-new")  # fallback
    provider = KeycloakTokenProvider(_cfg())
    provider._http = httpx.AsyncClient(transport=mock.transport())

    assert await provider.get_token() == "initial"
    # Forceer een refresh
    provider._access_expires_at = time.time()
    t2 = await provider.get_token()
    assert t2 == "fresh"
    # 3 calls: initial password, gefaalde refresh, fallback password
    assert len(mock.calls) == 3
    assert mock.calls[1]["grant_type"] == "refresh_token"
    assert mock.calls[2]["grant_type"] == "password"
    await provider.aclose()


@pytest.mark.asyncio
async def test_concurrent_callers_share_refresh():
    """100 parallelle get_token() calls mogen samen maar 1 token-request triggeren."""
    mock = MockKeycloak()
    mock.add(access_token="shared", expires_in=300)
    provider = KeycloakTokenProvider(_cfg())
    provider._http = httpx.AsyncClient(transport=mock.transport())

    tokens = await asyncio.gather(*[provider.get_token() for _ in range(100)])
    assert all(t == "shared" for t in tokens)
    assert len(mock.calls) == 1
    await provider.aclose()


@pytest.mark.asyncio
async def test_build_token_provider_returns_static_when_no_keycloak():
    # Expliciet alle Keycloak-velden leeg houden zodat een eventueel
    # .env-bestand in CWD ons hier niet beinvloedt.
    cfg = Settings(
        cluster_jwt="static-jwt",
        keycloak_url=None,
        keycloak_realm=None,
        keycloak_client_id=None,
    )
    p = build_token_provider(cfg)
    assert isinstance(p, StaticTokenProvider)
    assert await p.get_token() == "static-jwt"
    await p.aclose()


@pytest.mark.asyncio
async def test_build_token_provider_returns_keycloak_when_configured():
    cfg = _cfg()
    p = build_token_provider(cfg)
    assert isinstance(p, KeycloakTokenProvider)
    await p.aclose()


def test_keycloak_provider_validates_config():
    # Mist client_id
    with pytest.raises(ValueError):
        KeycloakTokenProvider(_cfg(keycloak_client_id=None))
    # Geen username+password \u00e9n geen client_secret
    with pytest.raises(ValueError):
        KeycloakTokenProvider(_cfg(
            keycloak_username=None, keycloak_password=None, keycloak_client_secret=None
        ))
