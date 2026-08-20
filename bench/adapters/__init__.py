"""Adapter registry.

Adding a database is: write an Adapter subclass, add a PlatformSpec in
config.py, add one line to `_BUILDERS`. Nothing in the harness knows the name
of any specific database.

Imports are lazy so that a missing optional driver only breaks the platform
that needs it, not the whole CLI.
"""

from __future__ import annotations

from typing import Callable

from .. import config as cfg
from .base import Adapter, LoadResult

__all__ = ["Adapter", "LoadResult", "build", "available"]


def _bolt(cls_name: str) -> Callable[[cfg.PlatformSpec], Adapter]:
    def builder(spec: cfg.PlatformSpec) -> Adapter:
        from . import bolt

        cls = getattr(bolt, cls_name)
        return cls(spec.key, spec.display_name, spec.env_prefix)

    return builder


def _falkor(spec: cfg.PlatformSpec) -> Adapter:
    from .falkor import FalkorDBAdapter

    return FalkorDBAdapter(spec.key, spec.display_name, spec.env_prefix)


def _arango(spec: cfg.PlatformSpec) -> Adapter:
    from .arango import ArangoAdapter

    return ArangoAdapter(spec.key, spec.display_name, spec.env_prefix)


def _mock(spec: cfg.PlatformSpec) -> Adapter:
    from .mock import MockAdapter

    return MockAdapter(spec.key, spec.display_name, spec.env_prefix)


_BUILDERS: dict[str, Callable[[cfg.PlatformSpec], Adapter]] = {
    "cognodb": _bolt("CognoDBAdapter"),
    "neo4j_aura": _bolt("AuraAdapter"),
    "memgraph": _bolt("MemgraphAdapter"),
    "falkordb": _falkor,
    "arangodb": _arango,
    "mock": _mock,
}


def available() -> list[str]:
    return list(_BUILDERS)


def build(platform_key: str) -> Adapter:
    if platform_key not in _BUILDERS:
        raise SystemExit(
            f"Unknown platform '{platform_key}'. Known: {', '.join(available())}"
        )
    return _BUILDERS[platform_key](cfg.PLATFORMS[platform_key])
