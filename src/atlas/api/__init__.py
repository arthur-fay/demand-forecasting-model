"""ATLAS HTTP API (milestone M5): a FastAPI service over the M2-M4 research outputs.

Layers:

* transport: :mod:`atlas.api.routers` (HTTP), :mod:`atlas.api.schemas` (contracts),
  :mod:`atlas.api.middleware`, :mod:`atlas.api.errors`, :mod:`atlas.api.dependencies`;
* application: :mod:`atlas.api.reference` (committed outputs, checked at startup),
  :mod:`atlas.api.views` (response data), :mod:`atlas.api.simulation` (bounded M4
  simulations in worker processes), :mod:`atlas.api.settings`, :mod:`atlas.api.logs`;
* domain: the existing ATLAS modules, reused unchanged.

Start it with ``atlas api serve`` (see ``docs/API.md``).
"""

from atlas.api.app import create_app

__all__ = ["create_app"]
