"""Development entry point: `python -m labs`.

Production runs uvicorn directly against `labs.application:app` (see the
Dockerfile CMD), so this exists for local work only.
"""
from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LABS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "labs.application:app",
        host=os.environ.get("LABS_HOST", "0.0.0.0"),
        port=int(os.environ.get("LABS_PORT", "8000")),
        workers=int(os.environ.get("LABS_WORKERS", "1")),
        reload=os.environ.get("LABS_RELOAD", "").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    main()
