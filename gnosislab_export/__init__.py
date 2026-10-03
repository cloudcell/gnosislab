"""gnosislab provenance export — read-only emission of lab state.

Snapshot-once, emit-many: ``common.snapshot`` reads the servers' state
stores over immutable sqlite connections; each format subpackage
(``ro_crate``, ``prov``, ``jsonld``) is a thin emitter over the
resulting Snapshot. Nothing here is imported by, or imports, the
``ml_*_mcp`` server packages — the dependency arrow is one way.
"""

__version__ = "0.1.0"
