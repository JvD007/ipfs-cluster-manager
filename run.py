"""Entrypoint — start de IPFS Cluster Manager web UI.

Gebruik:
  python run.py            # gebruikt HOST/PORT uit env (default 127.0.0.1:8765)
  uvicorn app.main:app --reload   # voor development met auto-reload
"""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
