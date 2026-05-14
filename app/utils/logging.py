from __future__ import annotations

import logging


def configure_logging(log_level: str) -> None:
    """Configure a single consistent logging format for the whole service."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
