"""Manual Stage 6 live geometry canonicalization check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from gebal_cad_normalizer.cad import CanonicalizationError, canonicalize_dxf, write_canonical_json


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Stage 6 DXF geometry canonicalization.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--json-output", help="Optional deterministic full JSON output path.")
    parser.add_argument("--tessellation-tolerance", type=float, help="Enable opt-in curve tessellation with this positive tolerance.")
    args = parser.parse_args()

    input_path = Path(args.input)
    try:
        before = _sha256(input_path)
        result = canonicalize_dxf(
            input_path,
            tessellate_curves=args.tessellation_tolerance is not None,
            tessellation_tolerance=args.tessellation_tolerance,
        )
        if args.json_output:
            write_canonical_json(result, Path(args.json_output))
        after = _sha256(input_path)
    except (CanonicalizationError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    checksum_unchanged = before == after
    summary = {
        "success": checksum_unchanged,
        "source_entity_count": result.total_source_entities_visited,
        "canonical_entity_count": result.total_canonical_entities,
        "counts_by_status": result.counts_by_status,
        "counts_by_canonical_type": result.counts_by_canonical_type,
        "extents": result.canonical_extents,
        "issue_codes": [issue.code for issue in result.issues],
        "source_sha256": result.source_sha256,
        "source_checksum_unchanged": checksum_unchanged,
        "tessellation_enabled": result.tessellation_enabled,
        "tessellation_tolerance": result.tessellation_tolerance,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if checksum_unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
