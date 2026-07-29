"""Manual Stage 10 live CAD measurement check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from gebal_cad_normalizer.cad import (
    CanonicalizationError,
    DxfInventoryError,
    LayerClassificationConfig,
    MeasurementConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    measure_geometry,
    write_measurement_json,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _top(result, role: str) -> dict[str, object] | None:
    candidates = [candidate for candidate in result.candidates if candidate.role == role]
    if not candidates:
        return None
    candidate = sorted(candidates, key=lambda item: (-item.confidence, item.candidate_id))[0]
    return {
        "candidate_id": candidate.candidate_id,
        "layer": candidate.source_layer,
        "width": candidate.width,
        "depth": candidate.depth,
        "width_mm": candidate.width_mm,
        "depth_mm": candidate.depth_mm,
        "area": candidate.area,
        "confidence": candidate.confidence,
        "unit_status": candidate.unit_status,
        "warnings": candidate.warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Stage 10 deterministic CAD measurement.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--vendor-profile", help="Optional built-in vendor layer profile, for example bluestone_playground.")
    parser.add_argument("--unit", choices=("mm", "cm", "m", "in"), help="Explicit drawing-unit override.")
    parser.add_argument("--width", type=float, help="Expected JSON/product width in millimetres for optional unit inference.")
    parser.add_argument("--depth", type=float, help="Expected JSON/product depth in millimetres for optional unit inference.")
    parser.add_argument("--json-output", help="Optional deterministic measurement JSON output path.")
    args = parser.parse_args()

    input_path = Path(args.input)
    try:
        before = _sha256(input_path)
        inventory = inventory_dxf(input_path)
        canonical = canonicalize_dxf(input_path, tessellate_curves=True, tessellation_tolerance=1.0)
        class_config = LayerClassificationConfig(vendor_profile=args.vendor_profile) if args.vendor_profile else None
        classification = classify_layers(inventory, canonical, class_config)
        measure_config = MeasurementConfig(explicit_unit=args.unit, expected_width_mm=args.width, expected_depth_mm=args.depth)
        result = measure_geometry(inventory, canonical, classification, measure_config)
        if args.json_output:
            write_measurement_json(result, Path(args.json_output))
        after = _sha256(input_path)
    except (DxfInventoryError, CanonicalizationError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    issue_counts = Counter(issue.code for issue in result.issues)
    role_counts = Counter(candidate.role for candidate in result.candidates)
    summary = {
        "success": before == after,
        "candidate_counts": dict(sorted(role_counts.items())),
        "top_product": _top(result, "product_geometry"),
        "top_safety": _top(result, "safety_zone"),
        "unit_status": result.unit_status,
        "inferred_unit": result.inferred_unit,
        "scale_to_mm": result.scale_to_mm,
        "issue_codes": dict(sorted(issue_counts.items())),
        "source_checksum_unchanged": before == after,
        "json_output": args.json_output,
        "vendor_profile": args.vendor_profile,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if before == after else 1


if __name__ == "__main__":
    sys.exit(main())

