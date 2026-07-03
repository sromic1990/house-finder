"""Config loading. Single source of truth = config.yaml at the repo root."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_PATH = os.getenv("HOUSE_FINDER_CONFIG", "config.yaml")


def load_config(path: str | None = None) -> dict:
    p = Path(path or CONFIG_PATH)
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
