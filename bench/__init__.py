"""Graph database benchmark harness.

Everything in here is platform-agnostic except `bench.adapters`, which is
where each database's dialect lives. If you want to add a database, write an
adapter and register it in `bench/adapters/__init__.py` -- nothing else needs
to change.
"""

__version__ = "1.0.0"
