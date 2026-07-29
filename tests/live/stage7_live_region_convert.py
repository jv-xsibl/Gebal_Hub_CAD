"""Manual Stage 7 live REGION conversion check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from gebal_cad_normalizer.cad import convert_regions
from gebal_cad_normalizer.exceptions import CadNormalizerError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 7 DXF REGION to closed-polyline conversion.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--output", help="Optional output .dxf copy path. REGION replacement happens only when provided.")
    parser.add_argument("--tolerance", type=float, default=1e-6, help="Absolute area/perimeter deviation tolerance.")
    args = parser.parse_args()

    input_path = Path(args.input)
    try:
        before = _sha256(input_path)
        result = convert_regions(input_path, output_path=Path(args.output) if args.output else None, tolerance=args.tolerance)
        after = _sha256(input_path)
    except (CadNormalizerError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    area_deviations = [region.area_deviation for region in result.regions if region.area_deviation is not None]
    summary = {
        "success": before == after and result.failed_count == 0,
        "converted_count": result.converted_count,
        "failed_count": result.failed_count,
        "loop_count": result.loop_count,
        "approximation_count": result.approximated_count,
        "area_deviations": area_deviations,
        "issue_codes": sorted({issue.code for issue in result.issues}),
        "source_checksum_unchanged": before == after,
        "output_path": str(result.output_path) if result.output_path else None,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
