"""gnosislab-export — emit lab provenance in interchange formats.

    gnosislab-export --programme prog-abc123 --format ro-crate --out out/
    python -m gnosislab_export --trial trl-xyz --format ro-crate --zip

Reads state read-only; never writes to any store; runs against a
stopped stack.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common.snapshot import (
    DEFAULT_ANAMNESIS_DB,
    DEFAULT_EPISTEME_DB,
    load_snapshot,
)

_FORMATS = ("ro-crate", "prov", "jsonld")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gnosislab-export",
        description="Export gnosislab provenance (read-only).",
    )
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--programme", help="export one programme + its closure")
    scope.add_argument("--trial", help="export one trial + its closure")
    scope.add_argument(
        "--full", action="store_true", help="export the whole store"
    )
    p.add_argument("--format", choices=_FORMATS, required=True)
    p.add_argument(
        "--out",
        default="export-out",
        help="output directory (or .zip path with --zip)",
    )
    p.add_argument("--zip", action="store_true", help="also emit <out>.zip")
    p.add_argument("--episteme-db", default=DEFAULT_EPISTEME_DB)
    p.add_argument("--anamnesis-db", default=DEFAULT_ANAMNESIS_DB)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        snapshot = load_snapshot(
            episteme_db=args.episteme_db,
            anamnesis_db=args.anamnesis_db,
            programme_id=args.programme,
            trial_id=args.trial,
            full=args.full,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"export: {e}", file=sys.stderr)
        return 1

    if args.format == "ro-crate":
        from .ro_crate.exporter import export_rocrate as export

    elif args.format == "prov":
        from .prov.exporter import export_prov as export

    else:
        from .jsonld.exporter import export_jsonld as export

    try:
        path = export(snapshot, Path(args.out), zip_it=args.zip)
    except Exception as e:
        print(f"export: {e}", file=sys.stderr)
        return 1

    counts = {
        "programmes": len(snapshot.programmes),
        "trials": len(snapshot.trials),
        "artifacts": len(snapshot.artifacts),
        "claims": len(snapshot.claims),
    }
    print(f"export: {path}")
    print(f"export: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
