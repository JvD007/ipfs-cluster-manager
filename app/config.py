"""Configuratie via environment variables (.env wordt automatisch geladen)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Path) -> None:
    """Eenvoudige .env loader (geen externe dependency nodig)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    """Applicatie-instellingen."""

    cluster_api_url: str = os.getenv("CLUSTER_API_URL", "http://127.0.0.1:9094")
    cluster_username: Optional[str] = os.getenv("CLUSTER_USERNAME") or None
    cluster_password: Optional[str] = os.getenv("CLUSTER_PASSWORD") or None
    # Statisch JWT-token — alleen gebruiken als Keycloak NIET is geconfigureerd
    cluster_jwt: Optional[str] = os.getenv("CLUSTER_JWT") or None

    # --- Keycloak / OIDC token refresh ---
    # Wanneer keycloak_url + realm + client_id zijn ingesteld, haalt de tool
    # zelf tokens op en ververst ze automatisch vóórdat ze verlopen.
    keycloak_url: Optional[str] = os.getenv("KEYCLOAK_URL") or None
    keycloak_realm: Optional[str] = os.getenv("KEYCLOAK_REALM") or None
    keycloak_client_id: Optional[str] = os.getenv("KEYCLOAK_CLIENT_ID") or None
    keycloak_client_secret: Optional[str] = os.getenv("KEYCLOAK_CLIENT_SECRET") or None
    keycloak_username: Optional[str] = os.getenv("KEYCLOAK_USERNAME") or None
    keycloak_password: Optional[str] = os.getenv("KEYCLOAK_PASSWORD") or None
    # Hoeveel seconden vóór expiry we alvast refreshen (default 60s buffer)
    keycloak_refresh_skew: int = int(os.getenv("KEYCLOAK_REFRESH_SKEW", "60"))

    # TLS verificatie - zet op "false" voor self-signed certificaten in on-prem deployments
    verify_tls: bool = os.getenv("VERIFY_TLS", "true").lower() != "false"
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "30"))
    # Veiligheid: maximaal aantal items dat in één bulk-unpin operatie mag worden verwerkt
    max_bulk_unpin: int = int(os.getenv("MAX_BULK_UNPIN", "5000"))
    # Vereist een dry-run bevestigingstoken voordat daadwerkelijk wordt verwijderd
    require_confirm_token: bool = os.getenv("REQUIRE_CONFIRM_TOKEN", "true").lower() != "false"
    # Optionele webhook voor peer-restart, bv. https://my-api/restart/{peer_id}
    restart_webhook_url: Optional[str] = os.getenv("RESTART_WEBHOOK_URL") or None
    # IPFS API URLs per peer-naam (kommagescheiden: naam=url,naam=url)
    # Bv. ipfs-3=https://ford.ilse-ai.eu/api/v0,ipfs-4=https://ford.ilse-ai.eu/api/v0
    ipfs_api_urls: Optional[str] = os.getenv("IPFS_API_URLS") or None

    @property
    def ipfs_api_urls_map(self) -> dict:
        """Parses IPFS_API_URLS into {peer_name: base_url}."""
        if not self.ipfs_api_urls:
            return {}
        result = {}
        for part in self.ipfs_api_urls.split(","):
            part = part.strip()
            if "=" in part:
                name, _, url = part.partition("=")
                result[name.strip()] = url.strip().rstrip("/")
        return result
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8765"))

    @property
    def keycloak_configured(self) -> bool:
        """True als de tool zelf via Keycloak tokens kan ophalen."""
        return bool(
            self.keycloak_url and self.keycloak_realm and self.keycloak_client_id
        )


settings = Settings()
