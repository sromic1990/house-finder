"""Registry that turns config.yaml `criteria:` blocks into live Criterion objects."""
from __future__ import annotations

from typing import Type

from .base import Criterion

_REGISTRY: dict[str, Type[Criterion]] = {}


def register(cls: Type[Criterion]) -> Type[Criterion]:
    """Class decorator: make a criterion discoverable by its `key`."""
    if not getattr(cls, "key", ""):
        raise ValueError(f"{cls.__name__} must define a non-empty `key`")
    if cls.key in _REGISTRY:
        raise ValueError(f"duplicate criterion key: {cls.key!r}")
    _REGISTRY[cls.key] = cls
    return cls


def available() -> dict[str, Type[Criterion]]:
    return dict(_REGISTRY)


def build_criteria(criteria_config: dict) -> list[Criterion]:
    """Instantiate the enabled criteria named in config.

    Unknown keys are ignored with a warning so a typo never silently drops a
    requirement without telling you.
    """
    built: list[Criterion] = []
    for key, block in (criteria_config or {}).items():
        cls = _REGISTRY.get(key)
        if cls is None:
            print(f"[criteria] WARNING: no criterion registered for {key!r} — skipped")
            continue
        crit = cls(block or {})
        if crit.enabled:
            built.append(crit)
    return built
