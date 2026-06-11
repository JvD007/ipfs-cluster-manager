"""Production server voor lokaal draaien op je workstation.

Standaard binding is 127.0.0.1:8765. Override met HOST/PORT env vars
of in je .env bestand. Voor toegang vanaf andere machines op je LAN
zet HOST=0.0.0.0 (let op: dan is auth/firewalling je eigen verantwoordelijkheid).
"""
from __future__ import annotations

import os

import uvicorn

# Defaults voor lokaal draaien — overschrijfbaar via .env / env vars.
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8765")

from app.main import app  # noqa: E402


def main() -> None:
    host = os.environ["HOST"]
    port = int(os.environ["PORT"])
    print()
    print("=" * 60)
    print("  IPFS Cluster Manager")
    print("=" * 60)
    print(f"  Dashboard:  http://{host}:{port}/")
    print(f"  API docs:   http://{host}:{port}/docs")
    print(f"  Health:     http://{host}:{port}/api/health")
    print("=" * 60)
    print("  Stoppen: Ctrl+C")
    print()

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
