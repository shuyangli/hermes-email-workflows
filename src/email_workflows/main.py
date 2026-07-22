"""Application entry point."""

from __future__ import annotations

import logging
import os

import uvicorn

from .web import create_app

app = create_app()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "email_workflows.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("HEW_PORT", "8787")),
        access_log=True,
    )


if __name__ == "__main__":
    main()
